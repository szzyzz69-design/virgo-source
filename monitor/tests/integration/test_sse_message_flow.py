import asyncio
import json
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.services.sse import SseConnectionRegistry


async def read_one_event(registry, connection):
    stream = registry.stream(connection)
    try:
        return await anext(stream)
    finally:
        await stream.aclose()


def test_committed_message_is_published_to_target_device_without_state_change(
    clean_database,
):
    marker = clean_database.track_push_token("pytest-sse-" + uuid4().hex)
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    key = clean_database.track_message_key("pytest-sse-message-" + uuid4().hex)
    registry = SseConnectionRegistry(heartbeat_seconds=0.1)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret"),
        sse_registry=registry,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "sse-message-phone",
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

        connection = registry.register(credentials["id"])
        created = client.post(
            "/business/v1/messages",
            headers={
                "Authorization": "Bearer business-secret",
                "Idempotency-Key": key,
            },
            json={"phoneNumbers": [phone], "text": "wake target device"},
        )

    assert created.status_code == 201
    event = asyncio.run(read_one_event(registry, connection))
    assert "event: MessageEnqueued\n" in event
    assert f"id: {created.json()['id']}\n" in event
    data = event.split("data: ", 1)[1].splitlines()[0]
    assert json.loads(data) == {"messageId": created.json()["id"]}

    with psycopg.connect(clean_database.dsn) as database:
        state = database.execute(
            "SELECT state, pulled_at FROM messages WHERE id = %s",
            (created.json()["id"],),
        ).fetchone()
    assert state == ("Pending", None)
