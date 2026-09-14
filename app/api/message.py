from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.message import MessageCreateRequest, MessageCreateResponse
from app.security import secure_equals
from app.services.message_service import (
    IdempotencyConflict,
    MessageCreateResult,
    MessageStateConflict,
    MessageValidationError,
    NoAvailableDevice,
)


class MessageCreationService(Protocol):
    def create(
        self,
        request: MessageCreateRequest,
        idempotency_key: str,
    ) -> MessageCreateResult: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedMessageRequest:
    body: MessageCreateRequest
    idempotency_key: str


def create_message_router(
    business_api_token: str,
    service: MessageCreationService,
) -> APIRouter:
    def authenticate_business(
        authorization: str | None = Header(default=None),
    ) -> None:
        scheme, separator, supplied = (authorization or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not supplied
            or not secure_equals(business_api_token, supplied)
        ):
            raise ApiError(401, "UNAUTHORIZED", "Invalid business API token")

    def validate_idempotency_key(
        _: None = Depends(authenticate_business),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
    ) -> str:
        normalized = (idempotency_key or "").strip()
        if not normalized or len(normalized) > 200:
            raise ApiError(400, "VALIDATION_ERROR", "Invalid Idempotency-Key")
        return normalized

    async def authenticated_message_request(
        request: Request,
        idempotency_key: str = Depends(validate_idempotency_key),
    ) -> AuthenticatedMessageRequest:
        body = await parse_json_model(request, MessageCreateRequest)
        return AuthenticatedMessageRequest(body, idempotency_key)

    router = APIRouter(prefix="/business/v1", tags=["business-message"])

    @router.post(
        "/messages",
        response_model=MessageCreateResponse,
        status_code=201,
    )
    def create_message(
        response: Response,
        command: AuthenticatedMessageRequest = Depends(
            authenticated_message_request
        ),
    ) -> MessageCreateResponse:
        try:
            result = service.create(command.body, command.idempotency_key)
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
