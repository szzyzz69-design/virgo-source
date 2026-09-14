-- Keep a standalone audit snapshot before removing a SIM or changing its number.
-- No FK to sim_cards/devices: history must survive deletion of the live record.
CREATE TABLE IF NOT EXISTS sim_card_history (
    id BIGSERIAL PRIMARY KEY,
    sim_card_id VARCHAR(64) NOT NULL,
    device_id VARCHAR(64) NOT NULL,
    device_name TEXT,
    phone_number VARCHAR(50),
    replacement_phone VARCHAR(50),
    areas VARCHAR(100),
    event_type VARCHAR(30) NOT NULL,
    reason TEXT NOT NULL,
    occurred_at BIGINT NOT NULL,
    snapshot JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sim_card_history_time ON sim_card_history(occurred_at DESC,id DESC);

CREATE OR REPLACE FUNCTION archive_sim_card_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    change_reason TEXT;
    replacement TEXT;
    change_type TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        change_reason := CASE WHEN OLD.unregistered_at IS NOT NULL THEN '设备或 SIM 已注销' ELSE 'SIM 已删除' END;
        change_type := 'DELETED';
    ELSIF (NULLIF(regexp_replace(COALESCE(OLD.phone_number,''),'[^0-9]','','g'),'') IS NOT NULL
           AND regexp_replace(COALESCE(OLD.phone_number,''),'[^0-9]','','g')
               IS DISTINCT FROM regexp_replace(COALESCE(NEW.phone_number,''),'[^0-9]','','g'))
       OR (OLD.iccid_hash IS NOT NULL AND NEW.iccid_hash IS NOT NULL AND OLD.iccid_hash <> NEW.iccid_hash) THEN
        change_reason := '号码或 SIM 已更换';
        change_type := 'REPLACED';
        replacement := NEW.phone_number;
    ELSE
        RETURN NEW;
    END IF;
    INSERT INTO sim_card_history(sim_card_id,device_id,device_name,phone_number,replacement_phone,
                                 areas,event_type,reason,occurred_at,snapshot)
    VALUES(OLD.id,OLD.device_id,(SELECT name FROM devices WHERE id=OLD.device_id),OLD.phone_number,replacement,
           OLD.areas,change_type,change_reason,
           CASE WHEN TG_OP='DELETE' THEN COALESCE(OLD.unregistered_at,(EXTRACT(EPOCH FROM clock_timestamp())*1000)::BIGINT)
                ELSE (EXTRACT(EPOCH FROM clock_timestamp())*1000)::BIGINT END,to_jsonb(OLD));
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_sim_card_history ON sim_cards;
CREATE TRIGGER trg_sim_card_history BEFORE DELETE OR UPDATE OF phone_number,iccid_hash ON sim_cards
FOR EACH ROW EXECUTE FUNCTION archive_sim_card_change();

-- Unregistering a device now removes its SIM rows, after the audit trigger saves them.
CREATE OR REPLACE FUNCTION remove_unregistered_device_sims() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.unregistered_at IS NOT NULL AND OLD.unregistered_at IS NULL THEN
        UPDATE sim_cards SET unregistered_at=NEW.unregistered_at WHERE device_id=NEW.id;
        DELETE FROM sim_cards WHERE device_id=NEW.id;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_remove_unregistered_device_sims ON devices;
CREATE TRIGGER trg_remove_unregistered_device_sims AFTER UPDATE OF unregistered_at ON devices
FOR EACH ROW EXECUTE FUNCTION remove_unregistered_device_sims();

-- Remove already retired rows. Messages/conversations retain their data through SET NULL FKs.
DELETE FROM sim_cards s USING devices d
WHERE s.device_id=d.id AND (s.unregistered_at IS NOT NULL OR d.unregistered_at IS NOT NULL);
