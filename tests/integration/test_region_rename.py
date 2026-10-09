from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from app.database import Database
from app.security import hash_password
from app.services.agent_auth_service import AgentAuthService
from app.services.agent_contact_service import AgentContactService
from app.services.agent_conversation_service import AgentConversationService
from pg.admin_service import PgAdminService, RegionUpdate


@pytest.fixture
def region_database(clean_database):
    # A private schema in the explicitly configured test DB, never production.
    schema = "region_test_" + uuid4().hex
    with psycopg.connect(clean_database.dsn, autocommit=True) as connection:
        connection.execute(f"CREATE SCHEMA {schema}")
    dsn = make_conninfo(clean_database.dsn, options=f"-c search_path={schema}")
    try:
        with psycopg.connect(dsn) as connection:
            for path in sorted((Path(__file__).parents[2] / "pg" / "init").glob("*.sql")):
                connection.execute(path.read_text(encoding="utf-8"), prepare=False)
            connection.execute("INSERT INTO regions(id,created_at,updated_at) VALUES('North',10,20),('South',10,20)")
            connection.execute("INSERT INTO devices(id,name,token_hash,login) VALUES('device','phone','token','login')")
            connection.execute("""INSERT INTO sim_cards(id,device_id,slot_index,sim_number,phone_number,areas,esim_profile_name)
                VALUES('sim','device',0,1,'+16045550100','North','original remark')""")
            connection.execute("""INSERT INTO accounts(id,username,password_hash,areas,use_sims_id)
                VALUES('account','agent',%s,' North ','sim'),('other','other','hash','North-East',NULL)""", (hash_password("test-password"),))
            connection.execute("INSERT INTO account_sim_cards(account_id,sim_card_id) VALUES('account','sim')")
            connection.execute("""INSERT INTO contacts(id,phone_number,normalized_phone_number,areas,remark)
                VALUES('contact','+16045550101','+16045550101','North','keep remark'),
                      ('unassigned','+16045550102','+16045550102',NULL,'unassigned')""")
            connection.execute("""INSERT INTO conversations(id,external_phone_number,contact_id,device_id,sim_card_id,sim_number,areas,unread_count,last_message_preview)
                VALUES('conversation','+16045550101','contact','device','sim',1,'North',2,'keep preview')""")
            connection.execute("""INSERT INTO messages(id,conversation_id,direction,message_type,text_content,from_phone_number,state,device_id,sim_card_id,sim_number)
                VALUES('sms','conversation','INBOUND','SMS','original text','+16045550101','Received','device','sim',1),
                      ('mms','conversation','INBOUND','MMS','original photo','+16045550101','Received','device','sim',1)""")
            connection.execute("""INSERT INTO message_attachments(id,message_id,part_id,content_type,s3_bucket,s3_key,url)
                VALUES('photo','mms',0,'image/png','test-bucket','original/photo.png','https://example.invalid/photo.png')""")
            connection.execute("INSERT INTO products(id,menu,update_by,areas) VALUES('menu','original menu','account','North')")
            connection.execute("""INSERT INTO sim_card_history(sim_card_id,device_id,areas,event_type,reason,occurred_at,snapshot)
                VALUES('retired-sim','device','North','DELETED','original history',5,'{"areas":"North"}')""")
        yield Database(dsn)
    finally:
        with psycopg.connect(clean_database.dsn, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {schema} CASCADE")


def snapshot(database):
    with database.transaction() as connection:
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename").fetchall()
        return {
            table: connection.execute(f"SELECT to_jsonb(t) FROM {table} t ORDER BY to_jsonb(t)::text").fetchall()
            for (table,) in tables
        }


def test_rename_keeps_all_records_bindings_history_and_existing_login(region_database):
    auth = AgentAuthService(region_database)
    session = auth.login("agent", "test-password")
    before = snapshot(region_database)

    PgAdminService(region_database, now_ms=lambda: 123).update_region("North", RegionUpdate(name=" 新地区 "))

    expected = before
    for table in ("accounts", "sim_cards", "contacts", "conversations", "products"):
        for (row,) in expected[table]:
            if (row.get("areas") or "").strip() == "North":
                row["areas"] = "新地区"
    for (row,) in expected["regions"]:
        if row["id"] == "North":
            row.update(id="新地区", updated_at=123)
    after = snapshot(region_database)
    # Compare full rows, so timestamps, phone numbers, notes, content, attachment
    # keys, history snapshots, session tokens and unrelated areas must survive.
    for table in expected:
        assert sorted(expected[table], key=str) == sorted(after[table], key=str), table

    agent = auth.authenticate(session.token)
    assert agent.id == "account" and agent.areas == "新地区"
    contacts = AgentContactService(region_database)
    assert [item.id for item in contacts.list_contacts(agent.areas)] == ["contact"]
    assert contacts.list_sim_cards(agent.id)[0].phone_number == "+16045550100"
    assert contacts.list_menus(agent.id)[0].menu == "original menu"
    conversations = AgentConversationService(region_database, None)
    assert [item.id for item in conversations.list_conversations(agent)] == ["conversation"]
    messages = conversations.list_messages("conversation", agent)
    assert {item.id for item in messages} == {"sms", "mms"}
    photo = next(item for item in messages if item.id == "mms").attachments[0]
    assert photo.id == "photo" and photo.url == "https://example.invalid/photo.png"


@pytest.mark.parametrize("name,error", [
    ("  ", "Region name is required"),
    ("x" * 101, "Region name must be 100 characters or fewer"),
    (" South ", "Region name already exists"),
    ("North-East", "Region name already exists"),  # Legacy value without a region row.
])
def test_invalid_or_occupied_name_changes_nothing(region_database, name, error):
    before = snapshot(region_database)
    with pytest.raises(ValueError, match=error):
        PgAdminService(region_database).update_region("North", RegionUpdate(name=name))
    assert snapshot(region_database) == before


def test_same_name_is_noop_and_stale_region_is_rejected(region_database):
    service = PgAdminService(region_database)
    before = snapshot(region_database)
    service.update_region("North", RegionUpdate(name=" North "))
    assert snapshot(region_database) == before
    with pytest.raises(ValueError, match="Region not found"):
        service.update_region("missing", RegionUpdate(name="Renamed"))
    assert snapshot(region_database) == before


def test_late_database_failure_rolls_back_entire_rename(region_database):
    with region_database.transaction() as connection:
        connection.execute("ALTER TABLE products ADD CONSTRAINT reject_rename CHECK(areas <> 'Renamed')")
    before = snapshot(region_database)
    with pytest.raises(psycopg.errors.CheckViolation):
        PgAdminService(region_database).update_region("North", RegionUpdate(name="Renamed"))
    assert snapshot(region_database) == before
