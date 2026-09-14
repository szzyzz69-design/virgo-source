# PG Database Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI-mounted NiceGUI database admin module under `pg` for displaying devices, SIM cards, contacts, products, and accounts, with controlled write operations for products, accounts, and SIM cards.

**Architecture:** Add a small `pg` Python package beside the existing PostgreSQL init files. Keep database access in a psycopg-backed service with explicit table and column allowlists, then mount a NiceGUI page from the existing FastAPI application factory using the existing `Database` instance.

**Tech Stack:** Python 3, FastAPI, NiceGUI, psycopg 3, PostgreSQL, pytest.

---

## File Structure

- Create `pg/__init__.py`: marks `pg` as a Python package while leaving Docker SQL assets untouched.
- Create `pg/admin_service.py`: table allowlists, row listing, product CRUD, account CRUD, SIM edit logic.
- Create `pg/admin_ui.py`: NiceGUI page registration, tabs, tables, dialogs, and form handlers.
- Modify `app/application.py`: call the admin UI mount function after existing API routers are registered.
- Modify `requirements.txt`: add `nicegui`.
- Create `tests/test_pg_admin_service.py`: service-level tests using fake in-memory connection/cursor objects.
- Create `tests/test_pg_admin_application.py`: verifies `create_app` delegates admin mounting with the configured `Database`.

## Task 1: Service Contract and Read Queries

**Files:**
- Create: `tests/test_pg_admin_service.py`
- Create: `pg/__init__.py`
- Create: `pg/admin_service.py`

- [ ] **Step 1: Write failing service tests for allowed table listing**

```python
from pg.admin_service import PgAdminService


def test_list_rows_uses_allowed_columns_and_hides_password_hash():
    database = RecordingDatabase(rows=[{"id": "acc_1", "username": "alice"}])
    service = PgAdminService(database)

    rows = service.list_rows("accounts")

    assert rows == [{"id": "acc_1", "username": "alice"}]
    assert "password_hash" not in database.statements[0]
    assert "FROM accounts" in database.statements[0]
```

- [ ] **Step 2: Run failing test**

Run: `pytest tests/test_pg_admin_service.py::test_list_rows_uses_allowed_columns_and_hides_password_hash -v`

Expected: import failure because `pg.admin_service` does not exist.

- [ ] **Step 3: Implement minimal listing service**

Create a `PgAdminService` with `list_rows(table_name, limit=200)` and static table metadata. Use only hard-coded table names and columns.

- [ ] **Step 4: Run passing test**

Run: `pytest tests/test_pg_admin_service.py::test_list_rows_uses_allowed_columns_and_hides_password_hash -v`

Expected: PASS.

## Task 2: Product CRUD

**Files:**
- Modify: `tests/test_pg_admin_service.py`
- Modify: `pg/admin_service.py`

- [ ] **Step 1: Write failing product CRUD tests**

Cover:

- `create_product()` inserts `id`, `menu`, `update_by`, `areas`, and service-generated `update_time`.
- `update_product()` does not change `id` and refreshes `update_time`.
- `delete_product()` deletes by `id`.

- [ ] **Step 2: Run failing product tests**

Run: `pytest tests/test_pg_admin_service.py -k product -v`

Expected: failures for missing product methods.

- [ ] **Step 3: Implement product methods**

Add:

```python
def create_product(self, data: ProductInput) -> None: ...
def update_product(self, product_id: str, data: ProductInput) -> None: ...
def delete_product(self, product_id: str) -> None: ...
```

- [ ] **Step 4: Run product tests**

Run: `pytest tests/test_pg_admin_service.py -k product -v`

Expected: PASS.

## Task 3: Account CRUD

**Files:**
- Modify: `tests/test_pg_admin_service.py`
- Modify: `pg/admin_service.py`

- [ ] **Step 1: Write failing account CRUD tests**

Cover:

- `create_account()` hashes the supplied password into `password_hash`.
- `update_account()` leaves password unchanged when password is empty or `None`.
- `update_account()` hashes and updates password when a new password is supplied.
- `delete_account()` deletes by `id`.

- [ ] **Step 2: Run failing account tests**

Run: `pytest tests/test_pg_admin_service.py -k account -v`

Expected: failures for missing account methods.

- [ ] **Step 3: Implement account methods**

Use `app.security.hash_password()` and never return or display `password_hash` from list metadata.

- [ ] **Step 4: Run account tests**

Run: `pytest tests/test_pg_admin_service.py -k account -v`

Expected: PASS.

## Task 4: SIM Edit

**Files:**
- Modify: `tests/test_pg_admin_service.py`
- Modify: `pg/admin_service.py`

- [ ] **Step 1: Write failing SIM edit tests**

Cover:

- `update_sim_card()` updates only allowed mutable columns.
- Generated SQL does not update `id`, `device_id`, `slot_index`, or `sim_number`.
- `updated_at` is refreshed.

- [ ] **Step 2: Run failing SIM tests**

Run: `pytest tests/test_pg_admin_service.py -k sim -v`

Expected: failures for missing SIM method.

- [ ] **Step 3: Implement SIM update**

Use explicit `UPDATE sim_cards SET ... WHERE id = %s` SQL with a fixed column list.

- [ ] **Step 4: Run SIM tests**

Run: `pytest tests/test_pg_admin_service.py -k sim -v`

Expected: PASS.

## Task 5: NiceGUI Page and FastAPI Mount

**Files:**
- Create: `pg/admin_ui.py`
- Modify: `requirements.txt`
- Modify: `app/application.py`
- Create: `tests/test_pg_admin_application.py`

- [ ] **Step 1: Write failing application integration test**

Patch `app.application.mount_admin_ui` and assert `create_app()` passes the created `Database`.

- [ ] **Step 2: Run failing integration test**

Run: `pytest tests/test_pg_admin_application.py -v`

Expected: import or assertion failure because the mount hook is not wired.

- [ ] **Step 3: Implement mount hook**

Add `mount_admin_ui(app, database)` in `pg/admin_ui.py`; import and call it from `app.application.create_app()`.

- [ ] **Step 4: Build NiceGUI UI**

Create `/admin/db` page with tabs for devices, SIM cards, contacts, products, and accounts. Add refreshable tables and dialogs for the allowed write operations.

- [ ] **Step 5: Run integration test**

Run: `pytest tests/test_pg_admin_application.py -v`

Expected: PASS.

## Task 6: Verification

**Files:**
- All changed files.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_pg_admin_service.py tests/test_pg_admin_application.py -v`

Expected: all focused tests PASS.

- [ ] **Step 2: Run full test suite**

Run: `pytest`

Expected: existing tests still PASS. If PostgreSQL integration tests cannot connect, record the exact connection failure and run the non-integration subset that does not require Docker.

- [ ] **Step 3: Optional manual smoke**

Run with local dependencies installed:

```powershell
$env:DATABASE_URL='postgresql://admin:admin@127.0.0.1:5433/virgo_pg'
$env:VIRGO_CONFIG_FILE='config.toml'
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Visit `/admin/db` and confirm the tabs render.
