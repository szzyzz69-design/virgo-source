from typing import Protocol
from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.device import parse_json_model
from app.errors import ApiError
from app.schemas.inbound_message import InboundMessageRequest
from app.services.device_auth_service import AuthenticatedDevice, DeviceDisabled, InvalidDeviceToken
from app.services.inbound_message_service import InboundConflict, InboundDeviceUnavailable, InboundResult, InboundValidation


class InboundCreatingService(Protocol):
    def create(self, device_id:str, request:InboundMessageRequest)->InboundResult: ...


def create_inbox_router(auth_service, service:InboundCreatingService)->APIRouter:
    def authenticate(authorization:str|None=Header(default=None)):
        scheme,sep,token=(authorization or "").partition(" ")
        if scheme.lower()!="bearer" or sep!=" " or not token or token.strip()!=token: raise ApiError(401,"UNAUTHORIZED","Invalid device token")
        try:return auth_service.authenticate(token)
        except InvalidDeviceToken as error: raise ApiError(401,"UNAUTHORIZED","Invalid device token") from error
        except DeviceDisabled as error: raise ApiError(403,"FORBIDDEN","Device is disabled") from error
    async def body(request:Request,device:AuthenticatedDevice=Depends(authenticate)):
        return device,await parse_json_model(request,InboundMessageRequest)
    router=APIRouter(prefix="/mobile/v1",tags=["mobile-inbox"])
    @router.post("/inbox",status_code=201)
    def inbox(response:Response,command=Depends(body)):
        device,request=command
        try:result=service.create(device.id,request)
        except InboundConflict as error: raise ApiError(409,"IDEMPOTENCY_CONFLICT","Inbound id was used for different content") from error
        except InboundDeviceUnavailable as error: raise ApiError(403,"FORBIDDEN","Device is disabled") from error
        except InboundValidation as error: raise ApiError(400,"VALIDATION_ERROR","Inbound message is invalid") from error
        if not result.created: response.status_code=200
        return {"id":result.id,"created":result.created,"conversationId":result.conversation_id}
    return router
