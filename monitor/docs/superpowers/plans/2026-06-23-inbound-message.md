# Inbound Message Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement idempotent `POST /mobile/v1/inbox` persistence for inbound SMS/Data SMS with SIM, contact and conversation matching.

**Architecture:** Pydantic models normalize and digest Android payloads. A PostgreSQL service uses advisory locks and one transaction for replay detection and all related writes. A thin authenticated route selects 201/200, and a post-commit publisher remains injectable.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2, Psycopg 3, PostgreSQL 17, pytest

---

This workspace is not a Git repository, so commit/worktree steps are omitted.

### Task 1: Data SMS migration and inbound DTO

Create `pg/init/004_inbound_data_sms.sql`, `app/schemas/inbound_message.py`, and `tests/test_inbound_message_schema.py`. Test SMS/Data SMS exclusivity, strict Base64, phone handling, aware times and stable digest; verify RED, implement, then GREEN.

### Task 2: Atomic inbound service and publisher

Create `app/services/inbound_publisher.py`, `app/services/inbound_message_service.py`, and `tests/integration/test_inbound_message_service.py`. Test first create/replay/conflict, SIM number/recipient/no-SIM matching, contact/conversation reuse, unread count, history, concurrency, rollback and publisher failure. Use idempotency and route advisory locks; run against PostgreSQL.

### Task 3: Authenticated inbox API

Create `app/api/inbox.py`, `tests/test_inbox_api.py`; modify `app/application.py`. Test auth-before-body, validation, 201/200, 409 and dependency injection before implementation. Wire the default service and publisher, then run focused regressions.

### Task 4: Production flow and verification

Create `tests/integration/test_inbox_api.py`. Register/update a device, upload twice, assert one `INBOUND/Received` message and one unread increment. Apply the migration to the active test database, run the full suite, and audit secret-safe errors and post-commit publishing.
