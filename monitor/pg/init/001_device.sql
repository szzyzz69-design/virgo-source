-- 设备表。所有时间字段均为 UTC Unix 毫秒。
CREATE TABLE devices (
    id                VARCHAR(64) PRIMARY KEY,
    name              VARCHAR(200) NOT NULL,
    manufacturer      VARCHAR(100),
    model             VARCHAR(100),
    android_version   VARCHAR(50),
    app_version       VARCHAR(50),
    push_token        TEXT,
    token_hash        VARCHAR(255) NOT NULL UNIQUE,
    login             VARCHAR(100) NOT NULL UNIQUE,
    password_hash     VARCHAR(255),
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    status            VARCHAR(30) NOT NULL DEFAULT 'offline',
    last_seen_at      BIGINT,
    unregistered_at   BIGINT,
    registered        BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    created_at        BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at        BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_device_status
        CHECK (status IN ('online', 'offline', 'disabled'))
);

CREATE INDEX idx_devices_status ON devices(status);
CREATE INDEX idx_devices_last_seen_at ON devices(last_seen_at);

-- SIM / eSIM 表。表名与接口设计文档保持一致。
CREATE TABLE sim_cards (
    id                VARCHAR(64) PRIMARY KEY,
    device_id         VARCHAR(64) NOT NULL
                      REFERENCES devices(id) ON DELETE CASCADE,
    sim_type          VARCHAR(20) NOT NULL DEFAULT 'PHYSICAL',
    slot_index        INTEGER NOT NULL,
    sim_number        INTEGER NOT NULL,
    subscription_id   INTEGER,
    phone_number      VARCHAR(50),
    carrier_name      VARCHAR(100),
    iccid_hash        VARCHAR(255),
    esim_profile_name VARCHAR(100),
    esim_group_id     VARCHAR(100),
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    status            VARCHAR(30) NOT NULL DEFAULT 'active',
    last_used_at      BIGINT,
    unregistered_at   BIGINT,
    created_at        BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at        BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    areas             VARCHAR(100),

    CONSTRAINT chk_sim_type
        CHECK (sim_type IN ('PHYSICAL', 'ESIM')),
    CONSTRAINT chk_sim_status
        CHECK (status IN ('active', 'inactive', 'disabled')),
    CONSTRAINT chk_sim_slot_index
        CHECK (slot_index >= 0),
    CONSTRAINT chk_sim_number
        CHECK (sim_number >= 1),
    CONSTRAINT uq_sim_device_number
        UNIQUE (device_id, sim_number),
    CONSTRAINT uq_sim_device_slot
        UNIQUE (device_id, slot_index),
    CONSTRAINT uq_sim_device_subscription
        UNIQUE (device_id, subscription_id)
);

CREATE INDEX idx_sim_cards_device_id ON sim_cards(device_id);
CREATE INDEX idx_sim_cards_sim_type ON sim_cards(sim_type);
CREATE INDEX idx_sim_cards_status ON sim_cards(status);
CREATE INDEX idx_sim_cards_areas ON sim_cards(areas);
CREATE INDEX idx_sim_cards_last_used_at ON sim_cards(last_used_at);
