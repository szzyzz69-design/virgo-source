from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Annotated, Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)


NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
OptionalId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
PHONE_PATTERN = re.compile(r"^\+?\d{3,20}$")
PHONE_SEPARATORS = re.compile(r"[\s()\-]")


def normalize_phone(value: str) -> str:
    normalized = PHONE_SEPARATORS.sub("", value)
    if not PHONE_PATTERN.fullmatch(normalized):
        raise ValueError("phone number is invalid")
    return normalized


def to_utc_millis(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


class MessageCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    phone_numbers: list[str] = Field(alias="phoneNumbers", min_length=1, max_length=1)
    text: NonEmptyText
    device_id: OptionalId | None = Field(default=None, alias="deviceId")
    sim_number: int | None = Field(default=None, alias="simNumber", ge=1, strict=True)
    with_delivery_report: bool = Field(
        default=True,
        alias="withDeliveryReport",
        strict=True,
    )
    valid_until: AwareDatetime | None = Field(default=None, alias="validUntil")
    schedule_at: AwareDatetime | None = Field(default=None, alias="scheduleAt")
    priority: int = Field(default=0, ge=-128, le=127, strict=True)
    conversation_id: OptionalId | None = Field(default=None, alias="conversationId")
    metadata: dict[str, JsonValue] | None = None

    @field_validator("phone_numbers")
    @classmethod
    def normalize_phone_numbers(cls, values: list[str]) -> list[str]:
        return [normalize_phone(value) for value in values]

    @model_validator(mode="after")
    def validate_schedule_window(self) -> "MessageCreateRequest":
        if (
            self.schedule_at is not None
            and self.valid_until is not None
            and self.schedule_at > self.valid_until
        ):
            raise ValueError("scheduleAt must not be later than validUntil")
        return self


class MessageCreateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    state: str
    device_id: str = Field(alias="deviceId")
    sim_number: int = Field(alias="simNumber")
    conversation_id: str = Field(alias="conversationId")
    created_at: str = Field(alias="createdAt")


def request_digest(request: MessageCreateRequest) -> str:
    canonical: dict[str, Any] = {
        "phoneNumbers": request.phone_numbers,
        "text": request.text,
        "deviceId": request.device_id,
        "simNumber": request.sim_number,
        "withDeliveryReport": request.with_delivery_report,
        "validUntil": to_utc_millis(request.valid_until),
        "scheduleAt": to_utc_millis(request.schedule_at),
        "priority": request.priority,
        "conversationId": request.conversation_id,
        "metadata": request.metadata,
    }
    serialized = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
