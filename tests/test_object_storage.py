from app.services.object_storage import S3ObjectStorage, build_mms_object_key


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
