import hashlib
import secrets
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from app.database import Database
from app.schemas.device import DeviceRegisterRequest, SimCardRequest
from app.services.device_service import DeviceService, GeneratedIdentity
from tests.conftest import TEST_DATABASE_URL


def test_clean_database_preserves_untracked_device_during_setup_and_cleanup(request):
    suffix = secrets.token_hex(8)
    device_id = f"dev_untracked_{suffix}"
    dsn = TEST_DATABASE_URL
    with psycopg.connect(dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (id, name, token_hash, login)
            VALUES (%s, 'untracked', %s, %s)
            """,
            (device_id, f"hash-{suffix}", f"login-{suffix}"),
        )

    def verify_survived_cleanup():
        with psycopg.connect(dsn) as connection:
            try:
                count = connection.execute(
                    "SELECT count(*) FROM devices WHERE id = %s", (device_id,)
                ).fetchone()[0]
                assert count == 1
            finally:
                connection.execute("DELETE FROM devices WHERE id = %s", (device_id,))

    request.addfinalizer(verify_survived_cleanup)
    request.getfixturevalue("clean_database")

    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM devices WHERE id = %s", (device_id,)
        ).fetchone()[0] == 1


def valid_request() -> DeviceRegisterRequest:
    return DeviceRegisterRequest.model_validate(
        {
            "name": "Samsung/SM-G9910",
            "pushToken": "push-value",
            "simCards": [
                {
                    "slotIndex": 0,
                    "simNumber": 1,
                    "phoneNumber": "***1234",
                    "carrierName": "carrier",
                    "iccid": "raw-iccid",
                }
            ],
        }
    )


def test_register_persists_device_and_sim_without_plaintext(clean_database):
    before = time.time_ns() // 1_000_000
    response = DeviceService(Database(clean_database.dsn)).register(valid_request())
    clean_database.track(response.id)
    after = time.time_ns() // 1_000_000

    with psycopg.connect(clean_database.dsn, row_factory=dict_row) as connection:
        device = connection.execute(
            """
            SELECT name, push_token, token_hash, login, password_hash, status,
                   enabled, last_seen_at, registered, created_at, updated_at
            FROM devices WHERE id = %s
            """,
            (response.id,),
        ).fetchone()
        sim = connection.execute(
            """
            SELECT slot_index, sim_number, phone_number, carrier_name, iccid_hash,
                   status, enabled, sim_type, created_at, updated_at
            FROM sim_cards WHERE device_id = %s
            """,
            (response.id,),
        ).fetchone()

    assert device is not None
    assert device["name"] == "Samsung/SM-G9910"
    assert device["push_token"] == "push-value"
    assert device["token_hash"] == hashlib.sha256(response.token.encode()).hexdigest()
    assert device["login"] == response.login
    assert response.password is not None
    assert device["password_hash"].startswith("pbkdf2_sha256$")
    assert response.password not in device["password_hash"]
    assert device["status"] == "online"
    assert device["enabled"] is True
    for field in ("last_seen_at", "registered", "created_at", "updated_at"):
        assert isinstance(device[field], int)
        assert before <= device[field] <= after

    assert sim is not None
    assert sim["slot_index"] == 0
    assert sim["sim_number"] == 1
    assert sim["phone_number"] == "***1234"
    assert sim["carrier_name"] == "carrier"
    assert sim["iccid_hash"] == hashlib.sha256(b"raw-iccid").hexdigest()
    assert sim["status"] == "active"
    assert sim["enabled"] is True
    assert sim["sim_type"] == "PHYSICAL"
    for field in ("created_at", "updated_at"):
        assert isinstance(sim[field], int)
        assert before <= sim[field] <= after

    stored_values = " ".join(str(value) for value in (*device.values(), *sim.values()))
    assert response.token not in stored_values
    assert response.password not in stored_values
    assert "raw-iccid" not in stored_values


def test_register_rolls_back_and_does_not_retry_nonidentity_unique_violation(
    clean_database, monkeypatch
):
    service = DeviceService(Database(clean_database.dsn))
    suffix = secrets.token_hex(8)
    identity = GeneratedIdentity(
        f"dev_duplicate_sim_{suffix}",
        f"token-{suffix}",
        f"device-login-{suffix}",
        "password",
    )
    clean_database.track(identity.device_id)
    generated = 0

    def generate_identity():
        nonlocal generated
        generated += 1
        return identity

    monkeypatch.setattr(service, "_generate_identity", generate_identity)
    request = DeviceRegisterRequest.model_construct(
        name="phone",
        push_token=None,
        sim_cards=[
            SimCardRequest(slotIndex=0, simNumber=1),
            SimCardRequest(slotIndex=0, simNumber=2),
        ],
    )

    with pytest.raises(psycopg.errors.UniqueViolation) as captured:
        service.register(request)

    assert captured.value.diag.constraint_name == "uq_sim_device_slot"
    assert generated == 1
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM devices WHERE id = %s", (identity.device_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM sim_cards WHERE device_id = %s", (identity.device_id,)
        ).fetchone()[0] == 0


def test_register_retries_identity_constraint_and_uses_second_identity(
    clean_database, monkeypatch
):
    suffix = secrets.token_hex(8)
    conflict = GeneratedIdentity(
        f"dev_conflict_{suffix}",
        f"conflict-token-{suffix}",
        f"device-conflict-{suffix}",
        "pw",
    )
    expected = GeneratedIdentity(
        f"dev_created_{suffix}",
        f"created-token-{suffix}",
        f"device-created-{suffix}",
        "new-pw",
    )
    clean_database.track(conflict.device_id)
    clean_database.track(expected.device_id)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (id, name, token_hash, login, password_hash)
            VALUES (%s, 'existing', %s, %s, 'existing-password-hash')
            """,
            (
                conflict.device_id,
                hashlib.sha256(f"existing-token-{suffix}".encode()).hexdigest(),
                f"existing-login-{suffix}",
            ),
        )

    identities = iter((conflict, expected))
    service = DeviceService(Database(clean_database.dsn))
    monkeypatch.setattr(service, "_generate_identity", identities.__next__)

    response = service.register(
        DeviceRegisterRequest.model_validate({"name": "new-phone", "simCards": []})
    )

    assert response.id == expected.device_id
    assert response.token == expected.token
    assert response.login == expected.login
    assert response.password == expected.password
    with psycopg.connect(clean_database.dsn) as connection:
        created = connection.execute(
            "SELECT name FROM devices WHERE id = %s", (expected.device_id,)
        ).fetchone()
        assert created == ("new-phone",)
        assert connection.execute(
            "SELECT count(*) FROM devices WHERE id = %s", (conflict.device_id,)
        ).fetchone()[0] == 1


def test_register_retries_sim_primary_key_collision_with_a_fresh_transaction(
    clean_database, monkeypatch
):
    suffix = secrets.token_hex(8)
    owner_id = clean_database.track(f"dev_sim_owner_{suffix}")
    first = GeneratedIdentity(
        clean_database.track(f"dev_sim_first_{suffix}"),
        f"first-token-{suffix}",
        f"first-login-{suffix}",
        "first-password",
    )
    expected = GeneratedIdentity(
        clean_database.track(f"dev_sim_expected_{suffix}"),
        f"expected-token-{suffix}",
        f"expected-login-{suffix}",
        "expected-password",
    )
    conflicting_sim_id = f"sim_conflict_{suffix}"
    expected_sim_id = f"sim_expected_{suffix}"
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (id, name, token_hash, login)
            VALUES (%s, 'sim-owner', %s, %s)
            """,
            (owner_id, f"owner-hash-{suffix}", f"owner-login-{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO sim_cards (id, device_id, slot_index, sim_number)
            VALUES (%s, %s, 0, 1)
            """,
            (conflicting_sim_id, owner_id),
        )

    identities = iter((first, expected))
    sim_ids = iter((conflicting_sim_id, expected_sim_id))
    service = DeviceService(Database(clean_database.dsn))
    monkeypatch.setattr(service, "_generate_identity", identities.__next__)
    monkeypatch.setattr(service, "_generate_sim_id", sim_ids.__next__)

    response = service.register(valid_request())

    assert response.id == expected.device_id
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM devices WHERE id = %s", (first.device_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM devices WHERE id = %s", (expected.device_id,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT device_id FROM sim_cards WHERE id = %s", (expected_sim_id,)
        ).fetchone() == (expected.device_id,)
        assert connection.execute(
            "SELECT device_id FROM sim_cards WHERE id = %s", (conflicting_sim_id,)
        ).fetchone() == (owner_id,)


def test_register_stops_after_three_identity_collisions(clean_database, monkeypatch):
    suffix = secrets.token_hex(8)
    collision = GeneratedIdentity(
        f"dev_conflict_{suffix}",
        f"token-{suffix}",
        f"device-login-{suffix}",
        "password",
    )
    clean_database.track(collision.device_id)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (id, name, token_hash, login)
            VALUES (%s, 'existing', %s, %s)
            """,
            (
                collision.device_id,
                hashlib.sha256(f"existing-token-{suffix}".encode()).hexdigest(),
                f"existing-login-{suffix}",
            ),
        )

    generated = 0

    def generate_identity():
        nonlocal generated
        generated += 1
        return collision

    service = DeviceService(Database(clean_database.dsn))
    monkeypatch.setattr(service, "_generate_identity", generate_identity)

    with pytest.raises(psycopg.errors.UniqueViolation) as captured:
        service.register(
            DeviceRegisterRequest.model_validate({"name": "new-phone", "simCards": []})
        )

    assert captured.value.diag.constraint_name == "devices_pkey"
    assert generated == 3
