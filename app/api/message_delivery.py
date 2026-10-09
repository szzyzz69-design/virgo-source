from fastapi import APIRouter, Depends, Header, Request

from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.message_delivery import DeliveryAckCommand
from app.services.device_auth_service import AuthenticatedDevice, DeviceDisabled, InvalidDeviceToken
from app.services.message_delivery_service import DeliveryError
from app.services.message_pull_service import PullDeviceUnavailable


def create_message_delivery_router(auth_service, service) -> APIRouter:
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

    def error_response(error: DeliveryError):
        return ApiError(
            error.status, error.code,
            "Message not found" if error.status == 404 else "Delivery claim is not acceptable",
            {"messageId": error.message_id},
        )

    router = APIRouter(prefix="/mobile/v1", tags=["mobile-message"])

    @router.post("/message/ack")
    async def acknowledge(request: Request, device: AuthenticatedDevice = Depends(authenticate)):
        command = await parse_json_model(request, DeliveryAckCommand)
        try:
            service.acknowledge(device.id, command.items)
        except PullDeviceUnavailable as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error
        except DeliveryError as error:
            raise error_response(error) from error
        if isinstance(command.root, list):
            return {"ok": True, "ids": [item.id for item in command.items]}
        return {"ok": True, "id": command.root.id}

    @router.get("/message/{message_id}/state")
    def state(message_id: str, device: AuthenticatedDevice = Depends(authenticate)):
        try:
            return service.state(device.id, message_id)
        except PullDeviceUnavailable as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error
        except DeliveryError as error:
            raise error_response(error) from error

    return router
