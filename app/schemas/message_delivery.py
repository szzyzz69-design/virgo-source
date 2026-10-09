import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, model_validator

from app.schemas.message_pull import MessagePullItem
from app.schemas.message_status import IdText


DeliveryToken = Annotated[str, StringConstraints(pattern=r"^dlv_[A-Za-z0-9_-]{43}$")]
PayloadHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class DeliveryClaim(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    token: DeliveryToken
    payload_hash: PayloadHash = Field(alias="payloadHash")
    lease_expires_at: int = Field(alias="leaseExpiresAt", gt=0, strict=True)
    sim_card_id: str = Field(alias="simCardId")


class ReliableMessagePullItem(MessagePullItem):
    delivery: DeliveryClaim | None
    legacy_transport: bool = Field(alias="legacyTransport")

    @model_validator(mode="after")
    def require_explicit_transport(self):
        if self.legacy_transport:
            if self.delivery is not None or not self.id.startswith("check_"):
                raise ValueError("only diagnostics may use explicit legacy transport")
        elif self.delivery is None:
            raise ValueError("reliable message requires a delivery claim")
        return self


def payload_hash(item: MessagePullItem) -> str:
    # Deliberately exclude transport metadata, even for a subclass instance.
    body = {key: value for key, value in item.model_dump(by_alias=True).items()
            if key not in {"delivery", "legacyTransport"}}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DeliveryAck(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: IdText
    token: DeliveryToken
    payload_hash: PayloadHash = Field(alias="payloadHash")


class DeliveryAckCommand(RootModel[DeliveryAck | list[DeliveryAck]]):
    @model_validator(mode="after")
    def validate_batch(self):
        items = self.items
        if not 1 <= len(items) <= 100:
            raise ValueError("ack batch requires 1 to 100 items")
        if len({item.id for item in items}) != len(items):
            raise ValueError("ack message ids must be unique")
        return self

    @property
    def items(self) -> list[DeliveryAck]:
        return self.root if isinstance(self.root, list) else [self.root]
