from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    key: str
    url: str | None
    etag: str | None


def extension_for_content_type(content_type: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "audio/amr": "amr"}.get(content_type, "bin")


def _safe_key_segment(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return cleaned or fallback


def safe_filename(name: str | None, message_id: str, part_id: int, content_type: str) -> str:
    safe_message_id = _safe_key_segment(message_id)
    if not name:
        name = f"mms-{safe_message_id}-part-{part_id}.{extension_for_content_type(content_type)}"
    leaf = name.replace("\\", "/").split("/")[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", leaf).strip(".-")
    return cleaned or f"mms-{safe_message_id}-part-{part_id}.{extension_for_content_type(content_type)}"


def build_mms_object_key(
    device_id: str,
    message_id: str,
    part_id: int,
    name: str | None,
    content_type: str,
) -> str:
    device_segment = _safe_key_segment(device_id)
    message_segment = _safe_key_segment(message_id)
    filename = safe_filename(name, message_segment, part_id, content_type)
    return f"mms/{device_segment}/{message_segment}/{part_id}-{filename}"


class S3ObjectStorage:
    def __init__(self, *, client, bucket: str, public_base_url: str = ""):
        self._client = client
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")

    def upload_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        response = self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        etag = response.get("ETag")
        if isinstance(etag, str):
            etag = etag.strip('"')
        url = f"{self._public_base_url}/{key}" if self._public_base_url else None
        return StoredObject(bucket=self._bucket, key=key, url=url, etag=etag)
