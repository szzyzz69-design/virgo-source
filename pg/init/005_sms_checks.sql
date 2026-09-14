-- Diagnostic traffic is intentionally separate from messages/conversations.
CREATE TABLE IF NOT EXISTS sms_check_runs (
    id VARCHAR(64) PRIMARY KEY,
    request_key VARCHAR(200) NOT NULL UNIQUE,
    target_phone VARCHAR(50) NOT NULL,
    receiver_device_id VARCHAR(64),
    receiver_sim_number INTEGER,
    timeout_seconds INTEGER NOT NULL,
    created_at BIGINT NOT NULL,
    deadline_at BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS sms_checks (
    id VARCHAR(64) PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL REFERENCES sms_check_runs(id) ON DELETE CASCADE,
    sim_card_id VARCHAR(64) NOT NULL,
    device_id VARCHAR(64) NOT NULL,
    sim_number INTEGER NOT NULL,
    source_phone VARCHAR(50),
    source_iccid_hash VARCHAR(255),
    token VARCHAR(32) NOT NULL UNIQUE,
    text_content TEXT NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('NOT_TESTED','PENDING','NORMAL','PROBLEM')),
    reason TEXT NOT NULL DEFAULT '',
    transport_state VARCHAR(20) NOT NULL DEFAULT 'Pending',
    created_at BIGINT NOT NULL,
    deadline_at BIGINT NOT NULL,
    pulled_at BIGINT,
    sent_at BIGINT,
    received_at BIGINT,
    receipt_source VARCHAR(20),
    updated_at BIGINT NOT NULL,
    UNIQUE (run_id, sim_card_id)
);
CREATE INDEX IF NOT EXISTS idx_sms_checks_pending ON sms_checks(device_id, deadline_at) WHERE status='PENDING';
CREATE INDEX IF NOT EXISTS idx_sms_checks_sim_latest ON sms_checks(sim_card_id, created_at DESC);
