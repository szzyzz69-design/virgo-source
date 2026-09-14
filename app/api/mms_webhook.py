from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request

from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.mms_webhook import MmsWebhookRequest
from app.services.inbound_message_service import (
    InboundConflict,
    InboundDeviceUnavailable,
    InboundValidation,
)
from app.services.mms_signature import InvalidMmsSignature, verify_mms_signature
from app.services.mms_webhook_service import (
    MmsPayloadTooLarge,
    MmsUnsupportedMediaType,
    MmsWebhookResult,
)


class MmsHandlingService(Protocol):
    def handle(self, request: MmsWebhookRequest) -> MmsWebhookResult: ...


def create_mms_webhook_router(
    service: MmsHandlingService,
    *,
    signing_key: str,
    timestamp_tolerance_seconds: int,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/webhooks/android-sms-gateway",
        tags=["mms-webhook"],
    )

    async def webhook_request(
        request: Request,
        x_timestamp: str | None = Header(default=None, alias="X-Timestamp"),
        x_signature: str | None = Header(default=None, alias="X-Signature"),
    ) -> MmsWebhookRequest:
        raw_body = await request.body()
        try:
            verify_mms_signature(
                signing_key,
                raw_body,
                x_timestamp,
                x_signature,
                tolerance_seconds=timestamp_tolerance_seconds,
            )
        except InvalidMmsSignature as error:
            raise ApiError(
                401,
                "UNAUTHORIZED",
                "Invalid MMS webhook signature",
            ) from error
        return await parse_json_model(request, MmsWebhookRequest)

    @router.post("/mms")
    def receive_mms(body: MmsWebhookRequest = Depends(webhook_request)):
        try:
            result = service.handle(body)
        except InboundConflict as error:
            raise ApiError(
                409,
                "IDEMPOTENCY_CONFLICT",
                "MMS webhook was used for different content",
            ) from error
        except InboundDeviceUnavailable as error:
            raise ApiError(
                403,
                "DEVICE_FORBIDDEN",
                "Device is unavailable",
            ) from error
        except InboundValidation as error:
            raise ApiError(
                400,
                "VALIDATION_ERROR",
                "MMS webhook is invalid",
            ) from error
        except MmsPayloadTooLarge as error:
            raise ApiError(
                413,
                "PAYLOAD_TOO_LARGE",
                "MMS attachment payload is too large",
            ) from error
        except MmsUnsupportedMediaType as error:
            raise ApiError(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "MMS attachment content type is unsupported",
            ) from error
        return {
            "ok": True,
            "messageId": result.message_id,
            "created": result.created,
        }

    return router
