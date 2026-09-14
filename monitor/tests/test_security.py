import base64
import hashlib

import pytest

from app.security import hash_password, hash_sha256, secure_equals


def test_hash_sha256_returns_expected_hex_digest():
    assert hash_sha256("secret") == hashlib.sha256(b"secret").hexdigest()


def test_hash_password_uses_random_salt_and_does_not_expose_plaintext():
    password = "initial-password"

    first_hash = hash_password(password)
    second_hash = hash_password(password)

    assert first_hash.startswith("pbkdf2_sha256$")
    assert second_hash.startswith("pbkdf2_sha256$")
    assert first_hash != second_hash
    assert password not in first_hash
    assert password not in second_hash


@pytest.mark.parametrize("iterations", [True, 600_000.0, 599_999])
def test_hash_password_rejects_invalid_iterations(iterations):
    with pytest.raises(ValueError):
        hash_password("initial-password", iterations=iterations)


def test_hash_password_output_contains_verifiable_pbkdf2_components():
    password = "initial-password"

    encoded = hash_password(password)
    algorithm, iterations, encoded_salt, encoded_digest = encoded.split("$")
    salt = base64.urlsafe_b64decode(encoded_salt)
    digest = base64.urlsafe_b64decode(encoded_digest)

    assert algorithm == "pbkdf2_sha256"
    assert iterations == "600000"
    assert len(salt) == 16
    assert len(digest) == 32
    assert digest == hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        600_000,
    )


def test_secure_equals_matches_only_identical_strings():
    assert secure_equals("same-value", "same-value") is True
    assert secure_equals("same-value", "different-value") is False


def test_secure_equals_handles_unicode_strings():
    assert secure_equals("密碼🔐", "密碼🔐") is True
    assert secure_equals("密碼🔐", "密码🔐") is False
