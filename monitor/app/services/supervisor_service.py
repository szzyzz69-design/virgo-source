import base64
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from psycopg.rows import dict_row

from app.database import Database


class SupervisorNotFound(Exception):
    pass


class SupervisorScopeError(Exception):
    pass


class SupervisorService:
    def __init__(self, database: Database):
        self._database = database

    @staticmethod
    def day_bounds_ms(timezone: str, now: datetime | None = None) -> tuple[int, int]:
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("invalid SUPERVISOR_TIMEZONE") from error
        local_now = (now or datetime.now(zone)).astimezone(zone)
        start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)

    def list_agents(self, timezone: str) -> list[dict]:
        start, end = self.day_bounds_ms(timezone)
        sql = """
        WITH last_messages AS (
          SELECT DISTINCT ON (m.conversation_id) m.conversation_id, m.direction, m.state
          FROM messages m ORDER BY m.conversation_id, m.created_at DESC, m.id DESC
        ), sim_stats AS (
          SELECT s.id,
            COUNT(DISTINCT CASE WHEN m.direction='INBOUND' AND m.created_at >= %s AND m.created_at < %s
              THEN COALESCE(m.from_phone_number, c.external_phone_number) END) AS today_customers,
            COUNT(DISTINCT CASE WHEN lm.direction='INBOUND' OR (lm.direction='OUTBOUND' AND lm.state='Failed')
              THEN c.id END) AS waiting
          FROM sim_cards s LEFT JOIN conversations c ON c.sim_card_id=s.id
          LEFT JOIN messages m ON m.conversation_id=c.id LEFT JOIN last_messages lm ON lm.conversation_id=c.id
          GROUP BY s.id
        )
        SELECT a.id account_id, a.username, a.areas account_area,
          s.id sim_card_id, s.phone_number,
          NULLIF(BTRIM(s.areas),'') note,
          COALESCE(ss.today_customers,0) today_customers,
          COALESCE(ss.waiting,0) waiting
        FROM accounts a LEFT JOIN account_sim_cards acs ON acs.account_id=a.id
        LEFT JOIN sim_cards s ON s.id=acs.sim_card_id LEFT JOIN sim_stats ss ON ss.id=s.id
        WHERE a.status='ACTIVE'
        ORDER BY a.areas NULLS LAST,a.username,s.phone_number NULLS LAST,s.id
        """
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, (start, end))
                rows = cursor.fetchall()
        accounts: dict[str, dict] = {}
        for row in rows:
            account = accounts.setdefault(row["account_id"], {
                "id": row["account_id"], "username": row["username"],
                "area": row["account_area"],
                "todayCustomers": 0, "waiting": 0, "simCards": [],
            })
            if row["sim_card_id"] is not None:
                account["simCards"].append({
                    "id": row["sim_card_id"], "phoneNumber": row["phone_number"],
                    "note": row["note"], "todayCustomers": row["today_customers"],
                    "waiting": row["waiting"],
                })
        totals_sql = """
        WITH last_messages AS (
          SELECT DISTINCT ON (conversation_id) conversation_id,direction,state
          FROM messages ORDER BY conversation_id,created_at DESC,id DESC
        )
        SELECT a.id,a.areas,
          COUNT(DISTINCT CASE WHEN m.direction='INBOUND' AND m.created_at >= %s AND m.created_at < %s
            THEN COALESCE(m.from_phone_number,c.external_phone_number) END),
          COUNT(DISTINCT CASE WHEN lm.direction='INBOUND' OR (lm.direction='OUTBOUND' AND lm.state='Failed') THEN c.id END)
        FROM accounts a LEFT JOIN account_sim_cards acs ON acs.account_id=a.id
        LEFT JOIN conversations c ON c.sim_card_id=acs.sim_card_id
        LEFT JOIN messages m ON m.conversation_id=c.id LEFT JOIN last_messages lm ON lm.conversation_id=c.id
        WHERE a.status='ACTIVE' GROUP BY a.id,a.areas
        """
        with self._database.transaction() as connection:
            account_totals = connection.execute(totals_sql, (start, end)).fetchall()
        for account_id, _area, today, waiting in account_totals:
            accounts[account_id].update(todayCustomers=today, waiting=waiting)

        area_totals_sql = """
        WITH last_messages AS (
          SELECT DISTINCT ON (conversation_id) conversation_id,direction,state
          FROM messages ORDER BY conversation_id,created_at DESC,id DESC
        )
        SELECT a.areas,
          COUNT(DISTINCT CASE WHEN m.direction='INBOUND' AND m.created_at >= %s AND m.created_at < %s
            THEN COALESCE(m.from_phone_number,c.external_phone_number) END),
          COUNT(DISTINCT CASE WHEN lm.direction='INBOUND' OR (lm.direction='OUTBOUND' AND lm.state='Failed') THEN c.id END)
        FROM accounts a LEFT JOIN account_sim_cards acs ON acs.account_id=a.id
        LEFT JOIN conversations c ON c.sim_card_id=acs.sim_card_id
        LEFT JOIN messages m ON m.conversation_id=c.id LEFT JOIN last_messages lm ON lm.conversation_id=c.id
        WHERE a.status='ACTIVE' GROUP BY a.areas
        """
        with self._database.transaction() as connection:
            area_totals = connection.execute(area_totals_sql, (start, end)).fetchall()
        areas = {
            area: {"area": area, "todayCustomers": today, "waiting": waiting, "accounts": []}
            for area, today, waiting in area_totals
        }
        for account in accounts.values():
            areas.setdefault(account["area"], {
                "area": account["area"], "todayCustomers": 0,
                "waiting": 0, "accounts": [],
            })["accounts"].append(account)
        return sorted(areas.values(), key=lambda item: (item["area"] is None, item["area"] or ""))

    def list_conversations(
        self, account_id: str, sim_card_id: str | None, status: str,
        search: str, limit: int, cursor: str | None,
    ) -> dict:
        limit = min(max(limit, 1), 50)
        before_at, before_id = self._decode_cursor(cursor)
        params: list = [account_id]
        where = ["acs.account_id=%s", "a.status='ACTIVE'", "c.status IN ('OPEN','CLOSED','ARCHIVED')"]
        if sim_card_id:
            where.append("c.sim_card_id=%s")
            params.append(sim_card_id)
        if search:
            where.append("(c.external_phone_number ILIKE %s OR COALESCE(ct.remark,'') ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])
        if status != "all":
            expression = "CASE WHEN lm.direction='OUTBOUND' AND lm.state='Failed' THEN 'failed' WHEN lm.direction='INBOUND' THEN 'waiting' ELSE 'replied' END"
            where.append(f"{expression}=%s")
            params.append(status)
        if before_at is not None:
            where.append("(COALESCE(lm.created_at,0),c.id)<(%s,%s)")
            params.extend([before_at, before_id])
        params.append(limit + 1)
        sql = f"""
        WITH latest AS (
          SELECT DISTINCT ON (conversation_id) conversation_id,id,direction,state,created_at,text_content
          FROM messages ORDER BY conversation_id,created_at DESC,id DESC
        )
        SELECT c.id,c.contact_id,c.external_phone_number,ct.remark customer_remark,
          s.phone_number,NULLIF(BTRIM(s.areas),'') note,a.id account_id,a.username,
          lm.id message_id,lm.created_at,lm.direction,lm.state,lm.text_content
        FROM conversations c
        JOIN contacts ct ON ct.id=c.contact_id
        JOIN account_sim_cards acs ON acs.sim_card_id=c.sim_card_id
        JOIN accounts a ON a.id=acs.account_id
        LEFT JOIN sim_cards s ON s.id=c.sim_card_id
        LEFT JOIN latest lm ON lm.conversation_id=c.id
        WHERE {' AND '.join(where)}
        ORDER BY lm.created_at DESC NULLS LAST,c.id DESC LIMIT %s
        """
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor_handle:
                cursor_handle.execute(sql, params)
                rows = [dict(row) for row in cursor_handle.fetchall()]
        has_more = len(rows) > limit
        rows = rows[:limit]
        return {
            "items": [self._conversation_json(row) for row in rows],
            "nextCursor": self._encode_cursor(rows[-1]["created_at"], rows[-1]["id"])
            if has_more and rows else None,
        }

    def get_conversation(self, conversation_id: str, account_id: str) -> dict:
        row = self._conversation_context(conversation_id, account_id)
        return self._conversation_json(row)

    def list_messages(
        self,
        conversation_id: str,
        account_id: str,
        limit: int,
        before: int | None,
    ) -> dict:
        self._conversation_context(conversation_id, account_id)
        limit = min(max(limit, 1), 100)
        params: list = [conversation_id]
        condition = "" if before is None else "AND created_at < %s"
        if before is not None:
            params.append(before)
        params.append(limit + 1)
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor_handle:
                cursor_handle.execute(
                    f"""SELECT id,direction,message_type,text_content,state,created_at,
                      received_at,sent_at,delivered_at,error_code,error_message
                    FROM messages WHERE conversation_id=%s {condition}
                    ORDER BY created_at DESC,id DESC LIMIT %s""",
                    params,
                )
                rows = [dict(row) for row in cursor_handle.fetchall()]
        more = len(rows) > limit
        rows = rows[:limit]
        rows.reverse()
        return {
            "items": [{
                "id": row["id"], "direction": row["direction"],
                "messageType": row["message_type"], "text": row["text_content"],
                "state": row["state"], "createdAt": row["created_at"],
                "receivedAt": row["received_at"], "sentAt": row["sent_at"],
                "deliveredAt": row["delivered_at"],
                "errorCode": row["error_code"], "errorMessage": row["error_message"],
            } for row in rows],
            "nextBefore": rows[0]["created_at"] if more and rows else None,
        }

    def _conversation_context(self, conversation_id: str, account_id: str) -> dict:
        sql = """
        SELECT c.id,c.contact_id,c.external_phone_number,ct.remark customer_remark,
          s.phone_number,NULLIF(BTRIM(s.areas),'') note,a.id account_id,a.username,
          a.areas account_areas,lm.id message_id,lm.created_at,
          lm.direction,lm.state,lm.text_content
        FROM conversations c
        JOIN contacts ct ON ct.id=c.contact_id
        JOIN account_sim_cards acs ON acs.sim_card_id=c.sim_card_id
        JOIN accounts a ON a.id=acs.account_id AND a.status='ACTIVE'
        LEFT JOIN sim_cards s ON s.id=c.sim_card_id
        LEFT JOIN LATERAL (
          SELECT id,direction,state,created_at,text_content FROM messages
          WHERE conversation_id=c.id ORDER BY created_at DESC,id DESC LIMIT 1
        ) lm ON TRUE
        WHERE c.id=%s AND a.id=%s
          AND c.status IN ('OPEN','CLOSED','ARCHIVED')
        """
        with self._database.transaction() as connection:
            with connection.cursor(row_factory=dict_row) as cursor_handle:
                cursor_handle.execute(sql, (conversation_id, account_id))
                row = cursor_handle.fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 FROM conversations WHERE id=%s",
                    (conversation_id,),
                ).fetchone()
        if row is None:
            if exists is not None:
                raise SupervisorScopeError(
                    "Conversation is not bound to the selected active account"
                )
            raise SupervisorNotFound
        return dict(row)

    @staticmethod
    def _conversation_json(row: dict) -> dict:
        direction = row.get("direction")
        message_state = row.get("state")
        reply_state = "failed" if direction == "OUTBOUND" and message_state == "Failed" else (
            "waiting" if direction == "INBOUND" else "replied"
        )
        last_message = None
        if row.get("message_id") is not None:
            last_message = {
                "id": row["message_id"], "direction": direction,
                "text": row.get("text_content"), "state": message_state,
                "createdAt": row.get("created_at"),
            }
        return {
            "id": row["id"], "contactId": row.get("contact_id"),
            "customerPhoneNumber": row["external_phone_number"],
            "customerRemark": row.get("customer_remark"),
            "servicePhoneNumber": row.get("phone_number"), "note": row.get("note"),
            "accountId": row.get("account_id"), "accountUsername": row["username"],
            "lastMessage": last_message,
            "lastMessageAt": row.get("created_at"), "replyStatus": reply_state,
        }

    @staticmethod
    def _encode_cursor(at: int | None, conversation_id: str) -> str:
        return base64.urlsafe_b64encode(json.dumps([at or 0, conversation_id]).encode()).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(value: str | None):
        if not value:
            return None, None
        try:
            at, conversation_id = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
            return int(at), str(conversation_id)
        except Exception as error:
            raise ValueError("invalid cursor") from error
