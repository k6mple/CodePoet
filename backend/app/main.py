"""Application startup and the implemented core health endpoint."""

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.api.admin import router as admin_router
from app.observability import set_request_id
from app.api.audit import router as audit_router
from app.api.auth import router as auth_router
from app.api.creation import router as creation_router
from app.api.engine import router as engine_router
from app.api.knowledge import router as knowledge_router
from app.api.render import router as render_router
from app.api.reviews import router as reviews_router
from app.api.rules import router as rules_router
from app.api.sessions import router as sessions_router
from app.api.templates import router as templates_router
from app.api.works import router as works_router
from app.db import create_database, create_session_factory
from app.errors import ApiError
from app.schemas.health import Health
from app.settings import Settings

logger = logging.getLogger(__name__)

# 契约 2：普通 JSON 请求上限 256KiB，导入包 5MiB（由各接口自行校验）
MAX_REQUEST_BYTES = 256 * 1024


def _content_too_large(request: Request) -> bool:
    raw = request.headers.get("content-length")
    if not raw:
        return False
    try:
        return int(raw) > MAX_REQUEST_BYTES
    except ValueError:
        return False


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
    # 让依赖（如 Cookie Secure 标志）能读到配置
    application.state.settings = config

    def error_response(request: Request, status: int, code: str, message: str):
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "fieldErrors": [],
            "requestId": request.state.request_id, "details": None,
        }})

    @application.middleware("http")
    async def response_metadata(request: Request, call_next):
        request.state.request_id = f"req-{uuid4().hex}"
        set_request_id(request.state.request_id)
        if _content_too_large(request):
            response = error_response(
                request, 413, "REQUEST_TOO_LARGE", "请求体超过 256KiB 限制"
            )
            response.headers["X-Request-Id"] = request.state.request_id
            response.headers["Cache-Control"] = "no-store"
            return response
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

    @application.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        """业务错误：code 按契约表格，客户端只看到 message，看不到堆栈。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "fieldErrors": exc.field_errors,
                    "requestId": getattr(request.state, "request_id", None),
                    "details": exc.details,
                }
            },
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        """把 FastAPI 默认的 {"detail":[...]} 转成契约的 VALIDATION_ERROR。

        不转的话前端拿不到 fieldErrors，也没法按 code 统一处理。
        """
        field_errors = []
        for item in exc.errors():
            location = [str(part) for part in item.get("loc", ())][1:]
            field_errors.append(
                {
                    "path": ".".join(location) or "body",
                    "message": item.get("msg", "不合法"),
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不合法",
                    "fieldErrors": field_errors,
                    "requestId": getattr(request.state, "request_id", None),
                    "details": None,
                }
            },
        )

    @application.get("/api/v1/health", response_model=Health, tags=["系统"])
    def health(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return error_response(request, 503, "DATABASE_UNAVAILABLE", "数据库连接不可用")
        return Health()

    application.include_router(admin_router, prefix="/api/v1")
    application.include_router(audit_router, prefix="/api/v1")
    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(creation_router, prefix="/api/v1")
    application.include_router(engine_router, prefix="/api/v1")
    application.include_router(knowledge_router, prefix="/api/v1")
    application.include_router(templates_router, prefix="/api/v1")
    application.include_router(works_router, prefix="/api/v1")
    application.include_router(render_router, prefix="/api/v1")
    application.include_router(reviews_router, prefix="/api/v1")
    application.include_router(sessions_router, prefix="/api/v1")
    application.include_router(rules_router, prefix="/api/v1")

    return application


app = create_app()
