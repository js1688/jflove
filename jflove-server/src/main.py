import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.models.database import init_db
from src.controllers import (
    auth_controller,
    user_controller,
    virtual_disk_controller,
    permission_controller,
    file_controller,
    note_controller,
    config_controller,
    sync_controller,
)
from src.services import auth_service
from src.utils.crypto import encrypt
from src.utils.logger import get_logger

# 不需要加密错误响应的明文路径（密钥交换前不可能加密；健康检查不含敏感信息）
_PLAIN_PATHS = (
    "/health",
    "/api/v1/auth/key-exchange",
    "/api/v1/auth/admin-exists",
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("数据库初始化完成")
    yield
    logger.info("服务关闭")


app = FastAPI(
    title="JFLove Server",
    version="1.4.1",
    lifespan=lifespan,
)

# CORS 中间件：允许浏览器端 Web 应用跨域请求
# 注：JWT 走加密 body 的 token 字段，不依赖 Cookie，因此 allow_credentials=False
# （allow_credentials=True 与 allow_origins=["*"] 在 CORS 规范中互相冲突，浏览器会拒绝）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_controller.router)
app.include_router(user_controller.router)
app.include_router(virtual_disk_controller.router)
app.include_router(permission_controller.router)
app.include_router(file_controller.router)
app.include_router(note_controller.router)
app.include_router(config_controller.router)
app.include_router(sync_controller.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _maybe_encrypt_error(request: Request, status_code: int, detail: str) -> JSONResponse:
    """
    若请求路径属于受保护接口（非明文白名单）且会话密钥可用，把错误体加密返回。

    用途：避免错误 detail（可能含用户输入或业务细节）被企业 MITM 代理直接看见。
    无可用 session key 时退回通用 detail，避免泄露细节。

    :param request: FastAPI Request 对象
    :param status_code: HTTP 状态码
    :param detail: 错误详情
    :returns: 加密后的 JSONResponse；明文白名单路径时返回原 detail
    """
    path = request.url.path
    if any(path.startswith(p) for p in _PLAIN_PATHS):
        return JSONResponse(status_code=status_code, content={"detail": detail})

    session_id = request.headers.get("X-Session-ID")
    if session_id:
        session_key = auth_service.get_session_key(session_id)
        if session_key:
            ciphertext = encrypt(
                session_key,
                json.dumps({"detail": detail}, ensure_ascii=False).encode(),
            )
            return JSONResponse(status_code=status_code, content=ciphertext)

    # 没有可用会话密钥（首次连接 / 会话已失效）→ 通用 detail
    return JSONResponse(status_code=status_code, content={"detail": "请求失败"})


@app.exception_handler(HTTPException)
async def encrypted_http_exception_handler(request: Request, exc: HTTPException):
    """业务代码 raise 的 fastapi.HTTPException 自动加密 detail"""
    return _maybe_encrypt_error(request, exc.status_code, str(exc.detail))


@app.exception_handler(StarletteHTTPException)
async def encrypted_starlette_http_exception_handler(
    request: Request, exc: StarletteHTTPException
):
    """
    Starlette 自身抛出的 HTTPException（404 路径不匹配 / 405 方法不允许 / 等等）
    也走加密通道，避免明文 `{"detail": "Not Found"}` 暴露在响应体里。

    fastapi.HTTPException 是 starlette.HTTPException 的子类，但 FastAPI 注册
    HTTPException handler 时只匹配 fastapi.HTTPException，不会捕获 starlette
    原生抛出的实例 —— 必须额外注册此处理器。
    """
    return _maybe_encrypt_error(request, exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
async def encrypted_validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    """
    Pydantic 请求体/路径参数校验失败（422）也走加密通道。

    detail 默认是结构化错误数组（含字段名），虽然字段名不是用户输入数据本身，
    但企业 MITM 仍可由此推断接口结构。统一加密更稳妥。
    """
    # exc.errors() 是 list[dict]，需要可 JSON 化
    errors = exc.errors()
    detail = "请求参数校验失败"
    # 加密通道下把详细错误数组也带上，仅明文路径回退到 detail 字符串
    path = request.url.path
    if any(path.startswith(p) for p in _PLAIN_PATHS):
        return JSONResponse(status_code=422, content={"detail": errors})
    session_id = request.headers.get("X-Session-ID")
    if session_id:
        session_key = auth_service.get_session_key(session_id)
        if session_key:
            ciphertext = encrypt(
                session_key,
                json.dumps({"detail": detail, "errors": errors},
                           ensure_ascii=False, default=str).encode(),
            )
            return JSONResponse(status_code=422, content=ciphertext)
    return JSONResponse(status_code=422, content={"detail": "请求参数校验失败"})


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """所有未捕获异常 → 加密的通用 500 响应（不泄露内部异常信息）"""
    logger.error("未处理的异常: %s %s -> %s", request.method, request.url, exc, exc_info=True)
    return _maybe_encrypt_error(request, 500, "服务器内部错误")
