from dataclasses import dataclass
import base64
import logging
import re
import secrets
import time

from psycopg.types.json import Jsonb

from app.database import Database
from app.schemas.message import normalize_phone
from app.schemas.mms_webhook import (
    MmsDownloadedPayload,
    MmsWebhookRequest,
    mms_received_millis,
    mms_webhook_digest,
)
from app.services.agent_event_publisher import NoOpAgentEventPublisher
from app.services.inbound_message_service import (
    InboundConflict,
    InboundDeviceUnavailable,
    InboundValidation,
)
from app.services.inbound_publisher import InboundMessagePublisher, NoOpInboundMessagePublisher
from app.services.object_storage import build_mms_object_key


logger = logging.getLogger(__name__)


ALLOWED_MMS_ATTACHMENT_TYPES = {
    "image/jpeg",
    "image/png",
    "audio/amr",
    "application/octet-stream",
}
MAX_MMS_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_MMS_TOTAL_BYTES = 20 * 1024 * 1024
STANDARD_BASE64 = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


class MmsPayloadTooLarge(Exception):
    pass


class MmsUnsupportedMediaType(Exception):
    pass


@dataclass(frozen=True, slots=True)
class MmsWebhookResult:
    id: str
    conversation_id: str
    message_id: str
    created: bool


class MmsWebhookService:
    def __init__(
        self,
        database: Database,
        storage,
        publisher: InboundMessagePublisher = NoOpInboundMessagePublisher(),
        agent_publisher=NoOpAgentEventPublisher(),
    ):
        self._database = database
        self._storage = storage
        self._publisher = publisher
        self._agent_publisher = agent_publisher

    def handle(self, request: MmsWebhookRequest) -> MmsWebhookResult:
        now = time.time_ns() // 1_000_000
        received = mms_received_millis(request)
        if received > now + 300_000:
            raise InboundValidation

        self._validate_attachments(request)
        digest = mms_webhook_digest(request)
        identity = self._mms_identity(request)
        payload = request.payload
        idempotency_key = f"mms:{payload.message_id}"
        agent_account_ids: tuple[str, ...] = ()
        sim_id: str | None = None

        with self._database.transaction() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"mms:{request.device_id}:{payload.message_id}",),
            )
            device = connection.execute(
                "SELECT enabled FROM devices WHERE id=%s FOR UPDATE",
                (request.device_id,),
            ).fetchone()
            if device is None or not device[0]:
                raise InboundDeviceUnavailable

            existing = connection.execute(
                """
                SELECT id, conversation_id, sim_card_id, metadata,
                       from_phone_number, to_phone_number, sim_number
                FROM messages
                WHERE device_id=%s AND direction='INBOUND' AND idempotency_key=%s
                LIMIT 1
                """,
                (request.device_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                message_id = existing[0]
                conversation_id = existing[1]
                sim_id = existing[2]
                metadata = existing[3] or {}
                if self._stored_identity(metadata, request, existing) != identity:
                    raise InboundConflict
                digest_key = self._digest_key(request)
                if metadata.get(digest_key) == digest:
                    return MmsWebhookResult(message_id, conversation_id, payload.message_id, False)
                if metadata.get(digest_key) is not None:
                    raise InboundConflict
                agent_account_ids = self._agent_account_ids_for_sim(connection, sim_id)
                created = False
            else:
                sim = self._resolve_sim(connection, request.device_id, payload.sim_number, payload.recipient)
                sim_id = sim[0] if sim else None
                sim_number = sim[1] if sim else payload.sim_number
                area = sim[2] if sim else None
                conversation_id, agent_account_ids = self._get_or_create_conversation(
                    connection,
                    request.device_id,
                    payload.sender,
                    sim_id,
                    sim_number,
                    area,
                    received,
                    now,
                )
                message_id = f"msg_{secrets.token_hex(16)}"
                metadata = {
                    "mms": {
                        "messageId": payload.message_id,
                        "webhookId": request.webhook_id,
                        "webhookEventId": request.id,
                    },
                    "mmsIdentity": identity,
                    "simNumber": payload.sim_number,
                    "recipient": payload.recipient,
                }
                connection.execute(
                    """
                    INSERT INTO messages(
                        id, conversation_id, direction, message_type, text_content,
                        from_phone_number, to_phone_number, state, device_id, sim_card_id,
                        sim_number, idempotency_key, received_at, metadata, created_at, updated_at
                    )
                    VALUES(%s,%s,'INBOUND','MMS',%s,%s,%s,'Received',%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        message_id,
                        conversation_id,
                        payload.body if isinstance(payload, MmsDownloadedPayload) else None,
                        payload.sender,
                        payload.recipient,
                        request.device_id,
                        sim_id,
                        sim_number,
                        idempotency_key,
                        received,
                        Jsonb(metadata),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO message_state_history(message_id,state,source,reason,occurred_at,created_at)
                    VALUES(%s,'Received','DEVICE','Received MMS by Android device',%s,%s)
                    ON CONFLICT(message_id,state) DO NOTHING
                    """,
                    (message_id, received, now),
                )
                created = True

            metadata = self._merge_metadata(metadata, request, digest)
            if isinstance(payload, MmsDownloadedPayload):
                self._store_attachments(connection, request, message_id, now)

            new_text = payload.body if isinstance(payload, MmsDownloadedPayload) else None
            connection.execute(
                """
                UPDATE messages
                SET text_content = COALESCE(%s, text_content),
                    metadata = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (new_text, Jsonb(metadata), now, message_id),
            )

            if created or isinstance(payload, MmsDownloadedPayload):
                connection.execute(
                    """
                    UPDATE conversations
                    SET unread_count = CASE WHEN %s THEN unread_count + 1 ELSE unread_count END,
                        last_message_preview=%s,
                        last_message_direction='INBOUND',
                        last_message_at=%s,
                        updated_at=%s
                    WHERE id=%s
                    """,
                    (created, self._preview(request), received, now, conversation_id),
                )
            connection.execute(
                "UPDATE devices SET status='online',last_seen_at=%s,updated_at=%s WHERE id=%s",
                (now, now, request.device_id),
            )

        should_publish = created or (not created and isinstance(payload, MmsDownloadedPayload))
        if should_publish:
            self._publish(request.device_id, message_id, conversation_id, sim_id, new_text, now, agent_account_ids)
        return MmsWebhookResult(message_id, conversation_id, payload.message_id, created)

    def _store_attachments(self, connection, request: MmsWebhookRequest, message_id: str, now: int) -> None:
        payload = request.payload
        if not isinstance(payload, MmsDownloadedPayload):
            return
        for attachment in payload.attachments:
            data = attachment.decoded_data()
            if data is None:
                continue
            key = build_mms_object_key(
                request.device_id,
                payload.message_id,
                attachment.part_id,
                attachment.name,
                attachment.content_type,
            )
            stored = self._storage.upload_bytes(
                key=key,
                body=data,
                content_type=attachment.content_type,
            )
            attachment_id = f"att_{secrets.token_hex(16)}"
            connection.execute(
                """
                INSERT INTO message_attachments(
                    id, message_id, part_id, content_type, name, size, s3_bucket,
                    s3_key, url, etag, metadata, created_at, updated_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(message_id, part_id) DO UPDATE
                SET content_type=EXCLUDED.content_type,
                    name=EXCLUDED.name,
                    size=EXCLUDED.size,
                    s3_bucket=EXCLUDED.s3_bucket,
                    s3_key=EXCLUDED.s3_key,
                    url=EXCLUDED.url,
                    etag=EXCLUDED.etag,
                    metadata=EXCLUDED.metadata,
                    updated_at=EXCLUDED.updated_at
                """,
                (
                    attachment_id,
                    message_id,
                    attachment.part_id,
                    attachment.content_type,
                    attachment.name,
                    attachment.size,
                    stored.bucket,
                    stored.key,
                    stored.url,
                    stored.etag,
                    Jsonb({"source": "android-sms-gateway"}),
                    now,
                    now,
                ),
            )

    def _validate_attachments(self, request: MmsWebhookRequest) -> None:
        payload = request.payload
        if not isinstance(payload, MmsDownloadedPayload):
            return

        declared_total = 0
        decoded_total = 0
        for attachment in payload.attachments:
            if attachment.content_type not in ALLOWED_MMS_ATTACHMENT_TYPES:
                raise MmsUnsupportedMediaType

            if attachment.size is not None:
                if attachment.size > MAX_MMS_ATTACHMENT_BYTES:
                    raise MmsPayloadTooLarge
                next_declared_total = declared_total + attachment.size
                if next_declared_total > MAX_MMS_TOTAL_BYTES:
                    raise MmsPayloadTooLarge
                declared_total = next_declared_total

            if attachment.data is None:
                decoded_size = attachment.size or 0
                if decoded_total + decoded_size > MAX_MMS_TOTAL_BYTES:
                    raise MmsPayloadTooLarge
            else:
                estimated_size = self._estimated_base64_decoded_size(attachment.data)
                if estimated_size > MAX_MMS_ATTACHMENT_BYTES:
                    raise MmsPayloadTooLarge
                if decoded_total + estimated_size > MAX_MMS_TOTAL_BYTES:
                    raise MmsPayloadTooLarge
                try:
                    data = attachment.decoded_data()
                except (ValueError, base64.binascii.Error) as error:
                    raise InboundValidation from error
                decoded_size = len(data)
                if decoded_size > MAX_MMS_ATTACHMENT_BYTES:
                    raise MmsPayloadTooLarge
                if decoded_total + decoded_size > MAX_MMS_TOTAL_BYTES:
                    raise MmsPayloadTooLarge
            decoded_total += decoded_size

    def _estimated_base64_decoded_size(self, data: str) -> int:
        if len(data) % 4 != 0 or STANDARD_BASE64.fullmatch(data) is None:
            raise InboundValidation
        padding = 2 if data.endswith("==") else 1 if data.endswith("=") else 0
        return (len(data) // 4) * 3 - padding

    def _publish(
        self,
        device_id: str,
        message_id: str,
        conversation_id: str,
        sim_card_id: str | None,
        text_content: str | None,
        created_at: int,
        agent_account_ids: tuple[str, ...],
    ) -> None:
        try:
            self._publisher.publish(device_id, message_id, conversation_id)
        except Exception:
            logger.exception("Inbound publisher failed for MMS message %s", message_id)
        for account_id in agent_account_ids:
            try:
                self._agent_publisher.publish_inbound_message(
                    account_id,
                    message_id,
                    conversation_id,
                    sim_card_id,
                    text_content=text_content,
                    state="Received",
                    created_at=created_at,
                )
            except Exception:
                logger.exception("Agent event publisher failed for MMS message %s", message_id)

    def _resolve_sim(self, connection, device_id: str, sim_number: int | None, recipient: str | None):
        sim = None
        if sim_number is not None:
            sim = connection.execute(
                "SELECT id,sim_number,areas FROM sim_cards WHERE device_id=%s AND sim_number=%s LIMIT 1",
                (device_id, sim_number),
            ).fetchone()
        if sim is None and recipient:
            try:
                normalized_recipient = normalize_phone(recipient)
            except ValueError:
                normalized_recipient = None
            if normalized_recipient:
                sim = connection.execute(
                    """
                    SELECT id,sim_number,areas
                    FROM sim_cards
                    WHERE device_id=%s
                      AND regexp_replace(phone_number,'[\\s()\\-]','','g')=%s
                    LIMIT 1
                    """,
                    (device_id, normalized_recipient),
                ).fetchone()
        return sim

    def _get_or_create_conversation(
        self,
        connection,
        device_id: str,
        sender: str,
        sim_id: str | None,
        sim_number: int | None,
        area: str | None,
        received: int,
        now: int,
    ) -> tuple[str, tuple[str, ...]]:
        agent_account_ids = self._agent_account_ids_for_sim(connection, sim_id)
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"conversation:{device_id}:{sender}:{sim_id or 'none'}",),
        )
        contact_id = f"contact_{secrets.token_hex(16)}"
        contact_id = connection.execute(
            """
            INSERT INTO contacts(id,phone_number,normalized_phone_number,source,last_contact_at,created_at,updated_at,areas)
            VALUES(%s,%s,%s,'INBOUND_AUTO',%s,%s,%s,%s)
            ON CONFLICT(normalized_phone_number) DO UPDATE
            SET phone_number=EXCLUDED.phone_number,
                last_contact_at=EXCLUDED.last_contact_at,
                updated_at=EXCLUDED.updated_at,
                areas=EXCLUDED.areas
            RETURNING id
            """,
            (contact_id, sender, sender, received, now, now, area),
        ).fetchone()[0]
        conversation = connection.execute(
            """
            SELECT id
            FROM conversations
            WHERE external_phone_number=%s
              AND device_id=%s
              AND sim_card_id IS NOT DISTINCT FROM %s::varchar
              AND status='OPEN'
            FOR UPDATE
            """,
            (sender, device_id, sim_id),
        ).fetchone()
        if conversation is not None:
            return conversation[0], agent_account_ids
        conversation_id = f"conv_{secrets.token_hex(16)}"
        connection.execute(
            """
            INSERT INTO conversations(
                id, external_phone_number, contact_id, device_id, sim_card_id,
                sim_number, areas, status, created_at, updated_at
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s)
            """,
            (conversation_id, sender, contact_id, device_id, sim_id, sim_number, area, now, now),
        )
        return conversation_id, agent_account_ids

    def _agent_account_ids_for_sim(self, connection, sim_id: str | None) -> tuple[str, ...]:
        if sim_id is None:
            return ()
        return tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT a.id
                FROM account_sim_cards acs
                JOIN accounts a ON a.id = acs.account_id
                WHERE acs.sim_card_id = %s
                  AND a.status = 'ACTIVE'
                ORDER BY a.id
                """,
                (sim_id,),
            ).fetchall()
        )

    def _merge_metadata(self, metadata, request: MmsWebhookRequest, digest: str) -> dict:
        payload = request.payload
        merged = dict(metadata or {})
        merged["mmsIdentity"] = self._mms_identity(request)
        merged["mms"] = {
            **dict(merged.get("mms") or {}),
            "messageId": payload.message_id,
            "webhookId": request.webhook_id,
            "webhookEventId": request.id,
            "subject": payload.subject,
            "event": request.event,
        }
        merged[self._digest_key(request)] = digest
        return merged

    def _digest_key(self, request: MmsWebhookRequest) -> str:
        return "downloadedDigest" if request.event == "mms:downloaded" else "receivedDigest"

    def _mms_identity(self, request: MmsWebhookRequest) -> dict:
        payload = request.payload
        return {
            "deviceId": request.device_id,
            "messageId": payload.message_id,
            "sender": payload.sender,
            "recipient": payload.recipient,
            "simNumber": payload.sim_number,
            "subject": payload.subject,
        }

    def _stored_identity(self, metadata, request: MmsWebhookRequest, existing) -> dict:
        stored = (metadata or {}).get("mmsIdentity")
        if isinstance(stored, dict):
            return stored
        mms_metadata = dict((metadata or {}).get("mms") or {})
        return {
            "deviceId": request.device_id,
            "messageId": mms_metadata.get("messageId") or request.payload.message_id,
            "sender": existing[4],
            "recipient": (metadata or {}).get("recipient", existing[5]),
            "simNumber": (metadata or {}).get("simNumber", existing[6]),
            "subject": mms_metadata.get("subject"),
        }

    def _preview(self, request: MmsWebhookRequest) -> str:
        payload = request.payload
        if isinstance(payload, MmsDownloadedPayload) and payload.body:
            return payload.body[:255]
        if isinstance(payload, MmsDownloadedPayload) and payload.attachments:
            image_count = sum(
                1 for attachment in payload.attachments if attachment.content_type in {"image/jpeg", "image/png"}
            )
            if image_count:
                return "[MMS image]"
        return "[MMS]"
