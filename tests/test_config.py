from pathlib import Path

import pytest

from app.config import Settings


VALID_CONFIG = (
    'private_registration_token = "registration-secret"\n'
    'business_api_token = "business-secret"\n'
    'device_online_window_seconds = 300\n'
)


def test_settings_reads_environment_and_config_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "chosen-config.toml")
    read_paths = []

    def read_text(path, encoding):
        read_paths.append((path, encoding))
        return VALID_CONFIG

    monkeypatch.setattr(Path, "read_text", read_text)

    settings = Settings.from_env()

    assert settings.database_url == "postgresql://db/example"
    assert settings.private_registration_token == "registration-secret"
    assert settings.business_api_token == "business-secret"
    assert settings.device_online_window_seconds == 300
    assert read_paths == [(Path("chosen-config.toml"), "utf-8")]


@pytest.mark.parametrize("invalid_value", ["", " \t "])
def test_settings_rejects_empty_or_whitespace_database_url(monkeypatch, invalid_value):
    monkeypatch.setenv("DATABASE_URL", invalid_value)
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: VALID_CONFIG)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings.from_env()


def test_settings_rejects_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: VALID_CONFIG)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings.from_env()


@pytest.mark.parametrize(
    "contents,match",
    [
        (
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = 300\n',
            "private_registration_token",
        ),
        (
            'private_registration_token = "   "\n'
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = 300\n',
            "private_registration_token",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'device_online_window_seconds = 300\n',
            "business_api_token",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'business_api_token = "   "\n'
            'device_online_window_seconds = 300\n',
            "business_api_token",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = 0\n',
            "device_online_window_seconds",
        ),
        (
            'private_registration_token = "registration-secret"\n'
            'business_api_token = "business-secret"\n'
            'device_online_window_seconds = true\n',
            "device_online_window_seconds",
        ),
    ],
)
def test_settings_rejects_invalid_config(monkeypatch, contents, match):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "invalid-config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: contents)

    with pytest.raises(RuntimeError, match=match):
        Settings.from_env()


@pytest.mark.parametrize("invalid_value", ["true", "0", '"300"'])
def test_settings_rejects_invalid_mms_timestamp_tolerance(monkeypatch, invalid_value):
    contents = (
        VALID_CONFIG
        + f"mms_webhook_timestamp_tolerance_seconds = {invalid_value}\n"
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "invalid-config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: contents)

    with pytest.raises(RuntimeError, match="mms_webhook_timestamp_tolerance_seconds"):
        Settings.from_env()


def test_settings_rejects_missing_config_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "missing.toml")

    def missing_file(self, encoding):
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "read_text", missing_file)

    with pytest.raises(RuntimeError, match="VIRGO_CONFIG_FILE"):
        Settings.from_env()


def test_settings_loads_mms_and_s3_config(monkeypatch):
    config = """
private_registration_token = "reg"
business_api_token = "business"
mms_webhook_signing_key = "signing"
mms_webhook_timestamp_tolerance_seconds = 120
s3_endpoint_url = "https://s3.example.test"
s3_region = "us-east-1"
s3_bucket = "virgo-mms"
s3_access_key_id = "access"
s3_secret_access_key = "secret"
s3_public_base_url = "https://cdn.example.test"
"""
    monkeypatch.setenv("DATABASE_URL", "postgresql://db")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", "config.toml")
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: config)

    settings = Settings.from_env()

    assert settings.mms_webhook_signing_key == "signing"
    assert settings.mms_webhook_timestamp_tolerance_seconds == 120
    assert settings.s3_endpoint_url == "https://s3.example.test"
    assert settings.s3_region == "us-east-1"
    assert settings.s3_bucket == "virgo-mms"
    assert settings.s3_access_key_id == "access"
    assert settings.s3_secret_access_key == "secret"
    assert settings.s3_public_base_url == "https://cdn.example.test"
