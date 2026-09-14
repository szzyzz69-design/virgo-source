from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_business_api_creates_and_replays_message_in_postgres(clean_database):
    marker = clean_database.track_push_token("pytest-message-api-" + uuid4().hex)
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    key = clean_database.track_message_key("pytest-message-api-" + uuid4().hex)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "message-api-phone",
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

        headers = {
            "Authorization": "Bearer business-secret",
            "Idempotency-Key": key,
        }
        first = client.post(
            "/business/v1/messages",
            headers=headers,
            json={"phoneNumbers": [phone], "text": "integration message"},
        )
        replay = client.post(
            "/business/v1/messages",
            headers=headers,
            json={"phoneNumbers": [phone], "text": "integration message"},
        )
        conflict = client.post(
            "/business/v1/messages",
            headers=headers,
            json={"phoneNumbers": [phone], "text": "different message"},
        )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    with psycopg.connect(clean_database.dsn) as connection:
        stored = connection.execute(
            """
            SELECT state, device_id, sim_number, to_phone_number
            FROM messages WHERE id = %s
            """,
            (first.json()["id"],),
        ).fetchone()
        assert stored == ("Pending", credentials["id"], 1, phone)
