import hashlib
import hmac
import time

import pytest

from app.services.mms_signature import InvalidMmsSignature, verify_mms_signature


def sign(key: str, raw_body: bytes, timestamp: str) -> str:
    return hmac.new(key.encode(), raw_body + timestamp.encode(), hashlib.sha256).hexdigest()


def test_accepts_valid_signature():
    raw_body = b'{"ok":true}'
    timestamp = str(int(time.time()))
    signature = sign("secret", raw_body, timestamp)
    verify_mms_signature("secret", raw_body, timestamp, signature, tolerance_seconds=300)


def test_rejects_bad_signature():
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", b"{}", str(int(time.time())), "bad", tolerance_seconds=300)


def test_rejects_missing_timestamp():
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", b"{}", None, "signature", tolerance_seconds=300)


def test_rejects_missing_signature():
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", b"{}", str(int(time.time())), None, tolerance_seconds=300)


def test_rejects_invalid_timestamp():
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", b"{}", "not-a-timestamp", "signature", tolerance_seconds=300)


def test_rejects_expired_timestamp():
    raw_body = b"{}"
    timestamp = str(int(time.time()) - 301)
    signature = sign("secret", raw_body, timestamp)
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", raw_body, timestamp, signature, tolerance_seconds=300)


def test_rejects_future_timestamp_outside_tolerance():
    raw_body = b"{}"
    timestamp = str(int(time.time()) + 301)
    signature = sign("secret", raw_body, timestamp)
    with pytest.raises(InvalidMmsSignature):
        verify_mms_signature("secret", raw_body, timestamp, signature, tolerance_seconds=300)


def test_allows_unsigned_when_key_is_blank():
    verify_mms_signature("", b"{}", None, None, tolerance_seconds=300)
