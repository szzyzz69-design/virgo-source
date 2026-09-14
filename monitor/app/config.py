from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    private_registration_token: str
    business_api_token: str = ""
    device_online_window_seconds: int = 300
    mms_webhook_signing_key: str = ""
    mms_webhook_timestamp_tolerance_seconds: int = 300
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_public_base_url: str = ""
    supervisor_username: str = ""
    supervisor_password: str = ""
    supervisor_session_secret: str = ""
    supervisor_timezone: str = "America/Vancouver"

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv("DATABASE_URL")
        if database_url is None or not database_url.strip():
            raise RuntimeError("DATABASE_URL is required")

        config_path = Path(os.getenv("VIRGO_CONFIG_FILE", "config.toml"))
        try:
            config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise RuntimeError(
                f"VIRGO_CONFIG_FILE could not be loaded: {config_path}"
            ) from error

        private_registration_token = config.get("private_registration_token")
        if (
            not isinstance(private_registration_token, str)
            or not private_registration_token.strip()
        ):
            raise RuntimeError(
                "private_registration_token is required in VIRGO_CONFIG_FILE"
            )

        business_api_token = config.get("business_api_token")
        if not isinstance(business_api_token, str) or not business_api_token.strip():
            raise RuntimeError("business_api_token is required in VIRGO_CONFIG_FILE")

        online_window = config.get("device_online_window_seconds", 300)
        if (
            isinstance(online_window, bool)
            or not isinstance(online_window, int)
            or online_window <= 0
        ):
            raise RuntimeError(
                "device_online_window_seconds must be a positive integer"
            )

        mms_tolerance = config.get("mms_webhook_timestamp_tolerance_seconds", 300)
        if (
            isinstance(mms_tolerance, bool)
            or not isinstance(mms_tolerance, int)
            or mms_tolerance <= 0
        ):
            raise RuntimeError(
                "mms_webhook_timestamp_tolerance_seconds must be a positive integer"
            )

        return cls(
            database_url=database_url,
            private_registration_token=private_registration_token,
            business_api_token=business_api_token,
            device_online_window_seconds=online_window,
            mms_webhook_signing_key=str(config.get("mms_webhook_signing_key", "") or ""),
            mms_webhook_timestamp_tolerance_seconds=mms_tolerance,
            s3_endpoint_url=str(config.get("s3_endpoint_url", "") or ""),
            s3_region=str(config.get("s3_region", "us-east-1") or "us-east-1"),
            s3_bucket=str(config.get("s3_bucket", "") or ""),
            s3_access_key_id=str(config.get("s3_access_key_id", "") or ""),
            s3_secret_access_key=str(config.get("s3_secret_access_key", "") or ""),
            s3_public_base_url=str(config.get("s3_public_base_url", "") or ""),
            supervisor_username=os.getenv("SUPERVISOR_USERNAME", ""),
            supervisor_password=os.getenv("SUPERVISOR_PASSWORD", ""),
            supervisor_session_secret=os.getenv("SUPERVISOR_SESSION_SECRET", ""),
            supervisor_timezone=os.getenv("SUPERVISOR_TIMEZONE", "America/Vancouver"),
        )
