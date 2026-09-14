# Area-Based Agent Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the customer-service API so agents can log in, see only conversations for their configured area, read message history, reply by SMS through the existing phone gateway, and receive new-message events.

**Architecture:** Keep the existing SMS gateway as the delivery layer. Add an agent-facing API layer under `/agent/v1` that authenticates against `accounts`, filters conversations by `accounts.areas = conversations.areas`, and reuses the existing outbound-message service for replies. Store the conversation area on `conversations.areas` when a conversation is created so list queries and permission checks stay simple.

**Tech Stack:** FastAPI, Pydantic, psycopg 3, PostgreSQL, Server-Sent Events, pytest, FastAPI TestClient.

---

## Current Schema Change

`pg/init/002_conversation.sql` already adds:

```sql
areas VARCHAR(100)
```

and:

```sql
CREATE INDEX idx_conversations_areas ON conversations(areas);
```

For an existing development database that was initialized before this change, run this SQL once:

```sql
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS areas VARCHAR(100);
CREATE INDEX IF NOT EXISTS idx_conversations_areas ON conversations(areas);
```

## Business Rules

- `sim_cards.areas` is the source of truth for routing incoming conversations by receiving phone/SIM area.
- `conversations.areas` is copied from the matched `sim_cards.areas` when a conversation is created.
- `accounts.areas` controls which conversations an agent can see and operate on.
- An agent can access a conversation only when both fields are non-empty and equal after trimming.
- Admin UI remains responsible for editing `sim_cards.areas` and `accounts.areas`.
- Replying in a conversation must preserve the existing route: `conversation.device_id`, `conversation.sim_card_id`, and `conversation.sim_number`.
- Agent APIs use agent authentication, not the static `business_api_token`.

## Files

- Modify: `pg/init/002_conversation.sql`
- Modify: `app/security.py`
- Create: `app/schemas/agent_auth.py`
- Create: `app/schemas/agent_conversation.py`
- Create: `app/services/agent_auth_service.py`
- Create: `app/services/agent_conversation_service.py`
- Create: `app/services/agent_event_publisher.py`
- Create: `app/api/agent_auth.py`
- Create: `app/api/agent_conversation.py`
- Create: `app/api/agent_events.py`
- Modify: `app/application.py`
- Modify: `app/services/inbound_message_service.py`
- Modify: `app/services/message_service.py`
- Test: `tests/test_agent_auth_api.py`
- Test: `tests/test_agent_conversation_api.py`
- Test: `tests/integration/test_agent_conversation_area_flow.py`

---

### Task 1: Copy SIM Area Into Conversations

**Files:**
- Modify: `app/services/inbound_message_service.py`
- Modify: `app/services/message_service.py`
- Test: `tests/integration/test_agent_conversation_area_flow.py`

- [ ] **Step 1: Write the failing inbound-area test**

Create `tests/integration/test_agent_conversation_area_flow.py` with:

```python
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_inbound_conversation_copies_area_from_receiving_sim(clean_database):
    marker = clean_database.track_push_token("pytest-agent-area-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    inbox_id = clean_database.track_message_key("agent-area:" + uuid4().hex)
    recipient = "+8613900000000"
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "agent-area-phone",
                "pushToken": marker,
                "simCards": [
                    {
                        "slotIndex": 0,
                        "simNumber": 1,
                        "phoneNumber": recipient,
                    }
                ],
            },
        ).json()
        clean_database.track(registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("south", registration["id"]),
            )
            connection.commit()

        response = client.post(
            "/mobile/v1/inbox",
            headers={"Authorization": f"Bearer {registration['token']}"},
            json={
                "id": inbox_id,
                "type": "SMS",
                "sender": sender,
                "recipient": recipient,
                "simNumber": 1,
                "subscriptionId": 3,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
                "textMessage": {"text": "area hello"},
                "dataMessage": None,
            },
        )

    assert response.status_code == 201
    with psycopg.connect(clean_database.dsn) as connection:
        area = connection.execute(
            "SELECT areas FROM conversations WHERE id = %s",
            (response.json()["conversationId"],),
        ).fetchone()[0]
    assert area == "south"
```

- [ ] **Step 2: Run the failing test**

Run:

```powershell
pytest tests/integration/test_agent_conversation_area_flow.py::test_inbound_conversation_copies_area_from_receiving_sim -v
```

Expected: fail because `conversations.areas` is not populated.

- [ ] **Step 3: Update inbound conversation creation**

In `app/services/inbound_message_service.py`, change the SIM lookup rows so they select `areas`:

```sql
SELECT id, sim_number, areas
FROM sim_cards
WHERE device_id=%s AND sim_number=%s
LIMIT 1
```

and:

```sql
SELECT id, sim_number, areas
FROM sim_cards
WHERE device_id=%s
  AND regexp_replace(phone_number,'[\s()\-]','','g')=%s
LIMIT 1
```

Then derive:

```python
sim_id = sim[0] if sim else None
sim_number = sim[1] if sim else request.sim_number
area = sim[2] if sim else None
```

When inserting a new conversation, include `areas`:

```sql
INSERT INTO conversations(
    id, external_phone_number, contact_id, device_id,
    sim_card_id, sim_number, areas, status, created_at, updated_at
)
VALUES(%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s)
```

with arguments:

```python
(conversation_id, request.sender, contact_id, device_id, sim_id, sim_number, area, now, now)
```

- [ ] **Step 4: Run the inbound test again**

Run:

```powershell
pytest tests/integration/test_agent_conversation_area_flow.py::test_inbound_conversation_copies_area_from_receiving_sim -v
```

Expected: pass.

- [ ] **Step 5: Add the outbound-area test**

Append this test to `tests/integration/test_agent_conversation_area_flow.py`:

```python
def test_outbound_conversation_copies_area_from_selected_sim(clean_database):
    marker = clean_database.track_push_token("pytest-agent-outbound-area-" + uuid4().hex)
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    key = clean_database.track_message_key("agent-outbound-area:" + uuid4().hex)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "agent-outbound-area-phone",
                "pushToken": marker,
                "simCards": [{"slotIndex": 0, "simNumber": 1}],
            },
        ).json()
        clean_database.track(registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("north", registration["id"]),
            )
            connection.commit()

        response = client.post(
            "/business/v1/messages",
            headers={
                "Authorization": "Bearer business-secret",
                "Idempotency-Key": key,
            },
            json={"phoneNumbers": [phone], "text": "area outbound"},
        )

    assert response.status_code == 201
    with psycopg.connect(clean_database.dsn) as connection:
        area = connection.execute(
            "SELECT areas FROM conversations WHERE id = %s",
            (response.json()["conversationId"],),
        ).fetchone()[0]
    assert area == "north"
```

- [ ] **Step 6: Update outbound route selection**

In `app/services/message_service.py`, add `areas` to `Route`:

```python
@dataclass(frozen=True, slots=True)
class Route:
    device_id: str
    sim_card_id: str
    sim_number: int
    phone_number: str | None
    areas: str | None = None
    conversation_id: str | None = None
    contact_id: str | None = None
```

When selecting an existing conversation, include `c.areas` in the SQL result and pass it into `Route`.

When selecting a new route, change the SIM query to:

```sql
SELECT d.id, s.id, s.sim_number, s.phone_number, s.areas
FROM devices d
JOIN sim_cards s ON s.device_id = d.id
```

When inserting a new conversation, include `areas`:

```sql
INSERT INTO conversations (
    id, external_phone_number, contact_id, device_id,
    sim_card_id, sim_number, areas, status, created_at, updated_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s)
```

and pass `route.areas`.

- [ ] **Step 7: Run the area propagation tests**

Run:

```powershell
pytest tests/integration/test_agent_conversation_area_flow.py -v
```

Expected: both tests pass.

---

### Task 2: Add Agent Password Verification And Sessions

**Files:**
- Modify: `app/security.py`
- Create: `app/services/agent_auth_service.py`
- Create: `app/schemas/agent_auth.py`
- Create: `app/api/agent_auth.py`
- Modify: `pg/init/003_other.sql`
- Modify: `app/application.py`
- Test: `tests/test_agent_auth_api.py`

- [ ] **Step 1: Add password verification tests**

Create `tests/test_agent_auth_api.py` with:

```python
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.security import hash_password


def test_agent_login_returns_token_and_me(clean_database):
    account_id = "acct_" + uuid4().hex
    username = "agent_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO accounts(id, username, password_hash, areas, status)
            VALUES(%s, %s, %s, %s, 'ACTIVE')
            """,
            (account_id, username, hash_password(password), "north"),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/agent/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        me = client.get("/agent/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json() == {"id": account_id, "username": username, "areas": "north"}


def test_agent_login_rejects_wrong_password(clean_database):
    username = "agent_" + uuid4().hex
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO accounts(id, username, password_hash, areas, status)
            VALUES(%s, %s, %s, %s, 'ACTIVE')
            """,
            ("acct_" + uuid4().hex, username, hash_password("right-password"), "north"),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/agent/v1/auth/login",
            json={"username": username, "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
```

- [ ] **Step 2: Add `agent_sessions` table**

In `pg/init/003_other.sql`, after `accounts`, add:

```sql
CREATE TABLE agent_sessions (
    token_hash VARCHAR(255) PRIMARY KEY,
    account_id VARCHAR(64) NOT NULL
               REFERENCES accounts(id) ON DELETE CASCADE,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    expires_at BIGINT NOT NULL
);

CREATE INDEX idx_agent_sessions_account ON agent_sessions(account_id);
CREATE INDEX idx_agent_sessions_expires ON agent_sessions(expires_at);
```

- [ ] **Step 3: Add password verification**

In `app/security.py`, add:

```python
def verify_password(value: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, encoded_salt, encoded_digest = encoded.split("$", 3)
        iterations = int(iterations_text)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256" or iterations < PBKDF2_MIN_ITERATIONS:
        return False
    try:
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("utf-8"), salt, iterations)
    return secrets.compare_digest(digest, expected)
```

- [ ] **Step 4: Create auth schemas**

Create `app/schemas/agent_auth.py`:

```python
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


NonEmptyAgentText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]


class AgentLoginRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    username: NonEmptyAgentText
    password: NonEmptyAgentText


class AgentLoginResponse(BaseModel):
    token: str
    expiresAt: int


class AgentMeResponse(BaseModel):
    id: str
    username: str
    areas: str
```

- [ ] **Step 5: Create auth service**

Create `app/services/agent_auth_service.py`:

```python
from dataclasses import dataclass
import secrets
import time

from app.database import Database
from app.security import hash_sha256, verify_password


class InvalidAgentCredentials(Exception):
    pass


class InvalidAgentToken(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedAgent:
    id: str
    username: str
    areas: str


@dataclass(frozen=True, slots=True)
class AgentSession:
    token: str
    expires_at: int
    agent: AuthenticatedAgent


class AgentAuthService:
    def __init__(self, database: Database, session_ttl_seconds: int = 86400):
        self._database = database
        self._session_ttl_seconds = session_ttl_seconds

    def login(self, username: str, password: str) -> AgentSession:
        now = time.time_ns() // 1_000_000
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, username, password_hash, areas, status
                FROM accounts
                WHERE username = %s
                """,
                (username,),
            ).fetchone()
            if row is None or row[4] != "ACTIVE" or not row[3]:
                raise InvalidAgentCredentials
            if not verify_password(password, row[2]):
                raise InvalidAgentCredentials

            token = "agent_" + secrets.token_urlsafe(32)
            expires_at = now + self._session_ttl_seconds * 1000
            connection.execute(
                """
                INSERT INTO agent_sessions(token_hash, account_id, created_at, expires_at)
                VALUES(%s, %s, %s, %s)
                """,
                (hash_sha256(token), row[0], now, expires_at),
            )
            agent = AuthenticatedAgent(id=row[0], username=row[1], areas=row[3])
            return AgentSession(token=token, expires_at=expires_at, agent=agent)

    def authenticate(self, token: str) -> AuthenticatedAgent:
        now = time.time_ns() // 1_000_000
        row = self._database.fetch_one(
            """
            SELECT a.id, a.username, a.areas, a.status, s.expires_at
            FROM agent_sessions s
            JOIN accounts a ON a.id = s.account_id
            WHERE s.token_hash = %s
            """,
            (hash_sha256(token),),
        )
        if row is None or row[3] != "ACTIVE" or row[4] <= now or not row[2]:
            raise InvalidAgentToken
        return AuthenticatedAgent(id=row[0], username=row[1], areas=row[2])
```

- [ ] **Step 6: Create auth router**

Create `app/api/agent_auth.py` with `POST /agent/v1/auth/login` and `GET /agent/v1/me`. Reuse `parse_json_model` from `app.api.device`. Return `401 UNAUTHORIZED` for invalid credentials or token.

- [ ] **Step 7: Wire auth router**

In `app/application.py`, instantiate `AgentAuthService(database)` and include the auth router.

- [ ] **Step 8: Run auth tests**

Run:

```powershell
pytest tests/test_agent_auth_api.py -v
```

Expected: both tests pass.

---

### Task 3: Add Area-Filtered Conversation List And Message History

**Files:**
- Create: `app/schemas/agent_conversation.py`
- Create: `app/services/agent_conversation_service.py`
- Create: `app/api/agent_conversation.py`
- Modify: `app/application.py`
- Test: `tests/test_agent_conversation_api.py`

- [ ] **Step 1: Write access-control tests**

Create `tests/test_agent_conversation_api.py` with tests that create two active accounts and two conversations:

```python
def insert_account(connection, account_id, username, password_hash, area):
    connection.execute(
        """
        INSERT INTO accounts(id, username, password_hash, areas, status)
        VALUES(%s, %s, %s, %s, 'ACTIVE')
        """,
        (account_id, username, password_hash, area),
    )
```

The first test logs in as the north agent and asserts `GET /agent/v1/conversations` returns only the conversation whose `areas` is `"north"`.

The second test logs in as the north agent and asserts `GET /agent/v1/conversations/{southConversationId}/messages` returns `403`.

- [ ] **Step 2: Create conversation schemas**

Create `app/schemas/agent_conversation.py`:

```python
from pydantic import BaseModel, ConfigDict, Field


class AgentConversationItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    external_phone_number: str = Field(alias="externalPhoneNumber")
    contact_id: str = Field(alias="contactId")
    areas: str
    status: str
    unread_count: int = Field(alias="unreadCount")
    last_message_preview: str | None = Field(alias="lastMessagePreview")
    last_message_direction: str | None = Field(alias="lastMessageDirection")
    last_message_at: int | None = Field(alias="lastMessageAt")


class AgentMessageItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    conversation_id: str = Field(alias="conversationId")
    direction: str
    message_type: str = Field(alias="messageType")
    text_content: str | None = Field(alias="textContent")
    state: str
    from_phone_number: str | None = Field(alias="fromPhoneNumber")
    to_phone_number: str | None = Field(alias="toPhoneNumber")
    created_at: int = Field(alias="createdAt")
    received_at: int | None = Field(alias="receivedAt")
    sent_at: int | None = Field(alias="sentAt")
    delivered_at: int | None = Field(alias="deliveredAt")
```

- [ ] **Step 3: Create conversation service**

Create `app/services/agent_conversation_service.py` with:

```python
class ConversationForbidden(Exception):
    pass


class ConversationNotFound(Exception):
    pass
```

Use this access check in every per-conversation method:

```sql
SELECT id
FROM conversations
WHERE id = %s
  AND status IN ('OPEN', 'CLOSED', 'ARCHIVED')
  AND NULLIF(BTRIM(areas), '') = NULLIF(BTRIM(%s), '')
```

If the conversation id exists without matching the agent area, raise `ConversationForbidden`. If it does not exist, raise `ConversationNotFound`.

- [ ] **Step 4: Create conversation router**

Create `app/api/agent_conversation.py` with:

```text
GET /agent/v1/conversations
GET /agent/v1/conversations/{conversation_id}/messages
PATCH /agent/v1/conversations/{conversation_id}/read
```

Use agent bearer auth from `AgentAuthService.authenticate`.

- [ ] **Step 5: Implement read marking**

`PATCH /agent/v1/conversations/{conversation_id}/read` must:

```sql
UPDATE conversations
SET unread_count = 0,
    updated_at = %s
WHERE id = %s
  AND NULLIF(BTRIM(areas), '') = NULLIF(BTRIM(%s), '')
```

Return:

```json
{"ok": true}
```

- [ ] **Step 6: Wire and test**

Run:

```powershell
pytest tests/test_agent_conversation_api.py -v
```

Expected: list filtering, message access, and read marking pass.

---

### Task 4: Add Agent Reply API

**Files:**
- Modify: `app/schemas/agent_conversation.py`
- Modify: `app/services/agent_conversation_service.py`
- Modify: `app/api/agent_conversation.py`
- Test: `tests/test_agent_conversation_api.py`

- [ ] **Step 1: Add reply test**

Add a test that logs in as an agent whose `areas` matches the conversation, calls:

```text
POST /agent/v1/conversations/{conversationId}/messages
```

with:

```json
{"text": "客服回复"}
```

and asserts a new `messages` row exists with:

```text
direction = OUTBOUND
conversation_id = requested conversation id
to_phone_number = conversations.external_phone_number
device_id = conversations.device_id
sim_card_id = conversations.sim_card_id
sim_number = conversations.sim_number
state = Pending
```

- [ ] **Step 2: Add reply schema**

Add to `app/schemas/agent_conversation.py`:

```python
from typing import Annotated

from pydantic import StringConstraints


AgentReplyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


class AgentReplyRequest(BaseModel):
    text: AgentReplyText
```

- [ ] **Step 3: Reuse existing outbound creation**

In the agent conversation service, create a method that loads the conversation route after the area check and calls `MessageCommandService.create` with:

```python
MessageCreateRequest(
    phoneNumbers=[conversation.external_phone_number],
    text=request.text,
    deviceId=conversation.device_id,
    simNumber=conversation.sim_number,
    conversationId=conversation.id,
    metadata={"source": "agent", "agentId": agent.id},
)
```

Use an idempotency key generated by the agent router from:

```text
agent:{agentId}:conversation:{conversationId}:{Idempotency-Key}
```

Require `Idempotency-Key` on the reply endpoint and return `400 VALIDATION_ERROR` when it is missing or longer than 200 characters.

- [ ] **Step 4: Run reply tests**

Run:

```powershell
pytest tests/test_agent_conversation_api.py -v
```

Expected: reply creates an outbound pending SMS task and rejects cross-area access.

---

### Task 5: Add Agent SSE Events

**Files:**
- Create: `app/services/agent_event_publisher.py`
- Create: `app/api/agent_events.py`
- Modify: `app/application.py`
- Modify: `app/services/inbound_message_service.py`
- Test: `tests/integration/test_agent_conversation_area_flow.py`

- [ ] **Step 1: Define event payloads**

Use these event names:

```text
inbound_message
conversation_updated
message_status_updated
```

Use this payload shape:

```json
{
  "conversationId": "conv_xxx",
  "messageId": "msg_xxx",
  "areas": "north"
}
```

- [ ] **Step 2: Create area-aware registry**

Create `app/services/agent_event_publisher.py` with a registry keyed by agent area. It should mirror the existing `app/services/sse.py` style but register by `areas` instead of `device_id`.

- [ ] **Step 3: Create agent events router**

Create `app/api/agent_events.py`:

```text
GET /agent/v1/events
```

Authenticate the agent, register the SSE connection under `agent.areas`, and stream events with media type `text/event-stream`.

- [ ] **Step 4: Publish inbound events**

In `InboundMessageService.create`, after the transaction commits and after `InboundResult` is available, publish an agent event with `conversation_id`, `message_id`, and the conversation area.

- [ ] **Step 5: Run SSE integration tests**

Run:

```powershell
pytest tests/integration/test_agent_conversation_area_flow.py -v
```

Expected: an inbound message for area `north` is visible to a north agent event stream and not visible to a south agent event stream.

---

## Verification

Run the focused suite:

```powershell
pytest tests/test_agent_auth_api.py tests/test_agent_conversation_api.py tests/integration/test_agent_conversation_area_flow.py -v
```

Run the existing SMS gateway suite:

```powershell
pytest tests/test_message_api.py tests/test_inbox_api.py tests/integration/test_message_api.py tests/integration/test_inbox_api_integration.py -v
```

Run the full suite before handing off:

```powershell
pytest
```

Expected result: all tests pass.
