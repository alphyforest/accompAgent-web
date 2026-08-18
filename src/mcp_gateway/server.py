"""MCP Server（预留），通过 FastMCP 暴露核心业务能力为 Tool。"""

from typing import Any, Dict, Optional


def build_mcp_server() -> Optional[Any]:
    """构建 FastMCP Server 实例（预留）。"""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:  # pragma: no cover - mcp 为可选依赖
        return None

    mcp = FastMCP("ai-agent")

    # mcp 为可选依赖且类型存根不完善，装饰器无类型信息，属库边界。
    @mcp.tool()  # type: ignore[untyped-decorator]
    def get_mood() -> Dict[str, Any]:
        """获取当前气氛值。"""
        return {"mood": 0, "label": "平静"}

    @mcp.tool()  # type: ignore[untyped-decorator]
    def get_character_info() -> Dict[str, Any]:
        """获取角色信息。"""
        return {"name": "小暖", "description": "温柔体贴的陪伴型角色"}

    return mcp
