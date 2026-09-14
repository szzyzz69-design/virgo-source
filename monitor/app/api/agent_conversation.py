import re
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from app.api.device import parse_json_model
from app.api.agent_auth import AgentAuthenticationService, authenticate_agent_header
from app.errors import ApiError
from app.schemas.agent_conversation import (
    AgentConversationItem,
    AgentConversationSearchItem,
    AgentMessageItem,
    AgentReplyRequest,
)
from app.schemas.message import MessageCreateResponse, PHONE_SEPARATORS
from app.services.agent_auth_service import AuthenticatedAgent
from app.services.agent_conversation_service import (
    ConversationForbidden,
    ConversationNotFound,
)
from app.services.message_service import (
    IdempotencyConflict,
    MessageCreateResult,
    MessageStateConflict,
    MessageValidationError,
    NoAvailableDevice,
)


class AgentConversationQueryService(Protocol):
    def list_conversations(self, agent: AuthenticatedAgent) -> list[AgentConversationItem]: ...

    def search_conversations(
        self,
        agent: AuthenticatedAgent,
        phone_number: str,
    ) -> list[AgentConversationSearchItem]: ...

    def list_messages(
        self,
        conversation_id: str,
        agent: AuthenticatedAgent,
    ) -> list[AgentMessageItem]: ...

    def mark_read(self, conversation_id: str, agent: AuthenticatedAgent) -> None: ...

    def reply(
        self,
        conversation_id: str,
        agent: AuthenticatedAgent,
        request: AgentReplyRequest,
        idempotency_key: str,
    ) -> MessageCreateResult: ...


PHONE_SEARCH_PATTERN = re.compile(r"^\+?\d{1,20}$")


def normalize_phone_search_query(value: str) -> str:
    normalized = PHONE_SEPARATORS.sub("", value)
    if not PHONE_SEARCH_PATTERN.fullmatch(normalized):
        raise ValueError("phone number search query is invalid")
    return normalized


def create_agent_conversation_router(
    auth_service: AgentAuthenticationService,
    conversation_service: AgentConversationQueryService,
) -> APIRouter:
    def authenticate_agent(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedAgent:
        return authenticate_agent_header(auth_service, authorization)

    def validate_idempotency_key(
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
    ) -> str:
        normalized = (idempotency_key or "").strip()
        if not normalized or len(normalized) > 200:
            raise ApiError(400, "VALIDATION_ERROR", "Invalid Idempotency-Key")
        return normalized

    async def reply_request(request: Request) -> AgentReplyRequest:
        return await parse_json_model(request, AgentReplyRequest)

    def map_conversation_error(error: Exception) -> ApiError:
        if isinstance(error, ConversationForbidden):
            return ApiError(403, "FORBIDDEN", "Conversation is outside agent area")
        return ApiError(404, "NOT_FOUND", "Conversation not found")

    router = APIRouter(prefix="/agent/v1", tags=["agent-conversation"])

    @router.get(
        "/conversations",
        response_model=list[AgentConversationItem],
    )
    def list_conversations(
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> list[AgentConversationItem]:
        return conversation_service.list_conversations(agent)

    @router.get(
        "/conversation-search",
        response_model=list[AgentConversationSearchItem],
    )
    def search_conversations(
        phone_number: str = Query(alias="phoneNumber"),
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> list[AgentConversationSearchItem]:
        try:
            phone_number_query = normalize_phone_search_query(phone_number)
        except ValueError as error:
            raise ApiError(
                400,
                "VALIDATION_ERROR",
                "phoneNumber is invalid",
            ) from error
        return conversation_service.search_conversations(
            agent,
            phone_number_query,
        )

    @router.get(
        "/conversations/{conversation_id}/messages",
        response_model=list[AgentMessageItem],
    )
    def list_messages(
        conversation_id: str,
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> list[AgentMessageItem]:
        try:
            return conversation_service.list_messages(conversation_id, agent)
        except (ConversationForbidden, ConversationNotFound) as error:
            raise map_conversation_error(error) from error

    @router.patch("/conversations/{conversation_id}/read")
    def mark_read(
        conversation_id: str,
        agent: AuthenticatedAgent = Depends(authenticate_agent),
    ) -> dict[str, bool]:
        try:
            conversation_service.mark_read(conversation_id, agent)
        except (ConversationForbidden, ConversationNotFound) as error:
            raise map_conversation_error(error) from error
        return {"ok": True}

    @router.post(
        "/conversations/{conversation_id}/messages",
        response_model=MessageCreateResponse,
        status_code=201,
    )
    def reply(
        response: Response,
        conversation_id: str,
        body: AgentReplyRequest = Depends(reply_request),
        agent: AuthenticatedAgent = Depends(authenticate_agent),
        idempotency_key: str = Depends(validate_idempotency_key),
    ) -> MessageCreateResponse:
        scoped_key = (
            f"agent:{agent.id}:conversation:{conversation_id}:{idempotency_key}"
        )
        try:
            result = conversation_service.reply(
                conversation_id,
                agent,
                body,
                scoped_key,
            )
        except (ConversationForbidden, ConversationNotFound) as error:
            raise map_conversation_error(error) from error
        except IdempotencyConflict as error:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "Idempotency key was already used for a different request",
            ) from error
        except MessageStateConflict as error:
            raise ApiError(
                409,
                "STATE_CONFLICT",
                "Message state conflicts with the request",
            ) from error
        except NoAvailableDevice as error:
            raise ApiError(
                422,
                "NO_AVAILABLE_DEVICE",
                "No available device and SIM card",
            ) from error
        except MessageValidationError as error:
            raise ApiError(
                400,
                "VALIDATION_ERROR",
                "Message request is invalid",
            ) from error
        if result.replayed:
            response.status_code = 200
        return result.response

    return router
