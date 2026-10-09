"""A real isolated DB validates GIF upload, account access and retry idempotency."""
import base64
from uuid import uuid4

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mms_inbox import create_mms_inbox_router
from app.database import Database
from app.errors import install_error_handling
from app.services.agent_auth_service import AuthenticatedAgent
from app.services.agent_conversation_service import AgentConversationService
from app.services.device_auth_service import AuthenticatedDevice
from app.services.mms_webhook_service import MmsWebhookService
from tests.integration.test_mms_webhook_service import FakeStorage, bind_agent_to_sim, insert_device_with_sim, mms_downloaded
from tests.test_mms_gif_compatibility import GIF


def test_downloaded_gif_is_visible_to_its_original_account_and_retries_do_not_duplicate(clean_database):
    sender = clean_database.track_phone("+1" + str(uuid4().int)[:10])
    device_id, sim_id, recipient = insert_device_with_sim(clean_database)
    account_id = bind_agent_to_sim(clean_database, sim_id)
    storage = FakeStorage()
    request = mms_downloaded(device_id, sender, recipient, body=None, attachments=[{
        "partId": 7, "contentType": "image/gif", "data": base64.b64encode(GIF).decode(),
    }])
    clean_database.track_message_key("mms:" + request.payload.message_id)

    class Auth:
        def authenticate(self, token):
            assert token == "isolated-device-token"
            return AuthenticatedDevice(device_id, True, "online")

    app = FastAPI()
    install_error_handling(app)
    app.include_router(create_mms_inbox_router(Auth(), MmsWebhookService(Database(clean_database.dsn), storage)))
    try:
        with TestClient(app) as client:
            body = request.payload.model_dump(mode="json", by_alias=True)
            headers = {"Authorization": "Bearer isolated-device-token"}
            first = client.post("/mobile/v1/inbox/mms", json=body, headers=headers)
            assert first.status_code == 201, first.text
            repeated = client.post("/mobile/v1/inbox/mms", json=body, headers=headers)
            assert repeated.status_code == 200 and repeated.json()["created"] is False
            assert repeated.json()["id"] == first.json()["id"]
        assert len(storage.uploads) == 1 and storage.uploads[0][1:] == (GIF, "image/gif")
        assert storage.uploads[0][0].endswith(".gif")
        agent = AuthenticatedAgent(account_id, "original-gif-account", "south")
        messages = AgentConversationService(Database(clean_database.dsn), None).list_messages(first.json()["conversationId"], agent)
        assert len(messages) == 1
        assert messages[0].attachments[0].content_type == "image/gif"
        assert messages[0].attachments[0].size == len(GIF)
        assert messages[0].attachments[0].url.endswith(".gif")
        with psycopg.connect(clean_database.dsn) as connection:
            assert connection.execute("SELECT unread_count,last_message_preview FROM conversations WHERE id=%s", (first.json()["conversationId"],)).fetchone() == (1, "[MMS image]")
            assert connection.execute("SELECT count(*) FROM messages WHERE device_id=%s", (device_id,)).fetchone()[0] == 1
    finally:
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute("DELETE FROM accounts WHERE id=%s", (account_id,))
