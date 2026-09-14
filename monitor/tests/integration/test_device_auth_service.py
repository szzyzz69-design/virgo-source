import secrets

import psycopg
import pytest

from app.database import Database
from app.security import hash_sha256
from app.services.device_auth_service import (
    DeviceAuthService,
    DeviceDisabled,
    InvalidDeviceToken,
)


def seed_device(context, *, enabled=True, status="offline"):
    suffix = secrets.token_hex(8)
    device_id = context.track(f"dev_auth_{suffix}")
    token = f"device-token-{suffix}"
    with psycopg.connect(context.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (id, name, token_hash, login, enabled, status)
            VALUES (%s, 'auth-phone', %s, %s, %s, %s)
            """,
            (device_id, hash_sha256(token), f"login-{suffix}", enabled, status),
        )
    return device_id, token


def test_authenticate_returns_device_for_token_hash(clean_database):
    device_id, token = seed_device(clean_database)

    authenticated = DeviceAuthService(Database(clean_database.dsn)).authenticate(token)

    assert authenticated.id == device_id
    assert authenticated.enabled is True
    assert authenticated.status == "offline"


def test_authenticate_rejects_unknown_token(clean_database):
    service = DeviceAuthService(Database(clean_database.dsn))

    with pytest.raises(InvalidDeviceToken):
        service.authenticate("unknown-token")


def test_authenticate_rejects_disabled_device(clean_database):
    _, token = seed_device(clean_database, enabled=False, status="disabled")

    with pytest.raises(DeviceDisabled):
        DeviceAuthService(Database(clean_database.dsn)).authenticate(token)
