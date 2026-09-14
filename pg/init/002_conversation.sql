-- 联系人表。所有时间字段均为 UTC Unix 毫秒。
CREATE TABLE contacts (
    id                      VARCHAR(64) PRIMARY KEY,
    display_name            VARCHAR(100),
    phone_number            VARCHAR(50) NOT NULL,
    normalized_phone_number VARCHAR(50) NOT NULL,
    avatar_url              TEXT,
    remark                  TEXT,
    status                  VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
    source                  VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
    last_contact_at         BIGINT,
    metadata                JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at              BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at              BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    areas                   VARCHAR(100),

    CONSTRAINT chk_contact_status
        CHECK (status IN ('NORMAL', 'BLOCKED', 'ARCHIVED')),
    CONSTRAINT chk_contact_source
        CHECK (source IN ('MANUAL', 'INBOUND_AUTO', 'IMPORTED')),
    CONSTRAINT uq_contact_phone
        UNIQUE (normalized_phone_number)
);

CREATE INDEX idx_contacts_display_name ON contacts(display_name);
CREATE INDEX idx_contacts_phone_number ON contacts(normalized_phone_number);
CREATE INDEX idx_contacts_last_contact ON contacts(last_contact_at DESC);
CREATE INDEX idx_contacts_areas ON contacts(areas);

COMMENT ON COLUMN contacts.status IS
    'NORMAL=正常，BLOCKED=拉黑，ARCHIVED=管理面板删除时归档保留历史会话';

-- 会话表。
CREATE TABLE conversations (
    id                      VARCHAR(64) PRIMARY KEY,
    external_phone_number   VARCHAR(50) NOT NULL,
    contact_id              VARCHAR(64) NOT NULL
                            REFERENCES contacts(id) ON DELETE RESTRICT,
    device_id               VARCHAR(64) NOT NULL
                            REFERENCES devices(id) ON DELETE RESTRICT,
    sim_card_id             VARCHAR(64)
                            REFERENCES sim_cards(id) ON DELETE SET NULL,
    sim_number              INTEGER,
    areas                   VARCHAR(100),
    status                  VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    unread_count            INTEGER NOT NULL DEFAULT 0,
    last_message_preview    VARCHAR(255),
    last_message_direction  VARCHAR(20),
    last_message_at         BIGINT,
    created_at              BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at              BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_conversation_status
        CHECK (status IN ('OPEN', 'CLOSED', 'ARCHIVED')),
    CONSTRAINT chk_conversation_unread_count
        CHECK (unread_count >= 0),
    CONSTRAINT chk_conversation_sim_number
        CHECK (sim_number IS NULL OR sim_number >= 1),
    CONSTRAINT chk_last_message_direction
        CHECK (
            last_message_direction IS NULL
            OR last_message_direction IN ('OUTBOUND', 'INBOUND')
        ),
    CONSTRAINT uq_conversation_route
        UNIQUE (external_phone_number, device_id, sim_card_id)
);

CREATE INDEX idx_conversations_last_message ON conversations(last_message_at DESC);
CREATE INDEX idx_conversations_external_phone ON conversations(external_phone_number);
CREATE INDEX idx_conversations_contact ON conversations(contact_id);
CREATE INDEX idx_conversations_device ON conversations(device_id);
CREATE INDEX idx_conversations_areas ON conversations(areas);

CREATE TABLE messages (
    id                   VARCHAR(64) PRIMARY KEY,
    conversation_id      VARCHAR(64) NOT NULL
                         REFERENCES conversations(id) ON DELETE CASCADE,
    direction            VARCHAR(20) NOT NULL,
    message_type         VARCHAR(20) NOT NULL DEFAULT 'SMS',
    text_content         TEXT,
    data_base64          TEXT,
    data_port            INTEGER,
    from_phone_number    VARCHAR(50),
    to_phone_number      VARCHAR(50),
    state                VARCHAR(20) NOT NULL,
    device_id            VARCHAR(64) NOT NULL
                         REFERENCES devices(id) ON DELETE RESTRICT,
    sim_card_id          VARCHAR(64)
                         REFERENCES sim_cards(id) ON DELETE SET NULL,
    sim_number           INTEGER,
    priority             SMALLINT NOT NULL DEFAULT 0,
    with_delivery_report BOOLEAN NOT NULL DEFAULT TRUE,
    is_encrypted         BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key      VARCHAR(200),
    valid_until          BIGINT,
    schedule_at          BIGINT,
    pulled_at            BIGINT,
    sent_at              BIGINT,
    delivered_at         BIGINT,
    received_at          BIGINT,
    error_code           VARCHAR(100),
    error_message        TEXT,
    metadata             JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at           BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at           BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_message_direction
        CHECK (direction IN ('OUTBOUND', 'INBOUND')),
    CONSTRAINT chk_message_type
        CHECK (message_type IN ('SMS', 'DATA_SMS', 'MMS')),
    CONSTRAINT chk_message_state
        CHECK (state IN (
            'Pending',
            'Processed',
            'Sent',
            'Delivered',
            'Failed',
            'Received'
        )),
    CONSTRAINT chk_message_priority
        CHECK (priority BETWEEN -128 AND 127),
    CONSTRAINT chk_message_sim_number
        CHECK (sim_number IS NULL OR sim_number >= 1),
    CONSTRAINT chk_message_data_port
        CHECK (data_port IS NULL OR data_port BETWEEN 0 AND 65535),
    CONSTRAINT chk_message_content
        CHECK (
            (
                message_type = 'SMS'
                AND text_content IS NOT NULL
                AND data_base64 IS NULL
                AND data_port IS NULL
            )
            OR
            (
                message_type = 'DATA_SMS'
                AND text_content IS NULL
                AND data_base64 IS NOT NULL
                AND (direction = 'INBOUND' OR data_port IS NOT NULL)
            )
            OR
            (
                message_type = 'MMS'
                AND direction = 'INBOUND'
                AND data_base64 IS NULL
                AND data_port IS NULL
            )
        ),
    CONSTRAINT chk_message_route_phones
        CHECK (
            (direction = 'OUTBOUND' AND to_phone_number IS NOT NULL)
            OR
            (direction = 'INBOUND' AND from_phone_number IS NOT NULL)
        )
);

CREATE UNIQUE INDEX uq_messages_idempotency
    ON messages(device_id, direction, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at DESC);
CREATE INDEX idx_messages_device_state ON messages(device_id, state);
CREATE INDEX idx_messages_state ON messages(state);
CREATE INDEX idx_messages_pull_queue ON messages(device_id, state, created_at);

CREATE TABLE message_attachments (
    id           VARCHAR(64) PRIMARY KEY,
    message_id   VARCHAR(64) NOT NULL
                 REFERENCES messages(id) ON DELETE CASCADE,
    part_id      INTEGER NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    name         VARCHAR(255),
    size         BIGINT,
    s3_bucket    VARCHAR(255) NOT NULL,
    s3_key       TEXT NOT NULL,
    url          TEXT,
    etag         VARCHAR(255),
    metadata     JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at   BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at   BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_message_attachment_part_id
        CHECK (part_id >= 0),
    CONSTRAINT chk_message_attachment_size
        CHECK (size IS NULL OR size >= 0),
    CONSTRAINT uq_message_attachment_part
        UNIQUE (message_id, part_id)
);

CREATE INDEX idx_message_attachments_message
    ON message_attachments(message_id, part_id);

-- 每个接收号码的独立状态，兼容 Android recipients 数组。
CREATE TABLE message_recipients (
    id           BIGSERIAL PRIMARY KEY,
    message_id   VARCHAR(64) NOT NULL
                 REFERENCES messages(id) ON DELETE CASCADE,
    phone_number VARCHAR(50) NOT NULL,
    state        VARCHAR(20) NOT NULL DEFAULT 'Pending',
    error        TEXT,
    created_at   BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at   BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_recipient_state
        CHECK (state IN ('Pending', 'Processed', 'Sent', 'Delivered', 'Failed')),
    CONSTRAINT uq_message_recipient
        UNIQUE (message_id, phone_number)
);

CREATE INDEX idx_message_recipients_state
    ON message_recipients(message_id, state);

CREATE TABLE message_state_history (
    id              BIGSERIAL PRIMARY KEY,
    message_id      VARCHAR(64) NOT NULL
                    REFERENCES messages(id) ON DELETE CASCADE,
    state           VARCHAR(20) NOT NULL,
    source          VARCHAR(20) NOT NULL,
    reason          TEXT,
    occurred_at     BIGINT NOT NULL,
    created_at      BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_history_state
        CHECK (state IN (
            'Pending',
            'Processed',
            'Sent',
            'Delivered',
            'Failed',
            'Received'
        )),
    CONSTRAINT chk_history_source
        CHECK (source IN ('API', 'DEVICE', 'SERVER', 'SYSTEM')),
    CONSTRAINT uq_message_history_state
        UNIQUE (message_id, state)
);

CREATE INDEX idx_message_state_history
    ON message_state_history(message_id, occurred_at);
