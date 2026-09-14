# PostgreSQL Schema Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the three PostgreSQL initialization scripts with an interface-aligned schema that stores every instant as UTC Unix milliseconds in `BIGINT` columns.

**Architecture:** Keep the existing three-file initialization order. `001_device.sql` owns devices and SIM routing, `002_conversation.sql` owns contacts and messaging, and `003_other.sql` owns accounts and products. Foreign keys follow file and table creation order, while API-facing ISO-8601 conversion remains outside the database schema.

**Tech Stack:** PostgreSQL SQL, PowerShell/`rg` static verification, optional `psql` runtime verification.

---

### Task 1: Align device and SIM tables

**Files:**
- Modify: `pg/init/001_device.sql`

- [ ] **Step 1: Run contract checks and confirm the old schema fails**

Run:

```powershell
rg -n "TIMESTAMP WITH TIME ZONE|CREATE TABLE device_sim_cards|registered_at" pg/init/001_device.sql
rg -n "CREATE TABLE sim_cards|subscription_id|areas|push_token" pg/init/001_device.sql
```

Expected: the first command finds old definitions; the second command does not find the complete required definitions.

- [ ] **Step 2: Replace `001_device.sql` with the approved schema**

The replacement must define `devices` before `sim_cards`, use `BIGINT` for all times, use `registered` rather than `registered_at`, include nullable `push_token` and `subscription_id`, and include `areas`. Use these exact table and constraint shapes:

```sql
CREATE TABLE devices (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(100),
    model VARCHAR(100),
    android_version VARCHAR(50),
    app_version VARCHAR(50),
    push_token TEXT,
    token_hash VARCHAR(255) NOT NULL UNIQUE,
    login VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(30) NOT NULL DEFAULT 'offline',
    last_seen_at BIGINT,
    registered BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    CONSTRAINT chk_device_status CHECK (status IN ('online', 'offline', 'disabled'))
);

CREATE TABLE sim_cards (
    id VARCHAR(64) PRIMARY KEY,
    device_id VARCHAR(64) NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    sim_type VARCHAR(20) NOT NULL DEFAULT 'PHYSICAL',
    slot_index INTEGER NOT NULL,
    sim_number INTEGER NOT NULL,
    subscription_id INTEGER,
    phone_number VARCHAR(50),
    carrier_name VARCHAR(100),
    iccid_hash VARCHAR(255),
    esim_profile_name VARCHAR(100),
    esim_group_id VARCHAR(100),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(30) NOT NULL DEFAULT 'active',
    last_used_at BIGINT,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    areas VARCHAR(100),
    CONSTRAINT chk_sim_type CHECK (sim_type IN ('PHYSICAL', 'ESIM')),
    CONSTRAINT chk_sim_status CHECK (status IN ('active', 'inactive', 'disabled')),
    CONSTRAINT chk_sim_slot_index CHECK (slot_index >= 0),
    CONSTRAINT chk_sim_number CHECK (sim_number >= 1),
    CONSTRAINT uq_sim_device_number UNIQUE (device_id, sim_number),
    CONSTRAINT uq_sim_device_slot UNIQUE (device_id, slot_index),
    CONSTRAINT uq_sim_device_subscription UNIQUE (device_id, subscription_id)
);
```

Add these indexes:

```sql
CREATE INDEX idx_devices_status ON devices(status);
CREATE INDEX idx_devices_last_seen_at ON devices(last_seen_at);
CREATE INDEX idx_sim_cards_device_id ON sim_cards(device_id);
CREATE INDEX idx_sim_cards_sim_type ON sim_cards(sim_type);
CREATE INDEX idx_sim_cards_status ON sim_cards(status);
CREATE INDEX idx_sim_cards_areas ON sim_cards(areas);
CREATE INDEX idx_sim_cards_last_used_at ON sim_cards(last_used_at);
```

- [ ] **Step 3: Verify the device/SIM contract**

Run:

```powershell
rg -n "TIMESTAMP WITH TIME ZONE|CREATE TABLE device_sim_cards|registered_at" pg/init/001_device.sql
rg -n "CREATE TABLE sim_cards|subscription_id|areas|push_token|last_seen_at +BIGINT" pg/init/001_device.sql
```

Expected: the first command prints nothing; the second finds all required definitions.

### Task 2: Align contacts, conversations, messages, recipients, and history

**Files:**
- Modify: `pg/init/002_conversation.sql`

- [ ] **Step 1: Run contract checks and confirm the old schema fails**

Run:

```powershell
rg -n "contact_name|device_sim_cards|TIMESTAMP WITH TIME ZONE" pg/init/002_conversation.sql
rg -n "CREATE TABLE message_state_history" pg/init/002_conversation.sql
rg -n "CREATE TABLE message_recipients|contact_id|is_encrypted|data_base64" pg/init/002_conversation.sql
```

Expected: old fields and timestamp types are found, `message_state_history` appears twice, and required recipient/interface fields are incomplete.

- [ ] **Step 2: Reorder and replace table definitions**

Define tables in this order so every foreign key target exists first:

```text
contacts -> conversations -> messages -> message_recipients -> message_state_history
```

Use these exact definitions:

```sql
CREATE TABLE contacts (
    id VARCHAR(64) PRIMARY KEY,
    display_name VARCHAR(100),
    phone_number VARCHAR(50) NOT NULL,
    normalized_phone_number VARCHAR(50) NOT NULL,
    avatar_url TEXT,
    remark TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
    source VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
    last_contact_at BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    areas VARCHAR(100),
    CONSTRAINT chk_contact_status CHECK (status IN ('NORMAL', 'BLOCKED', 'ARCHIVED')),
    CONSTRAINT chk_contact_source CHECK (source IN ('MANUAL', 'INBOUND_AUTO', 'IMPORTED')),
    CONSTRAINT uq_contact_phone UNIQUE (normalized_phone_number)
);

CREATE TABLE conversations (
    id VARCHAR(64) PRIMARY KEY,
    external_phone_number VARCHAR(50) NOT NULL,
    contact_id VARCHAR(64) NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
    device_id VARCHAR(64) NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    sim_card_id VARCHAR(64) REFERENCES sim_cards(id) ON DELETE SET NULL,
    sim_number INTEGER,
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    unread_count INTEGER NOT NULL DEFAULT 0,
    last_message_preview VARCHAR(255),
    last_message_direction VARCHAR(20),
    last_message_at BIGINT,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    CONSTRAINT chk_conversation_status CHECK (status IN ('OPEN', 'CLOSED', 'ARCHIVED')),
    CONSTRAINT chk_conversation_unread_count CHECK (unread_count >= 0),
    CONSTRAINT chk_conversation_sim_number CHECK (sim_number IS NULL OR sim_number >= 1),
    CONSTRAINT chk_last_message_direction CHECK (
        last_message_direction IS NULL
        OR last_message_direction IN ('OUTBOUND', 'INBOUND')
    ),
    CONSTRAINT uq_conversation_route UNIQUE (external_phone_number, device_id, sim_card_id)
);

CREATE TABLE messages (
    id VARCHAR(64) PRIMARY KEY,
    conversation_id VARCHAR(64) NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction VARCHAR(20) NOT NULL,
    message_type VARCHAR(20) NOT NULL DEFAULT 'SMS',
    text_content TEXT,
    data_base64 TEXT,
    data_port INTEGER,
    from_phone_number VARCHAR(50),
    to_phone_number VARCHAR(50),
    state VARCHAR(20) NOT NULL,
    device_id VARCHAR(64) NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    sim_card_id VARCHAR(64) REFERENCES sim_cards(id) ON DELETE SET NULL,
    sim_number INTEGER,
    priority SMALLINT NOT NULL DEFAULT 0,
    with_delivery_report BOOLEAN NOT NULL DEFAULT TRUE,
    is_encrypted BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key VARCHAR(200),
    valid_until BIGINT,
    schedule_at BIGINT,
    pulled_at BIGINT,
    sent_at BIGINT,
    delivered_at BIGINT,
    received_at BIGINT,
    error_code VARCHAR(100),
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    CONSTRAINT chk_message_direction CHECK (direction IN ('OUTBOUND', 'INBOUND')),
    CONSTRAINT chk_message_type CHECK (message_type IN ('SMS', 'DATA_SMS')),
    CONSTRAINT chk_message_state CHECK (
        state IN ('Pending', 'Processed', 'Sent', 'Delivered', 'Failed', 'Received')
    ),
    CONSTRAINT chk_message_priority CHECK (priority BETWEEN -128 AND 127),
    CONSTRAINT chk_message_sim_number CHECK (sim_number IS NULL OR sim_number >= 1),
    CONSTRAINT chk_message_data_port CHECK (data_port IS NULL OR data_port BETWEEN 0 AND 65535),
    CONSTRAINT chk_message_content CHECK (
        (message_type = 'SMS' AND text_content IS NOT NULL AND data_base64 IS NULL AND data_port IS NULL)
        OR
        (message_type = 'DATA_SMS' AND text_content IS NULL AND data_base64 IS NOT NULL AND data_port IS NOT NULL)
    ),
    CONSTRAINT chk_message_route_phones CHECK (
        (direction = 'OUTBOUND' AND to_phone_number IS NOT NULL)
        OR
        (direction = 'INBOUND' AND from_phone_number IS NOT NULL)
    )
);

CREATE TABLE message_recipients (
    id BIGSERIAL PRIMARY KEY,
    message_id VARCHAR(64) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    phone_number VARCHAR(50) NOT NULL,
    state VARCHAR(20) NOT NULL DEFAULT 'Pending',
    error TEXT,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    CONSTRAINT chk_recipient_state CHECK (
        state IN ('Pending', 'Processed', 'Sent', 'Delivered', 'Failed')
    ),
    CONSTRAINT uq_message_recipient UNIQUE (message_id, phone_number)
);

CREATE TABLE message_state_history (
    id BIGSERIAL PRIMARY KEY,
    message_id VARCHAR(64) NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    state VARCHAR(20) NOT NULL,
    source VARCHAR(20) NOT NULL,
    reason TEXT,
    occurred_at BIGINT NOT NULL,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    CONSTRAINT chk_history_state CHECK (
        state IN ('Pending', 'Processed', 'Sent', 'Delivered', 'Failed', 'Received')
    ),
    CONSTRAINT chk_history_source CHECK (source IN ('API', 'DEVICE', 'SERVER', 'SYSTEM')),
    CONSTRAINT uq_message_history_state UNIQUE (message_id, state)
);
```

Use `((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT)` as the default for server-created times. `occurred_at` has no default because callers must explicitly choose the actual event time: the API/server current time for creation events or the validated Android-provided time for device events.

- [ ] **Step 3: Add query-path indexes**

Add exactly these indexes:

```sql
CREATE INDEX idx_contacts_display_name ON contacts(display_name);
CREATE INDEX idx_contacts_phone_number ON contacts(normalized_phone_number);
CREATE INDEX idx_contacts_last_contact ON contacts(last_contact_at DESC);
CREATE INDEX idx_contacts_areas ON contacts(areas);
CREATE INDEX idx_conversations_last_message ON conversations(last_message_at DESC);
CREATE INDEX idx_conversations_external_phone ON conversations(external_phone_number);
CREATE INDEX idx_conversations_contact ON conversations(contact_id);
CREATE INDEX idx_conversations_device ON conversations(device_id);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at DESC);
CREATE INDEX idx_messages_device_state ON messages(device_id, state);
CREATE INDEX idx_messages_state ON messages(state);
CREATE INDEX idx_messages_pull_queue ON messages(device_id, state, created_at);
CREATE UNIQUE INDEX uq_messages_idempotency
    ON messages(device_id, direction, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_message_recipients_state ON message_recipients(message_id, state);
CREATE INDEX idx_message_state_history ON message_state_history(message_id, occurred_at);
```

- [ ] **Step 4: Verify the messaging contract**

Run:

```powershell
rg -n "TIMESTAMP WITH TIME ZONE|contact_name|device_sim_cards" pg/init/002_conversation.sql
(rg -n "CREATE TABLE message_state_history" pg/init/002_conversation.sql | Measure-Object).Count
rg -n "CREATE TABLE message_recipients|contact_id|is_encrypted|data_base64|device_id, direction, idempotency_key" pg/init/002_conversation.sql
```

Expected: the first command prints nothing, the count is `1`, and all required fields/index keys are found.

### Task 3: Complete account and product tables

**Files:**
- Modify: `pg/init/003_other.sql`

- [ ] **Step 1: Confirm the existing file is incomplete**

Run:

```powershell
rg -n "CREATE TABLE accounts|password_hash|use_sims_id|update_by|areas|BIGINT" pg/init/003_other.sql
```

Expected: required definitions are missing.

- [ ] **Step 2: Replace the incomplete SQL**

Create `accounts` first, followed by `products`:

```sql
CREATE TABLE accounts (
    id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    areas VARCHAR(100),
    use_sims_id VARCHAR(64) REFERENCES sim_cards(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    CONSTRAINT chk_account_status CHECK (status IN ('ACTIVE', 'DISABLED'))
);

CREATE TABLE products (
    id VARCHAR(64) PRIMARY KEY,
    menu TEXT,
    update_time BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    update_by VARCHAR(64) REFERENCES accounts(id) ON DELETE SET NULL,
    areas VARCHAR(100)
);
```

Add these indexes:

```sql
CREATE INDEX idx_accounts_areas ON accounts(areas);
CREATE INDEX idx_accounts_use_sims ON accounts(use_sims_id);
CREATE INDEX idx_accounts_status ON accounts(status);
CREATE INDEX idx_products_areas ON products(areas);
CREATE INDEX idx_products_update_time ON products(update_time DESC);
```

- [ ] **Step 3: Verify account and product definitions**

Run:

```powershell
rg -n "CREATE TABLE accounts|password_hash|use_sims_id|CREATE TABLE products|update_time +BIGINT|update_by|areas" pg/init/003_other.sql
rg -n "TIMESTAMP WITH TIME ZONE" pg/init/003_other.sql
```

Expected: all required definitions are found and no timestamp type is found.

### Task 4: Verify all initialization scripts together

**Files:**
- Verify: `pg/init/001_device.sql`
- Verify: `pg/init/002_conversation.sql`
- Verify: `pg/init/003_other.sql`

- [ ] **Step 1: Run repository-wide static checks**

Run:

```powershell
rg -n "TIMESTAMP\s+(WITH|WITHOUT)\s+TIME\s+ZONE|\bTIMESTAMP\b" pg/init
rg -n "device_sim_cards|contact_name|registered_at" pg/init
rg -n "^CREATE TABLE" pg/init
```

Expected: the first two commands print nothing; the third lists exactly nine tables: `devices`, `sim_cards`, `contacts`, `conversations`, `messages`, `message_recipients`, `message_state_history`, `accounts`, and `products`.

- [ ] **Step 2: Check PostgreSQL tooling availability**

Run:

```powershell
Get-Command psql -ErrorAction SilentlyContinue
Get-Command docker -ErrorAction SilentlyContinue
```

Expected: if either tool and a reachable PostgreSQL runtime are available, perform an empty-database parse/execution test. Otherwise record that verification is static only.

- [ ] **Step 3: Execute against an empty PostgreSQL database when available**

Run the files in order:

```text
pg/init/001_device.sql
pg/init/002_conversation.sql
pg/init/003_other.sql
```

Expected: all statements succeed, all nine tables exist, and PostgreSQL reports no syntax, duplicate object, or missing relation errors.

- [ ] **Step 4: Review the final diff**

Run:

```powershell
git diff -- pg/init/001_device.sql pg/init/002_conversation.sql pg/init/003_other.sql
```

Expected when this workspace is a Git repository: only the three intended SQL files are changed. In the current workspace, which is not a Git repository, inspect the three full files directly instead.
