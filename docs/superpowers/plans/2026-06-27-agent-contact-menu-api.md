# Agent Contact And Menu API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build agent-facing contact and menu APIs and keep contact area in sync with inbound SIM routing.

**Architecture:** Extend the existing agent API layer under `/agent/v1` with a focused service, schemas, and router for contacts and menus. Use `contacts.areas` for contact authorization and update that field from inbound SIM area during contact upsert.

**Tech Stack:** FastAPI, Pydantic, psycopg 3, PostgreSQL, pytest, FastAPI TestClient.

---

### Task 1: Inbound Contact Area Propagation

**Files:**
- Modify: `app/services/inbound_message_service.py`
- Test: `tests/integration/test_agent_conversation_area_flow.py`

- [ ] **Step 1: Write failing integration tests**

Add tests proving inbound messages set `contacts.areas` from the receiving SIM, and later update the same contact to the latest inbound SIM area.

- [ ] **Step 2: Run the focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_agent_conversation_area_flow.py -v
```

Expected: the new contact-area tests fail because inbound upsert does not write `contacts.areas`.

- [ ] **Step 3: Update contact upsert**

Update `INSERT INTO contacts ... ON CONFLICT ... DO UPDATE` to include `areas` on insert and update it from the matched SIM area on conflict.

- [ ] **Step 4: Run the focused tests**

Run the same command. Expected: contact-area propagation tests pass.

### Task 2: Agent Contact And Menu APIs

**Files:**
- Create: `app/schemas/agent_contact.py`
- Create: `app/services/agent_contact_service.py`
- Create: `app/api/agent_contact.py`
- Modify: `app/application.py`
- Test: `tests/test_agent_contact_api.py`

- [ ] **Step 1: Write failing API tests**

Cover:
- `GET /agent/v1/contacts` returns only contacts matching the authenticated agent area.
- `PATCH /agent/v1/contacts/{contactId}/remark` updates a matching contact.
- `PATCH /agent/v1/contacts/{contactId}/remark` clears blank remarks.
- Cross-area remark update returns `403`.
- `GET /agent/v1/menus` returns only non-empty menus matching the authenticated agent area.

- [ ] **Step 2: Run the failing API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_contact_api.py -v
```

Expected: fail because the router and schemas do not exist.

- [ ] **Step 3: Create schemas**

Add Pydantic models for contact list items, remark update requests, and menu list items.

- [ ] **Step 4: Create service**

Add service methods for listing contacts, updating remarks with area authorization, and listing menus.

- [ ] **Step 5: Create router and wire application**

Add `/agent/v1/contacts`, `/agent/v1/contacts/{contactId}/remark`, and `/agent/v1/menus`; include the router from `create_app`.

- [ ] **Step 6: Run API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_contact_api.py -v
```

Expected: all tests pass.

### Task 3: Verification

**Files:**
- All changed files

- [ ] **Step 1: Run focused suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_agent_contact_api.py tests/test_agent_auth_api.py tests/test_agent_conversation_api.py tests/integration/test_agent_conversation_area_flow.py -v
```

- [ ] **Step 2: Run full suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Expected: all tests pass.
