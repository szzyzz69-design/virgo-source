# Device Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement authenticated `PATCH /mobile/v1/device` with three-state Push Token updates and atomic SIM snapshot synchronization.

**Architecture:** Add a reusable database-backed device authentication service, dedicated PATCH DTOs, and an update method on the existing device service. Refactor the device router so registration and device Token authentication remain separate domains and authentication always precedes JSON parsing. Keep all device/SIM mutations in one PostgreSQL transaction.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, Psycopg 3, PostgreSQL 17, pytest, HTTPX, Docker Compose

---

The workspace is not a Git repository. Commit steps are omitted because they cannot succeed.

## File map

- Modify `app/schemas/device.py`: add update request/response DTOs.
- Create `app/services/device_auth_service.py`: Token-hash lookup and device authorization context.
- Modify `app/services/device_service.py`: add atomic device/SIM update behavior.
- Modify `app/api/device.py`: separate POST registration auth from PATCH device auth and add PATCH route.
- Modify `app/application.py`: construct and inject `DeviceAuthService`.
- Modify `tests/test_device_schema.py`: update DTO behavior.
- Create `tests/integration/test_device_auth_service.py`: real database authentication tests.
- Create `tests/integration/test_device_update_service.py`: real database update/sync tests.
- Modify `tests/test_device_api.py`: PATCH HTTP contract and error mapping tests.
- Modify `tests/integration/test_device_registration_api.py`: production registration-then-update integration test.

### Task 1: Define PATCH DTOs

**Files:**
- Modify: `app/schemas/device.py`
- Modify: `tests/test_device_schema.py`

- [ ] **Step 1: Write failing DTO tests**

Add tests that demonstrate all PATCH states:

```python
from pydantic import ValidationError

from app.schemas.device import DeviceUpdateRequest, DeviceUpdateResponse


def test_device_update_distinguishes_omitted_null_and_value_fields():
    omitted = DeviceUpdateRequest.model_validate({"id": "dev_1"})
    cleared = DeviceUpdateRequest.model_validate(
        {"id": "dev_1", "pushToken": None, "simCards": None}
    )
    supplied = DeviceUpdateRequest.model_validate(
        {"id": "dev_1", "pushToken": "new", "simCards": []}
    )

    assert "push_token" not in omitted.model_fields_set
    assert "sim_cards" not in omitted.model_fields_set
    assert cleared.push_token is None
    assert cleared.sim_cards is None
    assert {"push_token", "sim_cards"} <= cleared.model_fields_set
    assert supplied.push_token == "new"
    assert supplied.sim_cards == []
    assert DeviceUpdateResponse().model_dump() == {"ok": True}


def test_device_update_rejects_invalid_id_and_duplicate_sims():
    with pytest.raises(ValidationError):
        DeviceUpdateRequest.model_validate({"id": "   "})
    with pytest.raises(ValidationError):
        DeviceUpdateRequest.model_validate({"id": "x" * 65})
    with pytest.raises(ValidationError, match="duplicate slotIndex"):
        DeviceUpdateRequest.model_validate(
            {
                "id": "dev_1",
                "simCards": [
                    {"slotIndex": 0, "simNumber": 1},
                    {"slotIndex": 0, "simNumber": 2},
                ],
            }
        )
```

- [ ] **Step 2: Run the DTO tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_schema.py -q
```

Expected: import fails because `DeviceUpdateRequest` and `DeviceUpdateResponse` do not exist.

- [ ] **Step 3: Implement update DTOs**

Add to `app/schemas/device.py`:

```python
DeviceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]


class DeviceUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: DeviceId
    push_token: str | None = Field(default=None, alias="pushToken")
    sim_cards: list[SimCardRequest] | None = Field(default=None, alias="simCards")

    @model_validator(mode="after")
    def reject_duplicate_sim_identity(self) -> "DeviceUpdateRequest":
        if self.sim_cards is None:
            return self
        for attribute, alias in (
            ("slot_index", "slotIndex"),
            ("sim_number", "simNumber"),
        ):
            values = [getattr(sim, attribute) for sim in self.sim_cards]
            if len(values) != len(set(values)):
                raise ValueError(f"simCards contains duplicate {alias}")
        return self


class DeviceUpdateResponse(BaseModel):
    ok: bool = True
```

- [ ] **Step 4: Run DTO and existing tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_schema.py -q
```

Expected: all schema tests pass.

### Task 2: Add reusable device Token authentication

**Files:**
- Create: `app/services/device_auth_service.py`
- Create: `tests/integration/test_device_auth_service.py`

- [ ] **Step 1: Write failing authentication integration tests**

```python
import secrets

import psycopg
import pytest

from app.database import Database
from app.security import hash_sha256
from app.services.device_auth_service import (
    DeviceAuthService,
    DeviceDisabled,
    InvalidDeviceToken,
)


def seed_device(context, *, enabled=True):
    suffix = secrets.token_hex(8)
    device_id = context.track(f"dev_auth_{suffix}")
    token = f"token-{suffix}"
    with psycopg.connect(context.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (id, name, token_hash, login, enabled)
            VALUES (%s, 'auth-phone', %s, %s, %s)
            """,
            (device_id, hash_sha256(token), f"login-{suffix}", enabled),
        )
    return device_id, token


def test_authenticate_returns_device_for_token_hash(clean_database):
    device_id, token = seed_device(clean_database)
    authenticated = DeviceAuthService(Database(clean_database.dsn)).authenticate(token)
    assert authenticated.id == device_id


def test_authenticate_rejects_unknown_and_disabled_device(clean_database):
    service = DeviceAuthService(Database(clean_database.dsn))
    with pytest.raises(InvalidDeviceToken):
        service.authenticate("unknown")
    _, token = seed_device(clean_database, enabled=False)
    with pytest.raises(DeviceDisabled):
        service.authenticate(token)
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_auth_service.py -q
```

Expected: collection fails because `app.services.device_auth_service` does not exist.

- [ ] **Step 3: Implement authentication service**

```python
# app/services/device_auth_service.py
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
```

- [ ] **Step 4: Run and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_auth_service.py -q
```

Expected: authentication tests pass against PostgreSQL.

### Task 3: Implement atomic device and SIM updates

**Files:**
- Modify: `app/services/device_service.py`
- Create: `tests/integration/test_device_update_service.py`

- [ ] **Step 1: Write failing PostgreSQL update tests**

Create `seed_update_device`, `seed_update_device_with_two_sims`, `read_push_token`, `read_sim_statuses`, and `read_sims` test helpers. Each seed helper must generate IDs with `secrets.token_hex(8)`, register every device ID through `clean_database.track()`, insert with parameterized Psycopg SQL, and return the device ID. Read helpers query only the supplied device ID. Then add these core assertions:

```python
def test_update_preserves_or_changes_push_token_by_field_presence(clean_database):
    device_id = seed_update_device(clean_database, push_token="old")
    service = DeviceService(Database(clean_database.dsn))

    service.update(device_id, DeviceUpdateRequest.model_validate({"id": device_id}))
    assert read_push_token(clean_database.dsn, device_id) == "old"

    service.update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id, "pushToken": None}),
    )
    assert read_push_token(clean_database.dsn, device_id) is None

    service.update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id, "pushToken": "new"}),
    )
    assert read_push_token(clean_database.dsn, device_id) == "new"


def test_update_distinguishes_null_empty_and_nonempty_sim_snapshots(clean_database):
    device_id = seed_update_device_with_two_sims(clean_database)
    service = DeviceService(Database(clean_database.dsn))

    service.update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id, "simCards": None}),
    )
    assert read_sim_statuses(clean_database.dsn, device_id) == {0: "active", 1: "active"}

    service.update(
        device_id,
        DeviceUpdateRequest.model_validate({"id": device_id, "simCards": []}),
    )
    assert read_sim_statuses(clean_database.dsn, device_id) == {0: "inactive", 1: "inactive"}

    service.update(
        device_id,
        DeviceUpdateRequest.model_validate(
            {
                "id": device_id,
                "simCards": [
                    {
                        "slotIndex": 0,
                        "simNumber": 1,
                        "phoneNumber": "new-phone",
                        "carrierName": "new-carrier",
                        "iccid": "new-iccid",
                    },
                    {"slotIndex": 2, "simNumber": 3},
                ],
            }
        ),
    )
    rows = read_sims(clean_database.dsn, device_id)
    assert rows[0]["status"] == "active"
    assert rows[1]["status"] == "inactive"
    assert rows[2]["status"] == "active"
```

Add explicit authorization and rollback tests:

```python
def test_update_rejects_mismatched_device_without_partial_writes(clean_database):
    device_id = seed_update_device(clean_database, push_token="old")
    service = DeviceService(Database(clean_database.dsn))
    request = DeviceUpdateRequest.model_validate(
        {"id": "dev_other", "pushToken": "new", "simCards": []}
    )
    with pytest.raises(DeviceOwnershipMismatch):
        service.update(device_id, request)
    assert read_push_token(clean_database.dsn, device_id) == "old"


def test_update_rechecks_disabled_device_inside_transaction(clean_database):
    device_id = seed_update_device(clean_database, push_token="old", enabled=False)
    service = DeviceService(Database(clean_database.dsn))
    with pytest.raises(DeviceDisabled):
        service.update(
            device_id,
            DeviceUpdateRequest.model_validate(
                {"id": device_id, "pushToken": "new"}
            ),
        )
    assert read_push_token(clean_database.dsn, device_id) == "old"


def test_update_rolls_back_device_when_sim_number_conflicts(clean_database):
    device_id = seed_update_device_with_two_sims(clean_database, push_token="old")
    before = read_sims(clean_database.dsn, device_id)
    service = DeviceService(Database(clean_database.dsn))
    request = DeviceUpdateRequest.model_validate(
        {
            "id": device_id,
            "pushToken": "new",
            "simCards": [{"slotIndex": 2, "simNumber": 1}],
        }
    )
    with pytest.raises(DeviceStateConflict):
        service.update(device_id, request)
    assert read_push_token(clean_database.dsn, device_id) == "old"
    assert read_sims(clean_database.dsn, device_id) == before
```

Assert existing SIM `id`, `created_at`, `last_used_at`, and `enabled=false` survive an Upsert; assert `last_seen_at/updated_at` fall between before/after UTC Unix millisecond timestamps.

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_update_service.py -q
```

Expected: tests fail because `DeviceService.update` and update domain exceptions do not exist.

- [ ] **Step 3: Implement domain exceptions and update method**

Add to `app/services/device_service.py`:

```python
from psycopg.errors import UniqueViolation

from app.schemas.device import DeviceUpdateRequest, DeviceUpdateResponse
from app.services.device_auth_service import DeviceDisabled, InvalidDeviceToken


class DeviceOwnershipMismatch(Exception):
    pass


class DeviceStateConflict(Exception):
    pass


SIM_STATE_CONSTRAINTS = {
    "uq_sim_device_number",
    "uq_sim_device_slot",
    "uq_sim_device_subscription",
}


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
            if error.diag.constraint_name in SIM_STATE_CONSTRAINTS:
                raise DeviceStateConflict from error
            if error.diag.constraint_name != "sim_cards_pkey" or attempt == 3:
                raise
            attempt += 1
            continue
        return DeviceUpdateResponse()
```

Implement `_update_once` with parameterized SQL:

```python
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
            UPDATE devices SET push_token = %s, status = 'online',
                last_seen_at = %s, updated_at = %s WHERE id = %s
            """,
            (request.push_token, now, now, authenticated_device_id),
        )
    else:
        connection.execute(
            """
            UPDATE devices SET status = 'online', last_seen_at = %s,
                updated_at = %s WHERE id = %s
            """,
            (now, now, authenticated_device_id),
        )

    if sim_rows is not None:
        connection.execute(
            "UPDATE sim_cards SET status = 'inactive', updated_at = %s WHERE device_id = %s",
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
```

- [ ] **Step 4: Run and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_update_service.py -q
```

Expected: all update and rollback tests pass.

### Task 4: Add PATCH route and HTTP error mapping

**Files:**
- Modify: `app/api/device.py`
- Modify: `app/application.py`
- Modify: `tests/test_device_api.py`

- [ ] **Step 1: Write failing PATCH API tests**

Extend the recording service with `update()` and inject a recording authentication service. Test:

```python
def test_patch_authenticates_then_updates_and_returns_ok():
    response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"id": "dev_test", "simCards": []},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert auth_service.tokens == ["device-token"]
    assert service.updates[0][0] == "dev_test"


@pytest.mark.parametrize(
    ("auth_error", "status", "code"),
    [
        (InvalidDeviceToken(), 401, "UNAUTHORIZED"),
        (DeviceDisabled(), 403, "FORBIDDEN"),
    ],
)
def test_patch_maps_authentication_errors(auth_error, status, code):
    client, service, _ = update_client(auth_error=auth_error)
    response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"id": "dev_test"},
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert service.updates == []


def test_patch_authentication_precedes_malformed_body():
    client, service, auth_service = update_client(auth_error=InvalidDeviceToken())
    response = client.patch(
        "/mobile/v1/device",
        headers={
            "Authorization": "Bearer wrong",
            "Content-Type": "application/json",
        },
        content="{not-json",
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert service.updates == []


def test_patch_maps_ownership_mismatch_to_403():
    client, service, _ = update_client(update_error=DeviceOwnershipMismatch())
    response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"id": "dev_other"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_patch_maps_state_conflict_to_409():
    client, service, _ = update_client(update_error=DeviceStateConflict())
    response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"id": "dev_test", "simCards": []},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "STATE_CONFLICT"


def test_registration_token_and_device_token_are_not_interchangeable():
    client, service, _ = update_client()
    patch_response = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json={"id": "dev_test"},
    )
    post_response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer device-token"},
        json={"name": "phone", "simCards": []},
    )
    assert patch_response.status_code == 401
    assert post_response.status_code == 401
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_api.py -q
```

Expected: PATCH returns 405 and application factory does not accept an authentication service.

- [ ] **Step 3: Refactor JSON parsing and authentication dependencies**

In `app/api/device.py`, replace the router-wide registration dependency with dependency chains:

```python
def authenticate_registration(
    authorization: str | None = Header(default=None),
) -> None:
    scheme, separator, supplied = (authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or separator != " "
        or not supplied
        or not secure_equals(private_registration_token, supplied)
    ):
        raise ApiError(401, "UNAUTHORIZED", "Invalid registration token")

def authenticate_device(authorization: str | None = Header(default=None)):
    scheme, separator, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not supplied:
        raise ApiError(401, "UNAUTHORIZED", "Invalid device token")
    try:
        return auth_service.authenticate(supplied)
    except InvalidDeviceToken as error:
        raise ApiError(401, "UNAUTHORIZED", "Invalid device token") from error
    except DeviceDisabled as error:
        raise ApiError(403, "FORBIDDEN", "Device is disabled") from error

async def authenticated_register_request(
    request: Request,
    _: None = Depends(authenticate_registration),
) -> DeviceRegisterRequest:
    return await parse_json_model(request, DeviceRegisterRequest)

async def authenticated_update_request(
    request: Request,
    device: AuthenticatedDevice = Depends(authenticate_device),
) -> AuthenticatedUpdateRequest:
    body = await parse_json_model(request, DeviceUpdateRequest)
    return AuthenticatedUpdateRequest(device, body)
```

Make `parse_json_model()` generic while preserving current Content-Type, UTF-8, JSON, and Pydantic error behavior. Add POST and PATCH routes without global authentication dependencies. Catch `DeviceOwnershipMismatch`, `DeviceDisabled`, `InvalidDeviceToken`, and `DeviceStateConflict` at the HTTP boundary and map them to the specified error contract.

Extend `create_app()`:

```python
def create_app(
    settings: Settings,
    device_service: DeviceRegistrationService | None = None,
    device_auth_service: DeviceAuthenticationService | None = None,
) -> FastAPI:
    database = Database(settings.database_url)
    service = device_service or DeviceService(database)
    auth_service = device_auth_service or DeviceAuthService(database)
    app.include_router(
        create_device_router(
            settings.private_registration_token,
            service,
            auth_service,
        )
    )
```

- [ ] **Step 4: Run API tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_api.py -q
```

Expected: existing POST tests and new PATCH tests all pass.

### Task 5: Verify production registration-then-update flow

**Files:**
- Modify: `tests/integration/test_device_registration_api.py`

- [ ] **Step 1: Add a failing production integration test**

Use the existing isolated `main` import and safe Push Token cleanup pattern:

```python
def test_production_app_registers_then_updates_device_and_sims(
    clean_database,
    monkeypatch,
):
    registration = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json={
            "name": "integration-phone",
            "pushToken": cleanup_marker,
            "simCards": [{"slotIndex": 0, "simNumber": 1}],
        },
    )
    credentials = registration.json()

    update = client.patch(
        "/mobile/v1/device",
        headers={"Authorization": f"Bearer {credentials['token']}"},
        json={
            "id": credentials["id"],
            "pushToken": cleanup_marker,
            "simCards": [
                {
                    "slotIndex": 0,
                    "simNumber": 1,
                    "carrierName": "updated-carrier",
                },
                {"slotIndex": 1, "simNumber": 2},
            ],
        },
    )

    assert update.status_code == 200
    assert update.json() == {"ok": True}
    # Query PostgreSQL by tracked device ID and assert online heartbeat,
    # updated carrier, and two active SIM rows.
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_registration_api.py -q
```

Expected: PATCH currently returns 405 before Task 4, then passes after the implementation is present.

- [ ] **Step 3: Run the complete verification suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
docker compose ps
docker compose exec -T postgres psql -U admin -d virgo_pg -Atc "SELECT count(*) FROM devices WHERE push_token LIKE 'pytest-%';"
```

Expected: all tests pass, PostgreSQL is healthy, and no pytest-marked device rows remain.

- [ ] **Step 4: Run a real Uvicorn/TCP smoke test**

Start Uvicorn on localhost with `DATABASE_URL` and `PRIVATE_REGISTRATION_TOKEN`, register a uniquely marked device, PATCH it with the returned Token, query PostgreSQL for the updated device/SIM state, and delete only the uniquely marked test device in `finally`.

Expected: POST returns 201, PATCH returns 200 with `{"ok": true}`, PostgreSQL reflects the update, and cleanup leaves no marked rows.

## Final requirements audit

- POST and PATCH use separate authentication domains.
- Device Token lookup uses only SHA-256 hashes.
- Disabled and mismatched devices cannot mutate state.
- Push Token and SIM nullable/omitted states match the approved design.
- SIM synchronization never deletes rows or re-enables admin-disabled SIMs.
- Device heartbeat and SIM changes are atomic.
- Conflicts roll back and map to 409.
- Existing first-interface tests remain green.
