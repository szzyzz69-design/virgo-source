from dataclasses import dataclass
import json
from typing import Protocol, TypeVar

from fastapi import APIRouter, Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.errors import ApiError
from app.schemas.device import (
    DeviceRegisterRequest,
    DeviceRegisterResponse,
    DeviceUpdateRequest,
    DeviceUpdateResponse,
)
from app.security import secure_equals
from app.services.device_auth_service import (
    AuthenticatedDevice,
    DeviceDisabled,
    InvalidDeviceToken,
)
from app.services.device_service import DeviceOwnershipMismatch, DeviceStateConflict


ModelT = TypeVar("ModelT", bound=BaseModel)


class DeviceRegistrationService(Protocol):
    def register(self, request: DeviceRegisterRequest) -> DeviceRegisterResponse: ...

    def update(
        self,
        authenticated_device_id: str,
        request: DeviceUpdateRequest,
    ) -> DeviceUpdateResponse: ...


class DeviceAuthenticationService(Protocol):
    def authenticate(self, token: str) -> AuthenticatedDevice: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedUpdateRequest:
    device: AuthenticatedDevice
    body: DeviceUpdateRequest


async def parse_json_model(request: Request, model_type: type[ModelT]) -> ModelT:
    media_type = request.headers.get("content-type", "").partition(";")[0].strip()
    if media_type.lower() != "application/json":
        raise RequestValidationError(
            [
                {
                    "type": "json_type",
                    "loc": ("body",),
                    "msg": "Content-Type must be application/json",
                    "input": None,
                }
            ]
        )

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        position = getattr(error, "pos", getattr(error, "start", 0))
        raise RequestValidationError(
            [
                {
                    "type": "json_invalid",
                    "loc": ("body", position),
                    "msg": "JSON decode error",
                    "input": {},
                }
            ],
            body=getattr(error, "doc", None),
        ) from error

    try:
        return model_type.model_validate(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors(), body=body) from error


def create_device_router(
    private_registration_token: str,
    service: DeviceRegistrationService,
    auth_service: DeviceAuthenticationService,
) -> APIRouter:
    def authenticate_registration(
        authorization: str | None = Header(default=None),
    ) -> None:
        scheme, separator, supplied = (authorization or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not supplied
            or not secure_equals(private_registration_token, supplied)
        ):
            raise ApiError(401, "UNAUTHORIZED", "Invalid registration token")

    def authenticate_device(
        authorization: str | None = Header(default=None),
    ) -> AuthenticatedDevice:
        scheme, separator, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or separator != " " or not supplied:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token")
        try:
            return auth_service.authenticate(supplied)
        except InvalidDeviceToken as error:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token") from error
        except DeviceDisabled as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error

    async def authenticated_register_request(
        request: Request,
        _: None = Depends(authenticate_registration),
    ) -> DeviceRegisterRequest:
        return await parse_json_model(request, DeviceRegisterRequest)

    async def authenticated_update_request(
        request: Request,
        device: AuthenticatedDevice = Depends(authenticate_device),
    ) -> AuthenticatedUpdateRequest:
        body = await parse_json_model(request, DeviceUpdateRequest)
        return AuthenticatedUpdateRequest(device=device, body=body)

    router = APIRouter(prefix="/mobile/v1", tags=["mobile-device"])

    @router.post("/device", response_model=DeviceRegisterResponse, status_code=201)
    def register_device(
        body: DeviceRegisterRequest = Depends(authenticated_register_request),
    ) -> DeviceRegisterResponse:
        return service.register(body)

    @router.patch("/device", response_model=DeviceUpdateResponse, status_code=200)
    def update_device(
        command: AuthenticatedUpdateRequest = Depends(authenticated_update_request),
    ) -> DeviceUpdateResponse:
        try:
            return service.update(command.device.id, command.body)
        except DeviceOwnershipMismatch as error:
            raise ApiError(
                403,
                "FORBIDDEN",
                "Device does not match authenticated token",
            ) from error
        except DeviceDisabled as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error
        except InvalidDeviceToken as error:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token") from error
        except DeviceStateConflict as error:
            raise ApiError(
                409,
                "STATE_CONFLICT",
                "SIM state conflicts with device",
            ) from error

    return router
