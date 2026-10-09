from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.supervisor import create_supervisor_router
from app.config import Settings
from app.errors import install_error_handling
from app.services.object_storage import ObjectStorageUnavailable
from app.services.supervisor_service import SupervisorNotFound, SupervisorScopeError


class Stream:
    def __init__(self):
        self.closed = False

    def iter_chunks(self, chunk_size):
        yield b"\xff\xd8test-photo\xff\xd9"

    def close(self):
        self.closed = True


class Service:
    def __init__(self):
        self.calls = []
        self.stream = Stream()

    def get_attachment(self, conversation_id, account_id, attachment_id):
        self.calls.append((conversation_id, account_id, attachment_id))
        if account_id != "original-account":
            raise SupervisorScopeError("Conversation is not bound to the selected active account")
        if attachment_id == "missing":
            raise SupervisorNotFound
        if attachment_id == "unavailable":
            raise ObjectStorageUnavailable
        return {"contentType": "image/svg+xml" if attachment_id == "svg" else "image/jpeg",
                "name": 'photo".jpg', "size": 14, "stream": self.stream}


def client(service, logged_in=True):
    app = FastAPI()
    install_error_handling(app)
    app.include_router(create_supervisor_router(Settings(
        "postgresql://unused", "unused-registration", "unused-business",
        supervisor_username="monitor", supervisor_password="test-password",
        supervisor_session_secret="s" * 32,
    ), service))
    result = TestClient(app)
    if logged_in:
        assert result.post('/supervisor/api/login', json={
            "username": "monitor", "password": "test-password",
        }).status_code == 200
    return result


def url(attachment="image", account="original-account"):
    return f"/supervisor/api/conversations/conversation/attachments/{attachment}?account_id={account}"


def test_photo_requires_original_monitor_session():
    service = Service()
    assert client(service, logged_in=False).get(url()).status_code == 401
    assert service.calls == []


def test_photo_enforces_account_scope_and_attachment_membership():
    c = client(Service())
    assert c.get(url(account="other-account")).status_code == 403
    assert c.get(url("missing")).status_code == 404


def test_photo_stream_preserves_bytes_and_closes_storage_body():
    service = Service()
    response = client(service).get(url())
    assert response.status_code == 200
    assert response.content == b"\xff\xd8test-photo\xff\xd9"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "photo%22.jpg" in response.headers["content-disposition"]
    assert service.stream.closed


def test_non_raster_file_is_downloaded_instead_of_rendered_inline():
    response = client(Service()).get(url("svg"))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")


def test_storage_failure_keeps_monitor_available():
    assert client(Service()).get(url("unavailable")).status_code == 503
