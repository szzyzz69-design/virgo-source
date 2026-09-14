from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request

from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.agent_auth import (
    AgentLoginRequest,
    AgentLoginResponse,
    AgentMeResponse,
)
from app.services.agent_auth_service import (
    AgentSession,
    AuthenticatedAgent,
    InvalidAgentCredentials,
    InvalidAgentToken,
)


class AgentAuthenticationService(Protocol):
    def login(self, username: str, password: str) -> AgentSession: ...

    def authenticate(self, token: str) -> AuthenticatedAgent: ...


def authenticate_agent_header(
    auth_service: AgentAuthenticationService,
    authorization: str | None,
) -> AuthenticatedAgent:
    scheme, separator, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or separator != " " or not supplied:
        raise ApiError(401, "UNAUTHORIZED", "Invalid agent token")
    try:
        return auth_service.authenticate(supplied)
    except InvalidAgentToken as error:
        raise ApiError(401, "UNAUTHORIZED", "Invalid agent token") from error


def create_agent_auth_router(
    auth_service: AgentAuthenticationService,
) -> APIRouter:
    async def login_request(request: Request) -> AgentLoginRequest:
        return await parse_json_model(request, AgentLoginRequest)

    def authenticate_agent(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedAgent:
        return authenticate_agent_header(auth_service, authorization)

    router = APIRouter(prefix="/agent/v1", tags=["agent-auth"])

    @router.post("/auth/login", response_model=AgentLoginResponse)
    def login(body: AgentLoginRequest = Depends(login_request)) -> AgentLoginResponse:
        try:
            session = auth_service.login(body.username, body.password)
        except InvalidAgentCredentials as error:
            raise ApiError(401, "UNAUTHORIZED", "Invalid agent credentials") from error
        return AgentLoginResponse(token=session.token, expiresAt=session.expires_at)

    @router.get("/me", response_model=AgentMeResponse)
    def me(agent: AuthenticatedAgent = Depends(authenticate_agent)) -> AgentMeResponse:
        return AgentMeResponse(id=agent.id, username=agent.username, areas=agent.areas)

    return router
