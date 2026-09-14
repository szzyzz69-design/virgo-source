from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.message import normalize_phone


class Status(StrEnum):
    PENDING = "Pending"
    PROCESSED = "Processed"
    SENT = "Sent"
    DELIVERED = "Delivered"
    FAILED = "Failed"


PROGRESS = {
    Status.PENDING: 0,
    Status.PROCESSED: 1,
    Status.SENT: 2,
    Status.DELIVERED: 3,
}


def can_transition(old: Status, new: Status) -> bool:
    if old == new:
        return True
    if old in {Status.DELIVERED, Status.FAILED}:
        return False
    if new is Status.FAILED:
        return True
    return PROGRESS[new] >= PROGRESS[old]


def aggregate_recipient_state(states: list[Status]) -> Status:
    if Status.PENDING in states:
        return Status.PENDING
    if Status.PROCESSED in states:
        return Status.PROCESSED
    if all(state is Status.DELIVERED for state in states):
        return Status.DELIVERED
    if all(state is Status.FAILED for state in states):
        return Status.FAILED
    return Status.SENT


def to_utc_millis(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


IdText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class RecipientStatusUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    phone_number: str = Field(alias="phoneNumber")
    state: Status
    error: str | None

    @field_validator("phone_number")
    @classmethod
    def normalize_recipient_phone(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("error")
    @classmethod
    def normalize_error(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 2000:
            raise ValueError("error must contain 1 to 2000 characters")
        return normalized

    @model_validator(mode="after")
    def validate_error_state(self) -> "RecipientStatusUpdate":
        if self.state is not Status.FAILED and self.error is not None:
            raise ValueError("error is only allowed for Failed recipients")
        return self


class MessageStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: IdText
    state: Status
    recipients: list[RecipientStatusUpdate] = Field(min_length=1)
    states: dict[Status, AwareDatetime] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_status_payload(self) -> "MessageStatusUpdate":
        phones = [item.phone_number for item in self.recipients]
        if len(phones) != len(set(phones)):
            raise ValueError("recipient phone numbers must be unique")
        if self.state not in self.states:
            raise ValueError("states must contain current state")
        if Status.FAILED in self.states and Status.DELIVERED in self.states:
            raise ValueError("Failed and Delivered histories conflict")
        ordered = [
            self.states.get(status)
            for status in (
                Status.PENDING,
                Status.PROCESSED,
                Status.SENT,
                Status.DELIVERED,
            )
        ]
        occurred = [value for value in ordered if value is not None]
        if occurred != sorted(occurred):
            raise ValueError("state times must be chronological")
        return self


class MessageStatusBatch(RootModel[list[MessageStatusUpdate]]):
    root: list[MessageStatusUpdate] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_unique_messages(self) -> "MessageStatusBatch":
        ids = [item.id for item in self.root]
        if len(ids) != len(set(ids)):
            raise ValueError("message ids must be unique")
        return self
