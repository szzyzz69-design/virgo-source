from typing import Literal, Protocol

from fastapi import APIRouter, Depends, Header, Query

from app.errors import ApiError
from app.schemas.message_pull import MessagePullItem
from app.services.device_auth_service import (
    AuthenticatedDevice,
    DeviceDisabled,
    InvalidDeviceToken,
)
from app.services.message_pull_service import PullDeviceUnavailable


class PullAuthenticationService(Protocol):
    def authenticate(self, token: str) -> AuthenticatedDevice: ...


class MessagePullingService(Protocol):
    def pull(self, device_id: str, order: str) -> list[MessagePullItem]: ...


def create_message_pull_router(
    auth_service: PullAuthenticationService,
    pull_service: MessagePullingService,
) -> APIRouter:
    def authenticate_device(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedDevice:
        scheme, separator, supplied = (authorization or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not supplied
            or supplied.strip() != supplied
        ):
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token")
        try:
            return auth_service.authenticate(supplied)
        except InvalidDeviceToken as error:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token") from error
        except DeviceDisabled as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error

    router = APIRouter(prefix="/mobile/v1", tags=["mobile-message"])

    @router.get("/message", response_model=list[MessagePullItem])
    def pull_messages(
        device: AuthenticatedDevice = Depends(authenticate_device),
        order: Literal["fifo", "lifo"] = Query(default="fifo"),
    ) -> list[MessagePullItem]:
        try:
            return pull_service.pull(device.id, order)
        except PullDeviceUnavailable as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error

    return router
