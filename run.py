"""统一入口（CLI + 服务启动）。"""

import os
import subprocess
from typing import List

import typer
import uvicorn
from src.config.settings import settings

app = typer.Typer()


@app.command()
def start(reload: bool = typer.Option(False, help="是否启用热重载（开发模式）")) -> None:
    """启动 HTTP 服务。"""
    uvicorn.run(
        "src.api.app:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
    )


def _find_listening_pids(port: int) -> List[int]:
    """查找监听指定端口的进程 PID 列表（Windows 下通过 netstat）。"""
    if os.name != "nt":
        return []
    try:
        output = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except OSError:
        return []

    pids: List[int] = []
    for line in output.splitlines():
        if f":{port}" not in line:
            continue
        if "LISTENING" not in line:
            continue
        parts = line.split()
        try:
            pids.append(int(parts[-1]))
        except (IndexError, ValueError):
            continue
    return list(dict.fromkeys(pids))  # 去重且保序


@app.command()
def stop() -> None:
    """停止占用服务端口的进程，清理残留实例。"""
    pids = _find_listening_pids(settings.port)
    if not pids:
        typer.echo(f"端口 {settings.port} 上没有正在运行的服务。")
        return

    for pid in pids:
        typer.echo(f"正在终止 PID {pid} ...")
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)

    remaining = _find_listening_pids(settings.port)
    if remaining:
        typer.echo(f"警告：以下进程未能终止：{remaining}")
    else:
        typer.echo(f"已停止端口 {settings.port} 上的服务。")


@app.command()
def mcp() -> None:
    """启动 MCP Server（预留）。"""
    typer.echo("MCP Server 模式（开发中）")


if __name__ == "__main__":
    app()
