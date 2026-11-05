"""Application startup and the implemented core health endpoint."""

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.db import create_database, create_session_factory
from app.schemas.health import Health
from app.settings import Settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        engine = create_database(config)
        application.state.engine = engine
        application.state.sessions = create_session_factory(engine)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            yield
        finally:
            engine.dispose()

    application = FastAPI(
        title="代码诗 API", version="1.1.0", lifespan=lifespan,
        docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
    )

    def error_response(request: Request, status: int, code: str, message: str):
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "fieldErrors": [],
            "requestId": request.state.request_id, "details": None,
        }})

    @application.middleware("http")
    async def response_metadata(request: Request, call_next):
        request.state.request_id = f"req-{uuid4().hex}"
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled request error: %s", request.state.request_id)
            response = error_response(request, 500, "INTERNAL_ERROR", "服务暂时不可用")
        response.headers["X-Request-Id"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return error_response(request, exc.status_code, code, str(exc.detail))

    @application.get("/api/v1/health", response_model=Health, tags=["系统"])
    def health(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return error_response(request, 503, "DATABASE_UNAVAILABLE", "数据库连接不可用")
        return Health()

    return application


app = create_app()
