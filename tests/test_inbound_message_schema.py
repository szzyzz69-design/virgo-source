from pydantic import ValidationError
import pytest

from app.schemas.inbound_message import InboundMessageRequest, inbound_digest


def sms(**overrides):
    body = {
        "id": "text:1", "type": "SMS", "sender": "+86 13800138000",
        "recipient": "masked-***", "simNumber": 1, "subscriptionId": 3,
        "receivedAt": "2026-06-22T12:00:00+08:00",
        "textMessage": {"text": " hello "}, "dataMessage": None,
    }
    body.update(overrides); return body


def test_sms_normalizes_sender_and_preserves_masked_recipient():
    request = InboundMessageRequest.model_validate(sms())
    assert request.sender == "+8613800138000"
    assert request.recipient == "masked-***"
    assert request.text_message.text == "hello"


def test_sms_allows_omitted_data_message():
    body = sms()
    body.pop("dataMessage")
    request = InboundMessageRequest.model_validate(body)
    assert request.data_message is None


def test_data_sms_requires_strict_base64():
    request = InboundMessageRequest.model_validate(sms(
        type="DATA_SMS", textMessage=None, dataMessage={"data": "AQJ/"}
    ))
    assert request.data_message.data == "AQJ/"
    with pytest.raises(ValidationError):
        InboundMessageRequest.model_validate(sms(
            type="DATA_SMS", textMessage=None, dataMessage={"data": "not base64"}
        ))


@pytest.mark.parametrize(
    "body",
    [
        sms(textMessage=None),
        sms(dataMessage={"data": "AQI="}),
        sms(receivedAt="2026-06-22T08:00:00"),
        sms(simNumber=0),
    ],
)
def test_invalid_payloads_are_rejected(body):
    with pytest.raises(ValidationError):
        InboundMessageRequest.model_validate(body)


def test_digest_is_stable_for_equivalent_phone_and_metadata_order():
    first = InboundMessageRequest.model_validate(sms())
    same = InboundMessageRequest.model_validate(sms(sender="+8613800138000"))
    changed = InboundMessageRequest.model_validate(sms(textMessage={"text": "changed"}))
    assert inbound_digest(first) == inbound_digest(same)
    assert inbound_digest(first) != inbound_digest(changed)
