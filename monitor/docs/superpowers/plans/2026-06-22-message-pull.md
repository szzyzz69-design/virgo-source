# Mobile Message Pull Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `GET /mobile/v1/message` so an authenticated device atomically claims up to 10 eligible outbound tasks in FIFO or LIFO order and receives Android-compatible JSON.

**Architecture:** Add dedicated response DTOs, a PostgreSQL command/query service using `FOR UPDATE SKIP LOCKED`, and a thin authenticated FastAPI route. Reuse device authentication and the application factory; keep selection, state transition, history, recipients, and heartbeat updates in one transaction.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, Psycopg 3, PostgreSQL 17, pytest

---

The workspace is not a Git repository, so commit and worktree steps are omitted.

## File map

- Create `app/schemas/message_pull.py` for Android response DTOs and millisecond conversion.
- Create `app/services/message_pull_service.py` for atomic selection and transition.
- Create `app/api/message_pull.py` for device authentication, query validation and response mapping.
- Modify `app/application.py` to construct and register the pull route.
- Create `tests/test_message_pull_schema.py` and `tests/test_message_pull_api.py`.
- Create `tests/integration/test_message_pull_service.py` and `tests/integration/test_message_pull_api.py`.

### Task 1: Android pull DTOs

**Files:**
- Create: `tests/test_message_pull_schema.py`
- Create: `app/schemas/message_pull.py`

- [ ] **Step 1: Write failing DTO tests**

Test exact aliases for SMS and Data SMS, nullable fields, UTC millisecond formatting, and list serialization. The desired models are `TextMessage(text)`, `DataMessage(data, port)`, and `MessagePullItem` with aliases `textMessage`, `dataMessage`, `phoneNumbers`, `simNumber`, `withDeliveryReport`, `isEncrypted`, `validUntil`, `scheduleAt`, and `createdAt`.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_pull_schema.py -q`

Expected: import fails because `app.schemas.message_pull` does not exist.

- [ ] **Step 3: Implement DTOs and conversion**

Use `ConfigDict(populate_by_name=True)`, nested Pydantic models, and `utc_iso_from_millis(value)` returning `None` or an ISO string with millisecond precision and `Z`. Validate that exactly one of `text_message` and `data_message` is non-null and `phone_numbers` is non-empty.

- [ ] **Step 4: Run GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_pull_schema.py -q`

Expected: all DTO tests pass.

### Task 2: Atomic PostgreSQL pull service

**Files:**
- Create: `tests/integration/test_message_pull_service.py`
- Create: `app/services/message_pull_service.py`

- [ ] **Step 1: Write failing service tests**

Seed tracked devices, SIMs, conversations, contacts, recipients and messages. Cover FIFO/LIFO, 10-row limit, device isolation, eligibility filters, SMS/Data SMS conversion, state/history/heartbeat updates, concurrent non-overlapping claims, device recheck, and rollback through a failing connection proxy.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_message_pull_service.py -q`

Expected: import fails because the pull service does not exist.

- [ ] **Step 3: Implement service transaction**

Define `PullDeviceUnavailable`. In `MessagePullService.pull(device_id, order)`, reject orders outside `fifo/lifo`, compute one UTC millisecond `now`, lock and update the enabled device, select at most 10 eligible message rows with the requested stable order and `FOR UPDATE OF m SKIP LOCKED`, update selected IDs to `Processed`, insert `Processed/SERVER` history with `ON CONFLICT DO NOTHING`, read recipients ordered by ID, and return `MessagePullItem` values before transaction exit.

- [ ] **Step 4: Run GREEN when PostgreSQL is available**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_message_pull_service.py -q`

Expected: all transactional tests pass. If Docker remains unavailable, collect the tests and report the blocker.

### Task 3: Authenticated pull API

**Files:**
- Create: `tests/test_message_pull_api.py`
- Create: `app/api/message_pull.py`
- Modify: `app/application.py`

- [ ] **Step 1: Write failing API tests**

Use recording auth and pull services. Assert default FIFO, explicit FIFO/LIFO, exact Android response, empty `[]`, invalid order 400, malformed/missing/unknown Token 401, disabled device 403, authentication before service invocation, and `PullDeviceUnavailable` to 403.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_pull_api.py -q`

Expected: route module is missing and `create_app()` lacks pull service injection.

- [ ] **Step 3: Implement route and wiring**

Create `create_message_pull_router(auth_service, pull_service)`. Reuse the existing device Bearer rules, accept `order: Literal['fifo','lifo']='fifo'`, call `pull(device.id, order)`, map the pull device race to 403, and declare `response_model=list[MessagePullItem]`. Extend `create_app()` with optional `message_pull_service` and default `MessagePullService(database)`.

- [ ] **Step 4: Run GREEN and non-database regressions**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_pull_api.py tests\test_message_pull_schema.py tests\test_device_api.py tests\test_device_update_api.py tests\test_events_api.py tests\test_message_api.py -q`

Expected: zero failures.

### Task 4: Production flow and final verification

**Files:**
- Create: `tests/integration/test_message_pull_api.py`

- [ ] **Step 1: Write end-to-end flow test**

Register and update a device, create an outbound message through the third interface, pull with the device Token, assert the Android JSON, pull again and assert `[]`, then query `Processed`, `pulled_at`, history, and device heartbeat.

- [ ] **Step 2: Run integration verification when Docker is available**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_message_pull_service.py tests\integration\test_message_pull_api.py -q`

Expected: all real PostgreSQL pull tests pass.

- [ ] **Step 3: Run all non-database tests**

Run: `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests\integration`

Expected: zero failures.

- [ ] **Step 4: Requirements audit**

Confirm fixed limit 10, stable FIFO/LIFO, device/SIM/time filters, target-device isolation, `SKIP LOCKED`, atomic `Processed` history, heartbeat update, Android field names, empty-list contract, and no recipient-state mutation.
