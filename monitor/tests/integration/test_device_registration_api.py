import hashlib
import importlib
from pathlib import Path
import sys
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from tests.conftest import TEST_DATABASE_URL


CONFIG_CONTENT = (
    'private_registration_token = "registration-secret"\n'
    'business_api_token = "business-secret"\n'
    'device_online_window_seconds = 300\n'
)


def test_clean_database_deletes_tracked_push_token_and_preserves_untracked(request):
    suffix = uuid4().hex
    tracked_id = f"dev_push_tracked_{suffix}"
    sentinel_id = f"dev_push_sentinel_{suffix}"
    tracked_push_token = f"pytest-tracked-{suffix}"
    sentinel_push_token = f"pytest-sentinel-{suffix}"

    def verify_cleanup():
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            try:
                tracked_count = connection.execute(
                    "SELECT count(*) FROM devices WHERE id = %s", (tracked_id,)
                ).fetchone()[0]
                sentinel_count = connection.execute(
                    "SELECT count(*) FROM devices WHERE id = %s", (sentinel_id,)
                ).fetchone()[0]
                assert tracked_count == 0
                assert sentinel_count == 1
            finally:
                connection.execute(
                    "DELETE FROM devices WHERE id = ANY(%s::varchar[])",
                    ([tracked_id, sentinel_id],),
                )

    request.addfinalizer(verify_cleanup)
    clean_database = request.getfixturevalue("clean_database")
    with psycopg.connect(clean_database.dsn) as connection:
        for values in (
            (
                tracked_id,
                "tracked-by-push-token",
                tracked_push_token,
                f"hash-{tracked_id}",
                f"login-{tracked_id}",
            ),
            (
                sentinel_id,
                "untracked-sentinel",
                sentinel_push_token,
                f"hash-{sentinel_id}",
                f"login-{sentinel_id}",
            ),
        ):
            connection.execute(
                """
                INSERT INTO devices (id, name, push_token, token_hash, login)
                VALUES (%s, %s, %s, %s, %s)
                """,
                values,
            )

    clean_database.track_push_token(tracked_push_token)


def test_production_app_registers_device_in_postgres(clean_database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", clean_database.dsn)
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: CONFIG_CONTENT)
    push_token_marker = clean_database.track_push_token("pytest-" + uuid4().hex)
    previous_main = sys.modules.pop("main", None)
    try:
        main = importlib.import_module("main")
        with TestClient(main.app, raise_server_exceptions=False) as client:
            response = client.post(
                "/mobile/v1/device",
                headers={"Authorization": "Bearer registration-secret"},
                json={
                    "name": "integration-phone",
                    "pushToken": push_token_marker,
                    "simCards": [
                        {
                            "slotIndex": 0,
                            "simNumber": 1,
                            "carrierName": "integration-carrier",
                        }
                    ],
                },
            )

        assert response.status_code == 201
        body = response.json()
        device_id = clean_database.track(body["id"])
        assert set(body) == {"id", "token", "login", "password"}

        with psycopg.connect(clean_database.dsn) as connection:
            device_count = connection.execute(
                "SELECT count(*) FROM devices WHERE id = %s", (device_id,)
            ).fetchone()[0]
            sim_count = connection.execute(
                "SELECT count(*) FROM sim_cards WHERE device_id = %s", (device_id,)
            ).fetchone()[0]
            token_hash = connection.execute(
                "SELECT token_hash FROM devices WHERE id = %s", (device_id,)
            ).fetchone()[0]

        assert device_count == 1
        assert sim_count == 1
        assert token_hash != body["token"]
        assert token_hash == hashlib.sha256(body["token"].encode()).hexdigest()
    finally:
        sys.modules.pop("main", None)
        if previous_main is not None:
            sys.modules["main"] = previous_main

    if previous_main is None:
        assert "main" not in sys.modules
    else:
        assert sys.modules["main"] is previous_main


def test_production_app_registers_then_updates_device_and_sims(
    clean_database,
    monkeypatch,
):
    monkeypatch.setenv("DATABASE_URL", clean_database.dsn)
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: CONFIG_CONTENT)
    marker = clean_database.track_push_token("pytest-update-" + uuid4().hex)
    previous_main = sys.modules.pop("main", None)
    try:
        main = importlib.import_module("main")
        with TestClient(main.app, raise_server_exceptions=False) as client:
            registration = client.post(
                "/mobile/v1/device",
                headers={"Authorization": "Bearer registration-secret"},
                json={
                    "name": "integration-update-phone",
                    "pushToken": marker,
                    "simCards": [{"slotIndex": 0, "simNumber": 1}],
                },
            )
            assert registration.status_code == 201
            credentials = registration.json()
            clean_database.track(credentials["id"])

            update = client.patch(
                "/mobile/v1/device",
                headers={"Authorization": f"Bearer {credentials['token']}"},
                json={
                    "id": credentials["id"],
                    "pushToken": marker,
                    "simCards": [
                        {
                            "slotIndex": 0,
                            "simNumber": 1,
                            "carrierName": "updated-carrier",
                        },
                        {"slotIndex": 1, "simNumber": 2},
                    ],
                },
            )

        assert update.status_code == 200
        assert update.json() == {"ok": True}

        with psycopg.connect(clean_database.dsn) as connection:
            device = connection.execute(
                "SELECT status, push_token, last_seen_at FROM devices WHERE id = %s",
                (credentials["id"],),
            ).fetchone()
            sims = connection.execute(
                """
                SELECT slot_index, sim_number, carrier_name, status
                FROM sim_cards WHERE device_id = %s ORDER BY slot_index
                """,
                (credentials["id"],),
            ).fetchall()

        assert device[0] == "online"
        assert device[1] == marker
        assert isinstance(device[2], int)
        assert sims == [
            (0, 1, "updated-carrier", "active"),
            (1, 2, None, "active"),
        ]
    finally:
        sys.modules.pop("main", None)
        if previous_main is not None:
            sys.modules["main"] = previous_main
