from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import (
    analyses,
    auth,
    documents,
    evidence,
    glossary,
    health,
    hypotheses,
    jobs,
    lab_records,
    modeling,
    multimodal,
    science125,
    settings,
)
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.legacy import db
from app.services.jobs import job_service


def create_app() -> FastAPI:
    config = get_settings()
    config.uploads_dir.mkdir(parents=True, exist_ok=True)
    config.analysis_assets_dir.mkdir(parents=True, exist_ok=True)
    config.multimodal_assets_dir.mkdir(parents=True, exist_ok=True)
    config.modeling_assets_dir.mkdir(parents=True, exist_ok=True)
    job_service.configure_store(config.web_db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db().init_db()
        job_service.fail_interrupted_jobs()
        try:
            yield
        finally:
            job_service.interrupt_for_shutdown()

    app = FastAPI(
        title=config.app_name,
        lifespan=lifespan,
        docs_url=None if config.is_production else "/docs",
        redoc_url=None if config.is_production else "/redoc",
        openapi_url=None if config.is_production else "/openapi.json",
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(documents.router, prefix="/api")
    app.include_router(analyses.router, prefix="/api")
    app.include_router(glossary.router, prefix="/api")
    app.include_router(lab_records.router, prefix="/api")
    app.include_router(hypotheses.router, prefix="/api")
    app.include_router(evidence.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(multimodal.router, prefix="/api")
    app.include_router(modeling.router, prefix="/api")
    app.include_router(science125.router, prefix="/api")
    return app


app = create_app()
