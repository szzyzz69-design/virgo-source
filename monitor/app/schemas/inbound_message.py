import base64
from datetime import datetime, timezone
import hashlib
import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.message import normalize_phone


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class InboundTextMessage(BaseModel):
    text: NonEmpty


class InboundDataMessage(BaseModel):
    data: NonEmpty

    @field_validator("data")
    @classmethod
    def validate_base64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise ValueError("data must be standard Base64") from error
        return value


class InboundMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    type: Literal["SMS", "DATA_SMS"]
    sender: str
    recipient: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = None
    sim_number: int | None = Field(default=None, alias="simNumber", ge=1, strict=True)
    subscription_id: int | None = Field(default=None, alias="subscriptionId", strict=True)
    received_at: AwareDatetime = Field(alias="receivedAt")
    text_message: InboundTextMessage | None = Field(alias="textMessage")
    data_message: InboundDataMessage | None = Field(default=None, alias="dataMessage")

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

    @model_validator(mode="after")
    def validate_content(self) -> "InboundMessageRequest":
        if self.type == "SMS":
            if self.text_message is None or self.data_message is not None:
                raise ValueError("SMS requires only textMessage")
        elif self.data_message is None or self.text_message is not None:
            raise ValueError("DATA_SMS requires only dataMessage")
        return self


def received_millis(request: InboundMessageRequest) -> int:
    return int(request.received_at.astimezone(timezone.utc).timestamp() * 1000)


def inbound_digest(request: InboundMessageRequest) -> str:
    canonical = {
        "type": request.type,
        "sender": request.sender,
        "recipient": request.recipient,
        "simNumber": request.sim_number,
        "subscriptionId": request.subscription_id,
        "receivedAt": received_millis(request),
        "text": request.text_message.text if request.text_message else None,
        "data": request.data_message.data if request.data_message else None,
    }
    value = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()
