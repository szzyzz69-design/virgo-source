import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.message import (
    MessageCreateRequest,
    MessageCreateResponse,
    request_digest,
    to_utc_millis,
)


def test_message_request_normalizes_single_phone_and_defaults():
    request = MessageCreateRequest.model_validate(
        {"phoneNumbers": ["+86 (139) 0000-0000"], "text": " hello "}
    )

    assert request.phone_numbers == ["+8613900000000"]
    assert request.text == "hello"
    assert request.with_delivery_report is True
    assert request.priority == 0


@pytest.mark.parametrize(
    "body",
    [
        {"phoneNumbers": [], "text": "hello"},
        {"phoneNumbers": ["1", "2"], "text": "hello"},
        {"phoneNumbers": ["not-phone"], "text": "hello"},
        {"phoneNumbers": ["+86139"], "text": "   "},
        {"phoneNumbers": ["+86139"], "priority": 128, "text": "hello"},
        {"phoneNumbers": ["+86139"], "priority": True, "text": "hello"},
        {"phoneNumbers": ["+86139"], "withDeliveryReport": 1, "text": "hello"},
        {"phoneNumbers": ["+86139"], "metadata": [], "text": "hello"},
    ],
)
def test_message_request_rejects_invalid_payloads(body):
    with pytest.raises(ValidationError):
        MessageCreateRequest.model_validate(body)


def test_message_request_requires_timezone_aware_dates():
    with pytest.raises(ValidationError):
        MessageCreateRequest.model_validate(
            {
                "phoneNumbers": ["+86139"],
                "text": "hello",
                "validUntil": "2030-01-01T00:00:00",
            }
        )


def test_message_request_rejects_schedule_after_valid_until():
    with pytest.raises(ValidationError, match="scheduleAt"):
        MessageCreateRequest.model_validate(
            {
                "phoneNumbers": ["+86139"],
                "text": "hello",
                "scheduleAt": "2030-01-02T00:00:00Z",
                "validUntil": "2030-01-01T00:00:00Z",
            }
        )


def test_message_request_uses_android_aliases_and_ignores_unknown_fields():
    request = MessageCreateRequest.model_validate(
        {
            "phoneNumbers": ["+86139"],
            "text": "hello",
            "deviceId": "dev_1",
            "simNumber": 1,
            "withDeliveryReport": False,
            "conversationId": "conv_1",
            "future": "ignored",
        }
    )

    dumped = request.model_dump(by_alias=True)
    assert dumped["deviceId"] == "dev_1"
    assert dumped["simNumber"] == 1
    assert dumped["withDeliveryReport"] is False
    assert dumped["conversationId"] == "conv_1"
    assert "future" not in dumped


def test_request_digest_is_stable_and_changes_with_content():
    first = MessageCreateRequest.model_validate(
        {
            "phoneNumbers": ["+86 13900000000"],
            "text": "hello",
            "metadata": {"b": 2, "a": 1},
            "validUntil": "2030-01-01T08:00:00+08:00",
        }
    )
    same = MessageCreateRequest.model_validate(
        {
            "metadata": {"a": 1, "b": 2},
            "text": "hello",
            "phoneNumbers": ["+8613900000000"],
            "validUntil": "2030-01-01T00:00:00Z",
        }
    )
    changed = MessageCreateRequest.model_validate(
        {"phoneNumbers": ["+8613900000000"], "text": "changed"}
    )

    assert request_digest(first) == request_digest(same)
    assert request_digest(first) != request_digest(changed)


def test_utc_millis_and_response_aliases():
    occurred_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    response = MessageCreateResponse(
        id="msg_1",
        state="Pending",
        deviceId="dev_1",
        simNumber=1,
        conversationId="conv_1",
        createdAt="2030-01-01T00:00:00.000Z",
    )

    assert to_utc_millis(occurred_at) == 1_893_456_000_000
    assert response.model_dump(by_alias=True)["deviceId"] == "dev_1"


def test_message_schema_allows_mms_and_defines_attachments():
    schema = Path("pg/init/002_conversation.sql").read_text(encoding="utf-8")
    content_constraint = re.search(
        r"CONSTRAINT chk_message_content\s+CHECK \((.*?)\),\s+CONSTRAINT chk_message_route_phones",
        schema,
        re.DOTALL,
    )
    attachment_table = re.search(
        r"CREATE TABLE message_attachments \((.*?)\);\s+CREATE INDEX idx_message_attachments_message",
        schema,
        re.DOTALL,
    )

    assert "message_type IN ('SMS', 'DATA_SMS', 'MMS')" in schema
    assert content_constraint is not None
    mms_branch = re.search(
        r"""
        \(
            \s*message_type\s*=\s*'MMS'
            \s+AND\s+direction\s*=\s*'INBOUND'
            \s+AND\s+data_base64\s+IS\s+NULL
            \s+AND\s+data_port\s+IS\s+NULL
            \s*
        \)
        """,
        content_constraint.group(1),
        re.DOTALL | re.VERBOSE,
    )
    assert mms_branch is not None
    assert "text_content" not in mms_branch.group(0)
    assert attachment_table is not None
    attachment_schema = attachment_table.group(1)
    assert "CREATE TABLE message_attachments" in schema
    assert "id           VARCHAR(64) PRIMARY KEY" in attachment_schema
    assert "message_id   VARCHAR(64) NOT NULL" in attachment_schema
    assert "REFERENCES messages(id) ON DELETE CASCADE" in attachment_schema
    assert "part_id      INTEGER NOT NULL" in attachment_schema
    assert "content_type VARCHAR(100) NOT NULL" in attachment_schema
    assert "name         VARCHAR(255)" in attachment_schema
    assert "size         BIGINT" in attachment_schema
    assert "s3_bucket    VARCHAR(255) NOT NULL" in attachment_schema
    assert "s3_key       TEXT NOT NULL" in attachment_schema
    assert "url          TEXT" in attachment_schema
    assert "etag         VARCHAR(255)" in attachment_schema
    assert "metadata     JSONB NOT NULL DEFAULT '{}'::JSONB" in attachment_schema
    assert "created_at   BIGINT NOT NULL DEFAULT" in attachment_schema
    assert "updated_at   BIGINT NOT NULL DEFAULT" in attachment_schema
    assert "CONSTRAINT chk_message_attachment_part_id" in attachment_schema
    assert "CHECK (part_id >= 0)" in attachment_schema
    assert "CONSTRAINT chk_message_attachment_size" in attachment_schema
    assert "CHECK (size IS NULL OR size >= 0)" in attachment_schema
    assert "UNIQUE (message_id, part_id)" in attachment_schema
    assert "CREATE INDEX idx_message_attachments_message" in schema
    assert "ON message_attachments(message_id, part_id)" in schema
