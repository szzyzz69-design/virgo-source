"""SIM diagnostics over the existing mobile transport, without chat records."""
import logging
import re
import secrets
import time

from psycopg.rows import dict_row

from app.schemas.message import normalize_phone
from app.schemas.message_pull import MessagePullItem, TextMessage, utc_iso_from_millis
from app.schemas.message_status import Status, to_utc_millis

logger = logging.getLogger(__name__)
PREFIX = 'VIRGO-CHECK:'
TOKEN = re.compile(r'VIRGO-CHECK:([0-9a-f]{32})')
LABELS = {'NOT_TESTED': '未测试', 'PENDING': '检测中', 'NORMAL': '正常', 'PROBLEM': '有问题，需要检查'}


def phone_key(value):
    """Compare full numbers; accept NANP national format, never suffix matching."""
    try:
        value = normalize_phone(value or '').lstrip('+')
    except ValueError:
        return None
    if len(value) == 10:
        value = '1' + value
    return value


def now_ms():
    return time.time_ns() // 1_000_000


class CheckConflict(ValueError):
    pass


class SmsCheckService:
    def __init__(self, database, publisher=None):
        self.database = database
        self.publisher = publisher

    @staticmethod
    def expire(connection, now):
        connection.execute("""UPDATE sms_checks SET status='PROBLEM',
            reason=CASE WHEN pulled_at IS NULL THEN '设备未拉取检测指令（离线或未连接）'
                        WHEN sent_at IS NULL THEN '未收到发送成功回执'
                        ELSE '接收超时：未收到发送号码和内容均匹配的短信' END,
            updated_at=%s WHERE status='PENDING' AND deadline_at <= %s""", (now, now))

    def start(self, target_phone, timeout_seconds=300, request_key=None):
        target_phone = normalize_phone(target_phone)
        if not 30 <= timeout_seconds <= 3600:
            raise ValueError('等待时间必须为 30–3600 秒')
        request_key = request_key or secrets.token_hex(16)
        if not request_key.strip() or len(request_key) > 200:
            raise ValueError('检测请求标识无效')
        now = now_ms()
        notifications = []
        with self.database.transaction() as connection:
            # Serialize launches so double clicks and concurrent admins cannot fan out twice.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('sms-check-launch',0))")
            existing = connection.execute('SELECT id,target_phone,timeout_seconds FROM sms_check_runs WHERE request_key=%s', (request_key,)).fetchone()
            if existing:
                if phone_key(existing[1]) != phone_key(target_phone) or existing[2] != timeout_seconds:
                    raise CheckConflict('同一请求标识已用于不同的检测参数')
                return existing[0]
            self.expire(connection, now)
            if connection.execute("SELECT 1 FROM sms_checks WHERE status='PENDING' LIMIT 1").fetchone():
                raise CheckConflict('已有检测正在进行，请等待完成后再开始')
            sims = connection.execute('''SELECT s.id,s.device_id,s.sim_number,s.phone_number,s.iccid_hash,
                s.enabled,s.status,d.enabled,d.name FROM sim_cards s JOIN devices d ON d.id=s.device_id
                WHERE s.unregistered_at IS NULL AND d.unregistered_at IS NULL
                    AND d.enabled AND s.enabled AND s.status='active'
                    AND NULLIF(btrim(s.phone_number),'') IS NOT NULL
                ORDER BY s.device_id,s.sim_number''').fetchall()
            receivers = [s for s in sims if phone_key(s[3]) == phone_key(target_phone) and s[5] and s[6] == 'active' and s[7]]
            if len(receivers) > 1:
                raise ValueError('接收号码对应多张基站 SIM，请先修正重复号码')
            receiver = receivers[0] if receivers else None
            run_id = 'checkrun_' + secrets.token_hex(16)
            deadline = now + timeout_seconds * 1000
            connection.execute('''INSERT INTO sms_check_runs
                (id,request_key,target_phone,receiver_device_id,receiver_sim_number,timeout_seconds,created_at,deadline_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                (run_id, request_key, target_phone, receiver[1] if receiver else None, receiver[2] if receiver else None, timeout_seconds, now, deadline))
            for sim in sims:
                reason = ''
                if phone_key(sim[3]) == phone_key(target_phone):
                    reason = '本轮接收号码，请更换接收号码后检测此卡'
                elif not sim[7]:
                    reason = '设备未启用'
                elif not sim[5] or sim[6] != 'active':
                    reason = 'SIM 未启用或未处于有效状态'
                elif not phone_key(sim[3]):
                    reason = '未填写有效的发送手机号，无法核对'
                token = secrets.token_hex(16)
                check_id = 'check_' + token
                connection.execute('''INSERT INTO sms_checks
                    (id,run_id,sim_card_id,device_id,sim_number,source_phone,source_iccid_hash,token,text_content,
                     status,reason,created_at,deadline_at,updated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (check_id,run_id,sim[0],sim[1],sim[2],sim[3],sim[4],token,PREFIX+token,
                     'NOT_TESTED' if reason else 'PENDING',reason,now,deadline,now))
                if not reason:
                    notifications.append((sim[1], check_id))
        for device_id, check_id in notifications:
            if self.publisher:
                try:
                    self.publisher.publish(device_id, check_id)
                except Exception:
                    logger.exception('Diagnostic notification failed; device polling will recover')
        return run_id

    def pull(self, connection, device_id, now, limit):
        if limit <= 0:
            return []
        self.expire(connection, now)
        rows = connection.execute('''SELECT c.id,c.text_content,c.sim_number,r.target_phone,c.created_at,c.deadline_at,
                c.source_phone,c.source_iccid_hash,s.phone_number,s.iccid_hash
            FROM sms_checks c JOIN sms_check_runs r ON r.id=c.run_id
            JOIN sim_cards s ON s.id=c.sim_card_id
            WHERE c.device_id=%s AND c.status='PENDING' AND c.pulled_at IS NULL
                AND c.deadline_at>%s AND s.enabled AND s.status='active'
            ORDER BY c.created_at,c.id LIMIT %s FOR UPDATE OF c SKIP LOCKED''', (device_id,now,limit)).fetchall()
        items = []
        for row in rows:
            if phone_key(row[6]) != phone_key(row[8]) or row[7] != row[9]:
                connection.execute("UPDATE sms_checks SET status='PROBLEM',reason='SIM 或手机号已变更，请重新检测',updated_at=%s WHERE id=%s", (now,row[0]))
                continue
            connection.execute("UPDATE sms_checks SET pulled_at=%s,transport_state='Processed',updated_at=%s WHERE id=%s", (now,now,row[0]))
            items.append(MessagePullItem(id=row[0],textMessage=TextMessage(text=row[1]),dataMessage=None,
                phoneNumbers=[row[3]],simNumber=row[2],withDeliveryReport=True,isEncrypted=False,
                validUntil=utc_iso_from_millis(row[5]),scheduleAt=None,priority=-1,createdAt=utc_iso_from_millis(row[4])))
        return items

    def update(self, connection, device_id, request, index, now):
        # Imported here to keep the transport service free of an import cycle.
        from app.services.message_state_service import MessageStatusNotFound, MessageStatusForbidden, MessageStatusValidation
        row = connection.execute('''SELECT c.device_id,c.status,c.pulled_at,c.sent_at,c.received_at,c.deadline_at,r.target_phone
            FROM sms_checks c JOIN sms_check_runs r ON r.id=c.run_id WHERE c.id=%s FOR UPDATE OF c''', (request.id,)).fetchone()
        if not row:
            raise MessageStatusNotFound(index,request.id,'diagnostic not found')
        if row[0] != device_id:
            raise MessageStatusForbidden(index,request.id,'diagnostic belongs to another device')
        if row[2] is None or len(request.recipients) != 1 or phone_key(request.recipients[0].phone_number) != phone_key(row[6]) or request.recipients[0].state != request.state:
            raise MessageStatusValidation(index,request.id,'diagnostic recipient or state is invalid')
        state_times = {state: to_utc_millis(value) for state,value in request.states.items()}
        if any(value > now + 300_000 for value in state_times.values()):
            raise MessageStatusValidation(index,request.id,'state time is too far in the future')
        if request.state in {Status.SENT,Status.DELIVERED} and not row[3] and Status.SENT not in state_times:
            raise MessageStatusValidation(index,request.id,'Sent time is required')
        # Terminal results and late/duplicate callbacks cannot overwrite a newer outcome.
        if row[1] != 'PENDING':
            return
        if row[5] <= now:
            self.expire(connection,now)
            return
        sent_at = row[3] or state_times.get(Status.SENT)
        status = 'PENDING'
        reason = '已发送，等待接收端核对' if sent_at else '指令已下发，等待发送回执'
        if request.state == Status.FAILED:
            status,reason = 'PROBLEM','发送失败：' + (request.recipients[0].error or '设备未提供原因')
        elif sent_at and row[4]:
            status,reason = 'NORMAL','发送成功，接收号码与短信内容核对通过'
        connection.execute('''UPDATE sms_checks SET status=%s,reason=%s,transport_state=%s,
            sent_at=%s,updated_at=%s WHERE id=%s''', (status,reason,request.state.value,sent_at,now,request.id))

    def receive(self, connection, device_id, request, now, *, manual=False):
        text = request.text_message.text if request.text_message else ''
        match = TOKEN.search(text)
        if not match and not text.startswith(PREFIX):
            return None
        # Reserve this protocol namespace, including damaged/late diagnostic SMS.
        if not match:
            return 'check_ignored'
        check_id = 'check_' + match[1]
        row = connection.execute('''SELECT c.status,c.source_phone,c.text_content,c.sent_at,c.deadline_at,
                r.receiver_device_id,r.receiver_sim_number,r.target_phone
            FROM sms_checks c JOIN sms_check_runs r ON r.id=c.run_id WHERE c.id=%s FOR UPDATE OF c''', (check_id,)).fetchone()
        if not row or row[0] != 'PENDING' or (not manual and device_id != row[5]):
            return check_id
        if row[4] <= now:
            self.expire(connection,now)
            return check_id
        # Require a receiving SIM identity; never credit a different SIM on the same phone.
        recipient_matches = phone_key(request.recipient) == phone_key(row[7]) if request.recipient else None
        sim_matches = request.sim_number == row[6] if request.sim_number is not None else None
        if (sim_matches is not True and recipient_matches is not True) or sim_matches is False or recipient_matches is False:
            reason = '接收 SIM 或接收手机号不匹配'
        elif phone_key(request.sender) != phone_key(row[1]):
            reason = '实际发送手机号与登记号码不匹配'
        elif text != row[2]:
            reason = '收到的检测短信内容不匹配'
        else:
            reason = None
        status = 'PROBLEM' if reason else ('NORMAL' if row[3] else 'PENDING')
        connection.execute('''UPDATE sms_checks SET status=%s,reason=%s,received_at=%s,receipt_source=%s,updated_at=%s WHERE id=%s''',
            (status,reason or ('发送成功，接收号码与短信内容核对通过' if row[3] else '接收核对通过，等待发送成功回执'),
             now if not reason else None,'MANUAL' if manual else 'DEVICE',now,check_id))
        return check_id

    def record_receipt(self, run_id, sender, text):
        from types import SimpleNamespace
        match = TOKEN.search(text)
        if not match:
            raise ValueError('短信内容中缺少有效的检测标识，请完整粘贴收到的短信')
        with self.database.transaction() as connection:
            row = connection.execute('''SELECT r.target_phone,r.receiver_sim_number,c.status,c.deadline_at
                FROM sms_checks c JOIN sms_check_runs r ON r.id=c.run_id
                WHERE c.id=%s AND r.id=%s FOR UPDATE OF c''', ('check_'+match[1],run_id)).fetchone()
            if not row:
                raise ValueError('这条短信不属于所选检测批次')
            if row[2] != 'PENDING' or row[3] <= now_ms():
                raise ValueError('本条检测已结束，请重新发起检测')
            request = SimpleNamespace(text_message=SimpleNamespace(text=text), sender=normalize_phone(sender),
                recipient=row[0],sim_number=row[1])
            return self.receive(connection,None,request,now_ms(),manual=True)

    def results(self, run_id=None):
        now = now_ms()
        with self.database.transaction() as connection:
            self.expire(connection,now)
            with connection.cursor(row_factory=dict_row) as cursor:
                if run_id:
                    cursor.execute('''SELECT c.*,d.name AS device_name,r.target_phone,s.areas FROM sms_checks c
                        JOIN sms_check_runs r ON r.id=c.run_id LEFT JOIN devices d ON d.id=c.device_id
                        LEFT JOIN sim_cards s ON s.id=c.sim_card_id
                        WHERE c.run_id=%s AND d.unregistered_at IS NULL AND s.unregistered_at IS NULL
                            AND d.enabled AND s.enabled AND s.status='active'
                            AND NULLIF(btrim(s.phone_number),'') IS NOT NULL
                        ORDER BY c.device_id,c.sim_number''',(run_id,))
                else:
                    cursor.execute('''SELECT s.id AS sim_card_id,s.device_id,s.sim_number,s.phone_number AS source_phone,
                        d.name AS device_name,s.areas,c.id,c.run_id,c.status,c.reason,c.created_at,c.sent_at,c.received_at,c.receipt_source,
                        c.source_phone AS tested_phone,c.source_iccid_hash,s.iccid_hash AS current_iccid,r.target_phone
                        FROM sim_cards s JOIN devices d ON d.id=s.device_id
                        LEFT JOIN LATERAL (SELECT * FROM sms_checks WHERE sim_card_id=s.id ORDER BY created_at DESC,id DESC LIMIT 1) c ON TRUE
                        LEFT JOIN sms_check_runs r ON r.id=c.run_id
                        WHERE s.unregistered_at IS NULL AND d.unregistered_at IS NULL
                            AND d.enabled AND s.enabled AND s.status='active'
                            AND NULLIF(btrim(s.phone_number),'') IS NOT NULL
                        ORDER BY s.device_id,s.sim_number''')
                rows = cursor.fetchall()
        for row in rows:
            row['status'] = row['status'] or 'NOT_TESTED'
            if not run_id and row.get('id') and (phone_key(row['tested_phone']) != phone_key(row['source_phone']) or row['source_iccid_hash'] != row['current_iccid']):
                row['status'],row['reason'] = 'NOT_TESTED','SIM 或手机号已变更，需要重新检测'
            row['status_label'] = LABELS[row['status']]
            row['reason'] = row['reason'] or ('尚未检测' if row['status'] == 'NOT_TESTED' else '等待设备拉取指令')
        return rows
