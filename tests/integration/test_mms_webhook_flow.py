from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.database import Database
from app.security import hash_password
from app.services.mms_webhook_service import MmsWebhookService
from app.services.object_storage import StoredObject


class FakeStorage:
    def upload_bytes(self, *, key, body, content_type):
        return StoredObject("bucket", key, f"https://cdn.example.test/{key}", "etag")


def test_mms_downloaded_is_visible_in_agent_messages(clean_database):
    marker = clean_database.track_push_token("pytest-mms-flow-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    recipient = "+8613900000000"
    account_id = "acct_" + uuid4().hex
    username = "agent_" + uuid4().hex
    password = "correct-password"

    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret"),
        mms_webhook_service=MmsWebhookService(Database(clean_database.dsn), FakeStorage()),
    )

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            registration_response = client.post(
                "/mobile/v1/device",
                headers={"Authorization": "Bearer registration-secret"},
                json={
                    "name": "mms-flow-phone",
                    "pushToken": marker,
                    "simCards": [
                        {"slotIndex": 0, "simNumber": 1, "phoneNumber": recipient}
                    ],
                },
            )
            assert registration_response.status_code == 201
            registration = registration_response.json()
            clean_database.track(registration["id"])

            with psycopg.connect(clean_database.dsn) as connection:
                sim_id = connection.execute(
                    "SELECT id FROM sim_cards WHERE device_id = %s AND sim_number = 1",
                    (registration["id"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO accounts(id, username, password_hash, areas, status)
                    VALUES(%s, %s, %s, NULL, 'ACTIVE')
                    """,
                    (account_id, username, hash_password(password)),
                )
                connection.execute(
                    "INSERT INTO account_sim_cards(account_id, sim_card_id) VALUES(%s, %s)",
                    (account_id, sim_id),
                )
                connection.commit()

            webhook = client.post(
                "/api/v1/webhooks/android-sms-gateway/mms",
                json={
                    "deviceId": registration["id"],
                    "event": "mms:downloaded",
                    "id": "wh_" + uuid4().hex,
                    "webhookId": "wh_mms",
                    "payload": {
                        "messageId": "mms_" + uuid4().hex,
                        "sender": sender,
                        "recipient": recipient,
                        "simNumber": 1,
                        "body": "image hello",
                        "subject": "Photo",
                        "attachments": [
                            {
                                "partId": 17,
                                "contentType": "image/jpeg",
                                "name": "photo.jpg",
                                "size": 3,
                                "data": "AQID",
                            }
                        ],
                        "receivedAt": datetime.now(timezone.utc).isoformat(),
                    },
                },
            )
            assert webhook.status_code == 200
            clean_database.track_message_key("mms:" + webhook.json()["messageId"])

            with psycopg.connect(clean_database.dsn) as connection:
                conversation_id = connection.execute(
                    "SELECT conversation_id FROM messages WHERE idempotency_key = %s",
                    ("mms:" + webhook.json()["messageId"],),
                ).fetchone()[0]

            login = client.post(
                "/agent/v1/auth/login",
                json={"username": username, "password": password},
            )
            assert login.status_code == 200

            messages = client.get(
                f"/agent/v1/conversations/{conversation_id}/messages",
                headers={"Authorization": f"Bearer {login.json()['token']}"},
            )

        assert messages.status_code == 200
        message = messages.json()[0]
        assert message["messageType"] == "MMS"
        assert message["textContent"] == "image hello"
        assert message["attachments"][0]["contentType"] == "image/jpeg"
        assert message["attachments"][0]["url"].startswith("https://cdn.example.test/")
    finally:
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute("DELETE FROM accounts WHERE id = %s", (account_id,))
            connection.commit()
