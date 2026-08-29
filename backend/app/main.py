from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.core.database import initialize_database
from app.core.errors import (
    DomainError,
    domain_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.services.keepsake import recover_interrupted_keepsakes


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    recover_interrupted_keepsakes()
    yield


app = FastAPI(
    title="念念 · 第一阶段 API",
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "X-Request-ID"],
)

app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)
app.include_router(router)
