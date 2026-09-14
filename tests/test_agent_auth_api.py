from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.security import hash_password


def test_agent_login_returns_token_and_me(clean_database):
    account_id = "acct_" + uuid4().hex
    username = "agent_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO accounts(id, username, password_hash, areas, status)
            VALUES(%s, %s, %s, %s, 'ACTIVE')
            """,
            (account_id, username, hash_password(password), "north"),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/agent/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        me = client.get("/agent/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json() == {"id": account_id, "username": username, "areas": "north"}


def test_agent_login_allows_sim_bound_account_without_area(clean_database):
    account_id = "acct_" + uuid4().hex
    username = "agent_" + uuid4().hex
    password = "correct-password"
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO accounts(id, username, password_hash, areas, status)
            VALUES(%s, %s, %s, NULL, 'ACTIVE')
            """,
            (account_id, username, hash_password(password)),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/agent/v1/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200
        token = login.json()["token"]
        me = client.get("/agent/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json() == {"id": account_id, "username": username, "areas": None}


def test_agent_login_rejects_wrong_password(clean_database):
    username = "agent_" + uuid4().hex
    with psycopg.connect(clean_database.dsn) as connection:
        connection.execute(
            """
            INSERT INTO accounts(id, username, password_hash, areas, status)
            VALUES(%s, %s, %s, %s, 'ACTIVE')
            """,
            ("acct_" + uuid4().hex, username, hash_password("right-password"), "north"),
        )
        connection.commit()

    app = create_app(Settings(clean_database.dsn, "registration-secret", "business-secret"))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/agent/v1/auth/login",
            json={"username": username, "password": "wrong-password"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
