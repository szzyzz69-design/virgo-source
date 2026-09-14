import json

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.schemas.device import DeviceRegisterResponse, DeviceUpdateResponse
from app.services.device_auth_service import (
    AuthenticatedDevice,
    DeviceDisabled,
    InvalidDeviceToken,
)
from app.services.device_service import DeviceOwnershipMismatch, DeviceStateConflict


class RecordingDeviceService:
    def __init__(self, update_error=None):
        self.updates = []
        self.registrations = []
        self.update_error = update_error

    def register(self, request):
        self.registrations.append(request)
        return DeviceRegisterResponse(
            id="dev_registered",
            token="registered-device-token",
            login="device-registered",
            password="password",
        )

    def update(self, authenticated_device_id, request):
        self.updates.append((authenticated_device_id, request))
        if self.update_error is not None:
            raise self.update_error
        return DeviceUpdateResponse()


class RecordingAuthService:
    def __init__(self, error=None):
        self.tokens = []
        self.error = error

    def authenticate(self, token):
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        if token != "device-token":
            raise InvalidDeviceToken
        return AuthenticatedDevice(id="dev_test", enabled=True, status="offline")


def update_client(*, auth_error=None, update_error=None):
    service = RecordingDeviceService(update_error=update_error)
    auth_service = RecordingAuthService(error=auth_error)
    app = create_app(
        Settings("postgresql://unused", "registration-secret"),
        device_service=service,
        device_auth_service=auth_service,
    )
    return TestClient(app, raise_server_exceptions=False), service, auth_service


def assert_error(response, status, code):
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert body["requestId"] == response.headers["X-Request-ID"]


def test_patch_authenticates_then_updates_and_returns_ok():
    client, service, auth_service = update_client()

    response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"id": "dev_test", "pushToken": None, "simCards": []},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth_service.tokens == ["device-token"]
    authenticated_id, request = service.updates[0]
    assert authenticated_id == "dev_test"
    assert request.id == "dev_test"
    assert request.sim_cards == []


@pytest.mark.parametrize(
    ("authorization", "auth_error", "status", "code"),
    [
        (None, None, 401, "UNAUTHORIZED"),
        ("Code device-token", None, 401, "UNAUTHORIZED"),
        ("Bearer wrong", InvalidDeviceToken(), 401, "UNAUTHORIZED"),
        ("Bearer device-token", DeviceDisabled(), 403, "FORBIDDEN"),
    ],
)
def test_patch_maps_authentication_errors(
    authorization,
    auth_error,
    status,
    code,
):
    client, service, _ = update_client(auth_error=auth_error)
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.patch(
        "/mobile/v1/device",
        headers=headers,
        json={"id": "dev_test"},
    )

    assert_error(response, status, code)
    assert service.updates == []


def test_patch_authentication_precedes_malformed_body():
    client, service, _ = update_client(auth_error=InvalidDeviceToken())

    response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer wrong", "Content-Type": "application/json"},
        content="{not-json",
    )

    assert_error(response, 401, "UNAUTHORIZED")
    assert service.updates == []


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DeviceOwnershipMismatch(), 403, "FORBIDDEN"),
        (DeviceDisabled(), 403, "FORBIDDEN"),
        (InvalidDeviceToken(), 401, "UNAUTHORIZED"),
        (DeviceStateConflict(), 409, "STATE_CONFLICT"),
    ],
)
def test_patch_maps_update_domain_errors(error, status, code):
    client, service, _ = update_client(update_error=error)

    response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"id": "dev_test", "simCards": []},
    )

    assert_error(response, status, code)
    assert len(service.updates) == 1


@pytest.mark.parametrize("content_type", [None, "text/plain"])
def test_patch_requires_json_content_type(content_type):
    client, service, _ = update_client()
    headers = {"Authorization": "Bearer device-token"}
    if content_type is not None:
        headers["Content-Type"] = content_type

    response = client.patch(
        "/mobile/v1/device",
        headers=headers,
        content=json.dumps({"id": "dev_test"}),
    )

    assert_error(response, 400, "VALIDATION_ERROR")
    assert service.updates == []


def test_registration_and_device_tokens_are_not_interchangeable():
    client, service, _ = update_client()

    patch_response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json={"id": "dev_test"},
    )
    post_response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"name": "phone", "simCards": []},
    )

    assert_error(patch_response, 401, "UNAUTHORIZED")
    assert_error(post_response, 401, "UNAUTHORIZED")
    assert service.updates == []
    assert service.registrations == []
