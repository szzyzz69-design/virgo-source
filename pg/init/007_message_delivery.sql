-- Additive protocol-v2 delivery ledger. Do not backfill historical Processed
-- messages: only a new v2 pull of a Pending message creates a claim.
CREATE TABLE IF NOT EXISTS message_deliveries (
    message_id VARCHAR(64) PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    device_id VARCHAR(64) NOT NULL REFERENCES devices(id) ON DELETE RESTRICT,
    claim_token_hash VARCHAR(64) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    sim_fingerprint VARCHAR(64) NOT NULL,
    claimed_at BIGINT NOT NULL,
    lease_expires_at BIGINT NOT NULL,
    accepted_at BIGINT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT chk_delivery_lease CHECK (lease_expires_at > claimed_at),
    CONSTRAINT chk_delivery_attempts CHECK (attempt_count >= 1)
);
CREATE INDEX IF NOT EXISTS idx_message_deliveries_unaccepted
    ON message_deliveries(device_id, lease_expires_at) WHERE accepted_at IS NULL;
COMMENT ON TABLE message_deliveries IS
    'v2 Pending claims; accepted_at means device durably accepted. Accepted tasks never lease-redeliver.';
