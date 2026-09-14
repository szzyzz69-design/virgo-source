import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.schemas.message import MessageCreateResponse
from app.services.message_service import (
    IdempotencyConflict,
    MessageCreateResult,
    MessageStateConflict,
    MessageValidationError,
    NoAvailableDevice,
)
from app.services.sse import SseConnectionRegistry


async def read_one_event(registry, connection):
    stream = registry.stream(connection)
    try:
        return await anext(stream)
    finally:
        await stream.aclose()


class RecordingMessageService:
    def __init__(self, *, replayed=False, error=None):
        self.replayed = replayed
        self.error = error
        self.calls = []

    def create(self, request, idempotency_key):
        self.calls.append((request, idempotency_key))
        if self.error is not None:
            raise self.error
        return MessageCreateResult(
            response=MessageCreateResponse(
                id="msg_test",
                state="Pending",
                deviceId="dev_test",
                simNumber=1,
                conversationId="conv_test",
                createdAt="2026-06-22T08:00:00.000Z",
            ),
            replayed=self.replayed,
        )


def make_client(service=None):
    service = service or RecordingMessageService()
    app = create_app(
        Settings("postgresql://unused", "registration-secret", "business-secret"),
        message_service=service,
    )
    return TestClient(app, raise_server_exceptions=False), service


def valid_body():
    return {"phoneNumbers": ["+8613800138000"], "text": "hello"}


def post(client, *, token="business-secret", key="request-1", **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post("/business/v1/messages", headers=headers, **kwargs)


def assert_error(response, status, code, message):
    assert response.status_code == status
    assert response.json() == {
        "code": code,
        "message": message,
        "requestId": response.headers["X-Request-ID"],
        "details": None,
    }


@pytest.mark.parametrize(("replayed", "status"), [(False, 201), (True, 200)])
def test_create_message_returns_contract_and_replay_status(replayed, status):
    client, service = make_client(RecordingMessageService(replayed=replayed))

    response = post(client, json=valid_body(), key=" stable-key ")

    assert response.status_code == status
    assert response.json() == {
        "id": "msg_test",
        "state": "Pending",
        "deviceId": "dev_test",
        "simNumber": 1,
        "conversationId": "conv_test",
        "createdAt": "2026-06-22T08:00:00.000Z",
    }
    assert service.calls[0][1] == "stable-key"
    assert service.calls[0][0].phone_numbers == ["+8613800138000"]


@pytest.mark.parametrize("token", [None, "wrong", "business-secret "])
def test_business_authentication_rejects_missing_or_wrong_token_before_body(token):
    client, service = make_client()

    response = post(
        client,
        token=token,
        key=None,
        content="{broken",
        headers={"Content-Type": "application/json"},
    )

    assert_error(response, 401, "UNAUTHORIZED", "Invalid business API token")
    assert service.calls == []


@pytest.mark.parametrize("key", [None, "   ", "x" * 201])
def test_idempotency_key_is_required_and_bounded(key):
    client, service = make_client()

    response = post(client, key=key, json=valid_body())

    assert_error(response, 400, "VALIDATION_ERROR", "Invalid Idempotency-Key")
    assert service.calls == []


def test_invalid_body_maps_to_validation_error_after_authentication():
    client, service = make_client()

    response = post(client, json={"phoneNumbers": [], "text": ""})

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (IdempotencyConflict(), 409, "IDEMPOTENCY_CONFLICT", "Idempotency key was already used for a different request"),
        (MessageStateConflict(), 409, "STATE_CONFLICT", "Message state conflicts with the request"),
        (NoAvailableDevice(), 422, "NO_AVAILABLE_DEVICE", "No available device and SIM card"),
        (MessageValidationError(), 400, "VALIDATION_ERROR", "Message request is invalid"),
    ],
)
def test_domain_errors_use_business_api_contract(error, status, code, message):
    client, _ = make_client(RecordingMessageService(error=error))

    response = post(client, json=valid_body())

    assert_error(response, status, code, message)


def test_registration_token_cannot_authenticate_business_api():
    client, service = make_client()

    response = post(client, token="registration-secret", json=valid_body())

    assert response.status_code == 401
    assert service.calls == []


def test_application_default_message_publisher_uses_injected_sse_registry(
    monkeypatch,
):
    constructed = {}

    class RecordingCommandService:
        def __init__(self, database, *, online_window_seconds, publisher):
            constructed["publisher"] = publisher

    monkeypatch.setattr(
        "app.application.MessageCommandService",
        RecordingCommandService,
    )
    registry = SseConnectionRegistry(heartbeat_seconds=0.01)
    connection = registry.register("dev_1")

    create_app(
        Settings("postgresql://unused", "registration-secret", "business-secret"),
        sse_registry=registry,
    )
    constructed["publisher"].publish("dev_1", "msg_1")

    event = asyncio.run(read_one_event(registry, connection))
    assert event.startswith("id: msg_1\n")
