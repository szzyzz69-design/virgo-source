import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mms_inbox import create_mms_inbox_router
from app.errors import install_error_handling
from app.services.device_auth_service import AuthenticatedDevice, DeviceDisabled, InvalidDeviceToken
from app.services.inbound_message_service import InboundConflict
from app.services.mms_webhook_service import MmsWebhookResult, MmsPayloadTooLarge, MmsUnsupportedMediaType
from app.services.object_storage import ObjectStorageUnavailable


class Auth:
    def authenticate(self, token):
        if token == "disabled":
            raise DeviceDisabled
        if token != "device-token":
            raise InvalidDeviceToken
        return AuthenticatedDevice("registered-device", True, "online")


class Service:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def handle(self, request):
        if self.error:
            raise self.error
        self.calls.append(request)
        return MmsWebhookResult("msg-1", "conv-1", request.payload.message_id, len(self.calls) == 1)


def client(service):
    app = FastAPI()
    install_error_handling(app)
    app.include_router(create_mms_inbox_router(Auth(), service))
    return TestClient(app)


def payload():
    return {
        "messageId": "123", "sender": "+14155550100", "simNumber": 1,
        "body": None, "receivedAt": "2026-10-06T00:00:00Z",
        "attachments": [{"partId": 17, "contentType": "image/jpeg", "size": 3,
                         "data": base64.b64encode(b"abc").decode()}],
    }


def post(api, body=None, token="device-token"):
    return api.post("/mobile/v1/inbox/mms", json=payload() if body is None else body,
                    headers={"Authorization": f"Bearer {token}"})


def test_device_identity_and_image_only_upload_and_replay():
    service = Service()
    api = client(service)
    assert post(api).status_code == 201
    request = service.calls[0]
    assert request.device_id == "registered-device"
    assert request.event == "mms:downloaded"
    assert request.payload.body is None
    assert request.payload.attachments[0].decoded_data() == b"abc"
    assert post(api).json() == {"id": "msg-1", "conversationId": "conv-1", "created": False}
    assert post(api).status_code == 200


@pytest.mark.parametrize("token,status", [("invalid", 401), ("disabled", 403), ("", 401)])
def test_authentication(token, status):
    service = Service()
    assert post(client(service), token=token).status_code == status
    assert service.calls == []


def test_cannot_override_authenticated_device():
    service = Service()
    assert post(client(service), {**payload(), "deviceId": "another-device"}).status_code == 400
    assert service.calls == []


@pytest.mark.parametrize("change", ["missing_data", "duplicate_id"])
def test_rejects_incomplete_or_duplicate_parts(change):
    body = payload()
    if change == "missing_data":
        body["attachments"][0]["data"] = None
    else:
        body["attachments"] *= 2
    assert post(client(Service()), body).status_code == 400


def test_canonicalizes_attachment_order():
    service = Service()
    body = payload()
    body["attachments"].append({**body["attachments"][0], "partId": 2})
    assert post(client(service), body).status_code == 201
    assert [p.part_id for p in service.calls[0].payload.attachments] == [2, 17]


@pytest.mark.parametrize("error,status", [(InboundConflict(), 409), (MmsPayloadTooLarge(), 413),
                                         (MmsUnsupportedMediaType(), 415), (ObjectStorageUnavailable(), 503)])
def test_service_errors(error, status):
    assert post(client(Service(error))).status_code == status


def test_request_limit_before_service(monkeypatch):
    monkeypatch.setattr("app.api.mms_inbox.MAX_MMS_REQUEST_BYTES", 100)
    service = Service()
    assert post(client(service)).status_code == 413
    assert service.calls == []


def test_text_only_mms_and_invalid_json():
    api = client(Service())
    assert post(api, {**payload(), "body": "hello", "attachments": []}).status_code == 201
    assert api.post("/mobile/v1/inbox/mms", content="{", headers={
        "Authorization": "Bearer device-token", "Content-Type": "application/json",
    }).status_code == 400


def test_invalid_base64_is_rejected_by_real_service_before_database_access():
    from app.services.mms_webhook_service import MmsWebhookService

    class NoDatabase:
        def transaction(self):
            raise AssertionError("invalid media must not reach the database")

    api = client(MmsWebhookService(NoDatabase(), None))
    body = payload()
    body["attachments"][0]["data"] = "not-base64!"
    assert post(api, body).status_code == 400
