from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import secrets
import string
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from app.database import Database
from app.schemas.message import MessageCreateRequest
from app.services.message_service import (
    IdempotencyConflict,
    MessageCommandService,
    MessageStateConflict,
    MessageValidationError,
    NoAvailableDevice,
)


class RecordingPublisher:
    def __init__(self, error=None):
        self.events = []
        self.error = error

    def publish(self, device_id, message_id):
        self.events.append((device_id, message_id))
        if self.error is not None:
            raise self.error


def random_phone(context):
    digits = "".join(secrets.choice(string.digits) for _ in range(12))
    return context.track_phone("+9" + digits)


def seed_route(
    context,
    *,
    sim_number=1,
    last_used_at=None,
    device_enabled=True,
    device_status="online",
    last_seen_at=None,
    sim_enabled=True,
    sim_status="active",
):
    suffix = secrets.token_hex(8)
    device_id = context.track(f"dev_message_{suffix}")
    sim_id = f"sim_message_{suffix}_{sim_number}"
    now = time.time_ns() // 1_000_000
    seen = now if last_seen_at is None else last_seen_at
    with psycopg.connect(context.dsn) as connection:
        connection.execute(
            """
            INSERT INTO devices (
                id, name, token_hash, login, enabled, status, last_seen_at
            ) VALUES (%s, 'message-phone', %s, %s, %s, %s, %s)
            """,
            (
                device_id,
                f"hash-{suffix}",
                f"login-{suffix}",
                device_enabled,
                device_status,
                seen,
            ),
        )
        connection.execute(
            """
            INSERT INTO sim_cards (
                id, device_id, slot_index, sim_number, phone_number,
                enabled, status, last_used_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                sim_id,
                device_id,
                sim_number - 1,
                sim_number,
                f"sender-{suffix}",
                sim_enabled,
                sim_status,
                last_used_at,
            ),
        )
    return device_id, sim_id


def valid_request(phone, **overrides):
    body = {"phoneNumbers": [phone], "text": "hello from business"}
    body.update(overrides)
    return MessageCreateRequest.model_validate(body)


def build_service(context, publisher=None, window=300):
    return MessageCommandService(
        Database(context.dsn),
        online_window_seconds=window,
        publisher=publisher or RecordingPublisher(),
    )


def read_message(dsn, message_id):
    with psycopg.connect(dsn, row_factory=dict_row) as connection:
        return connection.execute(
            "SELECT * FROM messages WHERE id = %s",
            (message_id,),
        ).fetchone()


def test_create_message_routes_and_writes_all_records(clean_database):
    device_id, sim_id = seed_route(clean_database, last_used_at=None)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-message-" + secrets.token_hex(8))
    publisher = RecordingPublisher()

    result = build_service(clean_database, publisher).create(valid_request(phone), key)

    assert result.replayed is False
    assert result.response.device_id == device_id
    assert result.response.sim_number == 1
    message = read_message(clean_database.dsn, result.response.id)
    assert message["conversation_id"] == result.response.conversation_id
    assert message["direction"] == "OUTBOUND"
    assert message["message_type"] == "SMS"
    assert message["state"] == "Pending"
    assert message["sim_card_id"] == sim_id
    assert message["to_phone_number"] == phone
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM message_recipients WHERE message_id = %s",
            (result.response.id,),
        ).fetchone()[0] == 1
        history = connection.execute(
            "SELECT state, source, reason FROM message_state_history WHERE message_id = %s",
            (result.response.id,),
        ).fetchone()
        contact_count = connection.execute(
            "SELECT count(*) FROM contacts WHERE normalized_phone_number = %s",
            (phone,),
        ).fetchone()[0]
    assert history == ("Pending", "API", "Created by business API")
    assert contact_count == 1
    assert publisher.events == [(device_id, result.response.id)]


def test_auto_route_prefers_never_used_then_oldest_sim(clean_database):
    old_device, _ = seed_route(clean_database, last_used_at=100)
    never_device, _ = seed_route(clean_database, last_used_at=None)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-route-" + secrets.token_hex(8))

    first = build_service(clean_database).create(valid_request(phone), key)

    assert first.response.device_id == never_device
    assert first.response.device_id != old_device


def test_specified_device_and_sim_are_honored(clean_database):
    seed_route(clean_database, sim_number=1, last_used_at=None)
    selected_device, _ = seed_route(clean_database, sim_number=2, last_used_at=999)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-specified-" + secrets.token_hex(8))

    result = build_service(clean_database).create(
        valid_request(phone, deviceId=selected_device, simNumber=2),
        key,
    )

    assert result.response.device_id == selected_device
    assert result.response.sim_number == 2


def test_specified_unavailable_device_does_not_fall_back(clean_database):
    unavailable_device, _ = seed_route(clean_database, device_status="offline")
    seed_route(clean_database)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-no-fallback-" + secrets.token_hex(8))

    with pytest.raises(NoAvailableDevice):
        build_service(clean_database).create(
            valid_request(phone, deviceId=unavailable_device),
            key,
        )


@pytest.mark.parametrize(
    "route_options",
    [
        {"device_enabled": False},
        {"device_status": "offline"},
        {"sim_enabled": False},
        {"sim_status": "inactive"},
    ],
)
def test_unavailable_routes_are_rejected(clean_database, route_options):
    seed_route(clean_database, **route_options)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-unavailable-" + secrets.token_hex(8))

    with pytest.raises(NoAvailableDevice):
        build_service(clean_database).create(valid_request(phone), key)


def test_expired_device_is_not_routed(clean_database):
    now = time.time_ns() // 1_000_000
    seed_route(clean_database, last_seen_at=now - 301_000)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-expired-route-" + secrets.token_hex(8))

    with pytest.raises(NoAvailableDevice):
        build_service(clean_database, window=300).create(valid_request(phone), key)


def test_identical_idempotent_replay_returns_original_without_republishing(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-replay-" + secrets.token_hex(8))
    publisher = RecordingPublisher()
    service = build_service(clean_database, publisher)
    request = valid_request(phone, metadata={"order": "one"})

    first = service.create(request, key)
    replay = service.create(request, key)

    assert replay.replayed is True
    assert replay.response == first.response
    assert publisher.events == [(first.response.device_id, first.response.id)]
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM messages WHERE idempotency_key = %s", (key,)
        ).fetchone()[0] == 1


def test_same_idempotency_key_with_different_content_conflicts(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-conflict-" + secrets.token_hex(8))
    service = build_service(clean_database)
    service.create(valid_request(phone, text="first"), key)

    with pytest.raises(IdempotencyConflict):
        service.create(valid_request(phone, text="second"), key)


def test_contact_and_conversation_are_reused_for_same_route(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    first_key = clean_database.track_message_key("pytest-conv-a-" + secrets.token_hex(8))
    second_key = clean_database.track_message_key("pytest-conv-b-" + secrets.token_hex(8))
    service = build_service(clean_database)

    first = service.create(valid_request(phone), first_key)
    second = service.create(valid_request(phone, text="second"), second_key)

    assert second.response.conversation_id == first.response.conversation_id
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM contacts WHERE normalized_phone_number = %s", (phone,)
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM conversations WHERE external_phone_number = %s", (phone,)
        ).fetchone()[0] == 1


def test_explicit_conversation_is_reused_and_rejects_phone_mismatch(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    other_phone = random_phone(clean_database)
    first_key = clean_database.track_message_key("pytest-explicit-a-" + secrets.token_hex(8))
    second_key = clean_database.track_message_key("pytest-explicit-b-" + secrets.token_hex(8))
    conflict_key = clean_database.track_message_key("pytest-explicit-c-" + secrets.token_hex(8))
    service = build_service(clean_database)
    first = service.create(valid_request(phone), first_key)

    second = service.create(
        valid_request(phone, conversationId=first.response.conversation_id),
        second_key,
    )
    assert second.response.conversation_id == first.response.conversation_id
    with pytest.raises(MessageStateConflict):
        service.create(
            valid_request(other_phone, conversationId=first.response.conversation_id),
            conflict_key,
        )


def test_transaction_failure_rolls_back_contact_conversation_and_message(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-rollback-" + secrets.token_hex(8))

    class FailingConnection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, query, params=None):
            if "INSERT INTO message_state_history" in query:
                raise RuntimeError("simulated history failure")
            return self.connection.execute(query, params)

    class FailingDatabase:
        @contextmanager
        def transaction(self):
            with Database(clean_database.dsn).transaction() as connection:
                yield FailingConnection(connection)

    service = MessageCommandService(
        FailingDatabase(),
        online_window_seconds=300,
        publisher=RecordingPublisher(),
    )

    with pytest.raises(RuntimeError, match="simulated history failure"):
        service.create(valid_request(phone), key)

    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM messages WHERE idempotency_key = %s", (key,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM conversations WHERE external_phone_number = %s",
            (phone,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM contacts WHERE normalized_phone_number = %s",
            (phone,),
        ).fetchone()[0] == 0


def test_expired_valid_until_is_rejected_before_writes(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-validity-" + secrets.token_hex(8))
    request = valid_request(
        phone,
        validUntil=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )

    with pytest.raises(MessageValidationError):
        build_service(clean_database).create(request, key)

    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM messages WHERE idempotency_key = %s", (key,)
        ).fetchone()[0] == 0


def test_publisher_failure_does_not_rollback_committed_message(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-publisher-" + secrets.token_hex(8))
    publisher = RecordingPublisher(error=RuntimeError("sse unavailable"))

    result = build_service(clean_database, publisher).create(valid_request(phone), key)

    assert read_message(clean_database.dsn, result.response.id) is not None
    assert publisher.events == [(result.response.device_id, result.response.id)]


def test_concurrent_replay_creates_one_message(clean_database):
    seed_route(clean_database)
    phone = random_phone(clean_database)
    key = clean_database.track_message_key("pytest-concurrent-" + secrets.token_hex(8))
    request = valid_request(phone)

    def create():
        return build_service(clean_database).create(request, key)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(), range(2)))

    assert results[0].response.id == results[1].response.id
    assert sorted(result.replayed for result in results) == [False, True]
    with psycopg.connect(clean_database.dsn) as connection:
        assert connection.execute(
            "SELECT count(*) FROM messages WHERE idempotency_key = %s", (key,)
        ).fetchone()[0] == 1
