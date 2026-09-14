from dataclasses import dataclass

from app.database import Database
from app.security import hash_sha256


class InvalidDeviceToken(Exception):
    pass


class DeviceDisabled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedDevice:
    id: str
    enabled: bool
    status: str


class DeviceAuthService:
    def __init__(self, database: Database):
        self._database = database

    def authenticate(self, token: str) -> AuthenticatedDevice:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT id, enabled, status FROM devices WHERE token_hash = %s",
                (hash_sha256(token),),
            ).fetchone()

        if row is None:
            raise InvalidDeviceToken

        device = AuthenticatedDevice(*row)
        if not device.enabled:
            raise DeviceDisabled
        return device
