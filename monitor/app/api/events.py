from typing import AsyncIterator, Protocol

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.errors import ApiError
from app.services.device_auth_service import (
    AuthenticatedDevice,
    DeviceDisabled,
    InvalidDeviceToken,
)
from app.services.sse import SseConnection, SseConnectionRegistry


class EventsAuthenticationService(Protocol):
    def authenticate(self, token: str) -> AuthenticatedDevice: ...


class EventsRegistry(Protocol):
    def register(self, device_id: str) -> SseConnection: ...

    def stream(self, connection: SseConnection) -> AsyncIterator[str]: ...


def create_events_router(
    auth_service: EventsAuthenticationService,
    registry: EventsRegistry,
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

    router = APIRouter(prefix="/mobile/v1", tags=["mobile-events"])

    @router.get("/events")
    def events(
        device: AuthenticatedDevice = Depends(authenticate_device),
    ) -> StreamingResponse:
        connection = registry.register(device.id)
        return StreamingResponse(
            registry.stream(connection),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
