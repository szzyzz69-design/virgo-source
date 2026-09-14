import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.events import create_events_router
from app.errors import install_error_handling
from app.services.device_auth_service import (
    AuthenticatedDevice,
    DeviceDisabled,
    InvalidDeviceToken,
)


class RecordingAuthService:
    def __init__(self, error=None):
        self.error = error
        self.tokens = []

    def authenticate(self, token):
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return AuthenticatedDevice("dev_1", True, "online")


class FiniteRegistry:
    def __init__(self):
        self.registered = []

    def register(self, device_id):
        connection = object()
        self.registered.append((device_id, connection))
        return connection

    def stream(self, connection):
        assert connection is self.registered[-1][1]
        yield ": ping test\n\n"


def make_client(auth_service=None, registry=None):
    auth_service = auth_service or RecordingAuthService()
    registry = registry or FiniteRegistry()
    app = FastAPI()
    install_error_handling(app)
    app.include_router(create_events_router(auth_service, registry))
    return TestClient(app, raise_server_exceptions=False), auth_service, registry


def assert_error(response, status, code, message):
    assert response.status_code == status
    assert response.json() == {
        "code": code,
        "message": message,
        "requestId": response.headers["X-Request-ID"],
        "details": None,
    }


def test_events_returns_sse_headers_for_authenticated_device():
    client, auth_service, registry = make_client()

    response = client.get(
        "/mobile/v1/events",
        headers={"Authorization": "Bearer device-token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["connection"] == "keep-alive"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text == ": ping test\n\n"
    assert auth_service.tokens == ["device-token"]
    assert registry.registered[0][0] == "dev_1"


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Basic device-token", "Bearer ", "Bearer device-token "],
)
def test_events_rejects_invalid_authorization_before_registration(authorization):
    client, auth_service, registry = make_client()
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get("/mobile/v1/events", headers=headers)

    assert_error(response, 401, "UNAUTHORIZED", "Invalid device token")
    assert auth_service.tokens == []
    assert registry.registered == []


def test_events_maps_unknown_token_to_401_before_registration():
    registry = FiniteRegistry()
    client, _, _ = make_client(
        RecordingAuthService(InvalidDeviceToken()),
        registry,
    )

    response = client.get(
        "/mobile/v1/events",
        headers={"Authorization": "Bearer unknown"},
    )

    assert_error(response, 401, "UNAUTHORIZED", "Invalid device token")
    assert registry.registered == []


def test_events_maps_disabled_device_to_403_before_registration():
    registry = FiniteRegistry()
    client, _, _ = make_client(RecordingAuthService(DeviceDisabled()), registry)

    response = client.get(
        "/mobile/v1/events",
        headers={"Authorization": "Bearer disabled-token"},
    )

    assert_error(response, 403, "FORBIDDEN", "Device is disabled")
    assert registry.registered == []
