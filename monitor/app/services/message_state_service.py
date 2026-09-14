import time

from app.database import Database
from app.schemas.message_status import (
    MessageStatusUpdate,
    Status,
    aggregate_recipient_state,
    can_transition,
    to_utc_millis,
)


class MessageStatusError(Exception):
    def __init__(self, index: int, message_id: str | None, message: str):
        super().__init__(message)
        self.index = index
        self.message_id = message_id


class MessageStatusNotFound(MessageStatusError):
    pass


class MessageStatusForbidden(MessageStatusError):
    pass


class MessageStateConflict(MessageStatusError):
    pass


class MessageStatusValidation(MessageStatusError):
    pass


class MessageStateService:
    CLOCK_SKEW_MILLIS = 5 * 60 * 1000

    def __init__(self, database: Database):
        self._database = database

    def update(self, device_id: str, requests: list[MessageStatusUpdate]) -> None:
        now = time.time_ns() // 1_000_000
        by_id = {item.id: (index, item) for index, item in enumerate(requests)}
        ids = sorted(by_id)
        with self._database.transaction() as connection:
            device = connection.execute(
                "SELECT enabled FROM devices WHERE id=%s FOR UPDATE", (device_id,)
            ).fetchone()
            if device is None or not device[0]:
                raise MessageStatusForbidden(0, None, "device is unavailable")
            rows = connection.execute(
                """
                SELECT id, direction, state, device_id, sim_card_id,
                       conversation_id, pulled_at, sent_at, delivered_at,
                       error_message
                FROM messages WHERE id = ANY(%s::varchar[])
                ORDER BY id FOR UPDATE
                """,
                (ids,),
            ).fetchall()
            row_by_id = {row[0]: row for row in rows}
            for message_id in ids:
                if message_id not in row_by_id:
                    index, _ = by_id[message_id]
                    raise MessageStatusNotFound(index, message_id, "message not found")

            recipient_rows = connection.execute(
                """
                SELECT message_id, phone_number, state
                FROM message_recipients
                WHERE message_id = ANY(%s::varchar[])
                ORDER BY message_id, phone_number FOR UPDATE
                """,
                (ids,),
            ).fetchall()
            existing_recipients: dict[str, dict[str, Status]] = {
                item: {} for item in ids
            }
            for message_id, phone, state in recipient_rows:
                existing_recipients[message_id][phone] = Status(state)

            prepared = []
            for message_id in ids:
                index, request = by_id[message_id]
                row = row_by_id[message_id]
                if row[3] != device_id or row[1] != "OUTBOUND":
                    raise MessageStatusForbidden(index, message_id, "message is not owned by device")
                old_state = Status(row[2])
                if not can_transition(old_state, request.state):
                    raise MessageStateConflict(index, message_id, "message state regressed")
                incoming = {item.phone_number: item for item in request.recipients}
                if set(incoming) != set(existing_recipients[message_id]):
                    raise MessageStateConflict(index, message_id, "recipient set does not match")
                aggregate = aggregate_recipient_state(
                    [item.state for item in request.recipients]
                )
                if aggregate is not request.state:
                    raise MessageStateConflict(index, message_id, "aggregate state does not match")
                for phone, update in incoming.items():
                    if not can_transition(existing_recipients[message_id][phone], update.state):
                        raise MessageStateConflict(index, message_id, "recipient state regressed")
                state_times = {
                    status: to_utc_millis(value)
                    for status, value in request.states.items()
                }
                if any(value > now + self.CLOCK_SKEW_MILLIS for value in state_times.values()):
                    raise MessageStatusValidation(index, message_id, "state time is too far in the future")
                if request.state in {Status.SENT, Status.DELIVERED} and row[7] is None and Status.SENT not in state_times:
                    raise MessageStatusValidation(index, message_id, "Sent time is required")
                prepared.append((index, request, row, incoming, state_times))

            for _, request, row, incoming, state_times in prepared:
                for phone, recipient in incoming.items():
                    connection.execute(
                        """
                        UPDATE message_recipients
                        SET state=%s, error=%s, updated_at=%s
                        WHERE message_id=%s AND phone_number=%s
                        """,
                        (recipient.state.value, recipient.error, now, request.id, phone),
                    )
                pulled_at = row[6] or state_times.get(Status.PROCESSED)
                sent_at = row[7] or state_times.get(Status.SENT)
                delivered_at = row[8] or state_times.get(Status.DELIVERED)
                first_error = next(
                    (item.error for item in request.recipients if item.error), None
                )
                error_message = row[9] or (
                    first_error if request.state is Status.FAILED else None
                )
                connection.execute(
                    """
                    UPDATE messages SET state=%s, pulled_at=%s, sent_at=%s,
                        delivered_at=%s, error_message=%s, updated_at=%s
                    WHERE id=%s
                    """,
                    (request.state.value, pulled_at, sent_at, delivered_at, error_message, now, request.id),
                )
                for status, occurred_at in state_times.items():
                    connection.execute(
                        """
                        INSERT INTO message_state_history (
                            message_id, state, source, reason, occurred_at, created_at
                        ) VALUES (%s, %s, 'DEVICE', 'Reported by device', %s, %s)
                        ON CONFLICT (message_id, state) DO NOTHING
                        """,
                        (request.id, status.value, occurred_at, now),
                    )
                if row[7] is None and sent_at is not None:
                    connection.execute(
                        "UPDATE sim_cards SET last_used_at=%s WHERE id=%s",
                        (sent_at, row[4]),
                    )
                    connection.execute(
                        "UPDATE conversations SET last_message_at=%s, updated_at=%s WHERE id=%s",
                        (sent_at, now, row[5]),
                    )
                    connection.execute(
                        """
                        UPDATE contacts SET last_contact_at=%s, updated_at=%s
                        WHERE id=(SELECT contact_id FROM conversations WHERE id=%s)
                        """,
                        (sent_at, now, row[5]),
                    )
            connection.execute(
                """
                UPDATE devices SET status='online', last_seen_at=%s, updated_at=%s
                WHERE id=%s
                """,
                (now, now, device_id),
            )
