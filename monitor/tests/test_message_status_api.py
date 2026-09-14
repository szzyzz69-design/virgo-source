from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.services.device_auth_service import AuthenticatedDevice
from app.services.message_state_service import (
    MessageStateConflict,
    MessageStatusNotFound,
)


class Auth:
    def __init__(self):
        self.tokens = []
    def authenticate(self, token):
        self.tokens.append(token)
        return AuthenticatedDevice("dev_1", True, "online")


class Service:
    def __init__(self, error=None):
        self.error = error
        self.calls = []
    def update(self, device_id, requests):
        self.calls.append((device_id, requests))
        if self.error:
            raise self.error


def body():
    return [{
        "id": "msg_1", "state": "Sent",
        "recipients": [{"phoneNumber": "+8613800138000", "state": "Sent", "error": None}],
        "states": {"Sent": "2026-06-22T08:00:00Z"},
    }]


def make_client(service=None):
    auth = Auth(); service = service or Service()
    app = create_app(
        Settings("postgresql://unused", "registration-secret", "business-secret"),
        device_auth_service=auth,
        message_state_service=service,
    )
    return TestClient(app, raise_server_exceptions=False), auth, service


def test_status_update_returns_ok_and_calls_service():
    client, auth, service = make_client()
    response = client.patch("/mobile/v1/message", headers={"Authorization": "Bearer token"}, json=body())
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth.tokens == ["token"]
    assert service.calls[0][0] == "dev_1"


def test_authentication_precedes_malformed_body():
    client, auth, service = make_client()
    response = client.patch(
        "/mobile/v1/message",
        headers={"Content-Type": "application/json"},
        content="{broken",
    )
    assert response.status_code == 401
    assert auth.tokens == [] and service.calls == []


def test_status_validation_returns_400():
    client, _, service = make_client()
    response = client.patch("/mobile/v1/message", headers={"Authorization": "Bearer token"}, json=[])
    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert service.calls == []


def test_not_found_includes_safe_batch_location():
    client, _, _ = make_client(Service(MessageStatusNotFound(2, "msg_2", "missing")))
    response = client.patch("/mobile/v1/message", headers={"Authorization": "Bearer token"}, json=body())
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert response.json()["details"] == {"index": 2, "messageId": "msg_2"}


def test_state_conflict_maps_to_409():
    client, _, _ = make_client(Service(MessageStateConflict(0, "msg_1", "regressed")))
    response = client.patch("/mobile/v1/message", headers={"Authorization": "Bearer token"}, json=body())
    assert response.status_code == 409
    assert response.json()["code"] == "STATE_CONFLICT"
