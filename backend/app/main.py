from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.auth_routes import router as auth_router
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
    if path in {"/api/v1/health", "/api/v1/auth/register", "/api/v1/auth/login"}:
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
    if (
        settings.formal_auth_required
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and response.status_code < 400
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

app.include_router(auth_router)
app.include_router(router)
