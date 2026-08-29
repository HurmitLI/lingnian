from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


@dataclass
class DomainError(Exception):
    code: str
    message: str
    status_code: int = 400


def error_payload(code: str, message: str, request_id: str | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id or str(uuid4()),
        }
    }


async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid4())
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, request_id),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid4())
    return JSONResponse(
        status_code=422,
        content=error_payload("VALIDATION_ERROR", "提交的信息不完整或格式不正确。", request_id),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid4())
    return JSONResponse(
        status_code=500,
        content=error_payload("INTERNAL_ERROR", "处理时遇到问题，请稍后重试。", request_id),
    )

