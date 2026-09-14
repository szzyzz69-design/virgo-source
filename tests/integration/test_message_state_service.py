import pytest

from app.schemas.message_status import MessageStatusBatch
from app.services.message_state_service import (
    MessageStateConflict,
    MessageStateService,
)


def test_status_service_module_exposes_atomic_update_contract():
    assert hasattr(MessageStateService, "update")


def test_status_conflict_carries_safe_batch_location():
    error = MessageStateConflict(2, "msg_2", "state regressed")
    assert error.index == 2
    assert error.message_id == "msg_2"
    assert str(error) == "state regressed"


def test_status_batch_fixture_is_valid_for_service_tests():
    batch = MessageStatusBatch.model_validate(
        [
            {
                "id": "msg_1",
                "state": "Sent",
                "recipients": [
                    {"phoneNumber": "+8613800138000", "state": "Sent", "error": None}
                ],
                "states": {"Sent": "2026-06-22T08:00:00Z"},
            }
        ]
    )
    assert batch.root[0].id == "msg_1"
