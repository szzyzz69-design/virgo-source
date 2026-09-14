from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_created_message_is_pulled_once_with_android_contract(clean_database):
    marker = clean_database.track_push_token("pytest-pull-api-" + uuid4().hex)
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    key = clean_database.track_message_key("pytest-pull-api-" + uuid4().hex)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "pull-api-phone",
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
                "simCards": [{"slotIndex": 0, "simNumber": 1}],
            },
        )
        assert update.status_code == 200

        created = client.post(
            "/business/v1/messages",
            headers={
                "Authorization": "Bearer business-secret",
                "Idempotency-Key": key,
            },
            json={
                "phoneNumbers": [phone],
                "text": "pull integration message",
                "priority": 3,
            },
        )
        assert created.status_code == 201

        headers = {"Authorization": f"Bearer {credentials['token']}"}
        first = client.get("/mobile/v1/message?order=fifo", headers=headers)
        second = client.get("/mobile/v1/message?order=fifo", headers=headers)

    assert first.status_code == 200
    assert first.json() == [
        {
            "id": created.json()["id"],
            "textMessage": {"text": "pull integration message"},
            "dataMessage": None,
            "phoneNumbers": [phone],
            "simNumber": 1,
            "withDeliveryReport": True,
            "isEncrypted": False,
            "validUntil": None,
            "scheduleAt": None,
            "priority": 3,
            "createdAt": created.json()["createdAt"],
        }
    ]
    assert second.status_code == 200
    assert second.json() == []

    with psycopg.connect(clean_database.dsn) as connection:
        message = connection.execute(
            "SELECT state, pulled_at FROM messages WHERE id=%s",
            (created.json()["id"],),
        ).fetchone()
        history = connection.execute(
            """
            SELECT source, reason FROM message_state_history
            WHERE message_id=%s AND state='Processed'
            """,
            (created.json()["id"],),
        ).fetchone()
        heartbeat = connection.execute(
            "SELECT status, last_seen_at FROM devices WHERE id=%s",
            (credentials["id"],),
        ).fetchone()
    assert message[0] == "Processed" and isinstance(message[1], int)
    assert history == ("SERVER", "Pulled by device")
    assert heartbeat[0] == "online" and isinstance(heartbeat[1], int)
