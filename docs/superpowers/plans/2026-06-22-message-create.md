# Business Message Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `POST /business/v1/messages` with TOML-backed business authentication, global request idempotency, online device/SIM routing, and atomic outbound message persistence.

**Architecture:** Add dedicated message DTO, publisher, command service, and API router modules. Keep routing and all contact/conversation/message writes inside one Psycopg transaction protected by an advisory lock. Reuse the existing FastAPI request/error infrastructure and PostgreSQL test cleanup discipline.

**Tech Stack:** Python 3.12, `tomllib`, FastAPI, Pydantic 2, Psycopg 3, PostgreSQL 17, pytest, HTTPX, Docker Compose

---

The workspace is not a Git repository, so commit steps are omitted.

## File map

- Create `config.toml`, `config.example.toml`; modify `.gitignore` and `.env.example`.
- Modify `app/config.py` for TOML business settings.
- Create `app/schemas/message.py` for request/response and canonical digest helpers.
- Create `app/services/message_publisher.py` for the post-commit notification boundary.
- Create `app/services/message_service.py` for idempotency, routing, and persistence.
- Create `app/api/message.py` for business authentication and HTTP mapping.
- Modify `app/application.py` to wire the business router.
- Add focused unit, integration, and production-flow tests.

### Task 1: TOML business configuration

**Files:**
- Create: `config.toml`
- Create: `config.example.toml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `app/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Write failing TOML tests**

```python
def test_settings_loads_business_config_file(monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'business_api_token = "business-secret"\n'
        'device_online_window_seconds = 300\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("PRIVATE_REGISTRATION_TOKEN", "registration-secret")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", str(config))

    settings = Settings.from_env()

    assert settings.business_api_token == "business-secret"
    assert settings.device_online_window_seconds == 300


@pytest.mark.parametrize(
    "contents",
    [
        'device_online_window_seconds = 300\n',
        'business_api_token = "   "\ndevice_online_window_seconds = 300\n',
        'business_api_token = "secret"\ndevice_online_window_seconds = 0\n',
        'business_api_token = "secret"\ndevice_online_window_seconds = true\n',
    ],
)
def test_settings_rejects_invalid_business_config(monkeypatch, tmp_path, contents):
    config = tmp_path / "config.toml"
    config.write_text(contents, encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db/example")
    monkeypatch.setenv("PRIVATE_REGISTRATION_TOKEN", "registration-secret")
    monkeypatch.setenv("VIRGO_CONFIG_FILE", str(config))
    with pytest.raises(RuntimeError):
        Settings.from_env()
```

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q`

Expected: `Settings` has no business fields and ignores the TOML file.

- [ ] **Step 3: Implement configuration loading**

Extend `Settings` with defaults so existing direct constructors remain compatible:

```python
business_api_token: str = ""
device_online_window_seconds: int = 300
```

In `from_env()`, resolve `Path(os.getenv("VIRGO_CONFIG_FILE", "config.toml"))`, read with `tomllib.loads()`, reject missing/non-string/blank Token, and reject boolean/non-integer/non-positive windows. Return all four settings fields.

Create both TOML files with:

```toml
business_api_token = "local-development-business-token-change-me"
device_online_window_seconds = 300
```

Add `config.toml` to `.gitignore` and `VIRGO_CONFIG_FILE=config.toml` to `.env.example`.

- [ ] **Step 4: Run GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_config.py -q`

Expected: all configuration tests pass.

### Task 2: Message DTO, normalization, and digest

**Files:**
- Create: `app/schemas/message.py`
- Create: `tests/test_message_schema.py`

- [ ] **Step 1: Write failing DTO tests**

```python
def test_message_request_normalizes_single_phone_and_defaults():
    request = MessageCreateRequest.model_validate(
        {"phoneNumbers": ["+86 (139) 0000-0000"], "text": " hello "}
    )
    assert request.phone_numbers == ["+8613900000000"]
    assert request.text == "hello"
    assert request.with_delivery_report is True
    assert request.priority == 0


@pytest.mark.parametrize(
    "body",
    [
        {"phoneNumbers": [], "text": "hello"},
        {"phoneNumbers": ["1", "2"], "text": "hello"},
        {"phoneNumbers": ["not-phone"], "text": "hello"},
        {"phoneNumbers": ["+86139"], "text": "   "},
        {"phoneNumbers": ["+86139"], "priority": 128, "text": "hello"},
        {"phoneNumbers": ["+86139"], "withDeliveryReport": 1, "text": "hello"},
        {"phoneNumbers": ["+86139"], "metadata": [], "text": "hello"},
    ],
)
def test_message_request_rejects_invalid_payloads(body):
    with pytest.raises(ValidationError):
        MessageCreateRequest.model_validate(body)


def test_request_digest_is_stable_and_changes_with_content():
    first = MessageCreateRequest.model_validate(
        {"phoneNumbers": ["+86 13900000000"], "text": "hello", "metadata": {"b": 2, "a": 1}}
    )
    same = MessageCreateRequest.model_validate(
        {"metadata": {"a": 1, "b": 2}, "text": "hello", "phoneNumbers": ["+8613900000000"]}
    )
    changed = MessageCreateRequest.model_validate(
        {"phoneNumbers": ["+8613900000000"], "text": "changed"}
    )
    assert request_digest(first) == request_digest(same)
    assert request_digest(first) != request_digest(changed)
```

Also test timezone-aware ISO input, rejection of naive datetimes, `scheduleAt <= validUntil`, Android aliases, and response aliases.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_schema.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement DTO and helpers**

`MessageCreateRequest` uses `ConfigDict(populate_by_name=True, extra="ignore")`, strict integer/boolean fields, `AwareDatetime`, aliases from the contract, and validators described in the specification. `normalize_phone()` removes whitespace, `(`, `)`, and `-`, then matches `^\+?\d{3,20}$`. `to_utc_millis()` converts aware datetimes. `request_digest()` serializes a canonical dict with `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` and hashes UTF-8 bytes with SHA-256.

`MessageCreateResponse` exposes `id`, `state`, `deviceId`, `simNumber`, `conversationId`, and `createdAt` using aliases.

- [ ] **Step 4: Run GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_schema.py -q`

Expected: all DTO/digest tests pass.

### Task 3: Atomic message command service

**Files:**
- Create: `app/services/message_publisher.py`
- Create: `app/services/message_service.py`
- Create: `tests/integration/test_message_service.py`

- [ ] **Step 1: Write failing integration tests**

Seed online devices and active SIMs with unique tracked IDs. Cover:

```python
def test_create_message_routes_and_writes_all_records(clean_database):
    device_id, sim_id = seed_route(clean_database, last_used_at=None)
    result = service(clean_database).create(valid_request(), "order-1")
    assert result.replayed is False
    assert result.response.device_id == device_id
    assert result.response.sim_number == 1
    assert counts_for_message(clean_database.dsn, result.response.id) == {
        "messages": 1,
        "recipients": 1,
        "history": 1,
    }
    assert read_message(clean_database.dsn, result.response.id)["state"] == "Pending"
```

Add tests for specified route, oldest/never-used SIM choice, offline/expired/disabled exclusions, no route, contact/conversation reuse, explicit conversation conflicts, identical idempotent replay, different-content conflict, transaction rollback, expired `validUntil`, and publisher success/failure behavior.

For concurrency, use two threads with separate connections and the same key; assert both return the same message ID and only one message exists.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_message_service.py -q`

Expected: message service and publisher modules do not exist.

- [ ] **Step 3: Implement publisher and domain models**

```python
class MessageEnqueuedPublisher(Protocol):
    def publish(self, device_id: str, message_id: str) -> None:
        raise NotImplementedError


class NoOpMessageEnqueuedPublisher:
    def publish(self, device_id: str, message_id: str) -> None:
        return None
```

Define `IdempotencyConflict`, `MessageStateConflict`, `NoAvailableDevice`, and `MessageValidationError`. Define `MessageCreateResult(response, replayed)` and a private `Route(device_id, sim_card_id, sim_number, phone_number)`.

- [ ] **Step 4: Implement the transaction**

`MessageCommandService.create()` computes `now`, rejects expired validity, computes the digest, and opens `Database.transaction()`. Inside it:

1. Execute `SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))`.
2. Query the existing outbound message by idempotency key; compare `metadata->>'requestDigest'` and return or raise.
3. Select a route with the online cutoff and `FOR UPDATE OF devices, sim_cards SKIP LOCKED`.
4. Upsert contact by `normalized_phone_number`.
5. Validate or upsert the open conversation for the route.
6. Insert `messages`, `message_recipients`, and `message_state_history` with parameterized SQL.
7. Update conversation and contact timestamps.
8. Exit the transaction, then publish only for a first creation. Catch and log publisher errors without changing the result.

Use random `contact_`, `conv_`, and `msg_` IDs from `secrets.token_hex(16)`. Convert response milliseconds to UTC ISO strings ending in `Z`.

- [ ] **Step 5: Run GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_message_service.py -q`

Expected: all routing, idempotency, atomicity, and publisher tests pass.

### Task 4: Business API route

**Files:**
- Create: `app/api/message.py`
- Modify: `app/application.py`
- Create: `tests/test_message_api.py`

- [ ] **Step 1: Write failing API tests**

Use injected recording command service and test:

```python
def test_create_message_returns_201_then_200_for_replay():
    first = client.post(
        "/business/v1/messages",
        headers={"Authorization": "Bearer business-secret", "Idempotency-Key": "order-1"},
        json={"phoneNumbers": ["+8613900000000"], "text": "hello"},
    )
    assert first.status_code == 201
    service.result = MessageCreateResult(service.result.response, replayed=True)
    replay = client.post(
        "/business/v1/messages",
        headers={"Authorization": "Bearer business-secret", "Idempotency-Key": "order-1"},
        json={"phoneNumbers": ["+8613900000000"], "text": "hello"},
    )
    assert replay.status_code == 200
```

Also cover missing/wrong Token, authentication before malformed body, missing/blank/long idempotency key, JSON validation, Token domain separation, and mapping of all four domain errors.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_api.py -q`

Expected: route is 404 and application factory lacks message-service injection.

- [ ] **Step 3: Implement route and wiring**

Create a router with prefix `/business/v1`. A dependency validates the business Bearer Token with `secure_equals`; an authenticated body dependency reuses `parse_json_model()` so authentication precedes parsing. Normalize and validate `Idempotency-Key` in a header dependency. The endpoint calls the command service and returns `JSONResponse` with status 200 or 201 using `response.model_dump(by_alias=True, mode="json")`.

Map domain errors to `IDEMPOTENCY_CONFLICT`, `STATE_CONFLICT`, `NO_AVAILABLE_DEVICE`, and `VALIDATION_ERROR`. Extend `create_app()` with optional message service/publisher injection and default construction from settings.

- [ ] **Step 4: Run GREEN and regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_message_api.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_device_api.py tests\test_device_update_api.py -q
```

Expected: new API tests and both mobile interfaces pass.

### Task 5: Production integration and verification

**Files:**
- Create: `tests/integration/test_message_api.py`

- [ ] **Step 1: Add production-flow integration test**

Load `main.app` with a temporary TOML file and test-specific environment. Register a device, PATCH it online, call `POST /business/v1/messages`, replay the same request, and send a conflicting replay. Assert 201, 200, and 409; query all message-related tables and track the device ID for cleanup.

- [ ] **Step 2: Run integration test**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_message_api.py -q`

Expected: production wiring and real PostgreSQL flow pass.

- [ ] **Step 3: Run full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run Uvicorn/TCP smoke and cleanup audit**

Start Uvicorn on localhost, register and update a uniquely marked device, create and replay a uniquely keyed message, verify PostgreSQL records, then delete only the tracked device/contact data in `finally`. Confirm PostgreSQL is healthy and no `pytest-%` marked devices/messages remain.

## Final requirements audit

- Business, registration, and device Token domains remain isolated.
- Idempotency key is required and globally serialized before routing.
- One normalized recipient is enforced.
- Device/SIM online routing honors the configurable window.
- Contacts, conversations, messages, recipients, and history are atomic.
- Replay does not duplicate data or publish events.
- Publisher failure cannot roll back committed work.
- Existing POST/PATCH device interfaces remain green.
