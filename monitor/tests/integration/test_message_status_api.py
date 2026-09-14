from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_device_reports_sent_and_delivered_idempotently(clean_database):
    marker = clean_database.track_push_token("pytest-status-" + uuid4().hex)
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    key = clean_database.track_message_key("pytest-status-" + uuid4().hex)
    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        registered = client.post(
            "/mobile/v1/device", headers={"Authorization": "Bearer registration-secret"},
            json={"name": "status-phone", "pushToken": marker, "simCards": [{"slotIndex": 0, "simNumber": 1}]},
        )
        credentials = registered.json(); clean_database.track(credentials["id"])
        headers = {"Authorization": f"Bearer {credentials['token']}"}
        assert client.patch(
            "/mobile/v1/device", headers=headers,
            json={"id": credentials["id"], "pushToken": marker, "simCards": [{"slotIndex": 0, "simNumber": 1}]},
        ).status_code == 200
        created_response = client.post(
            "/business/v1/messages",
            headers={"Authorization": "Bearer business-secret", "Idempotency-Key": key},
            json={"phoneNumbers": [phone], "text": "status integration"},
        )
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        assert client.get("/mobile/v1/message", headers=headers).status_code == 200
        sent = datetime.now(timezone.utc)
        sent_body = [{
            "id": created["id"], "state": "Sent",
            "recipients": [{"phoneNumber": phone, "state": "Sent", "error": None}],
            "states": {"Sent": sent.isoformat()},
        }]
        invalid_batch = sent_body + [{
            "id": "msg_missing", "state": "Sent",
            "recipients": [{"phoneNumber": phone, "state": "Sent", "error": None}],
            "states": {"Sent": sent.isoformat()},
        }]
        rejected = client.patch(
            "/mobile/v1/message", headers=headers, json=invalid_batch
        )
        assert rejected.status_code == 404
        with psycopg.connect(clean_database.dsn) as connection:
            unchanged = connection.execute(
                "SELECT state FROM messages WHERE id=%s", (created["id"],)
            ).fetchone()[0]
            recipient_unchanged = connection.execute(
                "SELECT state FROM message_recipients WHERE message_id=%s",
                (created["id"],),
            ).fetchone()[0]
        assert unchanged == "Processed"
        assert recipient_unchanged == "Pending"
        assert client.patch("/mobile/v1/message", headers=headers, json=sent_body).json() == {"ok": True}
        delivered = sent + timedelta(seconds=1)
        delivered_body = [{
            "id": created["id"], "state": "Delivered",
            "recipients": [{"phoneNumber": phone, "state": "Delivered", "error": None}],
            "states": {"Sent": sent.isoformat(), "Delivered": delivered.isoformat()},
        }]
        first = client.patch("/mobile/v1/message", headers=headers, json=delivered_body)
        replay = client.patch("/mobile/v1/message", headers=headers, json=delivered_body)
    assert first.status_code == replay.status_code == 200
    with psycopg.connect(clean_database.dsn) as connection:
        message = connection.execute(
            "SELECT state, sent_at, delivered_at FROM messages WHERE id=%s", (created["id"],)
        ).fetchone()
        recipient = connection.execute(
            "SELECT state, error FROM message_recipients WHERE message_id=%s", (created["id"],)
        ).fetchone()
        history = connection.execute(
            "SELECT state, count(*) FROM message_state_history WHERE message_id=%s GROUP BY state",
            (created["id"],),
        ).fetchall()
    assert message[0] == "Delivered" and message[1] is not None and message[2] is not None
    assert recipient == ("Delivered", None)
    assert all(count == 1 for _, count in history)
