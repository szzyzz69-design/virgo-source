import time

from app.database import Database
from app.schemas.message_pull import (
    DataMessage,
    MessagePullItem,
    TextMessage,
    utc_iso_from_millis,
)


class PullDeviceUnavailable(Exception):
    pass


class MessagePullService:
    LIMIT = 10

    def __init__(self, database: Database):
        self._database = database

    def pull(self, device_id: str, order: str) -> list[MessagePullItem]:
        if order not in {"fifo", "lifo"}:
            raise ValueError("order must be fifo or lifo")
        now = time.time_ns() // 1_000_000
        direction = "ASC" if order == "fifo" else "DESC"

        with self._database.transaction() as connection:
            device = connection.execute(
                "SELECT enabled FROM devices WHERE id = %s FOR UPDATE",
                (device_id,),
            ).fetchone()
            if device is None or not device[0]:
                raise PullDeviceUnavailable
            connection.execute(
                """
                UPDATE devices
                SET status = 'online', last_seen_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (now, now, device_id),
            )
            rows = connection.execute(
                f"""
                SELECT m.id, m.message_type, m.text_content,
                       m.data_base64, m.data_port, m.sim_number,
                       m.with_delivery_report, m.is_encrypted,
                       m.valid_until, m.schedule_at, m.priority, m.created_at
                FROM messages m
                JOIN sim_cards s ON s.id = m.sim_card_id
                WHERE m.device_id = %s
                  AND m.direction = 'OUTBOUND'
                  AND m.state = 'Pending'
                  AND (m.valid_until IS NULL OR m.valid_until > %s)
                  AND (m.schedule_at IS NULL OR m.schedule_at <= %s)
                  AND s.enabled = TRUE
                  AND s.status = 'active'
                ORDER BY m.created_at {direction}, m.id {direction}
                LIMIT {self.LIMIT}
                FOR UPDATE OF m SKIP LOCKED
                """,
                (device_id, now, now),
            ).fetchall()
            if not rows:
                return []

            message_ids = [row[0] for row in rows]
            connection.execute(
                """
                UPDATE messages
                SET state = 'Processed', pulled_at = %s, updated_at = %s
                WHERE id = ANY(%s::varchar[])
                """,
                (now, now, message_ids),
            )
            connection.execute(
                """
                INSERT INTO message_state_history (
                    message_id, state, source, reason, occurred_at, created_at
                )
                SELECT selected.message_id, 'Processed', 'SERVER',
                       'Pulled by device', %s, %s
                FROM unnest(%s::varchar[]) AS selected(message_id)
                ON CONFLICT (message_id, state) DO NOTHING
                """,
                (now, now, message_ids),
            )
            recipient_rows = connection.execute(
                """
                SELECT message_id, phone_number
                FROM message_recipients
                WHERE message_id = ANY(%s::varchar[])
                ORDER BY message_id, id
                """,
                (message_ids,),
            ).fetchall()
            recipients: dict[str, list[str]] = {item: [] for item in message_ids}
            for message_id, phone_number in recipient_rows:
                recipients[message_id].append(phone_number)

            return [self._to_item(row, recipients[row[0]]) for row in rows]

    def _to_item(self, row, phone_numbers: list[str]) -> MessagePullItem:
        message_type = row[1]
        text_message = (
            TextMessage(text=row[2]) if message_type == "SMS" else None
        )
        data_message = (
            DataMessage(data=row[3], port=row[4])
            if message_type == "DATA_SMS"
            else None
        )
        return MessagePullItem(
            id=row[0],
            textMessage=text_message,
            dataMessage=data_message,
            phoneNumbers=phone_numbers,
            simNumber=row[5],
            withDeliveryReport=row[6],
            isEncrypted=row[7],
            validUntil=utc_iso_from_millis(row[8]),
            scheduleAt=utc_iso_from_millis(row[9]),
            priority=row[10],
            createdAt=utc_iso_from_millis(row[11]),
        )
