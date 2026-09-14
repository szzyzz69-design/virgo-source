-- 账号表。
CREATE TABLE accounts (
    id            VARCHAR(64) PRIMARY KEY,
    username      VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    areas         VARCHAR(100),
    use_sims_id   VARCHAR(64)
                  REFERENCES sim_cards(id) ON DELETE SET NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',

    CONSTRAINT chk_account_status
        CHECK (status IN ('ACTIVE', 'DISABLED'))
);

CREATE INDEX idx_accounts_areas ON accounts(areas);
CREATE INDEX idx_accounts_use_sims ON accounts(use_sims_id);
CREATE INDEX idx_accounts_status ON accounts(status);

CREATE TABLE account_sim_cards (
    account_id  VARCHAR(64) NOT NULL
                REFERENCES accounts(id) ON DELETE CASCADE,
    sim_card_id VARCHAR(64) NOT NULL
                REFERENCES sim_cards(id) ON DELETE CASCADE,
    created_at  BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    PRIMARY KEY (account_id, sim_card_id)
);

CREATE INDEX idx_account_sim_cards_sim_card
    ON account_sim_cards(sim_card_id);

CREATE TABLE agent_sessions (
    token_hash VARCHAR(255) PRIMARY KEY,
    account_id VARCHAR(64) NOT NULL
               REFERENCES accounts(id) ON DELETE CASCADE,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    expires_at BIGINT NOT NULL
);

CREATE INDEX idx_agent_sessions_account ON agent_sessions(account_id);
CREATE INDEX idx_agent_sessions_expires ON agent_sessions(expires_at);

-- 商品/客服提醒表。update_time 为 UTC Unix 毫秒。
CREATE TABLE products (
    id          VARCHAR(64) PRIMARY KEY,
    menu        TEXT,
    update_time BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    update_by   VARCHAR(64)
                REFERENCES accounts(id) ON DELETE SET NULL,
    areas       VARCHAR(100)
);

CREATE INDEX idx_products_areas ON products(areas);
CREATE INDEX idx_products_update_time ON products(update_time DESC);

-- 地区表。账号和 SIM 卡等业务表继续保存 areas 字符串，后台管理从这里提供可选地区。
CREATE TABLE regions (
    id         VARCHAR(100) PRIMARY KEY,
    created_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),
    updated_at BIGINT NOT NULL DEFAULT ((EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT),

    CONSTRAINT chk_region_id_not_blank
        CHECK (BTRIM(id) <> '')
);
