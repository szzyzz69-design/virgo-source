import base64
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest

import app.services.mms_webhook_service as mms_webhook_service
from app.database import Database
from app.schemas.mms_webhook import MmsAttachment, MmsWebhookRequest
from app.services.inbound_message_service import InboundConflict, InboundValidation
from app.services.mms_webhook_service import MmsWebhookService
from app.services.object_storage import StoredObject


class FakeStorage:
    def __init__(self):
        self.uploads = []

    def upload_bytes(self, *, key, body, content_type):
        self.uploads.append((key, body, content_type))
        return StoredObject("bucket", key, f"https://cdn.example.test/{key}", "etag")


class RecordingInboundPublisher:
    def __init__(self):
        self.events = []

    def publish(self, device_id, message_id, conversation_id):
        self.events.append((device_id, message_id, conversation_id))


class RecordingAgentPublisher:
    def __init__(self):
        self.events = []

    def publish_inbound_message(
        self,
        account_id,
        message_id,
        conversation_id,
        sim_card_id,
        text_content=None,
        state="Received",
        created_at=None,
    ):
        self.events.append(
            {
                "account_id": account_id,
                "message_id": message_id,
                "conversation_id": conversation_id,
                "sim_card_id": sim_card_id,
                "text_content": text_content,
                "state": state,
                "created_at": created_at,
            }
        )


def insert_device_with_sim(clean_database, *, recipient="+8613900000000"):
    marker = clean_database.track_push_token("pytest-mms-" + uuid4().hex)
    device_id = clean_database.track("dev_" + uuid4().hex)
    sim_id = "sim_" + uuid4().hex
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices(
                id, name, push_token, token_hash, login, enabled, status,
                last_seen_at, created_at, updated_at
            )
            VALUES(%s, 'mms-phone', %s, 'unused', %s, TRUE, 'offline', NULL, 1783224000000, 1783224000000)
            """,
            (device_id, marker, "mms_" + uuid4().hex),
        )
        connection.execute(
            """
            INSERT INTO sim_cards(id, device_id, slot_index, sim_number, phone_number, enabled, status, areas)
            VALUES(%s, %s, 0, 1, %s, TRUE, 'active', 'south')
            """,
            (sim_id, device_id, recipient),
        )
        connection.commit()
    return device_id, sim_id, recipient


def bind_agent_to_sim(clean_database, sim_id):
    account_id = "acct_" + uuid4().hex
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO accounts(id, username, password_hash, areas, status)
            VALUES(%s, %s, 'unused', 'south', 'ACTIVE')
            """,
            (account_id, "mms_agent_" + uuid4().hex),
        )
        connection.execute(
            "INSERT INTO account_sim_cards(account_id, sim_card_id) VALUES(%s, %s)",
            (account_id, sim_id),
        )
        connection.commit()
    return account_id


def mms_downloaded(
    device_id,
    sender,
    recipient,
    *,
    message_id=None,
    body="image hello",
    data="AQID",
    sim_number=1,
    subject="Photo",
    attachments=None,
):
    message_id = message_id or "mms_" + uuid4().hex
    if attachments is None:
        attachment = {
            "partId": 17,
            "contentType": "image/jpeg",
            "name": "photo.jpg",
            "size": 3,
        }
        if data is not None:
            attachment["data"] = data
        attachments = [attachment]
    return MmsWebhookRequest.model_validate(
        {
            "deviceId": device_id,
            "event": "mms:downloaded",
            "id": "wh_" + uuid4().hex,
            "webhookId": "wh_mms",
            "payload": {
                "messageId": message_id,
                "sender": sender,
                "recipient": recipient,
                "simNumber": sim_number,
                "body": body,
                "subject": subject,
                "attachments": attachments,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
    )


def mms_received(device_id, sender, recipient, *, message_id, sim_number=1, subject="Photo"):
    return MmsWebhookRequest.model_validate(
        {
            "deviceId": device_id,
            "event": "mms:received",
            "id": "wh_" + uuid4().hex,
            "webhookId": "wh_mms",
            "payload": {
                "messageId": message_id,
                "sender": sender,
                "recipient": recipient,
                "simNumber": sim_number,
                "transactionId": "tx_" + uuid4().hex,
                "subject": subject,
                "size": 128,
                "contentClass": "IMAGE_BASIC",
                "receivedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
    )


def test_downloaded_mms_creates_message_attachment_preview_and_marks_device_online(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, sim_id, recipient = insert_device_with_sim(clean_database)
    request = mms_downloaded(device_id, sender, recipient)
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()

    result = MmsWebhookService(Database(clean_database.dsn), storage).handle(request)

    assert result.created is True
    assert result.message_id == request.payload.message_id
    assert storage.uploads[0][1] == b"\x01\x02\x03"
    with psycopg.connect(clean_database.dsn) as connection:
        message = connection.execute(
            """
            SELECT message_type, text_content, direction, sim_card_id, metadata->>'downloadedDigest'
            FROM messages
            WHERE id = %s
            """,
            (result.id,),
        ).fetchone()
        attachment = connection.execute(
            """
            SELECT part_id, content_type, s3_bucket, s3_key, url
            FROM message_attachments
            WHERE message_id = %s
            """,
            (result.id,),
        ).fetchone()
        conversation = connection.execute(
            """
            SELECT unread_count, last_message_preview, last_message_direction, areas
            FROM conversations
            WHERE id = %s
            """,
            (result.conversation_id,),
        ).fetchone()
        device = connection.execute(
            "SELECT status, last_seen_at FROM devices WHERE id = %s",
            (device_id,),
        ).fetchone()
    assert message == ("MMS", "image hello", "INBOUND", sim_id, message[4])
    assert message[4]
    assert attachment[0] == 17
    assert attachment[1] == "image/jpeg"
    assert attachment[2] == "bucket"
    assert attachment[4].startswith("https://cdn.example.test/")
    assert conversation == (1, "image hello", "INBOUND", "south")
    assert device[0] == "online"
    assert device[1] is not None


def test_replaying_same_downloaded_mms_returns_existing_message_without_duplicate_upload(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    request = mms_downloaded(device_id, sender, recipient)
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()
    service = MmsWebhookService(Database(clean_database.dsn), storage)

    first = service.handle(request)
    replay = service.handle(request)

    assert replay.created is False
    assert replay.id == first.id
    assert replay.conversation_id == first.conversation_id
    assert len(storage.uploads) == 1
    with psycopg.connect(clean_database.dsn) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM messages WHERE idempotency_key = %s),
                (SELECT count(*) FROM message_attachments WHERE message_id = %s),
                (SELECT unread_count FROM conversations WHERE id = %s)
            """,
            ("mms:" + request.payload.message_id, first.id, first.conversation_id),
        ).fetchone()
    assert counts == (1, 1, 1)


def test_same_downloaded_mms_key_with_different_digest_conflicts(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    message_id = "mms_" + uuid4().hex
    clean_database.track_message_key("mms:" + message_id)
    service = MmsWebhookService(Database(clean_database.dsn), FakeStorage())

    service.handle(mms_downloaded(device_id, sender, recipient, message_id=message_id))

    with pytest.raises(InboundConflict):
        service.handle(mms_downloaded(device_id, sender, recipient, message_id=message_id, body="changed"))


@pytest.mark.parametrize(
    "changed_field,downloaded_overrides",
    [
        ("sender", {"sender": "+8613811111111"}),
        ("recipient", {"recipient": "+8613922222222"}),
        ("simNumber", {"sim_number": 2}),
        ("subject", {"subject": "Other photo"}),
    ],
)
def test_downloaded_after_received_conflicts_when_identity_changes(
    clean_database,
    changed_field,
    downloaded_overrides,
):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    message_id = "mms_" + uuid4().hex
    clean_database.track_message_key("mms:" + message_id)
    storage = FakeStorage()
    service = MmsWebhookService(Database(clean_database.dsn), storage)

    service.handle(mms_received(device_id, sender, recipient, message_id=message_id))

    downloaded_kwargs = {
        "sender": sender,
        "recipient": recipient,
        "message_id": message_id,
        **downloaded_overrides,
    }
    with pytest.raises(InboundConflict):
        service.handle(mms_downloaded(device_id, **downloaded_kwargs))

    assert storage.uploads == []


def test_downloaded_mms_rejects_unsupported_attachment_type(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    request = mms_downloaded(
        device_id,
        sender,
        recipient,
        attachments=[
            {
                "partId": 17,
                "contentType": "application/x-msdownload",
                "name": "payload.exe",
                "size": 3,
                "data": "AQID",
            }
        ],
    )
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()

    with pytest.raises(mms_webhook_service.MmsUnsupportedMediaType):
        MmsWebhookService(Database(clean_database.dsn), storage).handle(request)

    assert storage.uploads == []


def test_downloaded_mms_rejects_attachment_declared_size_over_limit(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    request = mms_downloaded(
        device_id,
        sender,
        recipient,
        data=None,
        attachments=[
            {
                "partId": 17,
                "contentType": "image/jpeg",
                "name": "photo.jpg",
                "size": 10 * 1024 * 1024 + 1,
            }
        ],
    )
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()

    with pytest.raises(mms_webhook_service.MmsPayloadTooLarge):
        MmsWebhookService(Database(clean_database.dsn), storage).handle(request)

    assert storage.uploads == []


def test_downloaded_mms_rejects_attachment_decoded_size_over_limit(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    request = mms_downloaded(
        device_id,
        sender,
        recipient,
        attachments=[
            {
                "partId": 17,
                "contentType": "image/jpeg",
                "name": "photo.jpg",
                "size": 1,
                "data": base64.b64encode(b"\x00" * (10 * 1024 * 1024 + 1)).decode(),
            }
        ],
    )
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()

    with pytest.raises(mms_webhook_service.MmsPayloadTooLarge):
        MmsWebhookService(Database(clean_database.dsn), storage).handle(request)

    assert storage.uploads == []


def test_downloaded_mms_rejects_invalid_attachment_base64(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    request = mms_downloaded(
        device_id,
        sender,
        recipient,
        attachments=[
            {
                "partId": 17,
                "contentType": "image/jpeg",
                "name": "photo.jpg",
                "size": 3,
                "data": "not base64",
            }
        ],
    )
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()

    with pytest.raises(InboundValidation):
        MmsWebhookService(Database(clean_database.dsn), storage).handle(request)

    assert storage.uploads == []


def test_downloaded_mms_rejects_total_declared_size_over_limit(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    request = mms_downloaded(
        device_id,
        sender,
        recipient,
        attachments=[
            {"partId": 1, "contentType": "image/jpeg", "size": 10 * 1024 * 1024},
            {"partId": 2, "contentType": "image/png", "size": 10 * 1024 * 1024},
            {"partId": 3, "contentType": "audio/amr", "size": 1},
        ],
    )
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()

    with pytest.raises(mms_webhook_service.MmsPayloadTooLarge):
        MmsWebhookService(Database(clean_database.dsn), storage).handle(request)

    assert storage.uploads == []


def test_downloaded_mms_rejects_total_decoded_size_over_limit_before_decoding_overflow_part(
    clean_database,
    monkeypatch,
):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    chunk = b"\x00" * (7 * 1024 * 1024)
    encoded_chunk = base64.b64encode(chunk).decode()
    request = mms_downloaded(
        device_id,
        sender,
        recipient,
        attachments=[
            {
                "partId": 1,
                "contentType": "image/jpeg",
                "size": 1,
                "data": encoded_chunk,
            },
            {
                "partId": 2,
                "contentType": "image/png",
                "size": 1,
                "data": encoded_chunk,
            },
            {
                "partId": 3,
                "contentType": "application/octet-stream",
                "size": 1,
                "data": encoded_chunk,
            },
        ],
    )
    clean_database.track_message_key("mms:" + request.payload.message_id)
    storage = FakeStorage()
    original_decoded_data = MmsAttachment.decoded_data

    def decoded_data_spy(self):
        if self.part_id == 3:
            raise AssertionError("overflowing attachment should not be decoded")
        return original_decoded_data(self)

    monkeypatch.setattr(MmsAttachment, "decoded_data", decoded_data_spy)

    with pytest.raises(mms_webhook_service.MmsPayloadTooLarge):
        MmsWebhookService(Database(clean_database.dsn), storage).handle(request)

    assert storage.uploads == []


def test_received_after_downloaded_does_not_downgrade_downloaded_content(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, _, recipient = insert_device_with_sim(clean_database)
    message_id = "mms_" + uuid4().hex
    clean_database.track_message_key("mms:" + message_id)
    service = MmsWebhookService(Database(clean_database.dsn), FakeStorage())

    downloaded = service.handle(mms_downloaded(device_id, sender, recipient, message_id=message_id))
    received = service.handle(mms_received(device_id, sender, recipient, message_id=message_id))

    assert received.created is False
    assert received.id == downloaded.id
    with psycopg.connect(clean_database.dsn) as connection:
        row = connection.execute(
            """
            SELECT m.text_content,
                   m.metadata->>'downloadedDigest',
                   m.metadata->>'receivedDigest',
                   c.unread_count,
                   c.last_message_preview
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.id = %s
            """,
            (downloaded.id,),
        ).fetchone()
    assert row[0] == "image hello"
    assert row[1]
    assert row[2]
    assert row[3] == 1
    assert row[4] == "image hello"


def test_downloaded_after_received_fills_content_and_publishes_update_once(clean_database):
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    device_id, sim_id, recipient = insert_device_with_sim(clean_database)
    account_id = bind_agent_to_sim(clean_database, sim_id)
    message_id = "mms_" + uuid4().hex
    clean_database.track_message_key("mms:" + message_id)
    storage = FakeStorage()
    inbound_publisher = RecordingInboundPublisher()
    agent_publisher = RecordingAgentPublisher()
    service = MmsWebhookService(
        Database(clean_database.dsn),
        storage,
        inbound_publisher,
        agent_publisher,
    )

    received = service.handle(mms_received(device_id, sender, recipient, message_id=message_id))
    downloaded_request = mms_downloaded(device_id, sender, recipient, message_id=message_id)
    downloaded = service.handle(downloaded_request)
    replay = service.handle(downloaded_request)

    assert received.created is True
    assert downloaded.created is False
    assert downloaded.id == received.id
    assert replay.created is False
    assert len(storage.uploads) == 1
    assert inbound_publisher.events == [
        (device_id, received.id, received.conversation_id),
        (device_id, received.id, received.conversation_id),
    ]
    assert [event["text_content"] for event in agent_publisher.events] == [None, "image hello"]
    assert {event["account_id"] for event in agent_publisher.events} == {account_id}
    assert {event["sim_card_id"] for event in agent_publisher.events} == {sim_id}
    with psycopg.connect(clean_database.dsn) as connection:
        row = connection.execute(
            """
            SELECT m.text_content,
                   count(a.id),
                   c.unread_count,
                   c.last_message_preview
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            LEFT JOIN message_attachments a ON a.message_id = m.id
            WHERE m.id = %s
            GROUP BY m.text_content, c.unread_count, c.last_message_preview
            """,
            (received.id,),
        ).fetchone()
    assert row == ("image hello", 1, 1, "image hello")
