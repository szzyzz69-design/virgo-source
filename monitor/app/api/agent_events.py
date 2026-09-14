from typing import AsyncIterator, Protocol

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from app.api.agent_auth import AgentAuthenticationService, authenticate_agent_header
from app.services.agent_auth_service import AuthenticatedAgent
from app.services.agent_event_publisher import AgentEventConnection


class AgentEventsRegistry(Protocol):
    def register(self, account_id: str) -> AgentEventConnection: ...

    def stream(self, connection: AgentEventConnection) -> AsyncIterator[str]: ...


def create_agent_events_router(
    auth_service: AgentAuthenticationService,
    registry: AgentEventsRegistry,
) -> APIRouter:
    def authenticate_agent(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedAgent:
        return authenticate_agent_header(auth_service, authorization)

    router = APIRouter(prefix="/agent/v1", tags=["agent-events"])

    @router.get("/events")
    def events(
        request: Request,
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> StreamingResponse:
        connection = registry.register(agent.id)
        headers = {
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        }
        if request.scope.get("http_version") == "1.1":
            headers["Connection"] = "keep-alive"
        return StreamingResponse(
            registry.stream(connection),
            media_type="text/event-stream",
            headers=headers,
        )

    return router
