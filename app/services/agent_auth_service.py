from dataclasses import dataclass
import secrets
import time

from app.database import Database
from app.security import hash_sha256, verify_password


class InvalidAgentCredentials(Exception):
    pass


class InvalidAgentToken(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedAgent:
    id: str
    username: str
    areas: str | None


@dataclass(frozen=True, slots=True)
class AgentSession:
    token: str
    expires_at: int
    agent: AuthenticatedAgent


class AgentAuthService:
    def __init__(self, database: Database, session_ttl_seconds: int = 86400):
        self._database = database
        self._session_ttl_seconds = session_ttl_seconds

    def login(self, username: str, password: str) -> AgentSession:
        now = time.time_ns() // 1_000_000
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT id, username, password_hash, areas, status
                FROM accounts
                WHERE username = %s
                """,
                (username,),
            ).fetchone()
            if row is None or row[4] != "ACTIVE":
                raise InvalidAgentCredentials
            if not verify_password(password, row[2]):
                raise InvalidAgentCredentials

            token = "agent_" + secrets.token_urlsafe(32)
            expires_at = now + self._session_ttl_seconds * 1000
            connection.execute(
                """
                INSERT INTO agent_sessions(token_hash, account_id, created_at, expires_at)
                VALUES(%s, %s, %s, %s)
                """,
                (hash_sha256(token), row[0], now, expires_at),
            )
            agent = AuthenticatedAgent(id=row[0], username=row[1], areas=row[3])
            return AgentSession(token=token, expires_at=expires_at, agent=agent)

    def authenticate(self, token: str) -> AuthenticatedAgent:
        now = time.time_ns() // 1_000_000
        with self._database.transaction() as connection:
            row = connection.execute(
                """
                SELECT a.id, a.username, a.areas, a.status, s.expires_at
                FROM agent_sessions s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.token_hash = %s
                """,
                (hash_sha256(token),),
            ).fetchone()
        if row is None or row[3] != "ACTIVE" or row[4] <= now:
            raise InvalidAgentToken
        return AuthenticatedAgent(id=row[0], username=row[1], areas=row[2])
