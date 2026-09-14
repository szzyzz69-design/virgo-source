import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.services.agent_event_publisher import AgentEventRegistry


async def read_one_event(registry, connection):
    stream = registry.stream(connection)
    try:
        return await anext(stream)
    finally:
        await stream.aclose()


def test_inbound_conversation_copies_area_from_receiving_sim(clean_database):
    marker = clean_database.track_push_token("pytest-agent-area-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    inbox_id = clean_database.track_message_key("agent-area:" + uuid4().hex)
    recipient = "+8613900000000"
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "agent-area-phone",
                "pushToken": marker,
                "simCards": [
                    {
                        "slotIndex": 0,
                        "simNumber": 1,
                        "phoneNumber": recipient,
                    }
                ],
            },
        ).json()
        clean_database.track(registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("south", registration["id"]),
            )
            connection.commit()

        response = client.post(
            "/mobile/v1/inbox",
            headers={"Authorization": f"Bearer {registration['token']}"},
            json={
                "id": inbox_id,
                "type": "SMS",
                "sender": sender,
                "recipient": recipient,
                "simNumber": 1,
                "subscriptionId": 3,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
                "textMessage": {"text": "area hello"},
                "dataMessage": None,
            },
        )

    assert response.status_code == 201
    with psycopg.connect(clean_database.dsn) as connection:
        area = connection.execute(
            "SELECT areas FROM conversations WHERE id = %s",
            (response.json()["conversationId"],),
        ).fetchone()[0]
    assert area == "south"


def test_outbound_conversation_copies_area_from_selected_sim(clean_database):
    marker = clean_database.track_push_token("pytest-agent-outbound-area-" + uuid4().hex)
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    key = clean_database.track_message_key("agent-outbound-area:" + uuid4().hex)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "agent-outbound-area-phone",
                "pushToken": marker,
                "simCards": [{"slotIndex": 0, "simNumber": 1}],
            },
        ).json()
        clean_database.track(registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("north", registration["id"]),
            )
            connection.commit()

        response = client.post(
            "/business/v1/messages",
            headers={
                "Authorization": "Bearer business-secret",
                "Idempotency-Key": key,
            },
            json={"phoneNumbers": [phone], "text": "area outbound"},
        )

    assert response.status_code == 201
    with psycopg.connect(clean_database.dsn) as connection:
        area = connection.execute(
            "SELECT areas FROM conversations WHERE id = %s",
            (response.json()["conversationId"],),
        ).fetchone()[0]
    assert area == "north"


def test_inbound_message_publishes_only_to_accounts_bound_to_receiving_sim(clean_database):
    marker = clean_database.track_push_token("pytest-agent-event-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    inbox_id = clean_database.track_message_key("agent-event:" + uuid4().hex)
    registry = AgentEventRegistry(heartbeat_seconds=0.01)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret"),
        agent_event_registry=registry,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "agent-event-phone",
                "pushToken": marker,
                "simCards": [{"slotIndex": 0, "simNumber": 1}],
            },
        ).json()
        clean_database.track(registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            sim_id = connection.execute(
                "SELECT id FROM sim_cards WHERE device_id = %s AND sim_number = 1",
                (registration["id"],),
            ).fetchone()[0]
            bound_account_id = "acct_" + uuid4().hex
            unbound_account_id = "acct_" + uuid4().hex
            connection.execute(
                """
                INSERT INTO accounts(id, username, password_hash, areas, status)
                VALUES(%s, %s, 'unused', 'same-area', 'ACTIVE'),
                      (%s, %s, 'unused', 'same-area', 'ACTIVE')
                """,
                (
                    bound_account_id,
                    "bound_" + uuid4().hex,
                    unbound_account_id,
                    "unbound_" + uuid4().hex,
                ),
            )
            connection.execute(
                """
                INSERT INTO account_sim_cards(account_id, sim_card_id)
                VALUES(%s, %s)
                """,
                (bound_account_id, sim_id),
            )
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("north", registration["id"]),
            )
            connection.commit()

        bound = registry.register(bound_account_id)
        unbound = registry.register(unbound_account_id)
        response = client.post(
            "/mobile/v1/inbox",
            headers={"Authorization": f"Bearer {registration['token']}"},
            json={
                "id": inbox_id,
                "type": "SMS",
                "sender": sender,
                "recipient": None,
                "simNumber": 1,
                "subscriptionId": 3,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
                "textMessage": {"text": "north event"},
                "dataMessage": None,
            },
        )

    assert response.status_code == 201
    bound_event = asyncio.run(read_one_event(registry, bound))
    unbound_event = asyncio.run(read_one_event(registry, unbound))
    assert "event: inbound_message\n" in bound_event
    assert f'"conversationId":"{response.json()["conversationId"]}"' in bound_event
    assert f'"messageId":"{response.json()["id"]}"' in bound_event
    assert f'"accountId":"{bound_account_id}"' in bound_event
    assert f'"simCardId":"{sim_id}"' in bound_event
    assert '"textContent":"north event"' in bound_event
    assert '"state":"Received"' in bound_event
    assert '"createdAt":' in bound_event
    assert unbound_event == ": ping\n\n"


def test_inbound_contact_copies_area_from_receiving_sim(clean_database):
    marker = clean_database.track_push_token("pytest-contact-area-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    inbox_id = clean_database.track_message_key("contact-area:" + uuid4().hex)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "contact-area-phone",
                "pushToken": marker,
                "simCards": [{"slotIndex": 0, "simNumber": 1}],
            },
        ).json()
        clean_database.track(registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("east", registration["id"]),
            )
            connection.commit()

        response = client.post(
            "/mobile/v1/inbox",
            headers={"Authorization": f"Bearer {registration['token']}"},
            json={
                "id": inbox_id,
                "type": "SMS",
                "sender": sender,
                "recipient": None,
                "simNumber": 1,
                "subscriptionId": 3,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
                "textMessage": {"text": "contact area"},
                "dataMessage": None,
            },
        )

    assert response.status_code == 201
    with psycopg.connect(clean_database.dsn) as connection:
        area = connection.execute(
            "SELECT areas FROM contacts WHERE normalized_phone_number = %s",
            (sender,),
        ).fetchone()[0]
    assert area == "east"


def test_inbound_contact_area_updates_to_latest_receiving_sim(clean_database):
    first_marker = clean_database.track_push_token("pytest-contact-first-" + uuid4().hex)
    second_marker = clean_database.track_push_token("pytest-contact-second-" + uuid4().hex)
    sender = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    first_inbox_id = clean_database.track_message_key("contact-first:" + uuid4().hex)
    second_inbox_id = clean_database.track_message_key("contact-second:" + uuid4().hex)
    app = create_app(
        Settings(clean_database.dsn, "registration-secret", "business-secret")
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        first_registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "contact-first-phone",
                "pushToken": first_marker,
                "simCards": [{"slotIndex": 0, "simNumber": 1}],
            },
        ).json()
        second_registration = client.post(
            "/mobile/v1/device",
            headers={"Authorization": "Bearer registration-secret"},
            json={
                "name": "contact-second-phone",
                "pushToken": second_marker,
                "simCards": [{"slotIndex": 0, "simNumber": 1}],
            },
        ).json()
        clean_database.track(first_registration["id"])
        clean_database.track(second_registration["id"])
        with psycopg.connect(clean_database.dsn) as connection:
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("east", first_registration["id"]),
            )
            connection.execute(
                "UPDATE sim_cards SET areas = %s WHERE device_id = %s AND sim_number = 1",
                ("west", second_registration["id"]),
            )
            connection.commit()

        first_response = client.post(
            "/mobile/v1/inbox",
            headers={"Authorization": f"Bearer {first_registration['token']}"},
            json={
                "id": first_inbox_id,
                "type": "SMS",
                "sender": sender,
                "recipient": None,
                "simNumber": 1,
                "subscriptionId": 3,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
                "textMessage": {"text": "first area"},
                "dataMessage": None,
            },
        )
        second_response = client.post(
            "/mobile/v1/inbox",
            headers={"Authorization": f"Bearer {second_registration['token']}"},
            json={
                "id": second_inbox_id,
                "type": "SMS",
                "sender": sender,
                "recipient": None,
                "simNumber": 1,
                "subscriptionId": 3,
                "receivedAt": datetime.now(timezone.utc).isoformat(),
                "textMessage": {"text": "second area"},
                "dataMessage": None,
            },
        )

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    with psycopg.connect(clean_database.dsn) as connection:
        area = connection.execute(
            "SELECT areas FROM contacts WHERE normalized_phone_number = %s",
            (sender,),
        ).fetchone()[0]
    assert area == "west"
