from concurrent.futures import ThreadPoolExecutor
import secrets
import time

import psycopg

from app.database import Database
from app.services.message_pull_service import MessagePullService, PullDeviceUnavailable


def seed_messages(context, *, count=1, data_message=False, device_enabled=True):
    suffix = secrets.token_hex(8)
    device_id = context.track(f"dev_pull_{suffix}")
    sim_id = f"sim_pull_{suffix}"
    phone = context.track_phone("+7" + str(secrets.randbelow(10**12)).zfill(12))
    contact_id = f"contact_pull_{suffix}"
    conversation_id = f"conv_pull_{suffix}"
    now = time.time_ns() // 1_000_000
    message_ids = []
    with psycopg.connect(context.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (id, name, token_hash, login, enabled, status)
            VALUES (%s, 'pull-phone', %s, %s, %s, 'offline')
            """,
            (device_id, f"hash-{suffix}", f"login-{suffix}", device_enabled),
        )
        connection.execute(
            """
            INSERT INTO sim_cards (
                id, device_id, slot_index, sim_number, enabled, status
            ) VALUES (%s, %s, 0, 1, TRUE, 'active')
            """,
            (sim_id, device_id),
        )
        connection.execute(
            """
            INSERT INTO contacts (id, phone_number, normalized_phone_number)
            VALUES (%s, %s, %s)
            """,
            (contact_id, phone, phone),
        )
        connection.execute(
            """
            INSERT INTO conversations (
                id, external_phone_number, contact_id, device_id,
                sim_card_id, sim_number
            ) VALUES (%s, %s, %s, %s, %s, 1)
            """,
            (conversation_id, phone, contact_id, device_id, sim_id),
        )
        for index in range(count):
            message_id = f"msg_pull_{suffix}_{index:02d}"
            key = context.track_message_key(f"pull-key-{suffix}-{index}")
            message_ids.append(message_id)
            message_type = "DATA_SMS" if data_message and index == 0 else "SMS"
            connection.execute(
                """
                INSERT INTO messages (
                    id, conversation_id, direction, message_type,
                    text_content, data_base64, data_port,
                    to_phone_number, state, device_id, sim_card_id,
                    sim_number, idempotency_key, created_at, updated_at
                ) VALUES (
                    %s, %s, 'OUTBOUND', %s, %s, %s, %s,
                    %s, 'Pending', %s, %s, 1, %s, %s, %s
                )
                """,
                (
                    message_id,
                    conversation_id,
                    message_type,
                    None if message_type == "DATA_SMS" else f"message-{index}",
                    "AQJ/" if message_type == "DATA_SMS" else None,
                    53739 if message_type == "DATA_SMS" else None,
                    phone,
                    device_id,
                    sim_id,
                    key,
                    now + index,
                    now + index,
                ),
            )
            connection.execute(
                """
                INSERT INTO message_recipients (message_id, phone_number)
                VALUES (%s, %s)
                """,
                (message_id, phone),
            )
    return device_id, sim_id, phone, message_ids


def service(context):
    return MessagePullService(Database(context.dsn))


def test_fifo_claims_ten_and_updates_state_history_and_heartbeat(clean_database):
    device_id, _, _, message_ids = seed_messages(clean_database, count=12)
    before = time.time_ns() // 1_000_000

    result = service(clean_database).pull(device_id, "fifo")

    assert [item.id for item in result] == message_ids[:10]
    with psycopg.connect(clean_database.dsn) as connection:
        rows = connection.execute(
            "SELECT id, state, pulled_at FROM messages WHERE id = ANY(%s) ORDER BY created_at",
            (message_ids,),
        ).fetchall()
        history = connection.execute(
            """
            SELECT count(*) FROM message_state_history
            WHERE message_id = ANY(%s) AND state='Processed' AND source='SERVER'
            """,
            (message_ids,),
        ).fetchone()[0]
        device = connection.execute(
            "SELECT status, last_seen_at FROM devices WHERE id=%s", (device_id,)
        ).fetchone()
    assert all(state == "Processed" and pulled_at >= before for _, state, pulled_at in rows[:10])
    assert all(state == "Pending" and pulled_at is None for _, state, pulled_at in rows[10:])
    assert history == 10
    assert device[0] == "online" and device[1] >= before


def test_lifo_returns_newest_first(clean_database):
    device_id, _, _, message_ids = seed_messages(clean_database, count=3)

    result = service(clean_database).pull(device_id, "lifo")

    assert [item.id for item in result] == list(reversed(message_ids))


def test_data_sms_is_returned_with_android_shape(clean_database):
    device_id, _, phone, message_ids = seed_messages(
        clean_database, count=1, data_message=True
    )

    item = service(clean_database).pull(device_id, "fifo")[0]

    assert item.id == message_ids[0]
    assert item.text_message is None
    assert item.data_message.data == "AQJ/"
    assert item.data_message.port == 53739
    assert item.phone_numbers == [phone]


def test_pull_filters_expired_future_processed_and_inbound_messages(clean_database):
    device_id, _, phone, message_ids = seed_messages(clean_database, count=5)
    now = time.time_ns() // 1_000_000
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            "UPDATE messages SET valid_until=%s WHERE id=%s",
            (now - 1, message_ids[0]),
        )
        connection.execute(
            "UPDATE messages SET schedule_at=%s WHERE id=%s",
            (now + 60_000, message_ids[1]),
        )
        connection.execute(
            "UPDATE messages SET state='Processed' WHERE id=%s",
            (message_ids[2],),
        )
        connection.execute(
            """
            UPDATE messages SET direction='INBOUND', from_phone_number=%s
            WHERE id=%s
            """,
            (phone, message_ids[3]),
        )

    result = service(clean_database).pull(device_id, "fifo")

    assert [item.id for item in result] == [message_ids[4]]


def test_pull_excludes_disabled_sim(clean_database):
    device_id, sim_id, _, _ = seed_messages(clean_database)
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute("UPDATE sim_cards SET enabled=FALSE WHERE id=%s", (sim_id,))

    assert service(clean_database).pull(device_id, "fifo") == []


def test_pull_cannot_claim_another_devices_messages(clean_database):
    first_device, _, _, first_messages = seed_messages(clean_database)
    _, _, _, second_messages = seed_messages(clean_database)

    result = service(clean_database).pull(first_device, "fifo")

    assert [item.id for item in result] == first_messages
    with psycopg.connect(clean_database.dsn) as connection:
        second_state = connection.execute(
            "SELECT state FROM messages WHERE id=%s", (second_messages[0],)
        ).fetchone()[0]
    assert second_state == "Pending"


def test_disabled_device_is_rejected_inside_pull_transaction(clean_database):
    device_id, _, _, _ = seed_messages(clean_database, device_enabled=False)

    try:
        service(clean_database).pull(device_id, "fifo")
    except PullDeviceUnavailable:
        pass
    else:
        raise AssertionError("disabled device was allowed to pull")


def test_concurrent_pulls_do_not_return_the_same_message(clean_database):
    device_id, _, _, message_ids = seed_messages(clean_database, count=12)

    def pull():
        return service(clean_database).pull(device_id, "fifo")

    with ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(executor.map(lambda _: pull(), range(2)))

    claimed = [item.id for batch in batches for item in batch]
    assert sorted(claimed) == sorted(message_ids)
    assert len(claimed) == len(set(claimed))
