from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_iso_from_millis(value: int | None) -> str | None:
    if value is None:
        return None
    return (
        datetime.fromtimestamp(value / 1000, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class TextMessage(BaseModel):
    text: str


class DataMessage(BaseModel):
    data: str
    port: int = Field(ge=0, le=65535)


class MessagePullItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    text_message: TextMessage | None = Field(alias="textMessage")
    data_message: DataMessage | None = Field(alias="dataMessage")
    phone_numbers: list[str] = Field(alias="phoneNumbers", min_length=1)
    sim_number: int | None = Field(alias="simNumber")
    with_delivery_report: bool | None = Field(alias="withDeliveryReport")
    is_encrypted: bool | None = Field(alias="isEncrypted")
    valid_until: str | None = Field(alias="validUntil")
    schedule_at: str | None = Field(alias="scheduleAt")
    priority: int | None = Field(ge=-128, le=127)
    created_at: str | None = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_payload(self) -> "MessagePullItem":
        if (self.text_message is None) == (self.data_message is None):
            raise ValueError("exactly one message payload is required")
        return self
