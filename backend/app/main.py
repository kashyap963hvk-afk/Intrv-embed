"""FastAPI application factory.

Run with: `uvicorn app.main:create_app --factory`. No work happens at import time, so tests can
build apps with explicit settings.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin import router as admin_router
from app.api import health
from app.auth import router as auth_router
from app.connectors import router as connectors_router
from app.core.config import Settings, get_settings
from app.core.email import EmailSender
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.core.readiness import build_checks
from app.core.resources import Resources
from app.cv import router as cv_router
from app.interviews import router as interviews_router
from app.knowledge import admin_router as knowledge_admin_router
from app.knowledge import router as knowledge_router
from app.llm import bridge_router
from app.llm import router as llm_router
from app.orgs.service import bootstrap_first_owner
from app.privacy import router as privacy_router
from app.questions import router as questions_router
from app.rolepacks import router as rolepacks_router

log = get_logger("app")

API_VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resources = Resources.create(settings)
        app.state.resources = resources
        app.state.readiness_checks = build_checks(resources, settings)
        log.info("app.startup", app_env=settings.app_env)
        if settings.owner_bootstrap_email:
            try:
                await bootstrap_first_owner(resources.session_factory, settings, app.state.mailer)
            except Exception:  # the API must still start (e.g. migrations not applied yet)
                log.exception("org.bootstrap_failed")
        try:
            yield
        finally:
            await resources.close()
            log.info("app.shutdown")

    app = FastAPI(
        title="Intrv-embed API",
        version=API_VERSION,
        lifespan=lifespan,
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_prod else "/openapi.json",
    )
    app.state.settings = settings
    app.state.mailer = EmailSender(settings)

    register_exception_handlers(app)
    # Starlette runs the last-added middleware outermost. Request context goes inside CORS so that every
    # response it produces, including the 500 it renders for unhandled errors, still gets CORS headers;
    # otherwise browsers report a server error as an opaque network failure.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Content-Disposition"],
        max_age=600,
    )

    app.include_router(health.router)
    app.include_router(auth_router.router)
    app.include_router(admin_router.router)
    app.include_router(llm_router.router)
    app.include_router(bridge_router.router)
    app.include_router(bridge_router.ws_router)
    app.include_router(knowledge_router.router)
    app.include_router(knowledge_admin_router.router)
    app.include_router(connectors_router.router)
    app.include_router(rolepacks_router.router)
    app.include_router(cv_router.router)
    app.include_router(questions_router.router)
    app.include_router(interviews_router.templates_router)
    app.include_router(interviews_router.router)
    app.include_router(interviews_router.join_router)
    app.include_router(interviews_router.ws_router)
    app.include_router(privacy_router.router)
    if settings.metrics_enabled:
        app.include_router(health.metrics_router)
    return app
