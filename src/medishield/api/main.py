import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from medishield.api.routers import (
    audit_router,
    cases_router,
    policies_router,
    review_router,
)
from medishield.core.config import settings
from medishield.core.telemetry import instrument_fastapi, scrub_phi

# Configure standard logging to stream application logs to terminal stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
logging.getLogger("medishield").setLevel(logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ADR-008: Initialize SQLite Schema and seed data on startup if fallback is active
    from medishield.db.session import Base, _use_sqlite, sync_engine
    if _use_sqlite:
        import logging
        logger = logging.getLogger("medishield.startup")
        logger.info("Initializing SQLite persistence fallback and seeding data (ADR-008)...")
        # Ensure models are imported so Base metadata is populated
        from medishield.db import models  # noqa: F401
        
        # Create all tables
        Base.metadata.create_all(bind=sync_engine)
        
        # Run seeder
        from sqlalchemy.orm import Session

        from medishield.db.seeder import (
            seed_benchmark_cluster_cases,
            seed_benchmark_dataset,
            seed_demo_cases_if_empty,
        )
        with Session(sync_engine) as session:
            seed_benchmark_dataset(session)
            seed_demo_cases_if_empty(session)
            seed_benchmark_cluster_cases(session)
        logger.info("SQLite persistence fallback initialization complete.")
    yield


def create_app() -> FastAPI:
    """Creates and configures the production MediShield FastAPI application."""
    app = FastAPI(
        title="MediShield Multi-Agent Adjudication Platform",
        description="HIPAA-compliant document intake, AI multi-agent orchestration, and claims adjudication API.",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # 1. CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. HIPAA PHI-Scrubbing Global Error Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        sanitized_msg = scrub_phi(str(exc.detail))
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": sanitized_msg},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        sanitized_msg = scrub_phi(str(exc))
        return JSONResponse(
            status_code=500,
            content={
                "error_code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred during request processing.",
                "detail": sanitized_msg,
                "details": {"error": sanitized_msg},
            },
        )

    # 3. Mount Routers under /api/v1
    api_prefix = settings.API_V1_STR
    app.include_router(cases_router, prefix=api_prefix)
    app.include_router(review_router, prefix=api_prefix)
    app.include_router(policies_router, prefix=api_prefix)
    app.include_router(audit_router, prefix=api_prefix)

    # 4. Liveness & Readiness Probes
    @app.get("/health", tags=["system"])
    @app.get(f"{api_prefix}/health", tags=["system"])
    def health_check() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": "medishield-api",
            "environment": settings.ENVIRONMENT,
        }

    # 5. OpenTelemetry / Logfire Instrumentation
    instrument_fastapi(app)

    return app


app = create_app()

