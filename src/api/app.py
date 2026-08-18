"""FastAPI 应用入口，注册路由与统一异常映射。"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import chat, control, events

app = FastAPI(title="AI 陪伴 Agent", version="0.1.0")

app.include_router(chat.router, prefix="/api")
app.include_router(events.router, prefix="/api")
app.include_router(control.router, prefix="/api")

# 前端静态资源挂载到根路径，访问 / 自动返回 index.html
# html=True 使 / 映射到 static/index.html
app.mount("/", StaticFiles(directory="static", html=True), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """统一异常映射，避免裸异常冒泡到前端。"""
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})
