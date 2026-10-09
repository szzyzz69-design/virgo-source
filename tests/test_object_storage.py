from app.services.object_storage import S3ObjectStorage, build_mms_object_key

from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import EndpointConnectionError

from app.config import Settings
from app.services.object_storage import create_s3_storage, ObjectStorageUnavailable


def test_private_links_use_external_endpoint_and_signature_v4():
    storage = create_s3_storage(Settings(
        database_url="postgresql://unused", private_registration_token="reg",
        s3_endpoint_url="http://minio:9000",
        s3_download_endpoint_url="http://192.168.50.24:9000",
        s3_bucket="virgo-mms", s3_access_key_id="access", s3_secret_access_key="secret",
        s3_addressing_style="path",
    ))
    url = urlsplit(storage.download_url(bucket="virgo-mms", key="mms/photo.png"))
    assert url.netloc == "192.168.50.24:9000"
    assert url.path == "/virgo-mms/mms/photo.png"
    assert parse_qs(url.query)["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert parse_qs(url.query)["X-Amz-Expires"] == ["900"]


def test_unavailable_upload_is_reported_as_storage_unavailable():
    class OfflineClient:
        def put_object(self, **kwargs):
            raise EndpointConnectionError(endpoint_url="http://unavailable")

    storage = S3ObjectStorage(client=OfflineClient(), bucket="virgo-mms")
    with pytest.raises(ObjectStorageUnavailable):
        storage.upload_bytes(key="mms/image.png", body=b"abc", content_type="image/png")


class FakeS3Client:
    def __init__(self, response=None):
        self.calls = []
        self.response = {"ETag": '"etag-1"'} if response is None else response

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_build_mms_object_key_sanitizes_name():
    key = build_mms_object_key("dev_1", "mms_1", 17, "../photo cat.jpg", "image/jpeg")
    assert key == "mms/dev_1/mms_1/17-photo-cat.jpg"


def test_build_mms_object_key_sanitizes_backslash_path_name():
    key = build_mms_object_key("dev_1", "mms_1", 17, r"..\photo cat.jpg", "image/jpeg")
    assert key == "mms/dev_1/mms_1/17-photo-cat.jpg"


def test_build_mms_object_key_uses_content_type_extension_without_name():
    key = build_mms_object_key("dev_1", "mms_1", 18, None, "image/png")
    assert key == "mms/dev_1/mms_1/18-mms-mms_1-part-18.png"


def test_build_mms_object_key_uses_safe_fallback_for_all_invalid_name():
    key = build_mms_object_key("dev_1", "mms/../bad id", 19, "////", "application/octet-stream")
    assert key == "mms/dev_1/mms-bad-id/19-mms-mms-bad-id-part-19.bin"


def test_build_mms_object_key_sanitizes_device_and_message_segments():
    key = build_mms_object_key(" dev/1\x00 ", r"mms\1 odd", 7, "photo.jpg", "image/jpeg")
    assert key == "mms/dev-1/mms-1-odd/7-photo.jpg"
    assert key.count("/") == 3


def test_build_mms_object_key_uses_stable_segment_fallbacks():
    key = build_mms_object_key("///", "\\\\", 1, "photo.jpg", "image/jpeg")
    assert key == "mms/unknown/unknown/1-photo.jpg"


def test_upload_bytes_writes_content_type_and_public_url():
    client = FakeS3Client()
    storage = S3ObjectStorage(
        client=client,
        bucket="bucket",
        public_base_url="https://cdn.example.test/base",
    )

    result = storage.upload_bytes(
        key="mms/dev/mms/1-photo.jpg",
        body=b"abc",
        content_type="image/jpeg",
    )

    assert client.calls == [
        {
            "Bucket": "bucket",
            "Key": "mms/dev/mms/1-photo.jpg",
            "Body": b"abc",
            "ContentType": "image/jpeg",
        }
    ]
    assert result.bucket == "bucket"
    assert result.key == "mms/dev/mms/1-photo.jpg"
    assert result.url == "https://cdn.example.test/base/mms/dev/mms/1-photo.jpg"
    assert result.etag == "etag-1"


def test_upload_bytes_trims_trailing_slash_from_public_base_url():
    client = FakeS3Client()
    storage = S3ObjectStorage(
        client=client,
        bucket="bucket",
        public_base_url="https://cdn.example.test/base/",
    )

    result = storage.upload_bytes(
        key="mms/dev/mms/1-photo.jpg",
        body=b"abc",
        content_type="image/jpeg",
    )

    assert result.url == "https://cdn.example.test/base/mms/dev/mms/1-photo.jpg"


def test_upload_bytes_preserves_unquoted_etag():
    client = FakeS3Client({"ETag": "etag-2"})
    storage = S3ObjectStorage(client=client, bucket="bucket")

    result = storage.upload_bytes(key="key", body=b"abc", content_type="image/jpeg")

    assert result.etag == "etag-2"


def test_upload_bytes_allows_missing_etag():
    client = FakeS3Client({})
    storage = S3ObjectStorage(client=client, bucket="bucket")

    result = storage.upload_bytes(key="key", body=b"abc", content_type="image/jpeg")

    assert result.etag is None
