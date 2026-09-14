from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request

from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.message_status import MessageStatusBatch, MessageStatusUpdate
from app.services.device_auth_service import AuthenticatedDevice, DeviceDisabled, InvalidDeviceToken
from app.services.message_state_service import (
    MessageStateConflict,
    MessageStatusForbidden,
    MessageStatusNotFound,
    MessageStatusValidation,
)


class MessageStateUpdatingService(Protocol):
    def update(self, device_id: str, requests: list[MessageStatusUpdate]) -> None: ...


class StatusAuthenticationService(Protocol):
    def authenticate(self, token: str) -> AuthenticatedDevice: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedStatusRequest:
    device: AuthenticatedDevice
    batch: MessageStatusBatch


def create_message_status_router(auth_service: StatusAuthenticationService, service: MessageStateUpdatingService) -> APIRouter:
    def authenticate(authorization: str | None = Header(default=None)) -> AuthenticatedDevice:
        scheme, separator, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or separator != " " or not token or token.strip() != token:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token")
        try:
            return auth_service.authenticate(token)
        except InvalidDeviceToken as error:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token") from error
        except DeviceDisabled as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error

    async def authenticated_body(request: Request, device: AuthenticatedDevice = Depends(authenticate)) -> AuthenticatedStatusRequest:
        batch = await parse_json_model(request, MessageStatusBatch)
        return AuthenticatedStatusRequest(device, batch)

    def details(error):
        return {"index": error.index, "messageId": error.message_id}

    router = APIRouter(prefix="/mobile/v1", tags=["mobile-message"])

    @router.patch("/message")
    def update_status(command: AuthenticatedStatusRequest = Depends(authenticated_body)) -> dict[str, bool]:
        try:
            service.update(command.device.id, command.batch.root)
        except MessageStatusNotFound as error:
            raise ApiError(404, "NOT_FOUND", "Message not found", details(error)) from error
        except MessageStatusForbidden as error:
            raise ApiError(403, "FORBIDDEN", "Message is not available to device", details(error)) from error
        except MessageStateConflict as error:
            raise ApiError(409, "STATE_CONFLICT", "Message state conflicts with request", details(error)) from error
        except MessageStatusValidation as error:
            raise ApiError(400, "VALIDATION_ERROR", "Message status request is invalid", details(error)) from error
        return {"ok": True}

    return router
