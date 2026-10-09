from contextlib import contextmanager

import pytest

from app.services.agent_auth_service import AuthenticatedAgent
from app.services.agent_conversation_service import AgentConversationService, ConversationForbidden
from app.services.object_storage import S3ObjectStorage


class Storage:
    def __init__(self):
        self.calls = []

    def download_url(self, **kwargs):
        self.calls.append(kwargs)
        return f"https://private.example.test/image?signature={len(self.calls)}"


class Database:
    def __init__(self, allowed=True):
        self.allowed = allowed

    @contextmanager
    def transaction(self):
        yield self

    def execute(self, query, params):
        self.query = query
        return self

    def fetchone(self):
        if "JOIN account_sim_cards" in self.query:
            return ("conv",) if self.allowed else None
        return ("conv",)

    def fetchall(self):
        if "FROM message_attachments" in self.query:
            return [("msg", "att", 17, "image/jpeg", "photo.jpg", 3, None, "private", "mms/image.jpg")]
        return [("msg", "conv", "INBOUND", "MMS", None, "Received", "+123", "+456",
                 100, 100, None, None, "+456", None)]


def test_authorized_message_query_returns_fresh_private_links():
    storage = Storage()
    service = AgentConversationService(Database(), None, attachment_storage=storage)
    agent = AuthenticatedAgent("agent", "agent", "area")
    first = service.list_messages("conv", agent)[0]
    second = service.list_messages("conv", agent)[0]
    assert first.attachments[0].url != second.attachments[0].url
    assert storage.calls == [{"bucket": "private", "key": "mms/image.jpg"}] * 2


def test_forbidden_conversation_never_generates_a_download_link():
    storage = Storage()
    service = AgentConversationService(Database(allowed=False), None, attachment_storage=storage)
    with pytest.raises(ConversationForbidden):
        service.list_messages("conv", AuthenticatedAgent("agent", "agent", "other-area"))
    assert storage.calls == []


def test_s3_links_use_object_bucket_and_expire_in_fifteen_minutes():
    class Client:
        def generate_presigned_url(self, operation, **kwargs):
            assert operation == "get_object"
            assert kwargs == {"Params": {"Bucket": "original-bucket", "Key": "mms/key"}, "ExpiresIn": 900}
            return "signed-url"

    storage = S3ObjectStorage(client=Client(), bucket="new-bucket")
    assert storage.download_url(bucket="original-bucket", key="mms/key") == "signed-url"
