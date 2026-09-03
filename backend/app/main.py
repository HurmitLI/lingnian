from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.auth_routes import router as auth_router
from app.api.generation_node_routes import router as generation_node_router
from app.core.config import get_settings
from app.core.database import SessionLocal, initialize_database
from app.core.errors import (
    DomainError,
    domain_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.services.keepsake import recover_interrupted_keepsakes
from app.services.auth import authenticate_session, current_auth
from app.services.database_snapshot import (
    DatabaseSnapshotError,
    persist_configured_database_snapshot,
    restore_configured_database_snapshot,
)


logger = logging.getLogger("uvicorn.error")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,80}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_formal_runtime()
    restore_configured_database_snapshot(settings)
    initialize_database()
    recover_interrupted_keepsakes()
    persist_configured_database_snapshot(settings)
    yield


app = FastAPI(
    title="聆年 · 家庭记忆 API",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Request-ID"],
)

app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)


@app.middleware("http")
async def formal_auth_middleware(request: Request, call_next):
    if not settings.formal_auth_required:
        return await call_next(request)
    path = request.url.path
    if path in {
        "/api/v1/health",
        "/api/v1/readiness",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
    } or path.startswith("/api/v1/generation-worker/"):
        return await call_next(request)
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return JSONResponse(
            status_code=401,
            content={"error": {"code": "AUTH_REQUIRED", "message": "请先登录。"}},
        )
    with SessionLocal() as db:
        context = authenticate_session(db, token)
    if context is None:
        response = JSONResponse(
            status_code=401,
            content={"error": {"code": "AUTH_EXPIRED", "message": "登录状态已经失效，请重新登录。"}},
        )
        response.delete_cookie(settings.auth_cookie_name, path="/")
        return response
    context_token = current_auth.set(context)
    try:
        return await call_next(request)
    finally:
        current_auth.reset(context_token)


@app.middleware("http")
async def formal_database_snapshot_middleware(request: Request, call_next):
    response = await call_next(request)
    high_frequency_worker_update = (
        request.url.path == "/api/v1/generation-worker/heartbeat"
        or (
            request.url.path.startswith("/api/v1/generation-worker/tasks/")
            and request.url.path.endswith("/progress")
        )
    )
    if (
        settings.formal_auth_required
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and response.status_code < 400
        and not high_frequency_worker_update
        and response.headers.get("X-Lingnian-No-Snapshot") != "1"
    ):
        try:
            persist_configured_database_snapshot(settings)
        except (DatabaseSnapshotError, OSError):
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "DATABASE_SNAPSHOT_FAILED",
                        "message": "本次修改未能安全写入云端，请稍后重试。",
                    }
                },
            )
    return response


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):
    candidate = request.headers.get("x-request-id", "")
    request_id = candidate if REQUEST_ID_PATTERN.fullmatch(candidate) else str(uuid4())
    request.state.request_id = request_id
    started_at = monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_complete %s",
        json.dumps(
            {
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((monotonic() - started_at) * 1000, 2),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    return response

app.include_router(auth_router)
app.include_router(router)
app.include_router(generation_node_router)
