"""Run only with an explicitly selected isolated TEST_DATABASE_URL."""
import secrets
from datetime import datetime, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row

from app.application import create_app
from app.config import Settings
from app.database import Database
from app.schemas.device import DeviceRegisterRequest, DeviceUpdateRequest
from app.schemas.agent_conversation import AgentReplyRequest
from app.schemas.inbound_message import InboundMessageRequest
from app.security import hash_password, hash_sha256
from app.services.agent_auth_service import AgentAuthService
from app.services.agent_conversation_service import AgentConversationService
from app.services.device_auth_service import DeviceDisabled
from app.services.device_service import DeviceService
from app.services.inbound_message_service import InboundMessageService
from app.services.inbound_publisher import NoOpInboundMessagePublisher
from app.services.message_service import MessageCommandService
from app.services.message_publisher import NoOpMessageEnqueuedPublisher


PRESERVED_TABLES = (
    "accounts", "account_sim_cards", "agent_sessions", "regions", "products", "contacts",
    "conversations", "messages", "message_recipients", "message_state_history", "message_attachments",
)


def table_snapshot(dsn, tables):
    with psycopg.connect(dsn) as connection:
        return {
            table: connection.execute(sql.SQL(
                "SELECT to_jsonb(t) FROM {} AS t ORDER BY to_jsonb(t)::text"
            ).format(sql.Identifier(table))).fetchall()
            for table in tables
        }


def sim_snapshot(dsn, device_id):
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        return connection.execute(
            "SELECT * FROM sim_cards WHERE device_id=%s ORDER BY slot_index", (device_id,),
        ).fetchall()


@pytest.fixture
def phone_fleet(clean_database):
    dsn = clean_database.dsn
    tag = secrets.token_hex(6)
    service = DeviceService(Database(dsn))
    credentials = service.register(DeviceRegisterRequest.model_validate({
        "name": "phone-sync-" + tag,
        "simCards": [
            {"slotIndex": 0, "simNumber": 1, "phoneNumber": "+1 (202) 555-0104",
             "iccid": "89122345678901234560", "carrierName": "Original carrier"},
            {"slotIndex": 1, "simNumber": 2, "phoneNumber": "+12025550105",
             "iccid": "89122345678901234561", "carrierName": "Other carrier"},
        ],
    }))
    clean_database.track(credentials.id)
    sims = sim_snapshot(dsn, credentials.id)
    region = "Sync region " + tag
    account_id, contact_id = "acct_sync_" + tag, "contact_sync_" + tag
    conversation_id, message_id = "conv_sync_" + tag, "msg_sync_" + tag
    mms_id, attachment_id = "mms_sync_" + tag, "att_sync_" + tag
    product_id = "menu_sync_" + tag
    contact_phone = "+1613555" + str(int(tag[:4], 16) % 10000).zfill(4)
    with psycopg.connect(dsn) as connection:
        connection.execute("INSERT INTO regions(id) VALUES (%s)", (region,))
        connection.execute(
            "UPDATE sim_cards SET areas=%s, esim_profile_name='Original remark', "
            "esim_group_id='original-group', sim_type='ESIM', last_used_at=12345 WHERE id=%s",
            (region, sims[0]["id"]),
        )
        connection.execute(
            "UPDATE sim_cards SET enabled=FALSE,status='disabled',areas=%s, "
            "esim_profile_name='Disabled unassigned' WHERE id=%s", (region, sims[1]["id"]),
        )
        connection.execute(
            "INSERT INTO accounts(id,username,password_hash,areas,use_sims_id) VALUES(%s,%s,%s,%s,%s)",
            (account_id, "sync-" + tag, hash_password("fixture-only-password"), region, sims[0]["id"]),
        )
        connection.execute(
            "INSERT INTO account_sim_cards(account_id,sim_card_id) VALUES(%s,%s)",
            (account_id, sims[0]["id"]),
        )
        connection.execute(
            "INSERT INTO agent_sessions(token_hash,account_id,created_at,expires_at) VALUES(%s,%s,1,9999999999999)",
            ("session-sync-" + tag, account_id),
        )
        connection.execute(
            "INSERT INTO products(id,menu,update_by,areas) VALUES(%s,'fixture menu',%s,%s)",
            (product_id, account_id, region),
        )
        connection.execute(
            "INSERT INTO contacts(id,phone_number,normalized_phone_number,remark,areas) "
            "VALUES(%s,%s,%s,'Original contact',%s)",
            (contact_id, contact_phone, contact_phone, region),
        )
        connection.execute(
            "INSERT INTO conversations(id,external_phone_number,contact_id,device_id,sim_card_id,sim_number,areas,unread_count) "
            "VALUES(%s,%s,%s,%s,%s,1,%s,4)",
            (conversation_id, contact_phone, contact_id, credentials.id, sims[0]["id"], region),
        )
        connection.execute(
            "INSERT INTO messages(id,conversation_id,direction,text_content,from_phone_number,to_phone_number,"
            "state,device_id,sim_card_id,sim_number,sent_at,delivered_at) "
            "VALUES(%s,%s,'OUTBOUND','fixture old SMS','+12025550104','+16135551234','Delivered',%s,%s,1,2000,3000)",
            (message_id, conversation_id, credentials.id, sims[0]["id"]),
        )
        connection.execute(
            "INSERT INTO messages(id,conversation_id,direction,message_type,text_content,from_phone_number,"
            "to_phone_number,state,device_id,sim_card_id,sim_number,received_at) "
            "VALUES(%s,%s,'INBOUND','MMS',NULL,'+16135551234','+12025550104','Received',%s,%s,1,4000)",
            (mms_id, conversation_id, credentials.id, sims[0]["id"]),
        )
        connection.execute(
            "INSERT INTO message_recipients(message_id,phone_number,state) VALUES(%s,'+16135551234','Delivered')",
            (message_id,),
        )
        connection.execute(
            "INSERT INTO message_state_history(message_id,state,source,occurred_at) "
            "VALUES(%s,'Delivered','DEVICE',3000)", (message_id,),
        )
        connection.execute(
            "INSERT INTO message_attachments(id,message_id,part_id,content_type,s3_bucket,s3_key,etag) "
            "VALUES(%s,%s,0,'image/png','fixture-bucket','fixture-photo','unchanged-etag')",
            (attachment_id, mms_id),
        )
        connection.execute(
            "INSERT INTO sim_card_history(sim_card_id,device_id,phone_number,event_type,reason,occurred_at,snapshot) "
            "SELECT id,device_id,'+12025550103','REPLACED','Prior audit',1000,to_jsonb(s) "
            "FROM sim_cards s WHERE id=%s", (sims[0]["id"],),
        )
    try:
        yield dsn, service, credentials
    finally:
        with psycopg.connect(dsn) as connection:
            connection.execute("DELETE FROM conversations WHERE id=%s", (conversation_id,))
            connection.execute("DELETE FROM products WHERE id=%s", (product_id,))
            connection.execute("DELETE FROM accounts WHERE id=%s", (account_id,))
            connection.execute("DELETE FROM contacts WHERE id=%s", (contact_id,))
            connection.execute("DELETE FROM regions WHERE id=%s", (region,))


def report(credentials, phone, *, sync=True, iccid=None, carrier=None):
    return DeviceUpdateRequest.model_validate({
        "id": credentials.id, "syncPhoneNumbers": sync,
        "simCards": [
            {"slotIndex": 0, "simNumber": 1, "phoneNumber": phone, "iccid": iccid, "carrierName": carrier},
            {"slotIndex": 1, "simNumber": 2, "phoneNumber": None},
        ],
    })


@pytest.mark.parametrize("iccid", [None, "", "unknown", "89122345678901234560", "89122345678901234599"])
def test_opt_in_replaces_only_phone_and_preserves_identity_relations_and_all_chats(phone_fleet, iccid):
    dsn, service, credentials = phone_fleet
    before = sim_snapshot(dsn, credentials.id)
    protected = table_snapshot(dsn, PRESERVED_TABLES)
    old_history = table_snapshot(dsn, ("sim_card_history",))["sim_card_history"]
    service.update(credentials.id, report(credentials, " +1 (202) 555-0106 ", iccid=iccid))
    after = sim_snapshot(dsn, credentials.id)
    assert after[0]["phone_number"] == "+1 (202) 555-0106"
    mutable = {"phone_number", "iccid_hash", "updated_at"}
    assert {k: v for k, v in after[0].items() if k not in mutable} == {
        k: v for k, v in before[0].items() if k not in mutable
    }
    assert after[0]["iccid_hash"] == (
        hash_sha256(iccid) if iccid not in (None, "", "unknown") else before[0]["iccid_hash"]
    )
    assert after[1]["phone_number"] == "+12025550105"
    assert after[1]["enabled"] is False and after[1]["status"] == "disabled"
    assert after[1]["iccid_hash"] == before[1]["iccid_hash"]
    assert table_snapshot(dsn, PRESERVED_TABLES) == protected
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM account_sim_cards WHERE sim_card_id=%s", (after[1]["id"],),
        ).fetchone()[0] == 0
    history_after = table_snapshot(dsn, ("sim_card_history",))["sim_card_history"]
    by_id = {item[0]["id"]: item for item in history_after}
    assert all(by_id[item[0]["id"]] == item for item in old_history)
    assert len(history_after) == len(old_history) + 1


@pytest.mark.parametrize("number", ["2025550104", "12025550104", "+12025550104", " +1 (202) 555-0104 "])
def test_equivalent_formats_keep_original_format_and_audit_unchanged(phone_fleet, number):
    dsn, service, credentials = phone_fleet
    before = table_snapshot(dsn, ("sim_card_history",) + PRESERVED_TABLES)
    service.update(credentials.id, report(credentials, number))
    assert sim_snapshot(dsn, credentials.id)[0]["phone_number"] == "+1 (202) 555-0104"
    assert table_snapshot(dsn, ("sim_card_history",) + PRESERVED_TABLES) == before


@pytest.mark.parametrize("number", [None, "", "unknown", "***0106", "202555010", "+1202555010", {}, 12345, "+１２０２５５５０１０６"])
def test_unknown_or_malformed_phone_preserves_existing_number_and_heartbeat(phone_fleet, number):
    dsn, service, credentials = phone_fleet
    before = table_snapshot(dsn, ("sim_card_history",) + PRESERVED_TABLES)
    service.update(credentials.id, report(credentials, number, carrier="unknown"))
    sim = sim_snapshot(dsn, credentials.id)[0]
    assert sim["phone_number"] == "+1 (202) 555-0104"
    assert sim["carrier_name"] == "Original carrier"
    assert table_snapshot(dsn, ("sim_card_history",) + PRESERVED_TABLES) == before
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT last_seen_at FROM devices WHERE id=%s", (credentials.id,)).fetchone()[0] > 1000


@pytest.mark.parametrize("opt_in", [False, None])
def test_old_clients_cannot_replace_existing_number(phone_fleet, opt_in):
    dsn, service, credentials = phone_fleet
    body = report(credentials, "+12025550106", sync=False).model_dump(by_alias=True)
    if opt_in is None:
        body.pop("syncPhoneNumbers")
    service.update(credentials.id, DeviceUpdateRequest.model_validate(body))
    assert sim_snapshot(dsn, credentials.id)[0]["phone_number"] == "+1 (202) 555-0104"


@pytest.mark.parametrize("number, expected", [("unknown", None), ("202555010", None), ("+44 (20) 7946-0958", "+44 (20) 7946-0958")])
def test_new_slot_stores_only_complete_explicit_numbers(phone_fleet, number, expected):
    dsn, service, credentials = phone_fleet
    service.update(credentials.id, DeviceUpdateRequest.model_validate({
        "id": credentials.id, "syncPhoneNumbers": True,
        "simCards": [{"slotIndex": 2, "simNumber": 3, "phoneNumber": number}],
    }))
    assert sim_snapshot(dsn, credentials.id)[2]["phone_number"] == expected


@pytest.mark.parametrize("existing", [None, "", "   "])
def test_unknown_report_preserves_even_blank_existing_storage(phone_fleet, existing):
    dsn, service, credentials = phone_fleet
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE sim_cards SET phone_number=%s WHERE device_id=%s AND slot_index=0",
            (existing, credentials.id),
        )
    service.update(credentials.id, report(credentials, "unknown"))
    assert sim_snapshot(dsn, credentials.id)[0]["phone_number"] == existing


def test_partial_opt_in_snapshot_leaves_omitted_slot_exactly_unchanged(phone_fleet):
    dsn, service, credentials = phone_fleet
    before = sim_snapshot(dsn, credentials.id)
    service.update(credentials.id, DeviceUpdateRequest.model_validate({
        "id": credentials.id, "syncPhoneNumbers": True,
        "simCards": [{"slotIndex": 0, "simNumber": 1, "phoneNumber": "+12025550106"}],
    }))
    after = sim_snapshot(dsn, credentials.id)
    assert after[1] == before[1]


def test_authenticated_http_opt_in_and_legacy_default_end_to_end(phone_fleet):
    dsn, _, credentials = phone_fleet
    protected = table_snapshot(dsn, PRESERVED_TABLES)
    client = TestClient(create_app(Settings(dsn, "fixture-registration-secret")))
    headers = {"Authorization": "Bearer " + credentials.token}
    body = report(credentials, "+12025550106").model_dump(by_alias=True)
    legacy = {k: v for k, v in body.items() if k != "syncPhoneNumbers"}
    assert client.patch("/mobile/v1/device", headers=headers, json=legacy).status_code == 200
    assert sim_snapshot(dsn, credentials.id)[0]["phone_number"] == "+1 (202) 555-0104"
    assert client.patch("/mobile/v1/device", json=body).status_code == 401
    assert client.patch("/mobile/v1/device", headers=headers, json={**body, "id": "another-device"}).status_code == 403
    response = client.patch("/mobile/v1/device", headers=headers, json=body)
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert sim_snapshot(dsn, credentials.id)[0]["phone_number"] == "+12025550106"
    assert table_snapshot(dsn, PRESERVED_TABLES) == protected


def test_disabled_device_does_not_sync_or_partially_write(phone_fleet):
    dsn, service, credentials = phone_fleet
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE devices SET enabled=FALSE WHERE id=%s", (credentials.id,))
    before = table_snapshot(dsn, ("devices", "sim_cards", "sim_card_history") + PRESERVED_TABLES)
    with pytest.raises(DeviceDisabled):
        service.update(credentials.id, report(credentials, "+12025550106"))
    assert table_snapshot(dsn, ("devices", "sim_cards", "sim_card_history") + PRESERVED_TABLES) == before


def test_original_account_history_reply_and_new_inbound_still_use_original_slot(phone_fleet):
    dsn, service, credentials = phone_fleet
    database = Database(dsn)
    with psycopg.connect(dsn) as connection:
        username, conversation_id, sim_id, sender = connection.execute(
            "SELECT a.username,c.id,c.sim_card_id,c.external_phone_number "
            "FROM accounts a JOIN account_sim_cards acs ON acs.account_id=a.id "
            "JOIN conversations c ON c.sim_card_id=acs.sim_card_id WHERE c.device_id=%s",
            (credentials.id,),
        ).fetchone()
        original_messages = connection.execute(
            "SELECT id,to_jsonb(m) FROM messages m WHERE device_id=%s ORDER BY id",
            (credentials.id,),
        ).fetchall()
    auth = AgentAuthService(database)
    agent = auth.login(username, "fixture-only-password").agent
    conversations = AgentConversationService(
        database,
        MessageCommandService(database, online_window_seconds=300, publisher=NoOpMessageEnqueuedPublisher()),
    )
    before = {item.id: item.model_dump() for item in conversations.list_messages(conversation_id, agent)}
    assert len(before) == 2 and sum(len(item["attachments"]) for item in before.values()) == 1
    service.update(credentials.id, report(credentials, "+12025550106"))
    assert auth.login(username, "fixture-only-password").agent.id == agent.id
    assert conversations.list_conversations(agent)[0].service_phone_number == "+12025550106"
    after = {item.id: item.model_dump() for item in conversations.list_messages(conversation_id, agent)}
    for message in before:
        assert after[message]["customer_sim_card"] == "+12025550106"
        assert {k: v for k, v in after[message].items() if k != "customer_sim_card"} == {
            k: v for k, v in before[message].items() if k != "customer_sim_card"
        }
    reply = conversations.reply(
        conversation_id, agent, AgentReplyRequest(text="fixture reply after number replacement"),
        "phone-sync-reply-" + secrets.token_hex(12),
    )
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT conversation_id,device_id,sim_card_id,sim_number,from_phone_number FROM messages WHERE id=%s",
            (reply.response.id,),
        ).fetchone() == (conversation_id, credentials.id, sim_id, 1, "+12025550106")
    inbound = InboundMessageService(database, NoOpInboundMessagePublisher()).create(
        credentials.id, InboundMessageRequest.model_validate({
            "id": "phone-sync-inbound-" + secrets.token_hex(12), "type": "SMS",
            "sender": sender, "recipient": "+12025550106", "simNumber": 1,
            "receivedAt": datetime.now(timezone.utc), "textMessage": {"text": "fixture new inbound"},
        }),
    )
    assert inbound.conversation_id == conversation_id and inbound.sim_card_id == sim_id
    assert inbound.agent_account_ids == (agent.id,)
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            "SELECT id,to_jsonb(m) FROM messages m WHERE id=ANY(%s::varchar[]) ORDER BY id",
            ([item[0] for item in original_messages],),
        ).fetchall() == original_messages
        assert connection.execute(
            "SELECT count(*) FROM conversations WHERE device_id=%s", (credentials.id,),
        ).fetchone()[0] == 1
