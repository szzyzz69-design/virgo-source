# Device SSE Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `GET /mobile/v1/events` with authenticated single-instance SSE connections and post-commit `MessageEnqueued` delivery from the third interface.

**Architecture:** A thread-safe in-memory registry owns one blocking queue per device and replaces older connections. A synchronous streaming generator emits queued events or 20-second heartbeat comments. The existing message publisher boundary is connected to the same registry by the application factory.

**Tech Stack:** Python 3.12, FastAPI `StreamingResponse`, standard-library `queue`/`threading`, pytest, HTTPX

---

The workspace is not a Git repository, so commit and worktree steps are omitted.

## File map

- Create `app/services/sse.py`: event encoding, connection lifecycle, registry, stream and heartbeat.
- Modify `app/services/message_publisher.py`: registry-backed publisher.
- Create `app/api/events.py`: device authentication and SSE response.
- Modify `app/application.py`: construct and share the singleton registry.
- Create `tests/test_sse.py`: registry and encoding tests.
- Create `tests/test_events_api.py`: authentication and HTTP contract tests.
- Modify `tests/test_message_api.py`: prove application publisher wiring.
- Create `tests/integration/test_sse_message_flow.py`: real message-to-SSE flow.

### Task 1: Thread-safe SSE registry

**Files:**
- Create: `tests/test_sse.py`
- Create: `app/services/sse.py`

- [ ] **Step 1: Write failing registry tests**

Test exact event bytes, targeted delivery, no-connection no-op, latest-connection replacement, identity-safe cleanup, heartbeat generation and final cleanup. Use a configurable `heartbeat_seconds=0.01` in tests and the production default of 20 seconds.

```python
def test_message_event_uses_android_contract():
    assert encode_message_enqueued("msg_1") == (
        'id: msg_1\nevent: MessageEnqueued\n'
        'data: {"messageId":"msg_1"}\n\n'
    )

def test_new_connection_replaces_old_without_old_cleanup_removing_new():
    registry = SseConnectionRegistry(heartbeat_seconds=0.01)
    old = registry.register("dev_1")
    new = registry.register("dev_1")
    assert list(registry.stream(old)) == []
    registry.unregister(old)
    registry.publish_message("dev_1", "msg_1")
    assert next(registry.stream(new)).startswith("id: msg_1\n")
```

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_sse.py -q`

Expected: import fails because `app.services.sse` does not exist.

- [ ] **Step 3: Implement the registry**

Define `SseConnection(device_id, queue, closed)`, a private close sentinel, `encode_message_enqueued()`, UTC heartbeat encoding, and `SseConnectionRegistry`. Protect the device map with `threading.Lock`; replace then close old connections; `publish_message()` snapshots the current connection under the lock and queues an encoded event; `stream()` blocks with timeout and unregisters by object identity in `finally`.

- [ ] **Step 4: Run GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_sse.py -q`

Expected: all registry tests pass.

### Task 2: Authenticated SSE API

**Files:**
- Create: `tests/test_events_api.py`
- Create: `app/api/events.py`

- [ ] **Step 1: Write failing route tests**

Use a finite fake registry so `TestClient` can consume the response without hanging. Assert valid device authentication, exact headers/media type, 401/403 mapping, and that failed authentication never calls `register()`.

```python
def test_events_returns_sse_headers_for_authenticated_device():
    response = client.get(
        "/mobile/v1/events",
        headers={"Authorization": "Bearer device-token"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
```

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_events_api.py -q`

Expected: route factory import fails.

- [ ] **Step 3: Implement the route**

Create `create_events_router(auth_service, registry)`. Parse the Bearer header exactly like the existing PATCH interface, map `InvalidDeviceToken` to 401 and `DeviceDisabled` to 403, register only after authentication, and return `StreamingResponse` with SSE headers.

- [ ] **Step 4: Run GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_events_api.py -q`

Expected: all API contract tests pass.

### Task 3: Connect post-commit publishing

**Files:**
- Modify: `tests/test_message_api.py`
- Modify: `app/services/message_publisher.py`
- Modify: `app/application.py`

- [ ] **Step 1: Write failing wiring test**

Inject a real registry into `create_app()`, build the default message service with a fake database/service boundary where appropriate, publish through the application-created publisher, and assert the registered device receives `MessageEnqueued`. Also assert a custom publisher remains injectable.

- [ ] **Step 2: Run RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests\test_message_api.py -q`

Expected: `create_app()` does not accept an SSE registry and the default publisher is still no-op.

- [ ] **Step 3: Implement publisher and application wiring**

Add `RegistryMessageEnqueuedPublisher.publish()` delegating to `registry.publish_message()`. Extend `create_app()` with optional `sse_registry`; create one default registry, use it for `create_events_router`, and use the registry publisher as the default `MessageCommandService` publisher. Preserve explicit message service and publisher injections.

- [ ] **Step 4: Run GREEN and mobile regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_message_api.py tests\test_device_api.py tests\test_device_update_api.py tests\test_events_api.py tests\test_sse.py -q
```

Expected: all focused and existing mobile API tests pass.

### Task 4: Real flow and final verification

**Files:**
- Create: `tests/integration/test_sse_message_flow.py`

- [ ] **Step 1: Write the PostgreSQL flow test**

Register and PATCH an online device, register an SSE connection on the injected registry, call `POST /business/v1/messages`, read one SSE event, and query PostgreSQL. Assert the event targets the created message and the row remains `state='Pending'` with `pulled_at IS NULL`.

- [ ] **Step 2: Run integration tests when Docker is available**

Run: `.\.venv\Scripts\python.exe -m pytest tests\integration\test_sse_message_flow.py tests\integration\test_message_api.py tests\integration\test_message_service.py -q`

Expected: all SSE/message PostgreSQL tests pass. If Docker service permission remains unavailable, report this as an explicit environment blocker rather than a passing result.

- [ ] **Step 3: Run full non-database verification**

Run: `.\.venv\Scripts\python.exe -m pytest -q --ignore=tests\integration`

Expected: zero failures.

- [ ] **Step 4: Audit requirements**

Confirm authentication precedes registration, headers disable buffering/caching, latest connection replaces old, delivery is target-only, heartbeats occur every 20 seconds, no-connection publish is harmless, message commit precedes publish, replay does not republish, and SSE never changes message state.
