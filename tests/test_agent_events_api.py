from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent_events import create_agent_events_router
from app.errors import install_error_handling
from app.services.agent_auth_service import AuthenticatedAgent, InvalidAgentToken


class RecordingAgentAuthService:
    def __init__(self, error=None):
        self.error = error
        self.tokens = []

    def authenticate(self, token):
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return AuthenticatedAgent("acct_1", "agent", "north")


class FiniteAgentRegistry:
    def __init__(self):
        self.registered = []

    def register(self, account_id):
        connection = object()
        self.registered.append((account_id, connection))
        return connection

    def stream(self, connection):
        assert connection is self.registered[-1][1]
        yield ": ping agent\n\n"


def make_client(auth_service=None, registry=None):
    auth_service = auth_service or RecordingAgentAuthService()
    registry = registry or FiniteAgentRegistry()
    app = FastAPI()
    install_error_handling(app)
    app.include_router(create_agent_events_router(auth_service, registry))
    return TestClient(app, raise_server_exceptions=False), auth_service, registry


def test_agent_events_returns_sse_headers_for_authenticated_agent():
    client, auth_service, registry = make_client()

    response = client.get(
        "/agent/v1/events",
        headers={"Authorization": "Bearer agent-token"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["connection"] == "keep-alive"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text == ": ping agent\n\n"
    assert auth_service.tokens == ["agent-token"]
    assert registry.registered[0][0] == "acct_1"


def test_agent_events_rejects_unknown_token_before_registration():
    registry = FiniteAgentRegistry()
    client, auth_service, _ = make_client(
        RecordingAgentAuthService(InvalidAgentToken()),
        registry,
    )

    response = client.get(
        "/agent/v1/events",
        headers={"Authorization": "Bearer unknown"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
    assert auth_service.tokens == ["unknown"]
    assert registry.registered == []
