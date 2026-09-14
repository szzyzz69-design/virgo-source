# Device Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `POST /mobile/v1/device` with private-token authentication, Android-compatible JSON, uniform errors, and atomic PostgreSQL persistence of devices and SIM cards.

**Architecture:** Keep FastAPI as the HTTP layer and use Psycopg 3 directly against the existing SQL schema. Split configuration, security helpers, DTO validation, request/error handling, persistence, and routing into focused modules so later mobile endpoints can reuse them. Unit/API tests use an injected recording service; PostgreSQL integration tests use the real Docker database.

**Tech Stack:** Python 3.12, FastAPI 0.137, Pydantic 2, Psycopg 3, PostgreSQL 17, pytest, HTTPX, Docker Compose

---

The workspace is not a Git repository. Commit steps are intentionally omitted because `git commit` cannot succeed here.

## File map

- Create `requirements.txt`: production dependencies.
- Create `requirements-dev.txt`: test dependencies.
- Create `.env.example`: documented local environment variables without a real secret.
- Create `app/__init__.py`: application package marker.
- Create `app/config.py`: environment-backed immutable settings.
- Create `app/security.py`: token comparison and deterministic/slow hashes.
- Create `app/schemas/device.py`: Android-compatible registration DTOs and validation.
- Create `app/errors.py`: request IDs and uniform HTTP error handling.
- Create `app/database.py`: Psycopg connection/transaction boundary.
- Create `app/services/device_service.py`: credential generation and atomic device/SIM inserts.
- Create `app/api/device.py`: registration authentication and route mapping.
- Create `app/application.py`: injectable FastAPI application factory.
- Replace `main.py`: production application entry point.
- Create tests under `tests/`: configuration, security, schemas, HTTP contract, and PostgreSQL integration.

### Task 1: Add dependencies and configuration

**Files:**
- Create: `requirements.txt`
- Create: `requirements-dev.txt`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing configuration tests**

```python
# tests/test_config.py
import pytest

from app.config import Settings


def test_settings_reads_required_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("PRIVATE_REGISTRATION_TOKEN", "registration-secret")

    settings = Settings.from_env()

    assert settings.database_url == "postgresql://db/example"
    assert settings.private_registration_token == "registration-secret"


@pytest.mark.parametrize("missing", ["DATABASE_URL", "PRIVATE_REGISTRATION_TOKEN"])
def test_settings_rejects_missing_environment(monkeypatch, missing):
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("PRIVATE_REGISTRATION_TOKEN", "registration-secret")
    monkeypatch.delenv(missing)

    with pytest.raises(RuntimeError, match=missing):
        Settings.from_env()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q
```

Expected: collection fails because `app.config` does not exist.

- [ ] **Step 3: Declare dependencies and implement settings**

```text
# requirements.txt
fastapi>=0.137,<1
psycopg[binary]>=3.2,<4
uvicorn>=0.30,<1
```

```text
# requirements-dev.txt
-r requirements.txt
httpx>=0.27,<1
pytest>=8,<10
```

```dotenv
# .env.example
DATABASE_URL=postgresql://admin:admin@127.0.0.1:5433/virgo_pg
PRIVATE_REGISTRATION_TOKEN=replace-with-a-long-random-secret
```

```python
# app/config.py
from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    private_registration_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        values: dict[str, str] = {}
        for name in ("DATABASE_URL", "PRIVATE_REGISTRATION_TOKEN"):
            value = os.getenv(name)
            if not value:
                raise RuntimeError(f"{name} is required")
            values[name] = value
        return cls(
            database_url=values["DATABASE_URL"],
            private_registration_token=values["PRIVATE_REGISTRATION_TOKEN"],
        )
```

Create empty `app/__init__.py`.

- [ ] **Step 4: Install dependencies**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Expected: exit code 0 and Psycopg, pytest, HTTPX, and Uvicorn are installed.

- [ ] **Step 5: Run the tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q
```

Expected: 3 tests pass.

### Task 2: Add security helpers

**Files:**
- Create: `app/security.py`
- Test: `tests/test_security.py`

- [ ] **Step 1: Write failing security tests**

```python
# tests/test_security.py
import hashlib

from app.security import hash_password, hash_sha256, secure_equals


def test_sha256_hash_is_deterministic():
    assert hash_sha256("secret") == hashlib.sha256(b"secret").hexdigest()


def test_password_hash_is_salted_and_does_not_contain_plaintext():
    first = hash_password("initial-password")
    second = hash_password("initial-password")

    assert first.startswith("pbkdf2_sha256$")
    assert first != second
    assert "initial-password" not in first


def test_secure_equals_requires_exact_match():
    assert secure_equals("expected", "expected") is True
    assert secure_equals("expected", "wrong") is False
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_security.py -q
```

Expected: collection fails because `app.security` does not exist.

- [ ] **Step 3: Implement security helpers**

```python
# app/security.py
import base64
import hashlib
import secrets


def hash_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(value: str, iterations: int = 600_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, iterations)
    return "$".join(
        (
            "pbkdf2_sha256",
            str(iterations),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def secure_equals(expected: str, supplied: str) -> bool:
    return secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_security.py -q
```

Expected: 3 tests pass.

### Task 3: Define and validate Android-compatible DTOs

**Files:**
- Create: `app/schemas/__init__.py`
- Create: `app/schemas/device.py`
- Test: `tests/test_device_schema.py`

- [ ] **Step 1: Write failing schema tests**

```python
# tests/test_device_schema.py
import pytest
from pydantic import ValidationError

from app.schemas.device import DeviceRegisterRequest


def valid_body():
    return {
        "name": " Samsung/SM-G9910 ",
        "pushToken": None,
        "simCards": [
            {
                "slotIndex": 0,
                "simNumber": 1,
                "phoneNumber": "***1234",
                "carrierName": "carrier",
                "iccid": "iccid-value",
            }
        ],
    }


def test_request_preserves_android_aliases_and_normalizes_name():
    request = DeviceRegisterRequest.model_validate(valid_body())

    assert request.name == "Samsung/SM-G9910"
    assert request.sim_cards[0].slot_index == 0
    assert request.model_dump(by_alias=True)["simCards"][0]["simNumber"] == 1


def test_sim_cards_is_required_but_may_be_empty():
    with pytest.raises(ValidationError):
        DeviceRegisterRequest.model_validate({"name": "phone"})
    assert DeviceRegisterRequest.model_validate({"name": "phone", "simCards": []}).sim_cards == []


@pytest.mark.parametrize("field", ["slotIndex", "simNumber"])
def test_request_rejects_duplicate_sim_identity(field):
    body = valid_body()
    duplicate = dict(body["simCards"][0])
    duplicate["slotIndex"] = 1
    duplicate["simNumber"] = 2
    duplicate[field] = body["simCards"][0][field]
    body["simCards"].append(duplicate)

    with pytest.raises(ValidationError, match=field):
        DeviceRegisterRequest.model_validate(body)


def test_request_ignores_unknown_fields():
    body = valid_body()
    body["futureField"] = "ignored"
    request = DeviceRegisterRequest.model_validate(body)
    assert "futureField" not in request.model_dump()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_schema.py -q
```

Expected: collection fails because `app.schemas.device` does not exist.

- [ ] **Step 3: Implement the DTOs and validators**

```python
# app/schemas/device.py
from typing import Annotated

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SimCardRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    slot_index: int = Field(alias="slotIndex", ge=0)
    sim_number: int = Field(alias="simNumber", ge=1)
    phone_number: str | None = Field(default=None, alias="phoneNumber")
    carrier_name: str | None = Field(default=None, alias="carrierName")
    iccid: str | None = None


class DeviceRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: NonEmptyName
    push_token: str | None = Field(default=None, alias="pushToken")
    sim_cards: list[SimCardRequest] = Field(alias="simCards")

    @model_validator(mode="after")
    def reject_duplicate_sim_identity(self) -> "DeviceRegisterRequest":
        for attribute, alias in (("slot_index", "slotIndex"), ("sim_number", "simNumber")):
            values = [getattr(sim, attribute) for sim in self.sim_cards]
            if len(values) != len(set(values)):
                raise ValueError(f"simCards contains duplicate {alias}")
        return self


class DeviceRegisterResponse(BaseModel):
    id: str
    token: str
    login: str
    password: str | None
```

Create empty `app/schemas/__init__.py`.

- [ ] **Step 4: Run the tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_schema.py -q
```

Expected: 5 tests pass.

### Task 4: Add persistence and atomic registration service

**Files:**
- Create: `app/services/__init__.py`
- Create: `app/database.py`
- Create: `app/services/device_service.py`
- Test: `tests/conftest.py`
- Test: `tests/integration/test_device_service.py`

- [ ] **Step 1: Start and inspect the real PostgreSQL service**

Run:

```powershell
docker compose up -d --wait
docker compose exec -T postgres psql -U admin -d virgo_pg -Atc "SELECT to_regclass('public.devices'), to_regclass('public.sim_cards');"
```

Expected: container is healthy and output identifies both tables.

- [ ] **Step 2: Write failing integration tests**

```python
# tests/conftest.py
import os

import psycopg
import pytest


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://admin:admin@127.0.0.1:5433/virgo_pg",
)


@pytest.fixture
def clean_database():
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("TRUNCATE sim_cards, devices CASCADE")
    yield TEST_DATABASE_URL
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("TRUNCATE sim_cards, devices CASCADE")
```

```python
# tests/integration/test_device_service.py
import hashlib

import psycopg
import pytest

from app.database import Database
from app.schemas.device import DeviceRegisterRequest, SimCardRequest
from app.services.device_service import DeviceService


def valid_request():
    return DeviceRegisterRequest.model_validate(
        {
            "name": "Samsung/SM-G9910",
            "pushToken": "push-value",
            "simCards": [
                {
                    "slotIndex": 0,
                    "simNumber": 1,
                    "phoneNumber": "***1234",
                    "carrierName": "carrier",
                    "iccid": "raw-iccid",
                }
            ],
        }
    )


def test_register_persists_device_and_sim_without_plaintext(clean_database):
    service = DeviceService(Database(clean_database))
    response = service.register(valid_request())

    with psycopg.connect(clean_database) as connection:
        device = connection.execute(
            "SELECT name, push_token, token_hash, password_hash, status, enabled, last_seen_at FROM devices WHERE id = %s",
            (response.id,),
        ).fetchone()
        sim = connection.execute(
            "SELECT slot_index, sim_number, phone_number, carrier_name, iccid_hash, status FROM sim_cards WHERE device_id = %s",
            (response.id,),
        ).fetchone()

    assert device[0:2] == ("Samsung/SM-G9910", "push-value")
    assert device[2] == hashlib.sha256(response.token.encode()).hexdigest()
    assert response.token not in device[2]
    assert response.password not in device[3]
    assert device[4:6] == ("online", True)
    assert isinstance(device[6], int)
    assert sim == (0, 1, "***1234", "carrier", hashlib.sha256(b"raw-iccid").hexdigest(), "active")


def test_register_rolls_back_device_when_sim_insert_fails(clean_database):
    service = DeviceService(Database(clean_database))
    request = DeviceRegisterRequest.model_construct(
        name="phone",
        push_token=None,
        sim_cards=[
            SimCardRequest(slotIndex=0, simNumber=1),
            SimCardRequest(slotIndex=0, simNumber=2),
        ],
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        service.register(request)

    with psycopg.connect(clean_database) as connection:
        assert connection.execute("SELECT count(*) FROM devices").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM sim_cards").fetchone()[0] == 0
```

- [ ] **Step 3: Run the integration tests and verify RED**

Run:

```powershell
$env:TEST_DATABASE_URL='postgresql://admin:admin@127.0.0.1:5433/virgo_pg'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_service.py -q
```

Expected: collection fails because the database/service modules do not exist.

- [ ] **Step 4: Implement the database boundary**

```python
# app/database.py
from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from psycopg import Connection


class Database:
    def __init__(self, dsn: str):
        self._dsn = dsn

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        with psycopg.connect(self._dsn) as connection:
            with connection.transaction():
                yield connection
```

- [ ] **Step 5: Implement atomic registration**

```python
# app/services/device_service.py
from dataclasses import dataclass
import secrets
import time

from psycopg.errors import UniqueViolation

from app.database import Database
from app.schemas.device import DeviceRegisterRequest, DeviceRegisterResponse
from app.security import hash_password, hash_sha256


IDENTITY_CONSTRAINTS = {"devices_pkey", "devices_token_hash_key", "devices_login_key"}


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
        for attempt in range(3):
            identity = self._generate_identity()
            try:
                self._persist(request, identity)
            except UniqueViolation as error:
                if error.diag.constraint_name in IDENTITY_CONSTRAINTS and attempt < 2:
                    continue
                raise
            return DeviceRegisterResponse(
                id=identity.device_id,
                token=identity.token,
                login=identity.login,
                password=identity.password,
            )
        raise RuntimeError("device identity generation exhausted")

    def _generate_identity(self) -> GeneratedIdentity:
        suffix = secrets.token_hex(16)
        return GeneratedIdentity(
            device_id=f"dev_{suffix}",
            token=secrets.token_urlsafe(32),
            login=f"device-{suffix}",
            password=secrets.token_urlsafe(24),
        )

    def _persist(self, request: DeviceRegisterRequest, identity: GeneratedIdentity) -> None:
        now = time.time_ns() // 1_000_000
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
                    hash_sha256(identity.token),
                    identity.login,
                    hash_password(identity.password),
                    now,
                    now,
                    now,
                    now,
                ),
            )
            for sim in request.sim_cards:
                connection.execute(
                    """
                    INSERT INTO sim_cards (
                        id, device_id, slot_index, sim_number, phone_number,
                        carrier_name, iccid_hash, enabled, status, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, 'active', %s, %s)
                    """,
                    (
                        f"sim_{secrets.token_hex(16)}",
                        identity.device_id,
                        sim.slot_index,
                        sim.sim_number,
                        sim.phone_number,
                        sim.carrier_name,
                        hash_sha256(sim.iccid) if sim.iccid else None,
                        now,
                        now,
                    ),
                )
```

Create empty `app/services/__init__.py`.

- [ ] **Step 6: Run the integration tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_service.py -q
```

Expected: 2 tests pass.

### Task 5: Add request IDs, uniform errors, and the registration route

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/device.py`
- Create: `app/errors.py`
- Create: `app/application.py`
- Test: `tests/test_device_api.py`

- [ ] **Step 1: Write failing API contract tests**

```python
# tests/test_device_api.py
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.schemas.device import DeviceRegisterResponse


class RecordingDeviceService:
    def __init__(self):
        self.requests = []

    def register(self, request):
        self.requests.append(request)
        return DeviceRegisterResponse(
            id="dev_test",
            token="device-token",
            login="device-test",
            password="initial-password",
        )


def client_and_service():
    service = RecordingDeviceService()
    app = create_app(
        Settings("postgresql://unused", "registration-secret"),
        device_service=service,
    )
    return TestClient(app, raise_server_exceptions=False), service


def valid_body():
    return {
        "name": "phone",
        "simCards": [{"slotIndex": 0, "simNumber": 1}],
        "futureField": "ignored",
    }


def test_registration_returns_android_contract_and_201():
    client, service = client_and_service()
    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json=valid_body(),
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": "dev_test",
        "token": "device-token",
        "login": "device-test",
        "password": "initial-password",
    }
    assert service.requests[0].name == "phone"
    assert response.headers["X-Request-ID"].startswith("req_")


def test_registration_rejects_missing_or_wrong_authentication():
    client, service = client_and_service()
    for authorization in (None, "Code registration-secret", "Bearer wrong"):
        headers = {} if authorization is None else {"Authorization": authorization}
        response = client.post("/mobile/v1/device", headers=headers, json=valid_body())
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"
        assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert service.requests == []


def test_registration_maps_validation_errors_to_400():
    client, service = client_and_service()
    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json={"name": "phone"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["details"] is not None
    assert service.requests == []


def test_registration_hides_unexpected_internal_errors():
    class FailingService:
        def register(self, request):
            raise RuntimeError("postgresql://admin:secret@localhost/database")

    app = create_app(
        Settings("postgresql://unused", "registration-secret"),
        device_service=FailingService(),
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json=valid_body(),
    )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "postgresql" not in response.text
    assert "secret" not in response.text
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_api.py -q
```

Expected: collection fails because `app.application` does not exist.

- [ ] **Step 3: Implement request IDs and error handlers**

```python
# app/errors.py
from dataclasses import dataclass
import secrets

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass(slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    details: object | None = None


def request_id(request: Request) -> str:
    return request.state.request_id


def error_response(request: Request, status_code: int, code: str, message: str, details=None):
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "requestId": request_id(request), "details": details},
    )


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "").strip()
        request.state.request_id = supplied[:128] if supplied else f"req_{secrets.token_hex(12)}"
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError):
        return error_response(request, error.status_code, error.code, error.message, error.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, error: RequestValidationError):
        details = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in error.errors()
        ]
        return error_response(request, 400, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception):
        return error_response(request, 500, "INTERNAL_ERROR", "An internal error occurred")
```

- [ ] **Step 4: Implement the authenticated registration route**

```python
# app/api/device.py
from typing import Protocol

from fastapi import APIRouter, Header

from app.errors import ApiError
from app.schemas.device import DeviceRegisterRequest, DeviceRegisterResponse
from app.security import secure_equals


class DeviceRegistrationService(Protocol):
    def register(self, request: DeviceRegisterRequest) -> DeviceRegisterResponse: ...


def create_device_router(private_registration_token: str, service: DeviceRegistrationService) -> APIRouter:
    router = APIRouter(prefix="/mobile/v1", tags=["mobile-device"])

    @router.post("/device", response_model=DeviceRegisterResponse, status_code=201)
    def register_device(
        body: DeviceRegisterRequest,
        authorization: str | None = Header(default=None),
    ) -> DeviceRegisterResponse:
        scheme, separator, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or separator != " " or not supplied or not secure_equals(
            private_registration_token, supplied
        ):
            raise ApiError(401, "UNAUTHORIZED", "Invalid registration token")
        return service.register(body)

    return router
```

- [ ] **Step 5: Implement the injectable application factory**

```python
# app/application.py
from fastapi import FastAPI

from app.api.device import DeviceRegistrationService, create_device_router
from app.config import Settings
from app.database import Database
from app.errors import install_error_handling
from app.services.device_service import DeviceService


def create_app(
    settings: Settings,
    device_service: DeviceRegistrationService | None = None,
) -> FastAPI:
    app = FastAPI(title="Virgo SMS Gateway")
    install_error_handling(app)
    service = device_service or DeviceService(Database(settings.database_url))
    app.include_router(create_device_router(settings.private_registration_token, service))
    return app
```

Create empty `app/api/__init__.py`.

- [ ] **Step 6: Run the API tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_device_api.py -q
```

Expected: 4 tests pass.

### Task 6: Wire the production entry point and run end-to-end verification

**Files:**
- Replace: `main.py`
- Test: `tests/integration/test_device_registration_api.py`

- [ ] **Step 1: Write a failing real-database HTTP test**

```python
# tests/integration/test_device_registration_api.py
import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_http_registration_is_persisted_in_postgresql(clean_database):
    app = create_app(Settings(clean_database, "registration-secret"))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/mobile/v1/device",
        headers={"Authorization": "Bearer registration-secret"},
        json={
            "name": "integration-phone",
            "pushToken": None,
            "simCards": [{"slotIndex": 0, "simNumber": 1}],
        },
    )

    assert response.status_code == 201
    with psycopg.connect(clean_database) as connection:
        assert connection.execute("SELECT count(*) FROM devices").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM sim_cards").fetchone()[0] == 1
```

- [ ] **Step 2: Replace the memory-backed entry point**

```python
# main.py
from app.application import create_app
from app.config import Settings


app = create_app(Settings.from_env())
```

- [ ] **Step 3: Run the real-database HTTP test**

Run:

```powershell
$env:TEST_DATABASE_URL='postgresql://admin:admin@127.0.0.1:5433/virgo_pg'
.\.venv\Scripts\python.exe -m pytest tests\integration\test_device_registration_api.py -q
```

Expected: 1 test passes.

- [ ] **Step 4: Run the complete test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all configuration, security, schema, API, service integration, and HTTP integration tests pass with zero failures.

- [ ] **Step 5: Verify Compose and database health**

Run:

```powershell
docker compose config --quiet
docker compose ps
docker compose exec -T postgres psql -U admin -d virgo_pg -Atc "SELECT current_database() || ':' || current_user;"
```

Expected: Compose validation exits 0, `virgo-postgres` is healthy, and PostgreSQL prints `virgo_pg:admin`.

- [ ] **Step 6: Run a production import smoke test**

Run:

```powershell
$env:DATABASE_URL='postgresql://admin:admin@127.0.0.1:5433/virgo_pg'
$env:PRIVATE_REGISTRATION_TOKEN='registration-secret'
.\.venv\Scripts\python.exe -c "from main import app; assert app.title == 'Virgo SMS Gateway'; print('application import ok')"
```

Expected: `application import ok` and exit code 0.

## Final requirements audit

- `POST /mobile/v1/device` is the only new endpoint.
- Only private Bearer registration is accepted.
- Configuration comes from `DATABASE_URL` and `PRIVATE_REGISTRATION_TOKEN`.
- Android field aliases and the four-field response are preserved.
- Validation failures return 400; authentication returns 401; internal failures return sanitized 500 responses.
- Every response receives `X-Request-ID`; error bodies include the same ID.
- Device and SIM inserts share one database transaction.
- Only hashes of device Token, password, and ICCID are stored.
- Docker PostgreSQL integration and rollback are tested.
