"""Device-authenticated upload of fully downloaded MMS messages."""

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import ConfigDict, ValidationError, model_validator

from app.api.mms_webhook import MmsHandlingService
from app.errors import ApiError
from app.schemas.mms_webhook import MmsDownloadedPayload, MmsWebhookRequest
from app.services.device_auth_service import DeviceDisabled, InvalidDeviceToken
from app.services.inbound_message_service import InboundConflict, InboundDeviceUnavailable, InboundValidation
from app.services.mms_webhook_service import MmsPayloadTooLarge, MmsUnsupportedMediaType
from app.services.object_storage import ObjectStorageUnavailable


# Allows the existing 20 MiB decoded limit plus base64 and JSON overhead.
MAX_MMS_REQUEST_BYTES = 29 * 1024 * 1024


class DownloadedMmsUpload(MmsDownloadedPayload):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    @model_validator(mode="after")
    def require_complete_parts(self):
        ids = [part.part_id for part in self.attachments]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate partId")
        if any(not part.data for part in self.attachments):
            raise ValueError("all attachments must contain downloaded data")
        self.attachments.sort(key=lambda part: part.part_id)
        return self


def create_mms_inbox_router(auth_service, service: MmsHandlingService) -> APIRouter:
    router = APIRouter(prefix="/mobile/v1", tags=["mobile-inbox"])

    def authenticate(authorization: str | None = Header(default=None)):
        scheme, separator, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not separator or not token or token.strip() != token:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token")
        try:
            return auth_service.authenticate(token)
        except InvalidDeviceToken as error:
            raise ApiError(401, "UNAUTHORIZED", "Invalid device token") from error
        except DeviceDisabled as error:
            raise ApiError(403, "FORBIDDEN", "Device is disabled") from error

    async def command(request: Request, device=Depends(authenticate)):
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Expected application/json")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_MMS_REQUEST_BYTES:
                raise ApiError(413, "PAYLOAD_TOO_LARGE", "MMS request is too large")
            body.extend(chunk)
        try:
            payload = DownloadedMmsUpload.model_validate_json(body)
        except ValidationError as error:
            raise ApiError(400, "VALIDATION_ERROR", "Expected a complete downloaded MMS") from error
        return MmsWebhookRequest(
            deviceId=device.id,
            event="mms:downloaded",
            id=f"mobile:{payload.message_id}",
            webhookId="mobile-inbox-mms",
            payload=payload,
        )

    @router.post("/inbox/mms", status_code=201)
    def upload(response: Response, body=Depends(command)):
        try:
            result = service.handle(body)
        except InboundConflict as error:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "MMS id was used for different content") from error
        except InboundDeviceUnavailable as error:
            raise ApiError(403, "FORBIDDEN", "Device is unavailable") from error
        except InboundValidation as error:
            raise ApiError(400, "VALIDATION_ERROR", "MMS is invalid") from error
        except MmsPayloadTooLarge as error:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "MMS attachments are too large") from error
        except MmsUnsupportedMediaType as error:
            raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Unsupported attachment type") from error
        except ObjectStorageUnavailable as error:
            raise ApiError(503, "STORAGE_UNAVAILABLE", "MMS storage is temporarily unavailable") from error
        if not result.created:
            response.status_code = 200
        return {"id": result.id, "conversationId": result.conversation_id, "created": result.created}

    return router
