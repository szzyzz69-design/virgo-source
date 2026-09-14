from pydantic import ValidationError
import pytest

from app.schemas.mms_webhook import (
    MmsDownloadedPayload,
    MmsReceivedPayload,
    MmsWebhookRequest,
    mms_received_millis,
    mms_webhook_digest,
)


def received_body(**overrides):
    body = {
        "deviceId": "dev_1",
        "event": "mms:received",
        "id": "wh_evt_1",
        "webhookId": "wh_1",
        "payload": {
            "messageId": "mms_1",
            "sender": "+86 13800138000",
            "recipient": "+8613900000000",
            "simNumber": 1,
            "transactionId": "tx_1",
            "subject": "Photo",
            "size": 128,
            "contentClass": "IMAGE_BASIC",
            "receivedAt": "2026-07-05T12:00:00+08:00",
        },
    }
    body.update(overrides)
    return body


def downloaded_body(**payload_overrides):
    body = received_body(event="mms:downloaded")
    payload = {
        "messageId": "mms_1",
        "sender": "+86 13800138000",
        "recipient": "+8613900000000",
        "simNumber": 1,
        "body": "hello image",
        "subject": "Photo",
        "attachments": [
            {
                "partId": 17,
                "contentType": "image/jpeg",
                "name": "photo.jpg",
                "size": 3,
                "data": "AQID",
            }
        ],
        "receivedAt": "2026-07-05T12:00:03+08:00",
    }
    payload.update(payload_overrides)
    body["payload"] = payload
    return body


def test_received_event_normalizes_sender_and_computes_digest():
    request = MmsWebhookRequest.model_validate(received_body())
    assert request.device_id == "dev_1"
    assert request.payload.sender == "+8613800138000"
    assert mms_received_millis(request) == 1783224000000
    assert mms_webhook_digest(request) == mms_webhook_digest(
        MmsWebhookRequest.model_validate(received_body(payload=received_body()["payload"]))
    )


def test_downloaded_event_accepts_image_attachment_base64():
    request = MmsWebhookRequest.model_validate(downloaded_body())
    assert request.event == "mms:downloaded"
    assert request.payload.attachments[0].content_type == "image/jpeg"
    assert request.payload.attachments[0].decoded_data() == b"\x01\x02\x03"


def test_downloaded_event_accepts_unsupported_attachment_type_for_service_validation():
    request = MmsWebhookRequest.model_validate(
        downloaded_body(
            attachments=[
                {
                    "partId": 17,
                    "contentType": "Application/X-MsDownload",
                    "name": "payload.exe",
                    "size": 3,
                    "data": "AQID",
                }
            ]
        )
    )

    assert request.payload.attachments[0].content_type == "application/x-msdownload"


def test_downloaded_event_accepts_invalid_base64_for_service_validation():
    request = MmsWebhookRequest.model_validate(
        downloaded_body(attachments=[{"partId": 1, "contentType": "image/jpeg", "data": "not base64"}])
    )

    assert request.payload.attachments[0].data == "not base64"


def test_received_event_uses_received_payload_even_with_downloaded_fields():
    body = received_body()
    body["payload"] = {
        **body["payload"],
        "body": "ignored downloaded text",
        "attachments": [
            {
                "partId": 17,
                "contentType": "image/jpeg",
                "size": 3,
                "data": "AQID",
            }
        ],
    }

    request = MmsWebhookRequest.model_validate(body)

    assert isinstance(request.payload, MmsReceivedPayload)
    assert request.payload.transaction_id == "tx_1"


def test_downloaded_event_uses_downloaded_payload_even_with_received_fields():
    request = MmsWebhookRequest.model_validate(
        downloaded_body(
            transactionId="ignored_tx",
            size=128,
            contentClass="IMAGE_BASIC",
        )
    )

    assert isinstance(request.payload, MmsDownloadedPayload)
    assert request.payload.body == "hello image"


def test_attachment_missing_data_and_empty_data_have_distinct_digests():
    missing_data = MmsWebhookRequest.model_validate(
        downloaded_body(attachments=[{"partId": 1, "contentType": "image/jpeg"}])
    )
    empty_data = MmsWebhookRequest.model_validate(
        downloaded_body(attachments=[{"partId": 1, "contentType": "image/jpeg", "data": ""}])
    )

    assert mms_webhook_digest(missing_data) != mms_webhook_digest(empty_data)


def test_attachment_content_changes_digest():
    first = MmsWebhookRequest.model_validate(
        downloaded_body(attachments=[{"partId": 1, "contentType": "image/jpeg", "data": "AQID"}])
    )
    changed = MmsWebhookRequest.model_validate(
        downloaded_body(attachments=[{"partId": 1, "contentType": "image/jpeg", "data": "BAID"}])
    )

    assert mms_webhook_digest(first) != mms_webhook_digest(changed)


@pytest.mark.parametrize(
    "body",
    [
        received_body(event="sms:received"),
        received_body(deviceId=""),
        received_body(payload={**received_body()["payload"], "messageId": ""}),
        received_body(payload={**received_body()["payload"], "receivedAt": "2026-07-05T12:00:00"}),
        received_body(payload={k: v for k, v in received_body()["payload"].items() if k != "transactionId"}),
        downloaded_body(attachments=[{"partId": 1, "contentType": "", "data": "AQID"}]),
    ],
)
def test_invalid_payloads_are_rejected(body):
    with pytest.raises(ValidationError):
        MmsWebhookRequest.model_validate(body)
