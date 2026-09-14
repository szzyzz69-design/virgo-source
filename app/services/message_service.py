from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import secrets
import time

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.database import Database
from app.schemas.message import (
    MessageCreateRequest,
    MessageCreateResponse,
    request_digest,
    to_utc_millis,
)
from app.services.message_publisher import MessageEnqueuedPublisher


logger = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    pass


class MessageStateConflict(Exception):
    pass


class NoAvailableDevice(Exception):
    pass


class MessageValidationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MessageCreateResult:
    response: MessageCreateResponse
    replayed: bool


@dataclass(frozen=True, slots=True)
class Route:
    device_id: str
    sim_card_id: str
    sim_number: int
    phone_number: str | None
    areas: str | None = None
    conversation_id: str | None = None
    contact_id: str | None = None


def utc_iso_from_millis(value: int) -> str:
    return (
        datetime.fromtimestamp(value / 1000, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class MessageCommandService:
    def __init__(
        self,
        database: Database,
        *,
        online_window_seconds: int,
        publisher: MessageEnqueuedPublisher,
    ):
        self._database = database
        self._online_window_seconds = online_window_seconds
        self._publisher = publisher

    def create(
        self,
        request: MessageCreateRequest,
        idempotency_key: str,
    ) -> MessageCreateResult:
        now = time.time_ns() // 1_000_000
        valid_until = to_utc_millis(request.valid_until)
        if valid_until is not None and valid_until <= now:
            raise MessageValidationError("validUntil must be in the future")
        digest = request_digest(request)

        with self._database.transaction() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (idempotency_key,),
            )
            existing = self._find_existing(connection, idempotency_key)
            if existing is not None:
                metadata = existing[6] or {}
                if metadata.get("requestDigest") != digest:
                    raise IdempotencyConflict
                return MessageCreateResult(
                    response=self._response_from_row(existing),
                    replayed=True,
                )

            route = self._select_route(connection, request, now)
            phone = request.phone_numbers[0]
            contact_id = route.contact_id or self._get_or_create_contact(
                connection,
                phone,
                now,
            )
            conversation_id = route.conversation_id or self._get_or_create_conversation(
                connection,
                phone,
                contact_id,
                route,
                now,
            )
            message_id = f"msg_{secrets.token_hex(16)}"
            metadata = {
                "client": request.metadata or {},
                "requestDigest": digest,
            }
            connection.execute(
                """
                INSERT INTO messages (
                    id, conversation_id, direction, message_type, text_content,
                    from_phone_number, to_phone_number, state, device_id,
                    sim_card_id, sim_number, priority, with_delivery_report,
                    idempotency_key, valid_until, schedule_at, metadata,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, 'OUTBOUND', 'SMS', %s,
                    %s, %s, 'Pending', %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    message_id,
                    conversation_id,
                    request.text,
                    route.phone_number,
                    phone,
                    route.device_id,
                    route.sim_card_id,
                    route.sim_number,
                    request.priority,
                    request.with_delivery_report,
                    idempotency_key,
                    valid_until,
                    to_utc_millis(request.schedule_at),
                    Jsonb(metadata),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO message_recipients (
                    message_id, phone_number, state, created_at, updated_at
                ) VALUES (%s, %s, 'Pending', %s, %s)
                """,
                (message_id, phone, now, now),
            )
            connection.execute(
                """
                INSERT INTO message_state_history (
                    message_id, state, source, reason, occurred_at, created_at
                ) VALUES (%s, 'Pending', 'API', 'Created by business API', %s, %s)
                """,
                (message_id, now, now),
            )
            connection.execute(
                """
                UPDATE conversations
                SET device_id = %s, sim_card_id = %s, sim_number = %s,
                    last_message_preview = %s,
                    last_message_direction = 'OUTBOUND',
                    last_message_at = %s, updated_at = %s
                WHERE id = %s
                """,
                (
                    route.device_id,
                    route.sim_card_id,
                    route.sim_number,
                    request.text[:255],
                    now,
                    now,
                    conversation_id,
                ),
            )
            connection.execute(
                "UPDATE contacts SET last_contact_at = %s, updated_at = %s WHERE id = %s",
                (now, now, contact_id),
            )
            response = MessageCreateResponse(
                id=message_id,
                state="Pending",
                deviceId=route.device_id,
                simNumber=route.sim_number,
                conversationId=conversation_id,
                createdAt=utc_iso_from_millis(now),
            )

        try:
            self._publisher.publish(response.device_id, response.id)
        except Exception:
            logger.exception(
                "MessageEnqueued publisher failed for message %s",
                response.id,
            )
        return MessageCreateResult(response=response, replayed=False)

    def _find_existing(self, connection: Connection, key: str):
        return connection.execute(
            """
            SELECT id, state, device_id, sim_number, conversation_id,
                   created_at, metadata
            FROM messages
            WHERE direction = 'OUTBOUND' AND idempotency_key = %s
            ORDER BY created_at
            LIMIT 1
            """,
            (key,),
        ).fetchone()

    def _response_from_row(self, row) -> MessageCreateResponse:
        return MessageCreateResponse(
            id=row[0],
            state=row[1],
            deviceId=row[2],
            simNumber=row[3],
            conversationId=row[4],
            createdAt=utc_iso_from_millis(row[5]),
        )

    def _select_route(
        self,
        connection: Connection,
        request: MessageCreateRequest,
        now: int,
    ) -> Route:
        cutoff = now - self._online_window_seconds * 1000
        if request.conversation_id is not None:
            row = connection.execute(
                """
                SELECT c.external_phone_number, c.device_id, c.sim_card_id,
                       c.sim_number, c.status, c.contact_id, s.phone_number,
                       d.enabled, d.status, d.last_seen_at, s.enabled, s.status,
                       c.areas
                FROM conversations c
                JOIN devices d ON d.id = c.device_id
                JOIN sim_cards s ON s.id = c.sim_card_id
                WHERE c.id = %s
                FOR UPDATE OF c, d, s
                """,
                (request.conversation_id,),
            ).fetchone()
            if row is None or row[4] != "OPEN":
                raise MessageStateConflict("conversation is unavailable")
            if row[0] != request.phone_numbers[0]:
                raise MessageStateConflict("conversation phone does not match")
            if request.device_id is not None and request.device_id != row[1]:
                raise MessageStateConflict("conversation device does not match")
            if request.sim_number is not None and request.sim_number != row[3]:
                raise MessageStateConflict("conversation SIM does not match")
            if (
                not row[7]
                or row[8] != "online"
                or row[9] is None
                or row[9] < cutoff
                or not row[10]
                or row[11] != "active"
            ):
                raise NoAvailableDevice
            return Route(
                device_id=row[1],
                sim_card_id=row[2],
                sim_number=row[3],
                phone_number=row[6],
                areas=row[12],
                conversation_id=request.conversation_id,
                contact_id=row[5],
            )

        row = connection.execute(
            """
            SELECT d.id, s.id, s.sim_number, s.phone_number, s.areas
            FROM devices d
            JOIN sim_cards s ON s.device_id = d.id
            WHERE d.enabled = TRUE
              AND d.status = 'online'
              AND d.last_seen_at IS NOT NULL
              AND d.last_seen_at >= %s
              AND s.enabled = TRUE
              AND s.status = 'active'
              AND (%s::varchar IS NULL OR d.id = %s::varchar)
              AND (%s::integer IS NULL OR s.sim_number = %s::integer)
            ORDER BY s.last_used_at ASC NULLS FIRST, d.id, s.sim_number
            LIMIT 1
            FOR UPDATE OF d, s SKIP LOCKED
            """,
            (
                cutoff,
                request.device_id,
                request.device_id,
                request.sim_number,
                request.sim_number,
            ),
        ).fetchone()
        if row is None:
            raise NoAvailableDevice
        return Route(*row)

    def _get_or_create_contact(
        self,
        connection: Connection,
        phone: str,
        now: int,
    ) -> str:
        contact_id = f"contact_{secrets.token_hex(16)}"
        return connection.execute(
            """
            INSERT INTO contacts (
                id, phone_number, normalized_phone_number, source,
                created_at, updated_at
            ) VALUES (%s, %s, %s, 'MANUAL', %s, %s)
            ON CONFLICT (normalized_phone_number) DO UPDATE
            SET phone_number = EXCLUDED.phone_number
            RETURNING id
            """,
            (contact_id, phone, phone, now, now),
        ).fetchone()[0]

    def _get_or_create_conversation(
        self,
        connection: Connection,
        phone: str,
        contact_id: str,
        route: Route,
        now: int,
    ) -> str:
        existing = connection.execute(
            """
            SELECT id, status FROM conversations
            WHERE external_phone_number = %s
              AND device_id = %s
              AND sim_card_id = %s
            FOR UPDATE
            """,
            (phone, route.device_id, route.sim_card_id),
        ).fetchone()
        if existing is not None:
            if existing[1] != "OPEN":
                raise MessageStateConflict("conversation is not open")
            return existing[0]

        conversation_id = f"conv_{secrets.token_hex(16)}"
        connection.execute(
            """
            INSERT INTO conversations (
                id, external_phone_number, contact_id, device_id,
                sim_card_id, sim_number, areas, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s)
            """,
            (
                conversation_id,
                phone,
                contact_id,
                route.device_id,
                route.sim_card_id,
                route.sim_number,
                route.areas,
                now,
                now,
            ),
        )
        return conversation_id
