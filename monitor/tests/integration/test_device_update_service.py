import hashlib
import secrets
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from app.database import Database
from app.schemas.device import DeviceUpdateRequest
from app.services.device_auth_service import DeviceDisabled, InvalidDeviceToken
from app.services.device_service import (
    DeviceOwnershipMismatch,
    DeviceService,
    DeviceStateConflict,
)


def seed_device(context, *, push_token="old-push", enabled=True):
    suffix = secrets.token_hex(8)
    device_id = context.track(f"dev_update_{suffix}")
    with psycopg.connect(context.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (
                id, name, push_token, token_hash, login, enabled,
                status, last_seen_at, updated_at
            ) VALUES (%s, 'update-phone', %s, %s, %s, %s, 'offline', 1000, 1000)
            """,
            (
                device_id,
                push_token,
                hashlib.sha256(f"token-{suffix}".encode()).hexdigest(),
                f"login-{suffix}",
                enabled,
            ),
        )
    return device_id


def seed_two_sims(context, device_id):
    suffix = secrets.token_hex(8)
    with psycopg.connect(context.dsn) as connection:
        connection.execute(
            """
            INSERT INTO sim_cards (
                id, device_id, slot_index, sim_number, phone_number,
                carrier_name, enabled, status, last_used_at, created_at, updated_at
            ) VALUES
                (%s, %s, 0, 1, 'old-0', 'carrier-0', FALSE, 'active', 700, 500, 600),
                (%s, %s, 1, 2, 'old-1', 'carrier-1', TRUE, 'active', 800, 501, 601)
            """,
            (f"sim_update_0_{suffix}", device_id, f"sim_update_1_{suffix}", device_id),
        )


def read_device(dsn, device_id):
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        return connection.execute(
            """
            SELECT push_token, status, enabled, last_seen_at, updated_at
            FROM devices WHERE id = %s
            """,
            (device_id,),
        ).fetchone()


def read_sims(dsn, device_id):
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        rows = connection.execute(
            """
            SELECT id, slot_index, sim_number, phone_number, carrier_name,
                   iccid_hash, enabled, status, last_used_at, created_at, updated_at
            FROM sim_cards WHERE device_id = %s ORDER BY slot_index
            """,
            (device_id,),
        ).fetchall()
    return {row["slot_index"]: row for row in rows}


def test_update_preserves_omitted_push_token_and_refreshes_heartbeat(clean_database):
    device_id = seed_device(clean_database)
    before = time.time_ns() // 1_000_000

    response = DeviceService(Database(clean_database.dsn)).update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id}),
    )
    after = time.time_ns() // 1_000_000
    device = read_device(clean_database.dsn, device_id)

    assert response.ok is True
    assert device["push_token"] == "old-push"
    assert device["status"] == "online"
    assert before <= device["last_seen_at"] <= after
    assert device["updated_at"] == device["last_seen_at"]


def test_update_clears_and_replaces_push_token(clean_database):
    device_id = seed_device(clean_database)
    service = DeviceService(Database(clean_database.dsn))

    service.update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id, "pushToken": None}),
    )
    assert read_device(clean_database.dsn, device_id)["push_token"] is None

    service.update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id, "pushToken": "new-push"}),
    )
    assert read_device(clean_database.dsn, device_id)["push_token"] == "new-push"


@pytest.mark.parametrize("body", [{"id": "placeholder"}, {"id": "placeholder", "simCards": None}])
def test_update_omitted_or_null_sim_snapshot_preserves_rows(clean_database, body):
    device_id = seed_device(clean_database)
    seed_two_sims(clean_database, device_id)
    before = read_sims(clean_database.dsn, device_id)
    request_body = {**body, "id": device_id}

    DeviceService(Database(clean_database.dsn)).update(
        device_id,
        DeviceUpdateRequest.model_validate(request_body),
    )

    assert read_sims(clean_database.dsn, device_id) == before


def test_update_empty_sim_snapshot_marks_all_inactive(clean_database):
    device_id = seed_device(clean_database)
    seed_two_sims(clean_database, device_id)

    DeviceService(Database(clean_database.dsn)).update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id, "simCards": []}),
    )

    sims = read_sims(clean_database.dsn, device_id)
    assert {row["status"] for row in sims.values()} == {"inactive"}


def test_update_nonempty_snapshot_upserts_and_preserves_admin_fields(clean_database):
    device_id = seed_device(clean_database)
    seed_two_sims(clean_database, device_id)
    before = read_sims(clean_database.dsn, device_id)

    DeviceService(Database(clean_database.dsn)).update(
        device_id,
        DeviceUpdateRequest.model_validate(
            {
                "id": device_id,
                "simCards": [
                    {
                        "slotIndex": 0,
                        "simNumber": 1,
                        "phoneNumber": "new-0",
                        "carrierName": "new-carrier",
                        "iccid": "new-iccid",
                    },
                    {"slotIndex": 2, "simNumber": 3},
                ],
            }
        ),
    )

    sims = read_sims(clean_database.dsn, device_id)
    assert sims[0]["id"] == before[0]["id"]
    assert sims[0]["created_at"] == before[0]["created_at"]
    assert sims[0]["last_used_at"] == before[0]["last_used_at"]
    assert sims[0]["enabled"] is False
    assert sims[0]["status"] == "active"
    assert sims[0]["phone_number"] == "new-0"
    assert sims[0]["carrier_name"] == "new-carrier"
    assert sims[0]["iccid_hash"] == hashlib.sha256(b"new-iccid").hexdigest()
    assert sims[1]["status"] == "inactive"
    assert sims[2]["status"] == "active"
    assert sims[2]["enabled"] is True


def test_update_null_sim_descriptors_clear_previous_values(clean_database):
    device_id = seed_device(clean_database)
    seed_two_sims(clean_database, device_id)

    DeviceService(Database(clean_database.dsn)).update(
        device_id,
        DeviceUpdateRequest.model_validate(
            {
                "id": device_id,
                "simCards": [
                    {
                        "slotIndex": 0,
                        "simNumber": 1,
                        "phoneNumber": None,
                        "carrierName": None,
                        "iccid": None,
                    }
                ],
            }
        ),
    )

    sim = read_sims(clean_database.dsn, device_id)[0]
    assert sim["phone_number"] is None
    assert sim["carrier_name"] is None
    assert sim["iccid_hash"] is None


def test_update_rejects_mismatched_id_without_partial_writes(clean_database):
    device_id = seed_device(clean_database)

    with pytest.raises(DeviceOwnershipMismatch):
        DeviceService(Database(clean_database.dsn)).update(
            device_id,
            DeviceUpdateRequest.model_validate(
                {"id": "dev_other", "pushToken": "new-push", "simCards": []}
            ),
        )

    device = read_device(clean_database.dsn, device_id)
    assert device["push_token"] == "old-push"
    assert device["status"] == "offline"


def test_update_rechecks_disabled_device_without_partial_writes(clean_database):
    device_id = seed_device(clean_database, enabled=False)

    with pytest.raises(DeviceDisabled):
        DeviceService(Database(clean_database.dsn)).update(
            device_id,
            DeviceUpdateRequest.model_validate({"id": device_id, "pushToken": "new-push"}),
        )

    assert read_device(clean_database.dsn, device_id)["push_token"] == "old-push"


def test_update_rejects_deleted_authenticated_device(clean_database):
    service = DeviceService(Database(clean_database.dsn))

    with pytest.raises(InvalidDeviceToken):
        service.update(
            "dev_missing",
            DeviceUpdateRequest.model_validate({"id": "dev_missing"}),
        )


def test_update_rolls_back_device_when_sim_number_conflicts(clean_database):
    device_id = seed_device(clean_database)
    seed_two_sims(clean_database, device_id)
    before_device = read_device(clean_database.dsn, device_id)
    before_sims = read_sims(clean_database.dsn, device_id)

    with pytest.raises(DeviceStateConflict):
        DeviceService(Database(clean_database.dsn)).update(
            device_id,
            DeviceUpdateRequest.model_validate(
                {
                    "id": device_id,
                    "pushToken": "new-push",
                    "simCards": [{"slotIndex": 2, "simNumber": 1}],
                }
            ),
        )

    assert read_device(clean_database.dsn, device_id) == before_device
    assert read_sims(clean_database.dsn, device_id) == before_sims


def test_update_retries_random_sim_primary_key_collision(clean_database, monkeypatch):
    owner_id = seed_device(clean_database)
    target_id = seed_device(clean_database)
    suffix = secrets.token_hex(8)
    conflicting_sim_id = f"sim_existing_{suffix}"
    expected_sim_id = f"sim_created_{suffix}"
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO sim_cards (id, device_id, slot_index, sim_number)
            VALUES (%s, %s, 0, 1)
            """,
            (conflicting_sim_id, owner_id),
        )

    generated_ids = iter((conflicting_sim_id, expected_sim_id))
    service = DeviceService(Database(clean_database.dsn))
    monkeypatch.setattr(service, "_generate_sim_id", generated_ids.__next__)

    response = service.update(
        target_id,
        DeviceUpdateRequest.model_validate(
            {"id": target_id, "simCards": [{"slotIndex": 0, "simNumber": 1}]}
        ),
    )

    assert response.ok is True
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT device_id FROM sim_cards WHERE id = %s",
            (expected_sim_id,),
        ).fetchone() == (target_id,)
        assert connection.execute(
            "SELECT device_id FROM sim_cards WHERE id = %s",
            (conflicting_sim_id,),
        ).fetchone() == (owner_id,)
