from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.message_status import (
    MessageStatusBatch,
    Status,
    aggregate_recipient_state,
    can_transition,
    to_utc_millis,
)


def item(**overrides):
    value = {
        "id": "msg_1",
        "state": "Sent",
        "recipients": [
            {"phoneNumber": "+86 13800138000", "state": "Sent", "error": None}
        ],
        "states": {"Processed": "2026-06-22T08:00:00Z", "Sent": "2026-06-22T08:01:00Z"},
    }
    value.update(overrides)
    return value


def test_batch_normalizes_phone_and_accepts_android_contract():
    batch = MessageStatusBatch.model_validate([item()])
    assert batch.root[0].recipients[0].phone_number == "+8613800138000"
    assert batch.root[0].state is Status.SENT


@pytest.mark.parametrize("body", [[], [item()] * 101])
def test_batch_enforces_bounds(body):
    with pytest.raises(ValidationError):
        MessageStatusBatch.model_validate(body)


def test_batch_rejects_duplicate_message_ids():
    with pytest.raises(ValidationError):
        MessageStatusBatch.model_validate([item(), item()])


def test_item_rejects_duplicate_recipients():
    recipient = {"phoneNumber": "+8613800138000", "state": "Sent", "error": None}
    with pytest.raises(ValidationError):
        MessageStatusBatch.model_validate([item(recipients=[recipient, recipient])])


def test_non_failed_recipient_cannot_include_error():
    with pytest.raises(ValidationError):
        MessageStatusBatch.model_validate([item(recipients=[{"phoneNumber": "+8613800138000", "state": "Sent", "error": "oops"}])])


def test_states_must_include_current_and_be_chronological():
    with pytest.raises(ValidationError):
        MessageStatusBatch.model_validate([item(states={"Processed": "2026-06-22T08:00:00Z"})])
    with pytest.raises(ValidationError):
        MessageStatusBatch.model_validate([item(states={"Processed": "2026-06-22T08:02:00Z", "Sent": "2026-06-22T08:01:00Z"})])


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([Status.PENDING, Status.SENT], Status.PENDING),
        ([Status.PROCESSED, Status.SENT], Status.PROCESSED),
        ([Status.DELIVERED, Status.DELIVERED], Status.DELIVERED),
        ([Status.FAILED, Status.FAILED], Status.FAILED),
        ([Status.SENT, Status.FAILED], Status.SENT),
    ],
)
def test_recipient_aggregation(states, expected):
    assert aggregate_recipient_state(states) is expected


@pytest.mark.parametrize(
    ("old", "new", "allowed"),
    [
        (Status.PENDING, Status.DELIVERED, True),
        (Status.PROCESSED, Status.FAILED, True),
        (Status.SENT, Status.PROCESSED, False),
        (Status.DELIVERED, Status.SENT, False),
        (Status.FAILED, Status.FAILED, True),
    ],
)
def test_state_transitions(old, new, allowed):
    assert can_transition(old, new) is allowed


def test_time_conversion_requires_aware_datetime():
    value = datetime(2026, 6, 22, 8, tzinfo=timezone.utc)
    assert to_utc_millis(value) == 1782115200000
