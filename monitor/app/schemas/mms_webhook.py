import base64
from datetime import timezone
import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.message import normalize_phone


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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
        return value.strip().lower()

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

    @model_validator(mode="before")
    @classmethod
    def validate_payload_for_event(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        event = data.get("event")
        payload = data.get("payload")
        if event == "mms:received":
            parsed_payload = MmsReceivedPayload.model_validate(payload)
        elif event == "mms:downloaded":
            parsed_payload = MmsDownloadedPayload.model_validate(payload)
        else:
            return data
        return {**data, "payload": parsed_payload}

    @model_validator(mode="after")
    def validate_payload_matches_event(self) -> "MmsWebhookRequest":
        if self.event == "mms:received" and not isinstance(self.payload, MmsReceivedPayload):
            raise ValueError("mms:received requires received payload")
        if self.event == "mms:downloaded" and not isinstance(self.payload, MmsDownloadedPayload):
            raise ValueError("mms:downloaded requires downloaded payload")
        return self


def mms_received_millis(request: MmsWebhookRequest) -> int:
    return int(request.payload.received_at.astimezone(timezone.utc).timestamp() * 1000)


def _attachment_data_sha256(attachment: MmsAttachment) -> str | None:
    decoded_data = attachment.decoded_data()
    if decoded_data is None:
        return None
    return hashlib.sha256(decoded_data).hexdigest()


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
        canonical.update(
            {
                "transactionId": payload.transaction_id,
                "size": payload.size,
                "contentClass": payload.content_class,
            }
        )
    else:
        canonical.update(
            {
                "body": payload.body,
                "attachments": [
                    {
                        "partId": attachment.part_id,
                        "contentType": attachment.content_type,
                        "name": attachment.name,
                        "size": attachment.size,
                        "dataSha256": _attachment_data_sha256(attachment),
                    }
                    for attachment in payload.attachments
                ],
            }
        )
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()
