import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

import app.services.mms_webhook_service as mms_webhook_service
from app.application import create_app
from app.config import Settings
from app.services.inbound_message_service import (
    InboundConflict,
    InboundDeviceUnavailable,
    InboundValidation,
)
from app.services.mms_webhook_service import MmsWebhookResult


class Service:
    def __init__(self, replay=False, error=None):
        self.replay = replay
        self.error = error
        self.calls = []

    def handle(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return MmsWebhookResult("msg_1", "conv_1", "mms_1", not self.replay)


def body():
    return {
        "deviceId": "dev_1",
        "event": "mms:received",
        "id": "wh_evt_1",
        "webhookId": "wh_1",
        "payload": {
            "messageId": "mms_1",
            "sender": "+8613800138000",
            "recipient": None,
            "simNumber": 1,
            "transactionId": "tx_1",
            "size": 128,
            "receivedAt": "2026-07-05T08:00:00Z",
        },
    }


def downloaded_body():
    request_body = body()
    request_body["event"] = "mms:downloaded"
    request_body["payload"] = {
        "messageId": "mms_1",
        "sender": "+8613800138000",
        "recipient": None,
        "simNumber": 1,
        "body": "hello image",
        "subject": "Photo",
        "attachments": [
            {
                "partId": 17,
                "contentType": "image/jpeg",
                "name": "photo.jpg",
                "size": 3,
                "data": "not base64",
            }
        ],
        "receivedAt": "2026-07-05T08:00:00Z",
    }
    return request_body


def client(service, signing_key=""):
    app = create_app(
        Settings(
            "postgresql://unused",
            "reg",
            "business",
            mms_webhook_signing_key=signing_key,
        ),
        mms_webhook_service=service,
    )
    return TestClient(app, raise_server_exceptions=False)


def signed_headers(signing_key, raw_body):
    timestamp = str(int(time.time()))
    signature = hmac.new(
        signing_key.encode(),
        raw_body + timestamp.encode(),
        hashlib.sha256,
    ).hexdigest()
    return {"X-Timestamp": timestamp, "X-Signature": signature}


def test_mms_webhook_returns_200_contract():
    service = Service()
    response = client(service).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "messageId": "mms_1", "created": True}
    assert service.calls[0].payload.message_id == "mms_1"


def test_mms_webhook_replay_returns_created_false():
    response = client(Service(replay=True)).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
    )

    assert response.status_code == 200
    assert response.json()["created"] is False


def test_mms_webhook_maps_conflict():
    response = client(Service(error=InboundConflict())).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_mms_webhook_maps_device_unavailable():
    response = client(Service(error=InboundDeviceUnavailable())).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "DEVICE_FORBIDDEN"


def test_mms_webhook_maps_validation_error():
    response = client(Service(error=InboundValidation())).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_mms_webhook_maps_invalid_attachment_base64_after_schema_accepts_it():
    service = Service(error=InboundValidation())
    response = client(service).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=downloaded_body(),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert len(service.calls) == 1


def test_mms_webhook_maps_payload_too_large():
    response = client(Service(error=mms_webhook_service.MmsPayloadTooLarge())).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
    )

    assert response.status_code == 413
    assert response.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_mms_webhook_maps_unsupported_media_type():
    response = client(Service(error=mms_webhook_service.MmsUnsupportedMediaType())).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
    )

    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_mms_webhook_schema_validation_error_does_not_call_service():
    service = Service()
    invalid = body()
    del invalid["payload"]["messageId"]

    response = client(service).post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=invalid,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert service.calls == []


def test_mms_webhook_rejects_invalid_signature():
    response = client(Service(), signing_key="secret").post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        json=body(),
        headers={"X-Timestamp": str(int(time.time())), "X-Signature": "bad"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_mms_webhook_accepts_valid_signature_over_raw_body():
    raw_body = json.dumps(body(), separators=(",", ":")).encode()
    response = client(Service(), signing_key="secret").post(
        "/api/v1/webhooks/android-sms-gateway/mms",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            **signed_headers("secret", raw_body),
        },
    )

    assert response.status_code == 200
    assert response.json()["messageId"] == "mms_1"
