import pytest
from pydantic import ValidationError

from app.schemas.device import (
    DeviceRegisterRequest,
    DeviceUpdateRequest,
    DeviceUpdateResponse,
)


def valid_body():
    return {
        "name": " Samsung/SM-G9910 ",
        "pushToken": "android-push-token",
        "simCards": [
            {
                "slotIndex": 0,
                "simNumber": 1,
                "phoneNumber": "***1234",
                "carrierName": "carrier",
                "iccid": "iccid-value",
            }
        ],
    }


def test_request_parses_android_body_and_round_trips_aliases():
    request = DeviceRegisterRequest.model_validate(valid_body())

    assert request.name == "Samsung/SM-G9910"
    assert request.push_token == "android-push-token"
    assert request.sim_cards[0].slot_index == 0
    assert request.sim_cards[0].sim_number == 1

    dumped = request.model_dump(by_alias=True)
    assert dumped["pushToken"] == "android-push-token"
    assert "simCards" in dumped
    assert "sim_cards" not in dumped
    assert dumped["simCards"][0]["slotIndex"] == 0
    assert dumped["simCards"][0]["simNumber"] == 1
    assert dumped["simCards"][0]["phoneNumber"] == "***1234"
    assert dumped["simCards"][0]["carrierName"] == "carrier"


def test_sim_cards_is_required_but_may_be_empty():
    with pytest.raises(ValidationError):
        DeviceRegisterRequest.model_validate({"name": "phone"})

    request = DeviceRegisterRequest.model_validate({"name": "phone", "simCards": []})
    assert request.sim_cards == []


@pytest.mark.parametrize("field", ["slotIndex", "simNumber"])
def test_request_rejects_duplicate_sim_identity(field):
    body = valid_body()
    duplicate = dict(body["simCards"][0])
    duplicate["slotIndex"] = 1
    duplicate["simNumber"] = 2
    duplicate[field] = body["simCards"][0][field]
    body["simCards"].append(duplicate)

    with pytest.raises(ValidationError, match=field):
        DeviceRegisterRequest.model_validate(body)


def test_request_ignores_unknown_top_level_fields():
    body = valid_body()
    body["futureField"] = "ignored"

    request = DeviceRegisterRequest.model_validate(body)

    assert "futureField" not in request.model_dump()


def test_request_ignores_unknown_nested_sim_fields():
    body = valid_body()
    body["simCards"][0]["futureSimField"] = "ignored"

    request = DeviceRegisterRequest.model_validate(body)

    assert "futureSimField" not in request.sim_cards[0].model_dump()


@pytest.mark.parametrize("name", [" \t ", "x" * 201])
def test_request_rejects_invalid_name_boundaries(name):
    body = valid_body()
    body["name"] = name

    with pytest.raises(ValidationError):
        DeviceRegisterRequest.model_validate(body)


def test_request_rejects_negative_slot_index():
    body = valid_body()
    body["simCards"][0]["slotIndex"] = -1

    with pytest.raises(ValidationError):
        DeviceRegisterRequest.model_validate(body)


def test_request_rejects_zero_sim_number():
    body = valid_body()
    body["simCards"][0]["simNumber"] = 0

    with pytest.raises(ValidationError):
        DeviceRegisterRequest.model_validate(body)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slotIndex", False),
        ("simNumber", True),
        ("slotIndex", "0"),
        ("simNumber", "1"),
    ],
)
def test_request_rejects_non_strict_sim_identity_types(field, value):
    body = valid_body()
    body["simCards"][0][field] = value

    with pytest.raises(ValidationError):
        DeviceRegisterRequest.model_validate(body)


def test_device_update_distinguishes_omitted_null_and_value_fields():
    omitted = DeviceUpdateRequest.model_validate({"id": "dev_1"})
    cleared = DeviceUpdateRequest.model_validate(
        {"id": "dev_1", "pushToken": None, "simCards": None}
    )
    supplied = DeviceUpdateRequest.model_validate(
        {"id": "dev_1", "pushToken": "new", "simCards": []}
    )

    assert "push_token" not in omitted.model_fields_set
    assert "sim_cards" not in omitted.model_fields_set
    assert cleared.push_token is None
    assert cleared.sim_cards is None
    assert {"push_token", "sim_cards"} <= cleared.model_fields_set
    assert supplied.push_token == "new"
    assert supplied.sim_cards == []
    assert DeviceUpdateResponse().model_dump() == {"ok": True}


@pytest.mark.parametrize("device_id", ["   ", "x" * 65])
def test_device_update_rejects_invalid_id(device_id):
    with pytest.raises(ValidationError):
        DeviceUpdateRequest.model_validate({"id": device_id})


@pytest.mark.parametrize("field", ["slotIndex", "simNumber"])
def test_device_update_rejects_duplicate_sim_identity(field):
    second = {"slotIndex": 1, "simNumber": 2}
    second[field] = 0 if field == "slotIndex" else 1
    with pytest.raises(ValidationError, match=field):
        DeviceUpdateRequest.model_validate(
            {
                "id": "dev_1",
                "simCards": [
                    {"slotIndex": 0, "simNumber": 1},
                    second,
                ],
            }
        )


def test_device_update_ignores_unknown_fields_and_uses_android_aliases():
    request = DeviceUpdateRequest.model_validate(
        {
            "id": "dev_1",
            "pushToken": "push",
            "simCards": [{"slotIndex": 0, "simNumber": 1, "future": "ignored"}],
            "future": "ignored",
        }
    )

    dumped = request.model_dump(by_alias=True)
    assert dumped["pushToken"] == "push"
    assert dumped["simCards"][0]["slotIndex"] == 0
    assert "future" not in dumped
    assert "future" not in dumped["simCards"][0]
