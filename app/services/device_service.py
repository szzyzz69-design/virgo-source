from dataclasses import dataclass
import secrets
import time

from psycopg.errors import UniqueViolation

from app.database import Database
from app.schemas.device import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceUpdateRequest,
    DeviceUpdateResponse,
)
from app.security import hash_password, hash_sha256
from app.services.device_auth_service import DeviceDisabled, InvalidDeviceToken


RETRYABLE_GENERATED_ID_CONSTRAINTS = {
    "devices_pkey",
    "devices_token_hash_key",
    "devices_login_key",
    "sim_cards_pkey",
}

SIM_STATE_CONSTRAINTS = {
    "uq_sim_device_number",
    "uq_sim_device_slot",
    "uq_sim_device_subscription",
}


class DeviceOwnershipMismatch(Exception):
    pass


class DeviceStateConflict(Exception):
    pass


@dataclass(frozen=True, slots=True)
class GeneratedIdentity:
    device_id: str
    token: str
    login: str
    password: str


class DeviceService:
    def __init__(self, database: Database):
        self._database = database

    def register(self, request: DeviceRegisterRequest) -> DeviceRegisterResponse:
        attempt = 1
        while True:
            identity = self._generate_identity()
            try:
                self._persist(request, identity)
            except UniqueViolation as error:
                if (
                    error.diag.constraint_name not in RETRYABLE_GENERATED_ID_CONSTRAINTS
                    or attempt == 3
                ):
                    raise
                attempt += 1
                continue
            return DeviceRegisterResponse(
                id=identity.device_id,
                token=identity.token,
                login=identity.login,
                password=identity.password,
            )

    def _generate_identity(self) -> GeneratedIdentity:
        suffix = secrets.token_hex(16)
        return GeneratedIdentity(
            device_id=f"dev_{suffix}",
            token=secrets.token_urlsafe(32),
            login=f"device-{suffix}",
            password=secrets.token_urlsafe(24),
        )

    def _generate_sim_id(self) -> str:
        return f"sim_{secrets.token_hex(16)}"

    def update(
        self,
        authenticated_device_id: str,
        request: DeviceUpdateRequest,
    ) -> DeviceUpdateResponse:
        attempt = 1
        while True:
            try:
                self._update_once(authenticated_device_id, request)
            except UniqueViolation as error:
                constraint = error.diag.constraint_name
                if constraint in SIM_STATE_CONSTRAINTS:
                    raise DeviceStateConflict from error
                if constraint != "sim_cards_pkey" or attempt == 3:
                    raise
                attempt += 1
                continue
            return DeviceUpdateResponse()

    def _update_once(
        self,
        authenticated_device_id: str,
        request: DeviceUpdateRequest,
    ) -> None:
        now = time.time_ns() // 1_000_000
        sim_rows = None
        if request.sim_cards is not None:
            sim_rows = [
                (
                    self._generate_sim_id(),
                    authenticated_device_id,
                    sim.slot_index,
                    sim.sim_number,
                    sim.phone_number,
                    sim.carrier_name,
                    hash_sha256(sim.iccid) if sim.iccid else None,
                    now,
                    now,
                )
                for sim in request.sim_cards
            ]

        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT enabled FROM devices WHERE id = %s FOR UPDATE",
                (authenticated_device_id,),
            ).fetchone()
            if row is None:
                raise InvalidDeviceToken
            if not row[0]:
                raise DeviceDisabled
            if request.id != authenticated_device_id:
                raise DeviceOwnershipMismatch

            if "push_token" in request.model_fields_set:
                connection.execute(
                    """
                    UPDATE devices
                    SET push_token = %s, status = 'online',
                        last_seen_at = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (request.push_token, now, now, authenticated_device_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE devices
                    SET status = 'online', last_seen_at = %s, updated_at = %s
                    WHERE id = %s
                    """,
                    (now, now, authenticated_device_id),
                )

            if sim_rows is None:
                return

            connection.execute(
                """
                UPDATE sim_cards
                SET status = 'inactive', updated_at = %s
                WHERE device_id = %s
                """,
                (now, authenticated_device_id),
            )
            for sim_row in sim_rows:
                connection.execute(
                    """
                    INSERT INTO sim_cards (
                        id, device_id, slot_index, sim_number, phone_number,
                        carrier_name, iccid_hash, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
                    ON CONFLICT (device_id, slot_index) DO UPDATE SET
                        sim_number = EXCLUDED.sim_number,
                        phone_number = EXCLUDED.phone_number,
                        carrier_name = EXCLUDED.carrier_name,
                        iccid_hash = EXCLUDED.iccid_hash,
                        status = 'active',
                        updated_at = EXCLUDED.updated_at
                    """,
                    sim_row,
                )

    def _persist(
        self,
        request: DeviceRegisterRequest,
        identity: GeneratedIdentity,
    ) -> None:
        now = time.time_ns() // 1_000_000
        token_hash = hash_sha256(identity.token)
        password_hash = hash_password(identity.password)
        sim_rows = [
            (
                self._generate_sim_id(),
                identity.device_id,
                sim.slot_index,
                sim.sim_number,
                sim.phone_number,
                sim.carrier_name,
                hash_sha256(sim.iccid) if sim.iccid else None,
                now,
                now,
            )
            for sim in request.sim_cards
        ]

        with self._database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO devices (
                    id, name, push_token, token_hash, login, password_hash,
                    enabled, status, last_seen_at, registered, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, 'online', %s, %s, %s, %s)
                """,
                (
                    identity.device_id,
                    request.name,
                    request.push_token,
                    token_hash,
                    identity.login,
                    password_hash,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            for sim_row in sim_rows:
                connection.execute(
                    """
                    INSERT INTO sim_cards (
                        id, device_id, slot_index, sim_number, phone_number,
                        carrier_name, iccid_hash, enabled, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, 'active', %s, %s)
                    """,
                    sim_row,
                )
