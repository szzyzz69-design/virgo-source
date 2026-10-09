import hashlib
import hmac
import json
import secrets
import time

from app.database import Database
from app.schemas.message_delivery import DeliveryAck, DeliveryClaim, ReliableMessagePullItem, payload_hash
from app.schemas.message_pull import utc_iso_from_millis
from app.services.message_pull_service import MessagePullService, PullDeviceUnavailable


class DeliveryError(Exception):
    def __init__(self, code: str, message_id: str, status: int = 409):
        self.code = code
        self.message_id = message_id
        self.status = status


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def sim_fingerprint(row) -> str:
    """A server-side route fence; clients additionally fence physical subscriptions."""
    return hashlib.sha256(json.dumps(list(row), separators=(",", ":")).encode("utf-8")).hexdigest()


class MessageDeliveryService:
    LIMIT = 10
    LEASE_MILLIS = 60_000

    def __init__(self, database: Database, sms_checks=None):
        self._database = database
        self._sms_checks = sms_checks
        self._legacy = MessagePullService(database)

    @staticmethod
    def _require_device(connection, device_id: str):
        row = connection.execute(
            "SELECT enabled FROM devices WHERE id=%s FOR UPDATE", (device_id,),
        ).fetchone()
        if row is None or not row[0]:
            raise PullDeviceUnavailable

    @staticmethod
    def _recipients(connection, ids):
        result = {message_id: [] for message_id in ids}
        if ids:
            for message_id, phone in connection.execute(
                "SELECT message_id,phone_number FROM message_recipients "
                "WHERE message_id=ANY(%s::varchar[]) ORDER BY message_id,id", (ids,),
            ).fetchall():
                result[message_id].append(phone)
        return result

    def pull(self, device_id: str, order: str) -> list[ReliableMessagePullItem]:
        if order not in {"fifo", "lifo"}:
            raise ValueError("order must be fifo or lifo")
        direction = "ASC" if order == "fifo" else "DESC"
        with self._database.transaction() as connection:
            self._require_device(connection, device_id)
            now = time.time_ns() // 1_000_000
            connection.execute(
                "UPDATE devices SET status='online',last_seen_at=%s,updated_at=%s WHERE id=%s",
                (now, now, device_id),
            )
            rows = connection.execute(
                f"""
                SELECT m.id,m.message_type,m.text_content,m.data_base64,m.data_port,m.sim_number,
                       m.with_delivery_report,m.is_encrypted,m.valid_until,m.schedule_at,m.priority,m.created_at,
                       s.id,s.device_id,s.slot_index,s.sim_number,s.phone_number,s.iccid_hash
                FROM messages m JOIN sim_cards s ON s.id=m.sim_card_id
                LEFT JOIN message_deliveries d ON d.message_id=m.id
                WHERE m.device_id=%s AND m.direction='OUTBOUND' AND m.state='Pending'
                    AND (m.valid_until IS NULL OR m.valid_until>%s)
                    AND (m.schedule_at IS NULL OR m.schedule_at<=%s)
                    AND s.enabled AND s.status='active'
                    AND s.device_id=m.device_id AND s.sim_number=m.sim_number
                    AND (d.message_id IS NULL OR
                         (d.device_id=m.device_id AND d.accepted_at IS NULL AND d.lease_expires_at<=%s))
                ORDER BY m.created_at {direction},m.id {direction}
                LIMIT {self.LIMIT} FOR UPDATE OF m SKIP LOCKED
                """,
                (device_id, now, now, now),
            ).fetchall()
            recipients = self._recipients(connection, [row[0] for row in rows])
            items = []
            for row in rows:
                item = self._legacy._to_item(row[:12], recipients[row[0]])
                digest = payload_hash(item)
                token = "dlv_" + secrets.token_urlsafe(32)
                expires = now + self.LEASE_MILLIS
                connection.execute(
                    """
                    INSERT INTO message_deliveries(message_id,device_id,claim_token_hash,payload_hash,
                        sim_fingerprint,claimed_at,lease_expires_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(message_id) DO UPDATE SET
                        claim_token_hash=EXCLUDED.claim_token_hash,payload_hash=EXCLUDED.payload_hash,
                        sim_fingerprint=EXCLUDED.sim_fingerprint,claimed_at=EXCLUDED.claimed_at,
                        lease_expires_at=EXCLUDED.lease_expires_at,attempt_count=message_deliveries.attempt_count+1
                    WHERE message_deliveries.accepted_at IS NULL
                    """,
                    (item.id, device_id, token_hash(token), digest, sim_fingerprint(row[12:]), now, expires),
                )
                items.append(ReliableMessagePullItem(
                    **item.model_dump(by_alias=True),
                    delivery=DeliveryClaim(token=token, payloadHash=digest, leaseExpiresAt=expires, simCardId=row[12]),
                    legacyTransport=False,
                ))
            if self._sms_checks:
                for item in self._sms_checks.pull(connection, device_id, now, self.LIMIT - len(items)):
                    items.append(ReliableMessagePullItem(
                        **item.model_dump(by_alias=True), delivery=None, legacyTransport=True,
                    ))
            return items

    def acknowledge(self, device_id: str, requests: list[DeliveryAck]) -> None:
        with self._database.transaction() as connection:
            self._require_device(connection, device_id)
            now = time.time_ns() // 1_000_000
            # Consistent lock order with status updates and pulls.
            for request in sorted(requests, key=lambda item: item.id):
                row = connection.execute(
                    """
                    SELECT m.state,m.direction,m.sim_card_id,m.sim_number,m.valid_until,m.schedule_at,
                           d.claim_token_hash,d.payload_hash,d.sim_fingerprint,d.lease_expires_at,d.accepted_at
                    FROM messages m JOIN message_deliveries d ON d.message_id=m.id
                    WHERE m.id=%s AND m.device_id=%s AND d.device_id=%s FOR UPDATE OF m,d
                    """,
                    (request.id, device_id, device_id),
                ).fetchone()
                if row is None:
                    raise DeliveryError("NOT_FOUND", request.id, 404)
                if not hmac.compare_digest(row[6], token_hash(request.token)):
                    raise DeliveryError("DELIVERY_STALE", request.id)
                if not hmac.compare_digest(row[7], request.payload_hash):
                    raise DeliveryError("DELIVERY_PAYLOAD_CHANGED", request.id)
                if row[10] is not None:
                    continue  # Same accepted token/hash remains idempotent after expiry.
                if row[9] <= now:
                    raise DeliveryError("DELIVERY_EXPIRED", request.id)
                if (row[0] != "Pending" or row[1] != "OUTBOUND"
                        or (row[4] is not None and row[4] <= now)
                        or (row[5] is not None and row[5] > now)):
                    raise DeliveryError("DELIVERY_UNAVAILABLE", request.id)
                sim = connection.execute(
                    "SELECT id,device_id,slot_index,sim_number,phone_number,iccid_hash,enabled,status "
                    "FROM sim_cards WHERE id=%s FOR SHARE", (row[2],),
                ).fetchone()
                if (sim is None or sim[1] != device_id or sim[3] != row[3]
                        or not sim[6] or sim[7] != "active"
                        or sim_fingerprint(sim[:6]) != row[8]):
                    raise DeliveryError("DELIVERY_UNAVAILABLE", request.id)
                payload = connection.execute(
                    "SELECT id,message_type,text_content,data_base64,data_port,sim_number,with_delivery_report,"
                    "is_encrypted,valid_until,schedule_at,priority,created_at FROM messages WHERE id=%s",
                    (request.id,),
                ).fetchone()
                recipients = self._recipients(connection, [request.id])[request.id]
                if payload_hash(self._legacy._to_item(payload, recipients)) != row[7]:
                    raise DeliveryError("DELIVERY_PAYLOAD_CHANGED", request.id)
                connection.execute(
                    "UPDATE message_deliveries SET accepted_at=%s WHERE message_id=%s", (now, request.id),
                )
                connection.execute(
                    "UPDATE messages SET state='Processed',pulled_at=%s,updated_at=%s WHERE id=%s",
                    (now, now, request.id),
                )
                connection.execute(
                    """
                    INSERT INTO message_state_history(message_id,state,source,reason,occurred_at,created_at)
                    VALUES(%s,'Processed','SERVER','Durably accepted by device (protocol 2)',%s,%s)
                    ON CONFLICT(message_id,state) DO NOTHING
                    """, (request.id, now, now),
                )

    def state(self, device_id: str, message_id: str) -> dict:
        # SELECT only: reconciliation must never acknowledge or resend a task.
        with self._database.transaction() as connection:
            device = connection.execute("SELECT enabled FROM devices WHERE id=%s", (device_id,)).fetchone()
            if device is None or not device[0]:
                raise PullDeviceUnavailable
            row = connection.execute(
                "SELECT m.state,m.pulled_at,m.sent_at,m.delivered_at,d.accepted_at,d.message_id "
                "FROM messages m LEFT JOIN message_deliveries d ON d.message_id=m.id "
                "WHERE m.id=%s AND m.device_id=%s AND m.direction='OUTBOUND'",
                (message_id, device_id),
            ).fetchone()
            if row is None:
                if message_id.startswith("check_"):
                    check = connection.execute(
                        "SELECT c.transport_state,c.pulled_at,c.sent_at,c.reason,r.target_phone "
                        "FROM sms_checks c JOIN sms_check_runs r ON r.id=c.run_id "
                        "WHERE c.id=%s AND c.device_id=%s", (message_id, device_id),
                    ).fetchone()
                    if check is not None:
                        times = {state: utc_iso_from_millis(value) for state, value in (
                            ("Processed", check[1]), ("Sent", check[2])) if value is not None}
                        return {"id": message_id, "state": check[0], "recipients": [{
                            "phoneNumber": check[4], "state": check[0],
                            "error": check[3] if check[0] == "Failed" else None,
                        }], "states": times, "deliveryAccepted": None}
                raise DeliveryError("NOT_FOUND", message_id, 404)
            recipients = [{"phoneNumber": phone, "state": state, "error": error}
                          for phone, state, error in connection.execute(
                              "SELECT phone_number,state,error FROM message_recipients "
                              "WHERE message_id=%s ORDER BY id", (message_id,),
                          ).fetchall()]
            states = {state: utc_iso_from_millis(occurred) for state, occurred in connection.execute(
                "SELECT state,occurred_at FROM message_state_history WHERE message_id=%s ORDER BY occurred_at,id",
                (message_id,),
            ).fetchall()}
            for state, value in (("Processed", row[1]), ("Sent", row[2]), ("Delivered", row[3])):
                if value is not None:
                    states.setdefault(state, utc_iso_from_millis(value))
            return {"id": message_id, "state": row[0], "recipients": recipients, "states": states,
                    "deliveryAccepted": row[4] is not None if row[5] is not None else None}
