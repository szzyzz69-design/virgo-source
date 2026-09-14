# Mobile Message Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement atomic `PATCH /mobile/v1/message` status uploads with device ownership, monotonic state transitions, recipient validation, history idempotency, and related timestamp updates.

**Architecture:** Pydantic DTOs handle shape and local cross-field checks. A PostgreSQL service locks the entire batch in stable order, validates every item before writing, then updates all related rows in one transaction. A thin authenticated API maps indexed domain failures to the existing error contract.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, Psycopg 3, PostgreSQL 17, pytest

---

The workspace is not a Git repository, so commit/worktree steps are omitted.

### Task 1: Status upload DTOs and pure state rules

**Files:** Create `app/schemas/message_status.py`, `tests/test_message_status_schema.py`.

- [ ] Write failing tests for 1–100 batch bounds, aliases, states, duplicate IDs/phones, errors, aware times, chronological states, aggregation, and monotonic transitions.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests\test_message_status_schema.py -q`; expect missing-module RED.
- [ ] Implement `Status`, `RecipientStatusUpdate`, `MessageStatusUpdate`, `MessageStatusBatch`, `aggregate_recipient_state()`, `can_transition()`, and UTC millisecond conversion.
- [ ] Re-run the DTO tests; expect zero failures.

### Task 2: Atomic message state service

**Files:** Create `app/services/message_state_service.py`, `tests/integration/test_message_state_service.py`.

- [ ] Write PostgreSQL tests for normal progress, failure, replay, cross-level advance, terminal rollback, ownership, recipient mismatch, five-minute skew, first-Sent related timestamps, multi-message rollback, and concurrent updates.
- [ ] Run the service test; expect missing-service RED.
- [ ] Implement indexed domain errors and `MessageStateService.update(device_id, batch)` with stable locks, complete validation before writes, recipient/message/history updates, first-write timestamps, related Sent updates, and heartbeat.
- [ ] Run or collect the integration tests depending on Docker availability.

### Task 3: Authenticated PATCH route

**Files:** Create `app/api/message_status.py`, `tests/test_message_status_api.py`; modify `app/application.py`.

- [ ] Write failing API tests for auth-before-body, JSON validation, success, 401/403, and indexed 404/409 mappings.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests\test_message_status_api.py -q`; expect missing route/injection RED.
- [ ] Implement authenticated body parsing, route, domain mapping, `{"ok": true}`, optional service injection, and default service wiring.
- [ ] Run focused API regressions; expect zero failures.

### Task 4: Production flow and verification

**Files:** Create `tests/integration/test_message_status_api.py`.

- [ ] Register/update a device, create and pull a message, PATCH Sent then Delivered, replay, and assert message/recipient/history/SIM/contact/conversation fields.
- [ ] Run PostgreSQL tests when Docker is available; otherwise collect and report the blocker.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests\integration`; expect zero failures.
- [ ] Audit batch atomicity, state monotonicity, five-minute skew, idempotency, ownership and secret-safe errors.
