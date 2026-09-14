from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request

from app.api.agent_auth import AgentAuthenticationService, authenticate_agent_header
from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.agent_contact import (
    AgentContactItem,
    AgentContactRemarkRequest,
    AgentMenuItem,
    AgentSimCardItem,
)
from app.services.agent_auth_service import AuthenticatedAgent
from app.services.agent_contact_service import ContactForbidden, ContactNotFound


class AgentContactQueryService(Protocol):
    def list_contacts(self, agent_area: str) -> list[AgentContactItem]: ...

    def update_remark(
        self,
        contact_id: str,
        agent_area: str,
        remark: str,
    ) -> None: ...

    def list_menus(self, agent_area: str) -> list[AgentMenuItem]: ...

    def list_sim_cards(self, account_id: str) -> list[AgentSimCardItem]: ...


def create_agent_contact_router(
    auth_service: AgentAuthenticationService,
    contact_service: AgentContactQueryService,
) -> APIRouter:
    def authenticate_agent(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedAgent:
        return authenticate_agent_header(auth_service, authorization)

    async def remark_request(request: Request) -> AgentContactRemarkRequest:
        return await parse_json_model(request, AgentContactRemarkRequest)

    def map_contact_error(error: Exception) -> ApiError:
        if isinstance(error, ContactForbidden):
            return ApiError(403, "FORBIDDEN", "Contact is outside agent area")
        return ApiError(404, "NOT_FOUND", "Contact not found")

    router = APIRouter(prefix="/agent/v1", tags=["agent-contact"])

    @router.get("/contacts", response_model=list[AgentContactItem])
    def list_contacts(
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> list[AgentContactItem]:
        return contact_service.list_contacts(agent.areas)

    @router.patch("/contacts/{contact_id}/remark")
    def update_remark(
        contact_id: str,
        body: AgentContactRemarkRequest = Depends(remark_request),
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> dict[str, bool]:
        try:
            contact_service.update_remark(contact_id, agent.areas, body.remark)
        except (ContactForbidden, ContactNotFound) as error:
            raise map_contact_error(error) from error
        return {"ok": True}

    @router.get("/menus", response_model=list[AgentMenuItem])
    def list_menus(
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> list[AgentMenuItem]:
        return contact_service.list_menus(agent.areas)

    @router.get("/sim-cards", response_model=list[AgentSimCardItem])
    def list_sim_cards(
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> list[AgentSimCardItem]:
        return contact_service.list_sim_cards(agent.id)

    return router
