from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import secrets

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

from app.application import create_app
from app.config import Settings
from app.database import Database
from app.schemas.message_delivery import DeliveryAck
from app.schemas.message_status import MessageStatusUpdate
from app.security import hash_sha256
from app.services.message_delivery_service import DeliveryError, MessageDeliveryService
from app.services.message_pull_service import MessagePullService, PullDeviceUnavailable
from app.services.message_state_service import MessageStateConflict, MessageStateService
from app.services.sms_check_service import SmsCheckService
from tests.integration.test_message_pull_service import seed_messages


def reliable(context):
    return MessageDeliveryService(Database(context.dsn))


def ack(item):
    return DeliveryAck(id=item.id, token=item.delivery.token, payloadHash=item.delivery.payload_hash)


def expire_claim(dsn, message_id):
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "UPDATE message_deliveries SET claimed_at=claimed_at-120000,lease_expires_at=lease_expires_at-120000 "
            "WHERE message_id=%s", (message_id,),
        )


def snapshot(dsn, tables):
    with psycopg.connect(dsn) as connection:
        return {name: connection.execute(sql.SQL(
            "SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text"
        ).format(sql.Identifier(name))).fetchall() for name in tables}


def test_claim_keeps_pending_and_all_business_rows_until_ack(clean_database):
    device_id, _, _, ids = seed_messages(clean_database, count=2, data_message=True)
    protected = ("sim_cards", "accounts", "account_sim_cards", "regions", "products", "contacts",
                 "conversations", "messages", "message_recipients", "message_state_history", "message_attachments")
    before = snapshot(clean_database.dsn, protected)
    items = reliable(clean_database).pull(device_id, "fifo")
    assert [item.id for item in items] == ids
    assert all(item.delivery is not None and not item.legacy_transport for item in items)
    assert snapshot(clean_database.dsn, protected) == before
    assert reliable(clean_database).pull(device_id, "fifo") == []
    assert MessagePullService(Database(clean_database.dsn)).pull(device_id, "fifo") == []
    with psycopg.connect(clean_database.dsn) as connection:
        stored = connection.execute(
            "SELECT claim_token_hash FROM message_deliveries WHERE message_id=%s", (ids[0],),
        ).fetchone()[0]
    assert stored == hash_sha256(items[0].delivery.token) and stored != items[0].delivery.token


def test_expired_unaccepted_claim_rotates_and_fences_old_ack(clean_database):
    device_id, _, _, ids = seed_messages(clean_database)
    service = reliable(clean_database)
    first = service.pull(device_id, "fifo")[0]
    expire_claim(clean_database.dsn, ids[0])
    with pytest.raises(DeliveryError) as error:
        service.acknowledge(device_id, [ack(first)])
    assert error.value.code == "DELIVERY_EXPIRED"
    # Even expired v2 claims cannot be silently taken by v1.
    assert MessagePullService(Database(clean_database.dsn)).pull(device_id, "fifo") == []
    second = service.pull(device_id, "fifo")[0]
    assert second.id == first.id and second.delivery.token != first.delivery.token
    assert second.delivery.payload_hash == first.delivery.payload_hash
    with pytest.raises(DeliveryError) as error:
        service.acknowledge(device_id, [ack(first)])
    assert error.value.code == "DELIVERY_STALE"
    service.acknowledge(device_id, [ack(second)])
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT state,pulled_at FROM messages WHERE id=%s", (ids[0],),
        ).fetchone()[0] == "Processed"
        assert connection.execute(
            "SELECT attempt_count FROM message_deliveries WHERE message_id=%s", (ids[0],),
        ).fetchone()[0] == 2


def test_accepted_ack_never_expires_or_redelivers_and_history_is_idempotent(clean_database):
    device_id, _, _, ids = seed_messages(clean_database)
    service = reliable(clean_database)
    item = service.pull(device_id, "fifo")[0]
    service.acknowledge(device_id, [ack(item)])
    before = snapshot(clean_database.dsn, ("messages", "message_deliveries", "message_state_history"))
    service.acknowledge(device_id, [ack(item)])
    assert snapshot(clean_database.dsn, ("messages", "message_deliveries", "message_state_history")) == before
    expire_claim(clean_database.dsn, ids[0])
    service.acknowledge(device_id, [ack(item)])
    assert service.pull(device_id, "fifo") == []
    assert MessagePullService(Database(clean_database.dsn)).pull(device_id, "fifo") == []
    with psycopg.connect(clean_database.dsn) as connection:
        # Even an accidental state edit cannot make an accepted task lease-redeliver.
        connection.execute("UPDATE messages SET state='Pending' WHERE id=%s", (ids[0],))
    assert service.pull(device_id, "fifo") == []


def test_old_processed_messages_never_get_claims_or_requeue(clean_database):
    device_id, _, _, ids = seed_messages(clean_database, count=3)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("UPDATE messages SET state='Processed',pulled_at=1000 WHERE id=%s", (ids[0],))
        connection.execute("UPDATE messages SET state='Sent',sent_at=2000 WHERE id=%s", (ids[1],))
    before = snapshot(clean_database.dsn, ("messages", "message_state_history"))
    items = reliable(clean_database).pull(device_id, "fifo")
    assert [item.id for item in items] == [ids[2]]
    assert snapshot(clean_database.dsn, ("messages", "message_state_history")) == before
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM message_deliveries WHERE message_id=ANY(%s::varchar[])", (ids[:2],),
        ).fetchone()[0] == 0


@pytest.mark.parametrize("change", ["phone", "iccid", "disabled", "simNumber", "payload", "hash"])
def test_ack_fences_changed_route_or_payload_and_never_marks_processed(clean_database, change):
    device_id, sim_id, _, ids = seed_messages(clean_database)
    service = reliable(clean_database)
    item = service.pull(device_id, "fifo")[0]
    command = ack(item)
    with psycopg.connect(clean_database.dsn) as connection:
        if change == "phone":
            connection.execute("UPDATE sim_cards SET phone_number='+12025550106' WHERE id=%s", (sim_id,))
        elif change == "iccid":
            connection.execute("UPDATE sim_cards SET iccid_hash='new-hash' WHERE id=%s", (sim_id,))
        elif change == "disabled":
            connection.execute("UPDATE sim_cards SET enabled=FALSE WHERE id=%s", (sim_id,))
        elif change == "simNumber":
            connection.execute("UPDATE sim_cards SET sim_number=2 WHERE id=%s", (sim_id,))
        elif change == "payload":
            connection.execute("UPDATE messages SET text_content='changed fixture' WHERE id=%s", (ids[0],))
        else:
            command.payload_hash = "0" * 64
    with pytest.raises(DeliveryError):
        service.acknowledge(device_id, [command])
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute("SELECT state,pulled_at FROM messages WHERE id=%s", (ids[0],)).fetchone() == ("Pending", None)
        assert connection.execute("SELECT accepted_at FROM message_deliveries WHERE message_id=%s", (ids[0],)).fetchone()[0] is None


def test_unaccepted_v2_task_cannot_skip_ack_by_reporting_state(clean_database):
    device_id, _, phone, ids = seed_messages(clean_database)
    reliable(clean_database).pull(device_id, "fifo")
    patch = MessageStatusUpdate.model_validate({
        "id": ids[0], "state": "Sent", "recipients": [{"phoneNumber": phone, "state": "Sent", "error": None}],
        "states": {"Sent": datetime.now(timezone.utc)},
    })
    with pytest.raises(MessageStateConflict):
        MessageStateService(Database(clean_database.dsn)).update(device_id, [patch])


def test_concurrent_claims_and_ack_are_unique_and_idempotent(clean_database):
    device_id, _, _, ids = seed_messages(clean_database, count=12)
    service = reliable(clean_database)
    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = list(pool.map(lambda _: service.pull(device_id, "fifo"), range(2)))
    items = [item for batch in batches for item in batch]
    assert sorted(item.id for item in items) == sorted(ids) and len(items) == 12
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: service.acknowledge(device_id, [ack(items[0])]), range(2)))
    assert service.state(device_id, items[0].id)["deliveryAccepted"] is True


def test_atomic_batch_rolls_back_all_if_one_claim_is_stale(clean_database):
    device_id, _, _, ids = seed_messages(clean_database, count=2)
    service = reliable(clean_database)
    items = service.pull(device_id, "fifo")
    commands = [ack(item) for item in items]
    commands[1].token = "dlv_" + "X" * 43
    with pytest.raises(DeliveryError):
        service.acknowledge(device_id, commands)
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM messages WHERE id=ANY(%s::varchar[]) AND state='Processed'", (ids,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM message_deliveries WHERE message_id=ANY(%s::varchar[]) AND accepted_at IS NOT NULL", (ids,),
        ).fetchone()[0] == 0


def test_http_auth_ownership_readonly_reconcile_and_single_batch_ack(clean_database):
    device_id, _, phone, ids = seed_messages(clean_database, count=2)
    other, _, _, other_ids = seed_messages(clean_database)
    token = "isolated-delivery-" + secrets.token_hex(12)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("UPDATE devices SET token_hash=%s WHERE id=%s", (hash_sha256(token), device_id))
    client = TestClient(create_app(Settings(clean_database.dsn, "fixture-registration", "fixture-business")))
    headers = {"Authorization": "Bearer " + token}
    response = client.get("/mobile/v1/message?protocol=2", headers=headers)
    assert response.status_code == 200
    bodies = response.json()
    assert isinstance(bodies[0]["delivery"]["leaseExpiresAt"], int)
    assert bodies[0]["legacyTransport"] is False
    commands = [{"id": item["id"], "token": item["delivery"]["token"], "payloadHash": item["delivery"]["payloadHash"]} for item in bodies]
    assert client.post("/mobile/v1/message/ack", json=commands[0]).status_code == 401
    assert client.post("/mobile/v1/message/ack", headers=headers, json={**commands[0], "id": other_ids[0]}).status_code == 404
    assert client.post("/mobile/v1/message/ack", headers=headers, json={**commands[0], "id": "nonexistent"}).status_code == 404
    first = client.post("/mobile/v1/message/ack", headers=headers, json=commands[0])
    assert first.json() == {"ok": True, "id": commands[0]["id"]}
    batch = client.post("/mobile/v1/message/ack", headers=headers, json=commands)
    assert batch.json() == {"ok": True, "ids": [item["id"] for item in commands]}
    patch = {"id": ids[0], "state": "Delivered", "recipients": [{"phoneNumber": phone, "state": "Delivered", "error": None}],
             "states": {"Sent": datetime.now(timezone.utc).isoformat(), "Delivered": datetime.now(timezone.utc).isoformat()}}
    assert client.patch("/mobile/v1/message", headers=headers, json=[patch]).status_code == 200
    before = snapshot(clean_database.dsn, ("devices", "messages", "message_deliveries", "message_recipients", "message_state_history"))
    state = client.get(f"/mobile/v1/message/{ids[0]}/state", headers=headers)
    assert state.status_code == 200
    assert state.json()["state"] == "Delivered" and state.json()["recipients"][0]["state"] == "Delivered"
    assert state.json()["deliveryAccepted"] is True and "Sent" in state.json()["states"]
    assert not any(key in state.json() for key in ("textContent", "textMessage", "token", "dataMessage"))
    assert client.get(f"/mobile/v1/message/{other_ids[0]}/state", headers=headers).status_code == 404
    assert client.get("/mobile/v1/message/nonexistent/state", headers=headers).status_code == 404
    assert snapshot(clean_database.dsn, ("devices", "messages", "message_deliveries", "message_recipients", "message_state_history")) == before
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("UPDATE devices SET enabled=FALSE WHERE id=%s", (device_id,))
    assert client.post("/mobile/v1/message/ack", headers=headers, json=commands[0]).status_code == 403
    assert client.get("/mobile/v1/message?protocol=2", headers=headers).status_code == 403
    assert client.get(f"/mobile/v1/message/{ids[0]}/state", headers=headers).status_code == 403


def test_v2_diagnostics_are_explicitly_legacy_and_keep_v1_status_compatibility(clean_database):
    device_id, sim_id, target, _ = seed_messages(clean_database, count=0)
    suffix = secrets.token_hex(16)
    run_id, check_id = "run_" + suffix, "check_" + suffix
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    database = Database(clean_database.dsn)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("UPDATE sim_cards SET phone_number='+12025550106' WHERE id=%s", (sim_id,))
        connection.execute(
            "INSERT INTO sms_check_runs(id,request_key,target_phone,timeout_seconds,created_at,deadline_at) "
            "VALUES(%s,%s,%s,600,%s,%s)", (run_id, "delivery-fixture-" + suffix, target, now, now + 600000),
        )
        connection.execute(
            "INSERT INTO sms_checks(id,run_id,sim_card_id,device_id,sim_number,source_phone,token,text_content,"
            "status,created_at,deadline_at,updated_at) VALUES(%s,%s,%s,%s,1,'+12025550106',%s,%s,'PENDING',%s,%s,%s)",
            (check_id, run_id, sim_id, device_id, suffix, "VIRGO-CHECK:" + suffix, now, now + 600000, now),
        )
    try:
        checks = SmsCheckService(database)
        service = MessageDeliveryService(database, checks)
        item = service.pull(device_id, "fifo")[0]
        assert item.id == check_id and item.delivery is None and item.legacy_transport is True
        with pytest.raises(DeliveryError) as error:
            service.acknowledge(device_id, [DeliveryAck(id=check_id, token="dlv_" + "z" * 43, payloadHash="a" * 64)])
        assert error.value.status == 404
        patch = MessageStatusUpdate.model_validate({
            "id": check_id, "state": "Sent", "recipients": [{"phoneNumber": target, "state": "Sent", "error": None}],
            "states": {"Sent": datetime.now(timezone.utc)},
        })
        MessageStateService(database, checks).update(device_id, [patch])
        assert service.state(device_id, check_id)["state"] == "Sent"
        assert service.state(device_id, check_id)["deliveryAccepted"] is None
    finally:
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute("DELETE FROM sms_check_runs WHERE id=%s", (run_id,))


def test_reapplying_additive_migration_does_not_backfill_or_modify_old_data(clean_database):
    from pathlib import Path
    device_id, _, _, ids = seed_messages(clean_database, count=2)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("UPDATE messages SET state='Processed',pulled_at=1234 WHERE id=%s", (ids[0],))
    tables = ("devices", "sim_cards", "contacts", "conversations", "messages", "message_recipients",
              "message_state_history", "message_attachments", "accounts", "account_sim_cards", "message_deliveries")
    before = snapshot(clean_database.dsn, tables)
    migration = Path(__file__).resolve().parents[2] / "pg/init/007_message_delivery.sql"
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(migration.read_text(encoding="utf-8"))
    assert snapshot(clean_database.dsn, tables) == before
    assert reliable(clean_database).state(device_id, ids[0])["deliveryAccepted"] is None
