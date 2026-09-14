# Agent SIM Card List API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `GET /agent/v1/sim-cards` so a logged-in customer-service agent can retrieve the phone numbers, carriers, and areas assigned to their account.

**Architecture:** Reuse the existing agent authentication dependency and agent contact router because the endpoint belongs to the same `/agent/v1` account-facing surface. Add a small schema and query method that joins `account_sim_cards` to `sim_cards` by authenticated account ID.

**Tech Stack:** FastAPI, Pydantic, psycopg, pytest, TestClient.

---

### Task 1: Failing API Tests

**Files:**
- Modify: `tests/test_agent_contact_api.py`

- [ ] **Step 1: Write the failing tests**

Add tests that create accounts, devices, SIM cards, and account bindings, then call `GET /agent/v1/sim-cards`.

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `pytest tests/test_agent_contact_api.py -q`

Expected: new tests fail with `404 Not Found` or missing method/schema errors because the endpoint does not exist yet.

### Task 2: Schema, Service, Router

**Files:**
- Modify: `app/schemas/agent_contact.py`
- Modify: `app/services/agent_contact_service.py`
- Modify: `app/api/agent_contact.py`

- [ ] **Step 1: Add response schema**

Create `AgentSimCardItem` with fields `id`, `phoneNumber`, `carrierName`, and `areas`.

- [ ] **Step 2: Add query method**

Implement `AgentContactService.list_sim_cards(agent)` using:

```sql
SELECT s.id, s.phone_number, s.carrier_name, s.areas
FROM account_sim_cards acs
JOIN sim_cards s ON s.id = acs.sim_card_id
WHERE acs.account_id = %s
ORDER BY s.phone_number ASC NULLS LAST, s.device_id ASC, s.sim_number ASC, s.id ASC
```

- [ ] **Step 3: Add route**

Expose `GET /agent/v1/sim-cards` with the existing bearer-token dependency.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_agent_contact_api.py -q`

Expected: all agent contact tests pass.

### Task 3: Developer Documentation And Verification

**Files:**
- Create: `docs/agent-sim-cards-api.md`

- [ ] **Step 1: Write API documentation**

Document the path, authentication, response fields, data source, sorting, and example response.

- [ ] **Step 2: Run regression tests**

Run: `pytest tests/test_agent_contact_api.py tests/test_agent_conversation_api.py -q`

Expected: both suites pass, confirming the new route and existing bound-SIM conversation behavior work together.
