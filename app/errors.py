from dataclasses import dataclass
import re
import secrets

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)


@dataclass(slots=True)
class ApiError(Exception):
    status_code: int
    code: str
    message: str
    details: object | None = None


def request_id(request: Request) -> str:
    return request.state.request_id


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: object | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": request_id(request)},
        content={
            "code": code,
            "message": message,
            "requestId": request_id(request),
            "details": jsonable_encoder(details),
        },
    )


def install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request.state.request_id = (
            supplied
            if SAFE_REQUEST_ID.fullmatch(supplied)
            else f"req_{secrets.token_hex(12)}"
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, error: ApiError):
        return error_response(
            request,
            error.status_code,
            error.code,
            error.message,
            error.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ):
        details = [
            {
                "location": list(item["loc"]),
                "message": item["msg"],
                "type": item["type"],
            }
            for item in error.errors()
        ]
        return error_response(
            request,
            400,
            "VALIDATION_ERROR",
            "Request validation failed",
            details,
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, error: HTTPException):
        known_errors = {
            404: ("NOT_FOUND", "Resource not found"),
            405: ("METHOD_NOT_ALLOWED", "Method not allowed"),
        }
        code, message = known_errors.get(
            error.status_code,
            (
                "HTTP_ERROR",
                error.detail if isinstance(error.detail, str) else "HTTP error",
            ),
        )
        response = error_response(request, error.status_code, code, message)
        if error.headers:
            response.headers.update(error.headers)
        response.headers["X-Request-ID"] = request_id(request)
        return response

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, error: Exception):
        return error_response(
            request,
            500,
            "INTERNAL_ERROR",
            "An internal error occurred",
        )
