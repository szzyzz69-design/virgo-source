# MMS Webhook Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Receive Android SMS Gateway MMS webhooks, store MMS image attachments in S3-compatible object storage, persist conversations and metadata in PostgreSQL, and expose MMS attachments through existing agent conversation messages.

**Architecture:** Add MMS request schemas, a signature verifier, an S3 storage adapter, and an `MmsWebhookService` that reuses the existing contact/conversation/message persistence pattern from inbound SMS. Represent MMS as `messages.message_type = 'MMS'` and store each MMS part in `message_attachments`.

**Tech Stack:** FastAPI, Pydantic, psycopg, PostgreSQL, boto3, pytest.

---

## File Structure

- Create `app/schemas/mms_webhook.py`: Pydantic models, Base64 validation, digest helpers, received-at millis conversion.
- Create `app/services/mms_signature.py`: HMAC timestamp/signature verification.
- Create `app/services/object_storage.py`: S3-compatible upload client and upload result dataclass.
- Create `app/services/mms_webhook_service.py`: MMS idempotency, conversation reuse, message persistence, S3 uploads, attachment metadata writes, publisher calls.
- Create `app/api/mms_webhook.py`: FastAPI router for `/api/v1/webhooks/android-sms-gateway/mms`.
- Modify `app/config.py`: MMS signing and S3 settings.
- Modify `app/application.py`: wire the MMS router and default services.
- Modify `pg/init/002_conversation.sql`: allow `MMS` message type and create `message_attachments`.
- Modify `app/schemas/agent_conversation.py`: add attachment response model.
- Modify `app/services/agent_conversation_service.py`: load attachments for returned messages.
- Modify `requirements.txt`: add `boto3`.
- Add tests listed below.

---

### Task 1: MMS Webhook Schema

**Files:**
- Create: `app/schemas/mms_webhook.py`
- Test: `tests/test_mms_webhook_schema.py`

- [ ] **Step 1: Write failing schema tests**

Create `tests/test_mms_webhook_schema.py`:

```python
from pydantic import ValidationError
import pytest

from app.schemas.mms_webhook import MmsWebhookRequest, mms_webhook_digest, mms_received_millis


def received_body(**overrides):
    body = {
        "deviceId": "dev_1",
        "event": "mms:received",
        "id": "wh_evt_1",
        "webhookId": "wh_1",
        "payload": {
            "messageId": "mms_1",
            "sender": "+86 13800138000",
            "recipient": "+8613900000000",
            "simNumber": 1,
            "transactionId": "tx_1",
            "subject": "Photo",
            "size": 128,
            "contentClass": "IMAGE_BASIC",
            "receivedAt": "2026-07-05T12:00:00+08:00",
        },
    }
    body.update(overrides)
    return body


def downloaded_body(**payload_overrides):
    body = received_body(event="mms:downloaded")
    payload = {
        "messageId": "mms_1",
        "sender": "+86 13800138000",
        "recipient": "+8613900000000",
        "simNumber": 1,
        "body": "hello image",
        "subject": "Photo",
        "attachments": [
            {
                "partId": 17,
                "contentType": "image/jpeg",
                "name": "photo.jpg",
                "size": 3,
                "data": "AQID",
            }
        ],
        "receivedAt": "2026-07-05T12:00:03+08:00",
    }
    payload.update(payload_overrides)
    body["payload"] = payload
    return body


def test_received_event_normalizes_sender_and_computes_digest():
    request = MmsWebhookRequest.model_validate(received_body())
    assert request.device_id == "dev_1"
    assert request.payload.sender == "+8613800138000"
    assert mms_received_millis(request) == 1783224000000
    assert mms_webhook_digest(request) == mms_webhook_digest(
        MmsWebhookRequest.model_validate(received_body(payload=received_body()["payload"]))
    )


def test_downloaded_event_accepts_image_attachment_base64():
    request = MmsWebhookRequest.model_validate(downloaded_body())
    assert request.event == "mms:downloaded"
    assert request.payload.attachments[0].content_type == "image/jpeg"
    assert request.payload.attachments[0].decoded_data() == b"\x01\x02\x03"


@pytest.mark.parametrize(
    "body",
    [
        received_body(event="sms:received"),
        received_body(deviceId=""),
        received_body(payload={**received_body()["payload"], "messageId": ""}),
        received_body(payload={**received_body()["payload"], "receivedAt": "2026-07-05T12:00:00"}),
        received_body(payload={k: v for k, v in received_body()["payload"].items() if k != "transactionId"}),
        downloaded_body(attachments=[{"partId": 1, "contentType": "", "data": "AQID"}]),
        downloaded_body(attachments=[{"partId": 1, "contentType": "image/jpeg", "data": "not base64"}]),
        downloaded_body(attachments=[{"partId": 1, "contentType": "application/x-msdownload", "data": "AQID"}]),
    ],
)
def test_invalid_payloads_are_rejected(body):
    with pytest.raises(ValidationError):
        MmsWebhookRequest.model_validate(body)
```

- [ ] **Step 2: Run schema tests to verify failure**

Run:

```bash
pytest tests/test_mms_webhook_schema.py -q
```

Expected: collection fails or tests fail because `app.schemas.mms_webhook` does not exist.

- [ ] **Step 3: Implement MMS schema**

Create `app/schemas/mms_webhook.py` with:

```python
import base64
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.message import normalize_phone


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ALLOWED_ATTACHMENT_TYPES = {"image/jpeg", "image/png", "audio/amr", "application/octet-stream"}


class MmsAttachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    part_id: int = Field(alias="partId", ge=0, strict=True)
    content_type: NonEmpty = Field(alias="contentType")
    name: str | None = None
    size: int | None = Field(default=None, ge=0, strict=True)
    data: str | None = None

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_ATTACHMENT_TYPES:
            raise ValueError("attachment contentType is unsupported")
        return normalized

    @field_validator("data")
    @classmethod
    def validate_data(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("attachment data must be standard Base64") from error
        return value

    def decoded_data(self) -> bytes | None:
        if self.data is None:
            return None
        return base64.b64decode(self.data, validate=True)


class MmsReceivedPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    message_id: NonEmpty = Field(alias="messageId")
    sender: str
    recipient: str | None = None
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    sim_number: int | None = Field(default=None, alias="simNumber", ge=1, strict=True)
    transaction_id: NonEmpty = Field(alias="transactionId")
    subject: str | None = None
    size: int = Field(ge=0, strict=True)
    content_class: str | None = Field(default=None, alias="contentClass")
    received_at: AwareDatetime = Field(alias="receivedAt")

    @field_validator("sender")
    @classmethod
    def normalize_sender(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("recipient")
    @classmethod
    def normalize_recipient(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_phone(value)
        except ValueError:
            return value


class MmsDownloadedPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    message_id: NonEmpty = Field(alias="messageId")
    sender: str
    recipient: str | None = None
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    sim_number: int | None = Field(default=None, alias="simNumber", ge=1, strict=True)
    body: str | None = None
    subject: str | None = None
    attachments: list[MmsAttachment]
    received_at: AwareDatetime = Field(alias="receivedAt")

    @field_validator("sender")
    @classmethod
    def normalize_sender(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("recipient")
    @classmethod
    def normalize_recipient(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return normalize_phone(value)
        except ValueError:
            return value


class MmsWebhookRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    device_id: NonEmpty = Field(alias="deviceId")
    event: Literal["mms:received", "mms:downloaded"]
    id: NonEmpty
    webhook_id: NonEmpty = Field(alias="webhookId")
    payload: MmsReceivedPayload | MmsDownloadedPayload

    @model_validator(mode="after")
    def validate_payload_matches_event(self) -> "MmsWebhookRequest":
        if self.event == "mms:received" and not isinstance(self.payload, MmsReceivedPayload):
            raise ValueError("mms:received requires received payload")
        if self.event == "mms:downloaded" and not isinstance(self.payload, MmsDownloadedPayload):
            raise ValueError("mms:downloaded requires downloaded payload")
        return self


def mms_received_millis(request: MmsWebhookRequest) -> int:
    return int(request.payload.received_at.astimezone(timezone.utc).timestamp() * 1000)


def mms_webhook_digest(request: MmsWebhookRequest) -> str:
    payload = request.payload
    canonical = {
        "event": request.event,
        "deviceId": request.device_id,
        "messageId": payload.message_id,
        "sender": payload.sender,
        "recipient": payload.recipient,
        "simNumber": payload.sim_number,
        "subject": payload.subject,
        "receivedAt": mms_received_millis(request),
    }
    if isinstance(payload, MmsReceivedPayload):
        canonical.update({
            "transactionId": payload.transaction_id,
            "size": payload.size,
            "contentClass": payload.content_class,
        })
    else:
        canonical.update({
            "body": payload.body,
            "attachments": [
                {
                    "partId": attachment.part_id,
                    "contentType": attachment.content_type,
                    "name": attachment.name,
                    "size": attachment.size,
                    "dataSha256": hashlib.sha256((attachment.data or "").encode()).hexdigest(),
                }
                for attachment in payload.attachments
            ],
        })
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()
```

- [ ] **Step 4: Run schema tests to verify pass**

Run:

```bash
pytest tests/test_mms_webhook_schema.py -q
```

Expected: all tests in `tests/test_mms_webhook_schema.py` pass.

- [ ] **Step 5: Commit schema task**

Run:

```bash
git add app/schemas/mms_webhook.py tests/test_mms_webhook_schema.py
git commit -m "feat: add mms webhook schema"
```

---

### Task 2: MMS Signature and S3 Configuration

**Files:**
- Create: `app/services/mms_signature.py`
- Modify: `app/config.py`
- Modify: `config.example.toml`
- Modify: `requirements.txt`
- Test: `tests/test_mms_signature.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing signature tests**

Create `tests/test_mms_signature.py`:

```python
import hashlib
import hmac
import time

import pytest

from app.services.mms_signature import InvalidMmsSignature, verify_mms_signature


def sign(key: str, raw_body: bytes, timestamp: str) -> str:
    return hmac.new(key.encode(), raw_body + timestamp.encode(), hashlib.sha256).hexdigest()


def test_accepts_valid_signature():
    raw_body = b'{"ok":true}'
    timestamp = str(int(time.time()))
    signature = sign("secret", raw_body, timestamp)
    verify_mms_signature("secret", raw_body, timestamp, signature, tolerance_seconds=300)


def test_rejects_bad_signature():
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", b"{}", str(int(time.time())), "bad", tolerance_seconds=300)


def test_rejects_expired_timestamp():
    raw_body = b"{}"
    timestamp = str(int(time.time()) - 301)
    signature = sign("secret", raw_body, timestamp)
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", raw_body, timestamp, signature, tolerance_seconds=300)


def test_allows_unsigned_when_key_is_blank():
    verify_mms_signature("", b"{}", None, None, tolerance_seconds=300)
```

- [ ] **Step 2: Run signature tests to verify failure**

Run:

```bash
pytest tests/test_mms_signature.py -q
```

Expected: collection fails or tests fail because `app.services.mms_signature` does not exist.

- [ ] **Step 3: Implement signature verifier**

Create `app/services/mms_signature.py`:

```python
import hashlib
import hmac
import time


class InvalidMmsSignature(Exception):
    pass


def verify_mms_signature(
    signing_key: str | None,
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    tolerance_seconds: int,
) -> None:
    if not signing_key:
        return
    if not timestamp or not signature:
        raise InvalidMmsSignature("missing signature headers")
    try:
        timestamp_seconds = int(timestamp)
    except ValueError as error:
        raise InvalidMmsSignature("invalid timestamp") from error
    if abs(int(time.time()) - timestamp_seconds) > tolerance_seconds:
        raise InvalidMmsSignature("timestamp outside tolerance")
    expected = hmac.new(
        signing_key.encode(),
        raw_body + timestamp.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidMmsSignature("signature mismatch")
```

- [ ] **Step 4: Write failing config tests**

Append to `tests/test_config.py`:

```python
def test_settings_loads_mms_and_s3_config(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        """
private_registration_token = "reg"
business_api_token = "business"
mms_webhook_signing_key = "signing"
mms_webhook_timestamp_tolerance_seconds = 120
s3_endpoint_url = "https://s3.example.test"
s3_region = "us-east-1"
s3_bucket = "virgo-mms"
s3_access_key_id = "access"
s3_secret_access_key = "secret"
s3_public_base_url = "https://cdn.example.test"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://db")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", str(config))

    settings = Settings.from_env()

    assert settings.mms_webhook_signing_key == "signing"
    assert settings.mms_webhook_timestamp_tolerance_seconds == 120
    assert settings.s3_endpoint_url == "https://s3.example.test"
    assert settings.s3_bucket == "virgo-mms"
    assert settings.s3_public_base_url == "https://cdn.example.test"
```

- [ ] **Step 5: Run config and signature tests to verify config failure**

Run:

```bash
pytest tests/test_mms_signature.py tests/test_config.py -q
```

Expected: signature tests pass and the new config test fails because `Settings` lacks MMS/S3 fields.

- [ ] **Step 6: Implement config fields and dependency**

Modify `app/config.py`:

```python
@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    private_registration_token: str
    business_api_token: str = ""
    device_online_window_seconds: int = 300
    mms_webhook_signing_key: str = ""
    mms_webhook_timestamp_tolerance_seconds: int = 300
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_base_url: str = ""
```

Inside `Settings.from_env`, parse and validate the values, then return:

```python
return cls(
    database_url=database_url,
    private_registration_token=private_registration_token,
    business_api_token=business_api_token,
    device_online_window_seconds=online_window,
    mms_webhook_signing_key=str(config.get("mms_webhook_signing_key", "") or ""),
    mms_webhook_timestamp_tolerance_seconds=mms_tolerance,
    s3_endpoint_url=str(config.get("s3_endpoint_url", "") or ""),
    s3_region=str(config.get("s3_region", "us-east-1") or "us-east-1"),
    s3_bucket=str(config.get("s3_bucket", "") or ""),
    s3_access_key_id=str(config.get("s3_access_key_id", "") or ""),
    s3_secret_access_key=str(config.get("s3_secret_access_key", "") or ""),
    s3_public_base_url=str(config.get("s3_public_base_url", "") or ""),
)
```

Add `mms_tolerance` validation:

```python
mms_tolerance = config.get("mms_webhook_timestamp_tolerance_seconds", 300)
if (
    isinstance(mms_tolerance, bool)
    or not isinstance(mms_tolerance, int)
    or mms_tolerance <= 0
):
    raise RuntimeError("mms_webhook_timestamp_tolerance_seconds must be a positive integer")
```

Append S3 sample keys to `config.example.toml` and add `boto3>=1.34,<2` to `requirements.txt`.

- [ ] **Step 7: Run tests to verify pass**

Run:

```bash
pytest tests/test_mms_signature.py tests/test_config.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit signature and config task**

Run:

```bash
git add app/services/mms_signature.py app/config.py config.example.toml requirements.txt tests/test_mms_signature.py tests/test_config.py
git commit -m "feat: add mms signature and storage config"
```

---

### Task 3: Object Storage Adapter

**Files:**
- Create: `app/services/object_storage.py`
- Test: `tests/test_object_storage.py`

- [ ] **Step 1: Write failing storage tests**

Create `tests/test_object_storage.py`:

```python
from app.services.object_storage import S3ObjectStorage, build_mms_object_key


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return {"ETag": '"etag-1"'}


def test_build_mms_object_key_sanitizes_name():
    key = build_mms_object_key("dev_1", "mms_1", 17, "../photo cat.jpg", "image/jpeg")
    assert key == "mms/dev_1/mms_1/17-photo-cat.jpg"


def test_upload_bytes_writes_content_type_and_public_url():
    client = FakeS3Client()
    storage = S3ObjectStorage(
        client=client,
        bucket="bucket",
        public_base_url="https://cdn.example.test/base",
    )

    result = storage.upload_bytes(
        key="mms/dev/mms/1-photo.jpg",
        body=b"abc",
        content_type="image/jpeg",
    )

    assert client.calls == [
        {
            "Bucket": "bucket",
            "Key": "mms/dev/mms/1-photo.jpg",
            "Body": b"abc",
            "ContentType": "image/jpeg",
        }
    ]
    assert result.bucket == "bucket"
    assert result.key == "mms/dev/mms/1-photo.jpg"
    assert result.url == "https://cdn.example.test/base/mms/dev/mms/1-photo.jpg"
    assert result.etag == "etag-1"
```

- [ ] **Step 2: Run storage tests to verify failure**

Run:

```bash
pytest tests/test_object_storage.py -q
```

Expected: collection fails or tests fail because `app.services.object_storage` does not exist.

- [ ] **Step 3: Implement object storage adapter**

Create `app/services/object_storage.py`:

```python
from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    key: str
    url: str | None
    etag: str | None


def extension_for_content_type(content_type: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "audio/amr": "amr"}.get(content_type, "bin")


def safe_filename(name: str | None, message_id: str, part_id: int, content_type: str) -> str:
    if not name:
        name = f"mms-{message_id}-part-{part_id}.{extension_for_content_type(content_type)}"
    leaf = name.replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", leaf).strip(".-")
    return cleaned or f"mms-{message_id}-part-{part_id}.{extension_for_content_type(content_type)}"


def build_mms_object_key(
    device_id: str,
    message_id: str,
    part_id: int,
    name: str | None,
    content_type: str,
) -> str:
    filename = safe_filename(name, message_id, part_id, content_type)
    return f"mms/{device_id}/{message_id}/{part_id}-{filename}"


class S3ObjectStorage:
    def __init__(self, *, client, bucket: str, public_base_url: str = ""):
        self._client = client
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")

    def upload_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        response = self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        etag = response.get("ETag")
        if isinstance(etag, str):
            etag = etag.strip('"')
        url = f"{self._public_base_url}/{key}" if self._public_base_url else None
        return StoredObject(bucket=self._bucket, key=key, url=url, etag=etag)
```

- [ ] **Step 4: Run storage tests to verify pass**

Run:

```bash
pytest tests/test_object_storage.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit storage adapter task**

Run:

```bash
git add app/services/object_storage.py tests/test_object_storage.py
git commit -m "feat: add mms object storage adapter"
```

---

### Task 4: Database Schema for MMS Messages and Attachments

**Files:**
- Modify: `pg/init/002_conversation.sql`
- Test: `tests/test_message_schema.py`

- [ ] **Step 1: Write failing schema text tests**

Append to `tests/test_message_schema.py`:

```python
from pathlib import Path


def test_message_schema_allows_mms_and_defines_attachments():
    schema = Path("pg/init/002_conversation.sql").read_text(encoding="utf-8")
    assert "message_type IN ('SMS', 'DATA_SMS', 'MMS')" in schema
    assert "CREATE TABLE message_attachments" in schema
    assert "UNIQUE (message_id, part_id)" in schema
    assert "message_type = 'MMS'" in schema
```

- [ ] **Step 2: Run schema text test to verify failure**

Run:

```bash
pytest tests/test_message_schema.py -q
```

Expected: the new test fails because MMS and `message_attachments` are absent.

- [ ] **Step 3: Update PostgreSQL schema**

Modify `pg/init/002_conversation.sql`:

```sql
CONSTRAINT chk_message_type
    CHECK (message_type IN ('SMS', 'DATA_SMS', 'MMS')),
```

Extend `chk_message_content`:

```sql
OR
(
    message_type = 'MMS'
    AND direction = 'INBOUND'
    AND data_base64 IS NULL
    AND data_port IS NULL
)
```

Add after `messages` indexes:

```sql
CREATE TABLE message_attachments (
    id           VARCHAR(64) PRIMARY KEY,
    message_id   VARCHAR(64) NOT NULL
                 REFERENCES messages(id) ON DELETE CASCADE,
    part_id      INTEGER NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    name         VARCHAR(255),
    size         BIGINT,
    s3_bucket    VARCHAR(255) NOT NULL,
    s3_key       TEXT NOT NULL,
    url          TEXT,
    etag         VARCHAR(255),
    metadata     JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at   BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at   BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_message_attachment_part_id
        CHECK (part_id >= 0),
    CONSTRAINT chk_message_attachment_size
        CHECK (size IS NULL OR size >= 0),
    CONSTRAINT uq_message_attachment_part
        UNIQUE (message_id, part_id)
);

CREATE INDEX idx_message_attachments_message
    ON message_attachments(message_id, part_id);
```

- [ ] **Step 4: Run schema text test to verify pass**

Run:

```bash
pytest tests/test_message_schema.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit database schema task**

Run:

```bash
git add pg/init/002_conversation.sql tests/test_message_schema.py
git commit -m "feat: add mms attachment schema"
```

---

### Task 5: MMS Webhook Service

**Files:**
- Create: `app/services/mms_webhook_service.py`
- Test: `tests/integration/test_mms_webhook_service.py`

- [ ] **Step 1: Write failing service integration tests**

Create `tests/integration/test_mms_webhook_service.py`:

```python
from datetime import datetime, timezone
from uuid import uuid4

import psycopg

from app.database import Database
from app.schemas.mms_webhook import MmsWebhookRequest
from app.services.mms_webhook_service import MmsWebhookService
from app.services.object_storage import StoredObject


class FakeStorage:
    def __init__(self):
        self.uploads = []

    def upload_bytes(self, *, key, body, content_type):
        self.uploads.append((key, body, content_type))
        return StoredObject("bucket", key, f"https://cdn.example.test/{key}", "etag")


def mms_downloaded(device_id, sender, recipient):
    return MmsWebhookRequest.model_validate(
        {
            "deviceId": device_id,
            "event": "mms:downloaded",
            "id": "wh_" + uuid4().hex,
            "webhookId": "wh_mms",
            "payload": {
                "messageId": "mms_" + uuid4().hex,
                "sender": sender,
                "recipient": recipient,
                "simNumber": 1,
                "body": "image hello",
                "subject": "Photo",
                "attachments": [
                    {
                        "partId": 17,
                        "contentType": "image/jpeg",
                        "name": "photo.jpg",
                        "size": 3,
                        "data": "AQID",
                    }
                ],
                "receivedAt": datetime.now(timezone.utc).isoformat(),
            },
        }
    )


def test_downloaded_mms_creates_message_and_attachment(clean_database):
    marker = clean_database.track_push_token("pytest-mms-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    recipient = "+8613900000000"
    storage = FakeStorage()
    database = Database(clean_database.dsn)

    with psycopg.connect(clean_database.dsn) as connection:
        device_id = "dev_" + uuid4().hex
        token_hash = "unused"
        connection.execute(
            """
            INSERT INTO devices(id, name, push_token, token_hash, enabled, status, last_seen_at, created_at, updated_at)
            VALUES(%s, 'mms-phone', %s, %s, TRUE, 'online', 1783224000000, 1783224000000, 1783224000000)
            """,
            (device_id, marker, token_hash),
        )
        connection.execute(
            """
            INSERT INTO sim_cards(id, device_id, slot_index, sim_number, phone_number, enabled, status)
            VALUES(%s, %s, 0, 1, %s, TRUE, 'active')
            """,
            ("sim_" + uuid4().hex, device_id, recipient),
        )
        connection.commit()
    clean_database.track(device_id)

    request = mms_downloaded(device_id, sender, recipient)
    result = MmsWebhookService(database, storage).handle(request)

    assert result.created is True
    assert storage.uploads[0][1] == b"\x01\x02\x03"
    with psycopg.connect(clean_database.dsn) as connection:
        message = connection.execute(
            "SELECT message_type, text_content, direction FROM messages WHERE id = %s",
            (result.id,),
        ).fetchone()
        attachment = connection.execute(
            "SELECT part_id, content_type, s3_bucket, s3_key, url FROM message_attachments WHERE message_id = %s",
            (result.id,),
        ).fetchone()
    assert message == ("MMS", "image hello", "INBOUND")
    assert attachment[0] == 17
    assert attachment[1] == "image/jpeg"
    assert attachment[2] == "bucket"
    assert attachment[4].startswith("https://cdn.example.test/")
```

- [ ] **Step 2: Run service integration test to verify failure**

Run:

```bash
pytest tests/integration/test_mms_webhook_service.py -q
```

Expected: collection fails or test fails because `MmsWebhookService` does not exist.

- [ ] **Step 3: Implement service dataclasses and core flow**

Create `app/services/mms_webhook_service.py` with:

```python
from dataclasses import dataclass
import logging
import secrets
import time

from psycopg.types.json import Jsonb

from app.database import Database
from app.schemas.inbound_message import InboundMessageRequest
from app.schemas.message import normalize_phone
from app.schemas.mms_webhook import MmsDownloadedPayload, MmsWebhookRequest, mms_received_millis, mms_webhook_digest
from app.services.agent_event_publisher import NoOpAgentEventPublisher
from app.services.inbound_message_service import InboundConflict, InboundDeviceUnavailable, InboundResult, InboundValidation
from app.services.inbound_publisher import InboundMessagePublisher, NoOpInboundMessagePublisher
from app.services.object_storage import build_mms_object_key


logger = logging.getLogger(__name__)


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
        digest = mms_webhook_digest(request)
        payload = request.payload
        idempotency_key = f"mms:{payload.message_id}"

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
                SELECT id, conversation_id, metadata
                FROM messages
                WHERE device_id=%s AND direction='INBOUND' AND idempotency_key=%s
                LIMIT 1
                """,
                (request.device_id, idempotency_key),
            ).fetchone()
            if existing:
                metadata = existing[2] or {}
                digest_key = "downloadedDigest" if request.event == "mms:downloaded" else "receivedDigest"
                if metadata.get(digest_key) == digest:
                    return MmsWebhookResult(existing[0], existing[1], payload.message_id, False)
                if metadata.get(digest_key) is not None:
                    raise InboundConflict
                message_id = existing[0]
                conversation_id = existing[1]
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
                    payload.recipient,
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
                    "simNumber": payload.sim_number,
                    "recipient": payload.recipient,
                }
                connection.execute(
                    """
                    INSERT INTO messages(id, conversation_id, direction, message_type, text_content,
                                         from_phone_number, to_phone_number, state, device_id,
                                         sim_card_id, sim_number, idempotency_key, received_at,
                                         metadata, created_at, updated_at)
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
                        INSERT INTO message_attachments(id, message_id, part_id, content_type, name,
                                                        size, s3_bucket, s3_key, url, etag,
                                                        metadata, created_at, updated_at)
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

            preview = self._preview(request)
            connection.execute(
                """
                UPDATE messages
                SET text_content = COALESCE(%s, text_content),
                    metadata = %s,
                    updated_at = %s
                WHERE id = %s
                """,
                (
                    payload.body if isinstance(payload, MmsDownloadedPayload) else None,
                    Jsonb(metadata),
                    now,
                    message_id,
                ),
            )
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
                (created, preview, received, now, conversation_id),
            )
            connection.execute(
                "UPDATE devices SET status='online',last_seen_at=%s,updated_at=%s WHERE id=%s",
                (now, now, request.device_id),
            )

        try:
            self._publisher.publish(request.device_id, message_id, conversation_id)
        except Exception:
            logger.exception("Inbound publisher failed for MMS message %s", message_id)
        return MmsWebhookResult(message_id, conversation_id, payload.message_id, created)
```

Add these helper methods in the same class:

```python
    def _resolve_sim(self, connection, device_id, sim_number, recipient):
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
                    "SELECT id,sim_number,areas FROM sim_cards WHERE device_id=%s AND regexp_replace(phone_number,'[\\s()\\-]','','g')=%s LIMIT 1",
                    (device_id, normalized_recipient),
                ).fetchone()
        return sim

    def _get_or_create_conversation(
        self,
        connection,
        device_id,
        sender,
        recipient,
        sim_id,
        sim_number,
        area,
        received,
        now,
    ):
        agent_account_ids = ()
        if sim_id is not None:
            agent_account_ids = tuple(
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
            INSERT INTO conversations(id,external_phone_number,contact_id,device_id,sim_card_id,sim_number,areas,status,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s)
            """,
            (conversation_id, sender, contact_id, device_id, sim_id, sim_number, area, now, now),
        )
        return conversation_id, agent_account_ids

    def _merge_metadata(self, metadata, request, digest):
        payload = request.payload
        merged = dict(metadata or {})
        merged["mms"] = {
            **dict(merged.get("mms") or {}),
            "messageId": payload.message_id,
            "webhookId": request.webhook_id,
            "webhookEventId": request.id,
            "subject": payload.subject,
            "event": request.event,
        }
        if request.event == "mms:downloaded":
            merged["downloadedDigest"] = digest
        else:
            merged["receivedDigest"] = digest
        return merged

    def _preview(self, request):
        payload = request.payload
        if isinstance(payload, MmsDownloadedPayload) and payload.body:
            return payload.body[:255]
        if isinstance(payload, MmsDownloadedPayload) and payload.attachments:
            image_count = sum(1 for item in payload.attachments if item.content_type in {"image/jpeg", "image/png"})
            if image_count:
                return "[MMS image]"
        return "[MMS]"
```

- [ ] **Step 4: Run service integration test to verify pass**

Run:

```bash
pytest tests/integration/test_mms_webhook_service.py -q
```

Expected: selected integration test passes against the local PostgreSQL test database.

- [ ] **Step 5: Commit MMS service task**

Run:

```bash
git add app/services/mms_webhook_service.py tests/integration/test_mms_webhook_service.py
git commit -m "feat: persist inbound mms messages"
```

---

### Task 6: MMS Webhook API and App Wiring

**Files:**
- Create: `app/api/mms_webhook.py`
- Modify: `app/application.py`
- Test: `tests/test_mms_webhook_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_mms_webhook_api.py`:

```python
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.services.inbound_message_service import InboundConflict
from app.services.mms_webhook_service import MmsWebhookResult


class Service:
    def __init__(self, replay=False, error=None):
        self.replay = replay
        self.error = error
        self.calls = []

    def handle(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return MmsWebhookResult("msg_1", "conv_1", "mms_1", not self.replay)


def body():
    return {
        "deviceId": "dev_1",
        "event": "mms:received",
        "id": "wh_evt_1",
        "webhookId": "wh_1",
        "payload": {
            "messageId": "mms_1",
            "sender": "+8613800138000",
            "recipient": None,
            "simNumber": 1,
            "transactionId": "tx_1",
            "size": 128,
            "receivedAt": "2026-07-05T08:00:00Z",
        },
    }


def client(service, signing_key=""):
    app = create_app(
        Settings("postgresql://unused", "reg", "business", mms_webhook_signing_key=signing_key),
        mms_webhook_service=service,
    )
    return TestClient(app, raise_server_exceptions=False)


def test_mms_webhook_returns_200_contract():
    response = client(Service()).post("/api/v1/webhooks/android-sms-gateway/mms", json=body())
    assert response.status_code == 200
    assert response.json() == {"ok": True, "messageId": "mms_1", "created": True}


def test_mms_webhook_replay_returns_created_false():
    response = client(Service(replay=True)).post("/api/v1/webhooks/android-sms-gateway/mms", json=body())
    assert response.status_code == 200
    assert response.json()["created"] is False


def test_mms_webhook_maps_conflict():
    response = client(Service(error=InboundConflict())).post("/api/v1/webhooks/android-sms-gateway/mms", json=body())
    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"
```

- [ ] **Step 2: Run API tests to verify failure**

Run:

```bash
pytest tests/test_mms_webhook_api.py -q
```

Expected: collection fails because `app.api.mms_webhook` or `mms_webhook_service` injection is missing.

- [ ] **Step 3: Implement API router**

Create `app/api/mms_webhook.py`:

```python
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request

from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.mms_webhook import MmsWebhookRequest
from app.services.inbound_message_service import InboundConflict, InboundDeviceUnavailable, InboundValidation
from app.services.mms_signature import InvalidMmsSignature, verify_mms_signature
from app.services.mms_webhook_service import MmsWebhookResult


class MmsHandlingService(Protocol):
    def handle(self, request: MmsWebhookRequest) -> MmsWebhookResult:
        raise NotImplementedError


def create_mms_webhook_router(
    service: MmsHandlingService,
    *,
    signing_key: str,
    timestamp_tolerance_seconds: int,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/webhooks/android-sms-gateway", tags=["mms-webhook"])

    async def webhook_request(
        request: Request,
        x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
        x_signature: str | None = Header(default=None, alias="X-Signature"),
    ) -> MmsWebhookRequest:
        raw_body = await request.body()
        try:
            verify_mms_signature(
                signing_key,
                raw_body,
                x_timestamp,
                x_signature,
                tolerance_seconds=timestamp_tolerance_seconds,
            )
        except InvalidMmsSignature as error:
            raise ApiError(401, "UNAUTHORIZED", "Invalid MMS webhook signature") from error
        return await parse_json_model(request, MmsWebhookRequest)

    @router.post("/mms")
    def receive_mms(body: MmsWebhookRequest = Depends(webhook_request)):
        try:
            result = service.handle(body)
        except InboundConflict as error:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "MMS webhook was used for different content") from error
        except InboundDeviceUnavailable as error:
            raise ApiError(403, "DEVICE_FORBIDDEN", "Device is unavailable") from error
        except InboundValidation as error:
            raise ApiError(400, "VALIDATION_ERROR", "MMS webhook is invalid") from error
        return {"ok": True, "messageId": result.message_id, "created": result.created}

    return router
```

- [ ] **Step 4: Wire router in application**

Modify `app/application.py`:

```python
from app.api.mms_webhook import MmsHandlingService, create_mms_webhook_router
from app.services.mms_webhook_service import MmsWebhookService
from app.services.object_storage import S3ObjectStorage
```

Add a `mms_webhook_service: MmsHandlingService | None = None` parameter to `create_app`.

Create the default S3 client with boto3 only when needed:

```python
import boto3
```

```python
mms_service = mms_webhook_service
if mms_service is None:
    s3_client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
    )
    mms_storage = S3ObjectStorage(
        client=s3_client,
        bucket=settings.s3_bucket,
        public_base_url=settings.s3_public_base_url,
    )
    mms_service = MmsWebhookService(
        database,
        mms_storage,
        inbound_publisher or NoOpInboundMessagePublisher(),
        RegistryAgentEventPublisher(agent_registry),
    )
app.include_router(
    create_mms_webhook_router(
        mms_service,
        signing_key=settings.mms_webhook_signing_key,
        timestamp_tolerance_seconds=settings.mms_webhook_timestamp_tolerance_seconds,
    )
)
```

- [ ] **Step 5: Run API tests to verify pass**

Run:

```bash
pytest tests/test_mms_webhook_api.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit API task**

Run:

```bash
git add app/api/mms_webhook.py app/application.py tests/test_mms_webhook_api.py
git commit -m "feat: add mms webhook api"
```

---

### Task 7: Agent Conversation Attachment Responses

**Files:**
- Modify: `app/schemas/agent_conversation.py`
- Modify: `app/services/agent_conversation_service.py`
- Test: `tests/test_agent_conversation_api.py`
- Test: `tests/integration/test_mms_agent_conversation.py`

- [ ] **Step 1: Write failing schema/API test**

Append to `tests/test_agent_conversation_api.py`:

```python
def test_agent_message_item_includes_attachments():
    from app.schemas.agent_conversation import AgentMessageAttachment, AgentMessageItem

    item = AgentMessageItem(
        id="msg_1",
        conversationId="conv_1",
        direction="INBOUND",
        messageType="MMS",
        textContent="hello",
        state="Received",
        fromPhoneNumber="+8613800138000",
        toPhoneNumber=None,
        createdAt=1,
        receivedAt=1,
        sentAt=None,
        deliveredAt=None,
        attachments=[
            AgentMessageAttachment(
                id="att_1",
                partId=17,
                contentType="image/jpeg",
                name="photo.jpg",
                size=3,
                url="https://cdn.example.test/photo.jpg",
            )
        ],
    )

    assert item.model_dump(by_alias=True)["attachments"][0]["partId"] == 17
```

- [ ] **Step 2: Run agent API tests to verify failure**

Run:

```bash
pytest tests/test_agent_conversation_api.py -q
```

Expected: the new test fails because `AgentMessageAttachment` is missing.

- [ ] **Step 3: Add attachment schemas**

Modify `app/schemas/agent_conversation.py`:

```python
class AgentMessageAttachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    part_id: int = Field(alias="partId")
    content_type: str = Field(alias="contentType")
    name: str | None
    size: int | None
    url: str | None
```

Add to `AgentMessageItem`:

```python
attachments: list[AgentMessageAttachment] = Field(default_factory=list)
```

- [ ] **Step 4: Load attachments in conversation service**

Modify `app/services/agent_conversation_service.py` so `list_messages` fetches message rows, then fetches attachments for those IDs:

```python
message_ids = [row[0] for row in rows]
attachments_by_message = {message_id: [] for message_id in message_ids}
if message_ids:
    attachment_rows = connection.execute(
        """
        SELECT message_id, id, part_id, content_type, name, size, url
        FROM message_attachments
        WHERE message_id = ANY(%s::varchar[])
        ORDER BY message_id, part_id
        """,
        (message_ids,),
    ).fetchall()
    for attachment in attachment_rows:
        attachments_by_message[attachment[0]].append(
            AgentMessageAttachment(
                id=attachment[1],
                partId=attachment[2],
                contentType=attachment[3],
                name=attachment[4],
                size=attachment[5],
                url=attachment[6],
            )
        )
```

Pass `attachments=attachments_by_message.get(row[0], [])` when building `AgentMessageItem`.

- [ ] **Step 5: Run agent API tests to verify pass**

Run:

```bash
pytest tests/test_agent_conversation_api.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit agent response task**

Run:

```bash
git add app/schemas/agent_conversation.py app/services/agent_conversation_service.py tests/test_agent_conversation_api.py
git commit -m "feat: expose mms attachments to agents"
```

---

### Task 8: End-to-End Verification

**Files:**
- Test: `tests/integration/test_mms_webhook_flow.py`
- Modify code found by the failing tests.

- [ ] **Step 1: Write failing end-to-end test**

Create `tests/integration/test_mms_webhook_flow.py` with a full `TestClient` flow:

```python
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.database import Database
from app.security import hash_password
from app.services.mms_webhook_service import MmsWebhookService
from app.services.object_storage import StoredObject


class FakeStorage:
    def upload_bytes(self, *, key, body, content_type):
        return StoredObject("bucket", key, f"https://cdn.example.test/{key}", "etag")


def test_mms_downloaded_is_visible_in_agent_messages(clean_database):
    marker = clean_database.track_push_token("pytest-mms-flow-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    recipient = "+8613900000000"
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret"),
        mms_webhook_service=MmsWebhookService(Database(clean_database.dsn), FakeStorage()),
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "mms-flow-phone",
                "pushToken": marker,
                "simCards": [{"slotIndex": 0, "simNumber": 1, "phoneNumber": recipient}],
            },
        ).json()
        clean_database.track(registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            sim_id = connection.execute(
                "SELECT id FROM sim_cards WHERE device_id = %s AND sim_number = 1",
                (registration["id"],),
            ).fetchone()[0]
            account_id = "acct_" + uuid4().hex
            username = "agent_" + uuid4().hex
            password = "correct-password"
            connection.execute(
                """
                INSERT INTO accounts(id, username, password_hash, areas, status)
                VALUES(%s, %s, %s, NULL, 'ACTIVE')
                """,
                (account_id, username, hash_password(password)),
            )
            connection.execute(
                "INSERT INTO account_sim_cards(account_id, sim_card_id) VALUES(%s, %s)",
                (account_id, sim_id),
            )
            connection.commit()

        webhook = client.post(
            "/api/v1/webhooks/android-sms-gateway/mms",
            json={
                "deviceId": registration["id"],
                "event": "mms:downloaded",
                "id": "wh_" + uuid4().hex,
                "webhookId": "wh_mms",
                "payload": {
                    "messageId": "mms_" + uuid4().hex,
                    "sender": sender,
                    "recipient": recipient,
                    "simNumber": 1,
                    "body": "image hello",
                    "subject": "Photo",
                    "attachments": [
                        {
                            "partId": 17,
                            "contentType": "image/jpeg",
                            "name": "photo.jpg",
                            "size": 3,
                            "data": "AQID",
                        }
                    ],
                    "receivedAt": datetime.now(timezone.utc).isoformat(),
                },
            },
        )
        assert webhook.status_code == 200

        with psycopg.connect(clean_database.dsn) as connection:
            conversation_id = connection.execute(
                "SELECT conversation_id FROM messages WHERE id = %s",
                (webhook.json()["messageId"],),
            ).fetchone()[0]

        login = client.post(
            "/agent/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200
        messages = client.get(
            f"/agent/v1/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {login.json()['token']}"},
        )

    assert messages.status_code == 200
    message = messages.json()[0]
    assert message["messageType"] == "MMS"
    assert message["textContent"] == "image hello"
    assert message["attachments"][0]["contentType"] == "image/jpeg"
    assert message["attachments"][0]["url"].startswith("https://cdn.example.test/")
```

- [ ] **Step 2: Run end-to-end test to verify failure**

Run:

```bash
pytest tests/integration/test_mms_webhook_flow.py -q
```

Expected: fails if the webhook route, MMS service, DB schema, or agent attachment serialization is incomplete.

- [ ] **Step 3: Complete the end-to-end test and fix discovered gaps**

If the test fails because `create_app` does not accept `mms_webhook_service`, finish Task 6 first. If it fails because the database lacks `message_attachments`, finish Task 4 first. If it fails because the agent response lacks `attachments`, finish Task 7 first.

- [ ] **Step 4: Run end-to-end test to verify pass**

Run:

```bash
pytest tests/integration/test_mms_webhook_flow.py -q
```

Expected: selected integration test passes.

- [ ] **Step 5: Run targeted full MMS suite**

Run:

```bash
pytest tests/test_mms_webhook_schema.py tests/test_mms_signature.py tests/test_object_storage.py tests/test_mms_webhook_api.py tests/integration/test_mms_webhook_service.py tests/integration/test_mms_webhook_flow.py -q
```

Expected: all selected MMS tests pass.

- [ ] **Step 6: Run affected regression tests**

Run:

```bash
pytest tests/test_config.py tests/test_message_schema.py tests/test_agent_conversation_api.py tests/test_inbox_api.py tests/integration/test_agent_conversation_area_flow.py -q
```

Expected: all selected regression tests pass.

- [ ] **Step 7: Commit final verification task**

Run:

```bash
git add app tests pg requirements.txt config.example.toml
git commit -m "test: verify mms webhook conversation flow"
```

---

## Final Verification

Run:

```bash
pytest -q
```

Expected: full test suite passes.

Run:

```bash
git status --short
```

Expected: only intentional untracked user files remain, including `mms-webhook-server-api-requirements.md` if it is still untracked.
