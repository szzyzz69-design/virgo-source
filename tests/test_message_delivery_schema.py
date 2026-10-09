import pytest
from pydantic import ValidationError

from app.schemas.message_delivery import DeliveryAckCommand, ReliableMessagePullItem, payload_hash
from app.schemas.message_pull import MessagePullItem
from tests.test_message_pull_schema import base_item


def ack_body():
    return {"id": "msg_1", "token": "dlv_" + "a" * 43, "payloadHash": "b" * 64}


def test_ack_accepts_single_and_batch_and_rejects_duplicates_or_empty():
    assert DeliveryAckCommand.model_validate(ack_body()).items[0].id == "msg_1"
    assert len(DeliveryAckCommand.model_validate([ack_body()]).items) == 1
    for body in ([], [ack_body(), ack_body()], [dict(ack_body(), id=str(n)) for n in range(101)]):
        with pytest.raises(ValidationError):
            DeliveryAckCommand.model_validate(body)


@pytest.mark.parametrize("field,value", [
    ("token", ""), ("token", "dlv_short"), ("payloadHash", "G" * 64), ("payloadHash", "abc"), ("id", ""),
])
def test_ack_rejects_malformed_claims(field, value):
    with pytest.raises(ValidationError):
        DeliveryAckCommand.model_validate({**ack_body(), field: value})


def test_transport_payload_hash_excludes_lease_and_token_and_is_utf8_stable():
    body = base_item(textMessage={"text": "fixture 文字"}, validUntil=None)
    plain = MessagePullItem.model_validate(body)
    reliable = ReliableMessagePullItem.model_validate({
        **body, "legacyTransport": False,
        "delivery": {**{k: v for k, v in ack_body().items() if k != "id"},
                     "leaseExpiresAt": 123456, "simCardId": "sim_1"},
    })
    assert payload_hash(plain) == payload_hash(reliable)
    reliable.delivery.token = "dlv_" + "z" * 43
    assert payload_hash(plain) == payload_hash(reliable)


def test_only_explicit_diagnostic_can_use_legacy_transport():
    ReliableMessagePullItem.model_validate({
        **base_item(id="check_fixture"), "delivery": None, "legacyTransport": True,
    })
    for body in (
        {**base_item(), "delivery": None, "legacyTransport": True},
        {**base_item(), "delivery": None, "legacyTransport": False},
    ):
        with pytest.raises(ValidationError):
            ReliableMessagePullItem.model_validate(body)
