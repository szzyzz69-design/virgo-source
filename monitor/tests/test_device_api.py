import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException
from starlette.requests import Request

from app.application import create_app
from app.config import Settings
from app.errors import ApiError, install_error_handling
from app.schemas.device import DeviceRegisterResponse


class RecordingDeviceService:
    def __init__(self):
        self.requests = []

    def register(self, request):
        self.requests.append(request)
        return DeviceRegisterResponse(
            id="dev_test",
            token="device-token",
            login="device-test",
            password="initial-password",
        )


class FailingService:
    def register(self, request):
        raise RuntimeError(
            "postgresql://admin:database-secret@localhost/virgo?token=service-secret"
        )


def client_and_service():
    service = RecordingDeviceService()
    app = create_app(
        Settings("postgresql://unused", "registration-secret"),
        device_service=service,
    )
    return TestClient(app, raise_server_exceptions=False), service


def valid_body():
    return {
        "name": "phone",
        "simCards": [{"slotIndex": 0, "simNumber": 1}],
        "futureField": "ignored",
    }


def assert_error_contract(response, status_code, code, message):
    assert response.status_code == status_code
    assert response.json() == {
        "code": code,
        "message": message,
        "requestId": response.headers["X-Request-ID"],
        "details": None,
    }


def test_api_error_details_are_json_encoded():
    app = FastAPI()
    install_error_handling(app)
    occurred_at = datetime(2026, 6, 21, 8, 30, tzinfo=timezone.utc)

    @app.get("/encoded-error")
    def encoded_error():
        raise ApiError(
            418,
            "EXAMPLE_ERROR",
            "Example error",
            details={"occurredAt": occurred_at},
        )

    response = TestClient(app, raise_server_exceptions=False).get("/encoded-error")

    assert response.status_code == 418
    assert response.json()["details"] == {"occurredAt": occurred_at.isoformat()}
    assert response.json()["requestId"] == response.headers["X-Request-ID"]


def test_http_exception_headers_cannot_override_request_id():
    app = FastAPI()
    install_error_handling(app)
    request = Request({"type": "http", "headers": []})
    request.state.request_id = "trusted-request-id"
    error = HTTPException(
        status_code=409,
        detail="Conflict",
        headers={
            "X-Request-ID": "forged-request-id",
            "X-Reason": "preserved",
        },
    )

    handler = app.exception_handlers[HTTPException]
    response = asyncio.run(handler(request, error))

    assert response.headers["X-Request-ID"] == "trusted-request-id"
    assert response.headers["X-Reason"] == "preserved"


def test_application_and_registration_return_android_contract_and_201():
    client, service = client_and_service()

    assert client.app.title == "Virgo SMS Gateway"
    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json=valid_body(),
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": "dev_test",
        "token": "device-token",
        "login": "device-test",
        "password": "initial-password",
    }
    assert len(service.requests) == 1
    assert service.requests[0].name == "phone"
    assert response.headers["X-Request-ID"].startswith("req_")


def test_create_app_builds_default_service_with_settings_database_url(monkeypatch):
    constructed = {}

    class RecordingDatabase:
        def __init__(self, dsn):
            constructed["dsn"] = dsn

    class ConstructedDeviceService:
        def __init__(self, database):
            constructed["database"] = database

        def register(self, request):
            constructed["request"] = request
            return DeviceRegisterResponse(
                id="dev_default",
                token="default-token",
                login="device-default",
                password=None,
            )

    monkeypatch.setattr("app.application.Database", RecordingDatabase)
    monkeypatch.setattr("app.application.DeviceService", ConstructedDeviceService)

    app = create_app(
        Settings("postgresql://configured/database", "registration-secret")
    )
    response = TestClient(app, raise_server_exceptions=False).post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json=valid_body(),
    )

    assert constructed["dsn"] == "postgresql://configured/database"
    assert isinstance(constructed["database"], RecordingDatabase)
    assert constructed["request"].name == "phone"
    assert response.status_code == 201
    assert response.json()["id"] == "dev_default"


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Code registration-secret",
        "Basic registration-secret",
        "Bearer ",
        "Bearer wrong",
        "Bearer registration-secret ",
    ],
)
def test_registration_rejects_missing_or_wrong_authentication(authorization):
    client, service = client_and_service()
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.post("/mobile/v1/device", headers=headers, json=valid_body())

    assert_error_contract(
        response,
        401,
        "UNAUTHORIZED",
        "Invalid registration token",
    )
    assert service.requests == []


@pytest.mark.parametrize(
    ("authorization", "request_kwargs"),
    [
        (None, {}),
        ("Bearer wrong", {}),
        (
            None,
            {
                "content": "{not-json",
                "headers": {"Content-Type": "application/json"},
            },
        ),
        (
            "Bearer wrong",
            {
                "content": "{not-json",
                "headers": {"Content-Type": "application/json"},
            },
        ),
        (None, {"json": {"name": "phone"}}),
        ("Bearer wrong", {"json": {"name": "phone"}}),
    ],
)
def test_authentication_precedes_missing_malformed_or_invalid_body(
    authorization,
    request_kwargs,
):
    client, service = client_and_service()
    request_options = dict(request_kwargs)
    headers = dict(request_options.pop("headers", {}))
    if authorization is not None:
        headers["Authorization"] = authorization

    response = client.post(
        "/mobile/v1/device",
        headers=headers,
        **request_options,
    )

    assert_error_contract(
        response,
        401,
        "UNAUTHORIZED",
        "Invalid registration token",
    )
    assert service.requests == []


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "BeArEr"])
def test_registration_accepts_case_insensitive_bearer_scheme(scheme):
    client, service = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": f"{scheme} registration-secret"},
        json=valid_body(),
    )

    assert response.status_code == 201
    assert len(service.requests) == 1


def test_registration_maps_validation_errors_to_400_with_json_details():
    client, service = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json={"name": "phone"},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "Request validation failed"
    assert body["requestId"] == response.headers["X-Request-ID"]
    assert body["details"]
    assert {"location", "message", "type"} <= body["details"][0].keys()
    json.dumps(body["details"])
    assert service.requests == []


def test_registration_maps_malformed_json_to_400():
    client, service = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer registration-secret",
            "Content-Type": "application/json",
        },
        content="{not-json",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert service.requests == []


def test_registration_maps_invalid_utf8_json_to_400():
    client, service = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer registration-secret",
            "Content-Type": "application/json",
        },
        content=b"\xff",
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert service.requests == []


@pytest.mark.parametrize("content_type", [None, "text/plain"])
def test_registration_rejects_missing_or_non_json_content_type(content_type):
    client, service = client_and_service()
    headers = {"Authorization": "Bearer registration-secret"}
    if content_type is not None:
        headers["Content-Type"] = content_type

    response = client.post(
        "/mobile/v1/device",
        headers=headers,
        content=json.dumps(valid_body()),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert service.requests == []


def test_registration_accepts_json_content_type_case_and_charset():
    client, service = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer registration-secret",
            "Content-Type": "Application/JSON; charset=utf-8",
        },
        content=json.dumps(valid_body()),
    )

    assert response.status_code == 201
    assert len(service.requests) == 1


def test_duplicate_sim_validator_error_details_are_json_serializable():
    client, service = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json={
            "name": "phone",
            "simCards": [
                {"slotIndex": 0, "simNumber": 1},
                {"slotIndex": 0, "simNumber": 2},
            ],
        },
    )

    assert response.status_code == 400
    details = response.json()["details"]
    assert details[0]["type"] == "value_error"
    assert "duplicate slotIndex" in details[0]["message"]
    json.dumps(details)
    assert service.requests == []


def test_request_id_at_most_128_characters_is_returned_unchanged():
    client, _ = client_and_service()
    supplied = "client-" + ("x" * 121)

    response = client.post(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer registration-secret",
            "X-Request-ID": supplied,
        },
        json=valid_body(),
    )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == supplied


def test_request_id_safe_ascii_punctuation_is_returned_unchanged():
    client, _ = client_and_service()
    supplied = "Client_1.trace:part-2"

    response = client.post(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer registration-secret",
            "X-Request-ID": supplied,
        },
        json=valid_body(),
    )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == supplied


@pytest.mark.parametrize(
    "supplied",
    [
        b" leading-space",
        b"trailing-space ",
        b"tab\tid",
        b"line\r\nid",
        b"non-ascii-\xff",
        b"slash/id",
        b"x" * 129,
    ],
)
def test_unsafe_request_id_is_replaced(supplied):
    client, _ = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers=[
            (b"Authorization", b"Bearer registration-secret"),
            (b"X-Request-ID", supplied),
            (b"Content-Type", b"application/json"),
        ],
        content=json.dumps(valid_body()),
    )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"].startswith("req_")


def test_request_id_longer_than_128_characters_is_replaced():
    client, _ = client_and_service()
    supplied = "client-" + ("x" * 122)

    response = client.post(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer registration-secret",
            "X-Request-ID": supplied,
        },
        json=valid_body(),
    )

    assert response.status_code == 201
    request_id = response.headers["X-Request-ID"]
    assert request_id.startswith("req_")
    assert request_id != supplied[:128]


def test_blank_request_id_is_replaced_with_generated_id_on_error():
    client, _ = client_and_service()

    response = client.post(
        "/mobile/v1/device",
        headers={"X-Request-ID": "   "},
        json=valid_body(),
    )

    assert response.status_code == 401
    request_id = response.headers["X-Request-ID"]
    assert request_id.startswith("req_")
    assert response.json()["requestId"] == request_id


def test_registration_hides_unexpected_internal_errors_and_returns_request_id():
    app = create_app(
        Settings("postgresql://unused", "registration-secret"),
        device_service=FailingService(),
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer registration-secret",
            "X-Request-ID": "client-error-id",
        },
        json=valid_body(),
    )

    assert_error_contract(
        response,
        500,
        "INTERNAL_ERROR",
        "An internal error occurred",
    )
    assert response.headers["X-Request-ID"] == "client-error-id"
    assert "postgresql" not in response.text
    assert "database-secret" not in response.text
    assert "service-secret" not in response.text


def test_unexpected_service_error_is_re_raised_for_server_logging():
    app = create_app(
        Settings("postgresql://unused", "registration-secret"),
        device_service=FailingService(),
    )
    client = TestClient(app, raise_server_exceptions=True)

    with pytest.raises(RuntimeError, match="database-secret"):
        client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json=valid_body(),
        )


@pytest.mark.parametrize(
    ("method", "path", "status_code", "code", "message"),
    [
        ("get", "/does-not-exist", 404, "NOT_FOUND", "Resource not found"),
        (
            "get",
            "/mobile/v1/device",
            405,
            "METHOD_NOT_ALLOWED",
            "Method not allowed",
        ),
    ],
)
def test_http_errors_use_uniform_error_contract(
    method,
    path,
    status_code,
    code,
    message,
):
    client, service = client_and_service()

    response = getattr(client, method)(path)

    assert_error_contract(response, status_code, code, message)
    assert service.requests == []
