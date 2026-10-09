import base64
from datetime import datetime, timezone

import pytest

from app.schemas.mms_webhook import MmsWebhookRequest
from app.services.mms_media import is_complete_gif
from app.services.mms_webhook_service import MmsUnsupportedMediaType, MmsWebhookService
from app.services.object_storage import build_mms_object_key


GIF = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")


def gif_request(data=GIF, *, content_type="image/gif"):
    return MmsWebhookRequest.model_validate({
        "deviceId": "dev_gif", "event": "mms:downloaded", "id": "gif-event", "webhookId": "gif-hook",
        "payload": {"messageId": "42", "sender": "+12025550108", "recipient": "+12025550109",
            "simNumber": 1, "body": None, "receivedAt": datetime.now(timezone.utc).isoformat(),
            "attachments": [{"partId": 7, "contentType": content_type,
                "data": None if data is None else base64.b64encode(data).decode()}]},
    })


def test_valid_gif87_and_gif89_container_are_accepted_and_previewed_as_images():
    service = MmsWebhookService(None, None)
    for data in (GIF, b"GIF87a" + GIF[6:]):
        request = gif_request(data)
        service._validate_attachments(request)
        assert service._preview(request) == "[MMS image]"
        assert is_complete_gif(data)


def test_missing_filename_gets_gif_extension():
    assert build_mms_object_key("device", "42", 7, None, "image/gif").endswith("7-mms-42-part-7.gif")


@pytest.mark.parametrize("data", [
    None, b"GIF89a", b"<html>not a gif</html>", b"\x89PNG\r\n\x1a\n",
    GIF[:-1], GIF + b"<script>bad</script>", GIF[:6] + b"\0\0" + GIF[8:],
    GIF[:6] + b"\xff\xff\xff\xff" + GIF[10:],
    GIF.replace(b"\x02\x01\x44\x00\x3b", b"\x02\xff\x44\x00\x3b"),
])
def test_forged_truncated_or_unbounded_gif_is_rejected_before_database_access(data):
    class NoDatabase:
        def transaction(self):
            raise AssertionError("invalid GIF must not reach the database")

    with pytest.raises(MmsUnsupportedMediaType):
        MmsWebhookService(NoDatabase(), None).handle(gif_request(data))


def test_unrelated_new_formats_are_still_rejected():
    with pytest.raises(MmsUnsupportedMediaType):
        MmsWebhookService(None, None)._validate_attachments(gif_request(content_type="image/webp"))


@pytest.mark.parametrize("content_type", ["image/jpeg", "image/png", "audio/amr", "application/octet-stream"])
def test_previous_supported_formats_keep_their_existing_contract(content_type):
    MmsWebhookService(None, None)._validate_attachments(gif_request(b"old-accepted-bytes", content_type=content_type))
