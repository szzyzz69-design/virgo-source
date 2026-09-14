from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from app.api.device import parse_json_model
from app.errors import ApiError
from app.security import secure_equals
from app.services.sms_check_service import CheckConflict


class StartCheck(BaseModel):
    target_phone: str = Field(min_length=3,max_length=50)
    timeout_seconds: int = Field(default=300,ge=30,le=3600)


class CheckReceipt(BaseModel):
    sender: str = Field(min_length=3,max_length=50)
    text: str = Field(min_length=1,max_length=2000)


def create_sms_checks_router(token, service):
    def authenticate(authorization: str | None = Header(default=None)):
        scheme, _, supplied = (authorization or '').partition(' ')
        if scheme.lower() != 'bearer' or not supplied or not secure_equals(token,supplied):
            raise ApiError(401,'UNAUTHORIZED','Invalid business API token')

    router = APIRouter(prefix='/business/v1/sms-checks', tags=['sms-diagnostics'], dependencies=[Depends(authenticate)])

    @router.post('')
    async def start(request: Request, idempotency_key: str = Header(alias='Idempotency-Key')):
        command = await parse_json_model(request,StartCheck)
        try:
            run_id = service.start(command.target_phone,command.timeout_seconds,idempotency_key)
        except CheckConflict as error:
            raise ApiError(409,'CHECK_CONFLICT',str(error)) from error
        except ValueError as error:
            raise ApiError(400,'VALIDATION_ERROR',str(error)) from error
        return {'run_id':run_id,'results':service.results(run_id)}

    @router.get('')
    def latest():
        return {'results':service.results()}

    @router.get('/{run_id}')
    def results(run_id: str):
        return {'run_id':run_id,'results':service.results(run_id)}

    @router.post('/{run_id}/receipts')
    async def receipt(run_id: str, request: Request):
        command = await parse_json_model(request,CheckReceipt)
        try:
            check_id = service.record_receipt(run_id,command.sender,command.text)
        except ValueError as error:
            raise ApiError(400,'VALIDATION_ERROR',str(error)) from error
        return {'id':check_id,'results':service.results(run_id)}

    return router
