import pytest
from pydantic import ValidationError

from app.schemas.message_pull import (
    DataMessage,
    MessagePullItem,
    TextMessage,
    utc_iso_from_millis,
)


def base_item(**overrides):
    values = {
        "id": "msg_1",
        "textMessage": {"text": "hello"},
        "dataMessage": None,
        "phoneNumbers": ["+8613800138000"],
        "simNumber": 1,
        "withDeliveryReport": True,
        "isEncrypted": False,
        "validUntil": "2026-06-22T12:00:00.000Z",
        "scheduleAt": None,
        "priority": 10,
        "createdAt": "2026-06-22T11:00:00.000Z",
    }
    values.update(overrides)
    return values


def test_text_message_serializes_exact_android_contract():
    item = MessagePullItem.model_validate(base_item())

    assert item.text_message == TextMessage(text="hello")
    assert item.model_dump(by_alias=True) == base_item()


def test_data_message_serializes_exact_android_contract():
    values = base_item(
        textMessage=None,
        dataMessage={"data": "AQJ/", "port": 53739},
        simNumber=None,
        validUntil=None,
    )

    item = MessagePullItem.model_validate(values)

    assert item.data_message == DataMessage(data="AQJ/", port=53739)
    assert item.model_dump(by_alias=True) == values


@pytest.mark.parametrize(
    ("text_message", "data_message"),
    [(None, None), ({"text": "hello"}, {"data": "AQI=", "port": 1})],
)
def test_exactly_one_message_payload_is_required(text_message, data_message):
    with pytest.raises(ValidationError):
        MessagePullItem.model_validate(
            base_item(textMessage=text_message, dataMessage=data_message)
        )


def test_at_least_one_recipient_is_required():
    with pytest.raises(ValidationError):
        MessagePullItem.model_validate(base_item(phoneNumbers=[]))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (0, "1970-01-01T00:00:00.000Z"),
        (1782115200123, "2026-06-22T08:00:00.123Z"),
    ],
)
def test_utc_iso_from_millis(value, expected):
    assert utc_iso_from_millis(value) == expected
