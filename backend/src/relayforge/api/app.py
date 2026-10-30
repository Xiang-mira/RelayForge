from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from relayforge.api.controlled import controlled_router
from relayforge.api.routes import router
from relayforge.config import Settings, get_settings
from relayforge.db import Database
from relayforge.journal.state import JournalError
from relayforge.runtime.executor import PermissionDenied


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    database = database or Database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        database.close()

    app = FastAPI(title="RelayForge", version="0.1.0", lifespan=lifespan)
    app.state.database = database
    app.state.settings = settings
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and origin not in {
                "http://127.0.0.1:5173",
                f"http://127.0.0.1:{settings.api_port}",
            }:
                return JSONResponse(status_code=403, content={"detail": "Untrusted browser origin"})
        return await call_next(request)

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "Database operation unavailable"})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default errors echo rejected request values, which may include keys.
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Request validation failed",
                "errors": [{"type": item["type"]} for item in exc.errors()],
            },
        )

    @app.exception_handler(JournalError)
    async def journal_error(request: Request, exc: JournalError):
        return JSONResponse(
            status_code=403 if isinstance(exc, PermissionDenied) else 409,
            content={"detail": "Runtime guard refused the operation", "kind": type(exc).__name__},
        )

    @app.exception_handler(ValueError)
    async def invalid_operation(request: Request, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": "Invalid controlled operation"})

    app.include_router(router(database, settings))
    app.include_router(controlled_router(database, settings))

    @app.get("/health/live")
    def live():
        return {"status": "alive", "provider": settings.model_provider}

    @app.get("/health")
    def health():
        ready = database.ready()
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ready" if ready else "unavailable",
                "database": "reachable" if ready else "unreachable",
                "provider": settings.model_provider,
            },
        )

    return app


app = create_app()
