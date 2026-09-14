from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.security import hash_password


def insert_account(connection, account_id, username, password_hash, area):
    connection.execute(
        """
        INSERT INTO accounts(id, username, password_hash, areas, status)
        VALUES(%s, %s, %s, %s, 'ACTIVE')
        """,
        (account_id, username, password_hash, area),
    )
    return account_id


def bind_account_sim(connection, account_id, sim_id):
    connection.execute(
        """
        INSERT INTO account_sim_cards(account_id, sim_card_id)
        VALUES(%s, %s)
        """,
        (account_id, sim_id),
    )


def _insert_conversation_fixture(connection, clean_database, area: str):
    suffix = uuid4().hex
    now = 1_800_000_000_000
    device_id = clean_database.track(f"dev_agent_{suffix}")
    sim_id = f"sim_agent_{suffix}"
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    contact_id = f"contact_agent_{suffix}"
    conversation_id = f"conv_agent_{suffix}"
    message_id = clean_database.track_message_key(f"msg-agent-{suffix}")
    connection.execute(
        """
        INSERT INTO devices(id, name, token_hash, login, enabled, status, last_seen_at)
        VALUES(%s, %s, %s, %s, TRUE, 'online', %s)
        """,
        (device_id, "agent phone", f"token_{suffix}", f"login_{suffix}", now),
    )
    connection.execute(
        """
        INSERT INTO sim_cards(id, device_id, slot_index, sim_number, areas)
        VALUES(%s, %s, 0, 1, %s)
        """,
        (sim_id, device_id, area),
    )
    connection.execute(
        """
        INSERT INTO contacts(id, phone_number, normalized_phone_number, source)
        VALUES(%s, %s, %s, 'MANUAL')
        """,
        (contact_id, phone, phone),
    )
    connection.execute(
        """
        INSERT INTO conversations(
            id, external_phone_number, contact_id, device_id, sim_card_id,
            sim_number, areas, status, unread_count, last_message_preview,
            last_message_direction, last_message_at, created_at, updated_at
        )
        VALUES(%s, %s, %s, %s, %s, 1, %s, 'OPEN', 3, %s, 'INBOUND', %s, %s, %s)
        """,
        (
            conversation_id,
            phone,
            contact_id,
            device_id,
            sim_id,
            area,
            f"{area} hello",
            now,
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO messages(
            id, conversation_id, direction, message_type, text_content,
            from_phone_number, to_phone_number, state, device_id, sim_card_id,
            sim_number, idempotency_key, received_at, created_at, updated_at
        )
        VALUES(%s, %s, 'INBOUND', 'SMS', %s, %s, NULL, 'Received', %s, %s, 1, %s, %s, %s, %s)
        """,
        (
            message_id,
            conversation_id,
            f"{area} message",
            phone,
            device_id,
            sim_id,
            message_id,
            now,
            now,
            now,
        ),
    )
    return conversation_id, message_id, sim_id


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/agent/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_agent_conversation_list_returns_only_account_bound_sim_conversations(clean_database):
    north_user = "north_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        north_account = insert_account(
            connection,
            "acct_" + uuid4().hex,
            north_user,
            hash_password(password),
            "north",
        )
        north_conversation, _, north_sim = _insert_conversation_fixture(
            connection, clean_database, "north"
        )
        north_unbound_conversation, _, north_unbound_sim = _insert_conversation_fixture(
            connection, clean_database, "north"
        )
        south_conversation, _, south_sim = _insert_conversation_fixture(
            connection, clean_database, "south"
        )
        connection.execute(
            """
            UPDATE sim_cards
            SET phone_number = CASE id
                WHEN %s THEN '+8613800000101'
                WHEN %s THEN '+8613800000102'
                ELSE phone_number
            END
            WHERE id IN (%s, %s)
            """,
            (north_sim, north_unbound_sim, north_sim, north_unbound_sim),
        )
        bind_account_sim(connection, north_account, north_sim)
        bind_account_sim(connection, north_account, south_sim)
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, north_user, password)
        response = client.get(
            "/agent/v1/conversations",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert set(ids) == {north_conversation, south_conversation}
    assert north_unbound_conversation not in ids
    assert {item["areas"] for item in response.json()} == {"north"}
    service_phone_by_conversation = {
        item["id"]: item["servicePhoneNumber"] for item in response.json()
    }
    assert service_phone_by_conversation[north_conversation] == "+8613800000101"


def test_agent_can_search_conversations_by_contact_phone(clean_database):
    username = "search_" + uuid4().hex
    password = "correct-password"
    suffix = uuid4().hex
    now = 1_800_000_000_000
    device_id = clean_database.track(f"dev_search_{suffix}")
    contact_phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    contact_id = f"contact_search_{suffix}"
    sim_a = f"sim_search_a_{suffix}"
    sim_b = f"sim_search_b_{suffix}"
    sim_hidden = f"sim_search_hidden_{suffix}"
    conversation_a = f"conv_search_a_{suffix}"
    conversation_b = f"conv_search_b_{suffix}"
    conversation_hidden = f"conv_search_hidden_{suffix}"
    with psycopg.connect(clean_database.dsn) as connection:
        account_id = insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        connection.execute(
            """
            INSERT INTO devices(id, name, token_hash, login, enabled, status, last_seen_at)
            VALUES(%s, %s, %s, %s, TRUE, 'online', %s)
            """,
            (device_id, "agent phone", f"token_{suffix}", f"login_{suffix}", now),
        )
        for sim_id, sim_number, service_phone, area in (
            (sim_a, 1, "+8613800000001", "north"),
            (sim_b, 2, "+8613800000002", "north"),
            (sim_hidden, 3, "+8613800000003", "east"),
        ):
            connection.execute(
                """
                INSERT INTO sim_cards(
                    id, device_id, slot_index, sim_number, phone_number, areas
                )
                VALUES(%s, %s, %s, %s, %s, %s)
                """,
                (sim_id, device_id, sim_number - 1, sim_number, service_phone, area),
            )
        bind_account_sim(connection, account_id, sim_a)
        bind_account_sim(connection, account_id, sim_b)
        connection.execute(
            """
            INSERT INTO contacts(
                id, phone_number, normalized_phone_number, remark, source, areas
            )
            VALUES(%s, %s, %s, %s, 'MANUAL', 'north')
            """,
            (contact_id, contact_phone, contact_phone, "VIP customer"),
        )
        for conversation_id, sim_id, sim_number, area in (
            (conversation_a, sim_a, 1, "north"),
            (conversation_b, sim_b, 2, "north"),
            (conversation_hidden, sim_hidden, 3, "east"),
        ):
            connection.execute(
                """
                INSERT INTO conversations(
                    id, external_phone_number, contact_id, device_id, sim_card_id,
                    sim_number, areas, status, created_at, updated_at
                )
                VALUES(%s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s)
                """,
                (
                    conversation_id,
                    contact_phone,
                    contact_id,
                    device_id,
                    sim_id,
                    sim_number,
                    area,
                    now,
                    now,
                ),
            )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            "/agent/v1/conversation-search",
            params={"phoneNumber": contact_phone[-6:]},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "contactPhoneNumber": contact_phone,
            "remark": "VIP customer",
            "servicePhoneNumber": "+8613800000001",
            "conversationId": conversation_a,
        },
        {
            "contactPhoneNumber": contact_phone,
            "remark": "VIP customer",
            "servicePhoneNumber": "+8613800000002",
            "conversationId": conversation_b,
        },
    ]


def test_agent_message_history_allows_bound_sim_with_legacy_area_mismatch(clean_database):
    username = "north_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        account_id = insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        south_conversation, message_id, south_sim = _insert_conversation_fixture(
            connection, clean_database, "south"
        )
        bind_account_sim(connection, account_id, south_sim)
        connection.execute(
            """
            UPDATE sim_cards
            SET phone_number = %s, esim_profile_name = %s
            WHERE id = %s
            """,
            ("+8613800000099", "south support line", south_sim),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            f"/agent/v1/conversations/{south_conversation}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()[0]["id"] == message_id
    assert response.json()[0]["conversationId"] == south_conversation
    assert response.json()[0]["customerSimCard"] == "+8613800000099"
    assert response.json()[0]["customerRemark"] == "south support line"
    assert response.json()[0]["attachments"] == []


def test_agent_message_item_includes_attachments():
    from app.schemas.agent_conversation import AgentMessageAttachment, AgentMessageItem

    item = AgentMessageItem(
        id="msg_1",
        conversationId="conv_1",
        direction="INBOUND",
        messageType="MMS",
        textContent="hello",
        state="Received",
        fromPhoneNumber="+8613800138000",
        toPhoneNumber=None,
        createdAt=1,
        receivedAt=1,
        sentAt=None,
        deliveredAt=None,
        attachments=[
            AgentMessageAttachment(
                id="att_1",
                partId=17,
                contentType="image/jpeg",
                name="photo.jpg",
                size=3,
                url="https://cdn.example.test/photo.jpg",
            )
        ],
    )

    assert item.model_dump(by_alias=True)["attachments"][0]["partId"] == 17


def test_agent_message_history_includes_ordered_attachments(clean_database):
    username = "mms_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        account_id = insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "south",
        )
        conversation_id, message_id, sim_id = _insert_conversation_fixture(
            connection, clean_database, "south"
        )
        bind_account_sim(connection, account_id, sim_id)
        connection.execute(
            """
            UPDATE messages
            SET message_type = 'MMS', text_content = 'hello image'
            WHERE id = %s
            """,
            (message_id,),
        )
        first_attachment_id = "att_" + uuid4().hex
        second_attachment_id = "att_" + uuid4().hex
        connection.execute(
            """
            INSERT INTO message_attachments(
                id, message_id, part_id, content_type, name, size, s3_bucket, s3_key, url
            )
            VALUES
                (%s, %s, 17, 'image/jpeg', 'photo.jpg', 3, 'bucket', 'mms/photo.jpg', %s),
                (%s, %s, 2, 'image/png', NULL, NULL, 'bucket', 'mms/preview.png', %s)
            """,
            (
                first_attachment_id,
                message_id,
                "https://cdn.example.test/photo.jpg",
                second_attachment_id,
                message_id,
                "https://cdn.example.test/preview.png",
            ),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            f"/agent/v1/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()[0]["messageType"] == "MMS"
    assert response.json()[0]["attachments"] == [
        {
            "id": second_attachment_id,
            "partId": 2,
            "contentType": "image/png",
            "name": None,
            "size": None,
            "url": "https://cdn.example.test/preview.png",
        },
        {
            "id": first_attachment_id,
            "partId": 17,
            "contentType": "image/jpeg",
            "name": "photo.jpg",
            "size": 3,
            "url": "https://cdn.example.test/photo.jpg",
        },
    ]


def test_agent_message_history_rejects_other_area_access(clean_database):
    username = "north_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        south_conversation, _, _ = _insert_conversation_fixture(
            connection, clean_database, "south"
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            f"/agent/v1/conversations/{south_conversation}/messages",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403


def test_agent_can_mark_matching_conversation_read(clean_database):
    username = "north_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        account_id = connection.execute(
            "SELECT id FROM accounts WHERE username = %s",
            (username,),
        ).fetchone()[0]
        conversation_id, _, sim_id = _insert_conversation_fixture(
            connection, clean_database, "south"
        )
        bind_account_sim(connection, account_id, sim_id)
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.patch(
            f"/agent/v1/conversations/{conversation_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    with psycopg.connect(clean_database.dsn) as connection:
        unread_count = connection.execute(
            "SELECT unread_count FROM conversations WHERE id = %s",
            (conversation_id,),
        ).fetchone()[0]
    assert unread_count == 0


def test_agent_rejects_marking_other_area_conversation_read(clean_database):
    username = "north_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        conversation_id, _, _ = _insert_conversation_fixture(
            connection, clean_database, "south"
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.patch(
            f"/agent/v1/conversations/{conversation_id}/read",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    with psycopg.connect(clean_database.dsn) as connection:
        unread_count = connection.execute(
            "SELECT unread_count FROM conversations WHERE id = %s",
            (conversation_id,),
        ).fetchone()[0]
    assert unread_count == 3


def test_agent_can_reply_to_matching_conversation_route(clean_database):
    username = "north_" + uuid4().hex
    password = "correct-password"
    key = clean_database.track_message_key("agent-reply:" + uuid4().hex)
    with psycopg.connect(clean_database.dsn) as connection:
        account_id = insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        conversation_id, _, sim_id = _insert_conversation_fixture(
            connection, clean_database, "south"
        )
        bind_account_sim(connection, account_id, sim_id)
        route = connection.execute(
            """
            SELECT external_phone_number, device_id, sim_card_id, sim_number
            FROM conversations
            WHERE id = %s
            """,
            (conversation_id,),
        ).fetchone()
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.post(
            f"/agent/v1/conversations/{conversation_id}/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": key,
            },
            json={"text": "客服回复"},
        )

    assert response.status_code == 201
    with psycopg.connect(clean_database.dsn) as connection:
        message = connection.execute(
            """
            SELECT direction, conversation_id, to_phone_number, device_id,
                   sim_card_id, sim_number, state
            FROM messages
            WHERE id = %s
            """,
            (response.json()["id"],),
        ).fetchone()
    assert message == (
        "OUTBOUND",
        conversation_id,
        route[0],
        route[1],
        route[2],
        route[3],
        "Pending",
    )


def test_shared_sim_conversation_is_accessible_to_bound_accounts_in_different_areas(
    clean_database,
):
    first_username = "shared_first_" + uuid4().hex
    second_username = "shared_second_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        first_account = insert_account(
            connection,
            "acct_" + uuid4().hex,
            first_username,
            hash_password(password),
            "Regina",
        )
        second_account = insert_account(
            connection,
            "acct_" + uuid4().hex,
            second_username,
            hash_password(password),
            "Richmond Hill",
        )
        conversation_id, message_id, sim_id = _insert_conversation_fixture(
            connection,
            clean_database,
            "legacy-sim-note",
        )
        bind_account_sim(connection, first_account, sim_id)
        bind_account_sim(connection, second_account, sim_id)
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        first_token = _login(client, first_username, password)
        second_token = _login(client, second_username, password)

        first_list = client.get(
            "/agent/v1/conversations",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        second_list = client.get(
            "/agent/v1/conversations",
            headers={"Authorization": f"Bearer {second_token}"},
        )
        first_detail = client.get(
            f"/agent/v1/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        second_detail = client.get(
            f"/agent/v1/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {second_token}"},
        )

    assert [item["id"] for item in first_list.json()] == [conversation_id]
    assert [item["id"] for item in second_list.json()] == [conversation_id]
    assert first_list.json()[0]["areas"] == "Regina"
    assert second_list.json()[0]["areas"] == "Richmond Hill"
    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
    assert first_detail.json()[0]["id"] == message_id
    assert second_detail.json()[0]["id"] == message_id


def test_agent_cannot_reply_to_same_area_unbound_sim_conversation(clean_database):
    username = "north_" + uuid4().hex
    password = "correct-password"
    key = clean_database.track_message_key("agent-reply:" + uuid4().hex)
    with psycopg.connect(clean_database.dsn) as connection:
        insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        conversation_id, _, _ = _insert_conversation_fixture(
            connection, clean_database, "north"
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.post(
            f"/agent/v1/conversations/{conversation_id}/messages",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": key,
            },
            json={"text": "客服回复"},
        )

    assert response.status_code == 403


def test_agent_reply_requires_idempotency_key(clean_database):
    username = "north_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        insert_account(
            connection,
            "acct_" + uuid4().hex,
            username,
            hash_password(password),
            "north",
        )
        account_id = connection.execute(
            "SELECT id FROM accounts WHERE username = %s",
            (username,),
        ).fetchone()[0]
        conversation_id, _, sim_id = _insert_conversation_fixture(
            connection, clean_database, "north"
        )
        bind_account_sim(connection, account_id, sim_id)
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.post(
            f"/agent/v1/conversations/{conversation_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "客服回复"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
