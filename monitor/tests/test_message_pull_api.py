import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.schemas.message_pull import MessagePullItem
from app.services.device_auth_service import (
    AuthenticatedDevice,
    DeviceDisabled,
    InvalidDeviceToken,
)
from app.services.message_pull_service import PullDeviceUnavailable


class RecordingAuthService:
    def __init__(self, error=None):
        self.error = error
        self.tokens = []

    def authenticate(self, token):
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return AuthenticatedDevice("dev_1", True, "online")


class RecordingPullService:
    def __init__(self, items=None, error=None):
        self.items = [] if items is None else items
        self.error = error
        self.calls = []

    def pull(self, device_id, order):
        self.calls.append((device_id, order))
        if self.error is not None:
            raise self.error
        return self.items


def android_item():
    return MessagePullItem(
        id="msg_1",
        textMessage={"text": "hello"},
        dataMessage=None,
        phoneNumbers=["+8613800138000"],
        simNumber=1,
        withDeliveryReport=True,
        isEncrypted=False,
        validUntil=None,
        scheduleAt=None,
        priority=0,
        createdAt="2026-06-22T08:00:00.000Z",
    )


def make_client(auth=None, pull=None):
    auth = auth or RecordingAuthService()
    pull = pull or RecordingPullService()
    app = create_app(
        Settings("postgresql://unused", "registration-secret", "business-secret"),
        device_auth_service=auth,
        message_pull_service=pull,
    )
    return TestClient(app, raise_server_exceptions=False), auth, pull


def assert_error(response, status, code, message):
    assert response.status_code == status
    assert response.json() == {
        "code": code,
        "message": message,
        "requestId": response.headers["X-Request-ID"],
        "details": None,
    }


@pytest.mark.parametrize(("query", "order"), [("", "fifo"), ("?order=fifo", "fifo"), ("?order=lifo", "lifo")])
def test_pull_uses_requested_order_and_android_contract(query, order):
    client, auth, pull = make_client(pull=RecordingPullService([android_item()]))

    response = client.get(
        "/mobile/v1/message" + query,
        headers={"Authorization": "Bearer device-token"},
    )

    assert response.status_code == 200
    assert response.json() == [android_item().model_dump(by_alias=True)]
    assert auth.tokens == ["device-token"]
    assert pull.calls == [("dev_1", order)]


def test_pull_returns_empty_array_when_no_tasks_exist():
    client, _, _ = make_client()

    response = client.get(
        "/mobile/v1/message",
        headers={"Authorization": "Bearer device-token"},
    )

    assert response.status_code == 200
    assert response.json() == []


def test_pull_rejects_invalid_order_without_calling_service():
    client, _, pull = make_client()

    response = client.get(
        "/mobile/v1/message?order=oldest",
        headers={"Authorization": "Bearer device-token"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert pull.calls == []


@pytest.mark.parametrize("authorization", [None, "Basic token", "Bearer ", "Bearer token "])
def test_pull_rejects_bad_authorization_before_service(authorization):
    client, auth, pull = make_client()
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get("/mobile/v1/message", headers=headers)

    assert_error(response, 401, "UNAUTHORIZED", "Invalid device token")
    assert auth.tokens == []
    assert pull.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (InvalidDeviceToken(), 401, "UNAUTHORIZED", "Invalid device token"),
        (DeviceDisabled(), 403, "FORBIDDEN", "Device is disabled"),
    ],
)
def test_pull_maps_authentication_errors(error, status, code, message):
    client, _, pull = make_client(auth=RecordingAuthService(error))

    response = client.get(
        "/mobile/v1/message",
        headers={"Authorization": "Bearer device-token"},
    )

    assert_error(response, status, code, message)
    assert pull.calls == []


def test_pull_maps_device_race_to_forbidden():
    client, _, _ = make_client(
        pull=RecordingPullService(error=PullDeviceUnavailable())
    )

    response = client.get(
        "/mobile/v1/message",
        headers={"Authorization": "Bearer device-token"},
    )

    assert_error(response, 403, "FORBIDDEN", "Device is disabled")
