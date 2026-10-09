from dataclasses import dataclass
import re

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings


class ObjectStorageUnavailable(Exception):
    pass


def create_s3_storage(settings: Settings):
    options = {
        "region_name": settings.s3_region,
        "aws_access_key_id": settings.s3_access_key_id or None,
        "aws_secret_access_key": settings.s3_secret_access_key or None,
        "config": Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.s3_addressing_style},
            connect_timeout=5,
            read_timeout=30,
            retries={"mode": "standard", "max_attempts": 2},
        ),
    }
    client = boto3.client("s3", endpoint_url=settings.s3_endpoint_url or None, **options)
    download_client = client
    if settings.s3_download_endpoint_url:
        download_client = boto3.client(
            "s3", endpoint_url=settings.s3_download_endpoint_url, **options
        )
    return S3ObjectStorage(
        client=client,
        download_client=download_client,
        bucket=settings.s3_bucket,
        public_base_url=settings.s3_public_base_url,
    )


@dataclass(frozen=True, slots=True)
class StoredObject:
    bucket: str
    key: str
    url: str | None
    etag: str | None


def extension_for_content_type(content_type: str) -> str:
    return {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "audio/amr": "amr"}.get(content_type, "bin")


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
    def __init__(self, *, client, bucket: str, public_base_url: str = "", download_client=None):
        self._client = client
        self._download_client = download_client if download_client is not None else client
        self._bucket = bucket
        self._public_base_url = public_base_url.rstrip("/")

    def download_url(self, *, bucket: str, key: str) -> str:
        # Generate on each authorized message query, never persist expiring URLs.
        try:
            return self._download_client.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=900,
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageUnavailable from error

    def upload_bytes(self, *, key: str, body: bytes, content_type: str) -> StoredObject:
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as error:
            raise ObjectStorageUnavailable from error
        etag = response.get("ETag")
        if isinstance(etag, str):
            etag = etag.strip('"')
        url = f"{self._public_base_url}/{key}" if self._public_base_url else None
        return StoredObject(bucket=self._bucket, key=key, url=url, etag=etag)
