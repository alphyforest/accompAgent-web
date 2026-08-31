"""FastAPI 应用入口，注册路由、lifespan 生命周期与统一异常映射。"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.dependencies import get_scheduler, get_tool_runtime
from src.api.routes import chat, control, events
from src.utils.paths import resource_path


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期：启动主动说话后台调度器，关闭时停止。

    工具子进程生命周期归 lifespan：MCP 来源为懒连接（首次对话同步时启动），
    关闭时统一优雅释放（规格 §6 / §7，与现有 scheduler 模式一致）。
    """
    scheduler = get_scheduler()
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await get_tool_runtime().close()


app = FastAPI(title="AI 陪伴 Agent", version="0.5.0", lifespan=lifespan)

app.include_router(chat.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(control.router, prefix="/api")

# 前端静态资源挂载到根路径，访问 / 自动返回 index.html
# html=True 使 / 映射到 static/index.html；路径经 resource_path 兼容打包形态
app.mount("/", StaticFiles(directory=str(resource_path("static")), html=True), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """统一异常映射，避免裸异常冒泡到前端。"""
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
