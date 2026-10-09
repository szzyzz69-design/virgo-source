"""Real PostgreSQL checks for existing administrative safety boundaries."""
from uuid import uuid4

import psycopg
import pytest

from app.database import Database
from pg.admin_service import AccountCreate, AccountUpdate, PgAdminService, SimCardUpdate


@pytest.fixture
def administrative_fixture(clean_database):
    suffix = uuid4().hex
    device = clean_database.track("dev_admin_boundary_" + suffix)
    account = "acct_admin_boundary_" + suffix
    sims = ["sim_admin_boundary_" + suffix + str(n) for n in range(4)]
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    contact, conversation = "contact_admin_" + suffix, "conv_admin_" + suffix
    message = "msg_admin_" + suffix
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("INSERT INTO devices(id,name,token_hash,login) VALUES(%s,'isolated admin fixture',%s,%s)", (device, suffix, suffix))
        for number, identity in enumerate(sims):
            connection.execute("INSERT INTO sim_cards(id,device_id,slot_index,sim_number,subscription_id,phone_number,iccid_hash,esim_group_id) VALUES(%s,%s,%s,%s,%s,%s,'original-iccid','original-group')", (identity, device, number, number + 1, 10 + number, "+1604555" + str(9000 + number)))
        connection.execute("UPDATE sim_cards SET enabled=FALSE,status='disabled' WHERE id=%s", (sims[1],))
        connection.execute("UPDATE sim_cards SET status='inactive' WHERE id=%s", (sims[2],))
        connection.execute("UPDATE sim_cards SET unregistered_at=123 WHERE id=%s", (sims[3],))
        connection.execute("INSERT INTO accounts(id,username,password_hash,areas,use_sims_id) VALUES(%s,%s,'preserve-hash','Original',%s)", (account, account, sims[0]))
        connection.execute("INSERT INTO account_sim_cards(account_id,sim_card_id) VALUES(%s,%s)", (account, sims[0]))
        connection.execute("INSERT INTO contacts(id,phone_number,normalized_phone_number,remark) VALUES(%s,%s,%s,'original customer remark')", (contact, phone, phone))
        connection.execute("INSERT INTO conversations(id,external_phone_number,contact_id,device_id,sim_card_id,sim_number,areas) VALUES(%s,%s,%s,%s,%s,1,'Original')", (conversation, phone, contact, device, sims[0]))
        connection.execute("INSERT INTO messages(id,conversation_id,direction,text_content,from_phone_number,state,device_id,sim_card_id,sim_number) VALUES(%s,%s,'INBOUND','original history',%s,'Received',%s,%s,1)", (message, conversation, phone, device, sims[0]))
    yield dict(dsn=clean_database.dsn, device=device, account=account, sims=sims, message=message)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("DELETE FROM accounts WHERE id=ANY(%s::varchar[])", ([account, "invalid_" + suffix],))


def account_snapshot(fixture):
    with psycopg.connect(fixture["dsn"]) as connection:
        return (connection.execute("SELECT to_jsonb(a) FROM accounts a WHERE id=%s", (fixture["account"],)).fetchone(),
                connection.execute("SELECT to_jsonb(a) FROM account_sim_cards a WHERE account_id=%s ORDER BY sim_card_id", (fixture["account"],)).fetchall())


def test_active_sim_options_exclude_disabled_inactive_and_unregistered(administrative_fixture):
    fixture = administrative_fixture
    options = PgAdminService(Database(fixture["dsn"])).list_sim_card_options()
    ids = {option["id"] for option in options}
    assert fixture["sims"][0] in ids
    assert not set(fixture["sims"][1:]) & ids


@pytest.mark.parametrize("index", [1, 2, 3])
def test_invalid_sim_binding_rolls_back_account_changes_and_original_binding(administrative_fixture, index):
    fixture = administrative_fixture
    before = account_snapshot(fixture)
    service = PgAdminService(Database(fixture["dsn"]))
    with pytest.raises(ValueError, match="SIM card is not enabled"):
        service.update_account(fixture["account"], AccountUpdate(username="changed name", password="changed password", areas="Changed", use_sims_ids=(fixture["sims"][0], fixture["sims"][index])))
    assert account_snapshot(fixture) == before


def test_safe_sim_edit_preserves_hardware_identity_account_binding_and_chat(administrative_fixture):
    fixture = administrative_fixture
    with psycopg.connect(fixture["dsn"]) as connection:
        identity_before = connection.execute("SELECT id,device_id,slot_index,sim_number,subscription_id,iccid_hash,esim_group_id FROM sim_cards WHERE id=%s", (fixture["sims"][0],)).fetchone()
        history_before = connection.execute("SELECT to_jsonb(m) FROM messages m WHERE id=%s", (fixture["message"],)).fetchone()
    account_before = account_snapshot(fixture)
    PgAdminService(Database(fixture["dsn"]), now_ms=lambda: 987654).update_sim_card(fixture["sims"][0], SimCardUpdate(phone_number="+16045559999", carrier_name="Updated display", esim_profile_name="Updated note", enabled=True, areas="Renamed display area", display_updated_at=123456))
    with psycopg.connect(fixture["dsn"]) as connection:
        assert connection.execute("SELECT id,device_id,slot_index,sim_number,subscription_id,iccid_hash,esim_group_id FROM sim_cards WHERE id=%s", (fixture["sims"][0],)).fetchone() == identity_before
        assert connection.execute("SELECT to_jsonb(m) FROM messages m WHERE id=%s", (fixture["message"],)).fetchone() == history_before
        assert connection.execute("SELECT phone_number,esim_profile_name,areas FROM sim_cards WHERE id=%s", (fixture["sims"][0],)).fetchone() == ("+16045559999", "Updated note", "Renamed display area")
    assert account_snapshot(fixture) == account_before
