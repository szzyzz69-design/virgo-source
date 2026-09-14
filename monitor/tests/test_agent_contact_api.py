from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.security import hash_password


def _insert_account(connection, username: str, password: str, area: str) -> str:
    account_id = "acct_" + uuid4().hex
    connection.execute(
        """
        INSERT INTO accounts(id, username, password_hash, areas, status)
        VALUES(%s, %s, %s, %s, 'ACTIVE')
        """,
        (account_id, username, hash_password(password), area),
    )
    return account_id


def _insert_agent_sim_card(
    connection,
    clean_database,
    phone_number: str | None,
    carrier_name: str | None,
    area: str | None,
    customer_remark: str | None = None,
) -> str:
    suffix = uuid4().hex
    now = 1_800_000_000_000
    device_id = clean_database.track(f"dev_agent_sim_{suffix}")
    sim_id = f"sim_agent_contact_{suffix}"
    connection.execute(
        """
        INSERT INTO devices(id, name, token_hash, login, enabled, status, last_seen_at)
        VALUES(%s, %s, %s, %s, TRUE, 'online', %s)
        """,
        (device_id, "agent sim phone", f"token_{suffix}", f"login_{suffix}", now),
    )
    connection.execute(
        """
        INSERT INTO sim_cards(
            id, device_id, slot_index, sim_number, phone_number, carrier_name,
            areas, esim_profile_name
        )
        VALUES(%s, %s, 0, 1, %s, %s, %s, %s)
        """,
        (sim_id, device_id, phone_number, carrier_name, area, customer_remark),
    )
    return sim_id


def _insert_contact(connection, clean_database, area: str, remark: str | None = None):
    suffix = uuid4().hex
    phone = clean_database.track_phone("+86" + str(uuid4().int)[:11])
    contact_id = f"contact_agent_contact_{suffix}"
    now = 1_800_000_000_000
    connection.execute(
        """
        INSERT INTO contacts(
            id, display_name, phone_number, normalized_phone_number, remark,
            status, source, last_contact_at, created_at, updated_at, areas
        )
        VALUES(%s, %s, %s, %s, %s, 'NORMAL', 'MANUAL', %s, %s, %s, %s)
        """,
        (
            contact_id,
            f"{area} contact",
            phone,
            phone,
            remark,
            now,
            now,
            now,
            area,
        ),
    )
    return contact_id, phone


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/agent/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_agent_contact_list_returns_only_matching_area(clean_database):
    username = "north_contact_" + uuid4().hex
    password = "correct-password"
    area = "north_" + uuid4().hex[:12]
    other_area = "south_" + uuid4().hex[:12]
    with psycopg.connect(clean_database.dsn) as connection:
        _insert_account(connection, username, password, area)
        north_contact, north_phone = _insert_contact(
            connection, clean_database, area, "north remark"
        )
        south_contact, _ = _insert_contact(
            connection, clean_database, other_area, "south remark"
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            "/agent/v1/contacts",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    contacts = response.json()
    assert [item["id"] for item in contacts] == [north_contact]
    assert contacts[0]["phoneNumber"] == north_phone
    assert contacts[0]["remark"] == "north remark"
    assert contacts[0]["areas"] == area
    assert south_contact not in [item["id"] for item in contacts]


def test_agent_can_update_matching_contact_remark(clean_database):
    username = "remark_contact_" + uuid4().hex
    password = "correct-password"
    area = "north_" + uuid4().hex[:12]
    with psycopg.connect(clean_database.dsn) as connection:
        _insert_account(connection, username, password, area)
        contact_id, _ = _insert_contact(connection, clean_database, area, None)
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.patch(
            f"/agent/v1/contacts/{contact_id}/remark",
            headers={"Authorization": f"Bearer {token}"},
            json={"remark": "VIP customer"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    with psycopg.connect(clean_database.dsn) as connection:
        remark = connection.execute(
            "SELECT remark FROM contacts WHERE id = %s",
            (contact_id,),
        ).fetchone()[0]
    assert remark == "VIP customer"


def test_agent_can_clear_matching_contact_remark(clean_database):
    username = "clear_contact_" + uuid4().hex
    password = "correct-password"
    area = "north_" + uuid4().hex[:12]
    with psycopg.connect(clean_database.dsn) as connection:
        _insert_account(connection, username, password, area)
        contact_id, _ = _insert_contact(
            connection, clean_database, area, "old remark"
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.patch(
            f"/agent/v1/contacts/{contact_id}/remark",
            headers={"Authorization": f"Bearer {token}"},
            json={"remark": "   "},
        )

    assert response.status_code == 200
    with psycopg.connect(clean_database.dsn) as connection:
        remark = connection.execute(
            "SELECT remark FROM contacts WHERE id = %s",
            (contact_id,),
        ).fetchone()[0]
    assert remark is None


def test_agent_contact_remark_rejects_cross_area_access(clean_database):
    username = "cross_contact_" + uuid4().hex
    password = "correct-password"
    area = "north_" + uuid4().hex[:12]
    other_area = "south_" + uuid4().hex[:12]
    with psycopg.connect(clean_database.dsn) as connection:
        _insert_account(connection, username, password, area)
        contact_id, _ = _insert_contact(
            connection, clean_database, other_area, "south remark"
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.patch(
            f"/agent/v1/contacts/{contact_id}/remark",
            headers={"Authorization": f"Bearer {token}"},
            json={"remark": "not allowed"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_agent_menu_list_returns_only_matching_non_empty_area_menus(clean_database):
    username = "menu_contact_" + uuid4().hex
    password = "correct-password"
    area = "north_" + uuid4().hex[:12]
    other_area = "south_" + uuid4().hex[:12]
    north_id = "prod_" + uuid4().hex
    south_id = "prod_" + uuid4().hex
    blank_id = "prod_" + uuid4().hex
    with psycopg.connect(clean_database.dsn) as connection:
        _insert_account(connection, username, password, area)
        connection.execute(
            """
            INSERT INTO products(id, menu, update_time, areas)
            VALUES(%s, %s, %s, %s)
            """,
            (north_id, "north script", 1_800_000_000_003, area),
        )
        connection.execute(
            """
            INSERT INTO products(id, menu, update_time, areas)
            VALUES(%s, %s, %s, %s)
            """,
            (south_id, "south script", 1_800_000_000_002, other_area),
        )
        connection.execute(
            """
            INSERT INTO products(id, menu, update_time, areas)
            VALUES(%s, %s, %s, %s)
            """,
            (blank_id, "   ", 1_800_000_000_001, area),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            "/agent/v1/menus",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": north_id,
            "menu": "north script",
            "updateTime": 1_800_000_000_003,
            "updateBy": None,
            "areas": area,
        }
    ]


def test_agent_sim_card_list_returns_only_bound_sim_cards(clean_database):
    username = "sim_contact_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        account_id = _insert_account(connection, username, password, "north")
        bound_a = _insert_agent_sim_card(
            connection,
            clean_database,
            "+8613800000001",
            "China Mobile",
            "north",
            "客服 A 专用",
        )
        bound_b = _insert_agent_sim_card(
            connection,
            clean_database,
            "+8613800000002",
            "China Unicom",
            "east",
        )
        unbound = _insert_agent_sim_card(
            connection,
            clean_database,
            "+8613800000003",
            "China Telecom",
            "north",
        )
        connection.execute(
            """
            INSERT INTO account_sim_cards(account_id, sim_card_id)
            VALUES(%s, %s), (%s, %s)
            """,
            (account_id, bound_a, account_id, bound_b),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            "/agent/v1/sim-cards",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": bound_a,
            "phoneNumber": "+8613800000001",
            "carrierName": "China Mobile",
            "areas": "north",
            "customerRemark": "客服 A 专用",
        },
        {
            "id": bound_b,
            "phoneNumber": "+8613800000002",
            "carrierName": "China Unicom",
            "areas": "east",
            "customerRemark": None,
        },
    ]
    assert unbound not in [item["id"] for item in response.json()]


def test_agent_sim_card_list_returns_empty_when_no_bound_sim_cards(clean_database):
    username = "empty_sim_contact_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        _insert_account(connection, username, password, "north")
        _insert_agent_sim_card(
            connection,
            clean_database,
            "+8613800000004",
            "China Mobile",
            "north",
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        token = _login(client, username, password)
        response = client.get(
            "/agent/v1/sim-cards",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == []
