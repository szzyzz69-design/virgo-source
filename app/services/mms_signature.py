import hashlib
import hmac
import time


class InvalidMmsSignature(Exception):
    pass


def verify_mms_signature(
    signing_key: str | None,
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    tolerance_seconds: int,
) -> None:
    if not signing_key:
        return
    if not timestamp or not signature:
        raise InvalidMmsSignature("missing signature headers")
    try:
        timestamp_seconds = int(timestamp)
    except ValueError as error:
        raise InvalidMmsSignature("invalid timestamp") from error
    if abs(int(time.time()) - timestamp_seconds) > tolerance_seconds:
        raise InvalidMmsSignature("timestamp outside tolerance")
    expected = hmac.new(
        signing_key.encode(),
        raw_body + timestamp.encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidMmsSignature("signature mismatch")
