"""ToolLoop 运行时（规格 §5）：带 tools 的模型请求循环。

状态机：带 tools 请求模型 → 解析 tool_calls → 注册表执行 → role:"tool" 回填 →
重试（轮数 ≤ max_rounds，单轮超时 call_timeout，整体超时 overall_timeout）→
模型不再调用工具则返回最终文本。

降级契约：
- 注册表为空/全部 disabled → run 返回 None（调用方走现有普通对话）
- 任何 LLM/整体异常 → 返回 None（普通对话兜底，不崩服务）
- 单工具异常归一化为带 isError 标记的文本回填，模型可决定重试或告知用户
- 禁止无进展重试：同工具同参数不重复执行（幂等约束，ADR-006）
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.llm.client import LLMClient
from src.core.tools.registry import ToolRegistry
from src.core.tools.spec import ToolError
from src.utils.logger import logger

# 默认参数（规格 §5 / settings 可覆盖）
DEFAULT_MAX_TOOL_ROUNDS = 4
DEFAULT_CALL_TIMEOUT = 30.0
DEFAULT_OVERALL_TIMEOUT = 120.0


def _no_progress_error() -> Dict[str, Any]:
    """同工具同参数重复调用时的错误回填（幂等约束）。"""
    return {
        "isError": True,
        "code": "no_progress_retry",
        "user_message": "同工具同参数不允许重复执行，请检查已有结果或换一种参数/方式。",
    }


class ToolLoop:
    """带 tools 的模型请求循环（依赖 ToolRegistry 接口，不感知具体工具）。"""

    def __init__(
        self,
        llm_client: LLMClient,
        registry: ToolRegistry,
        max_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
        overall_timeout: float = DEFAULT_OVERALL_TIMEOUT,
    ) -> None:
        self.llm = llm_client
        self.registry = registry
        self.max_rounds = max_rounds
        self.call_timeout = call_timeout
        self.overall_timeout = overall_timeout

    async def run(self, base_messages: List[Dict[str, str]]) -> Optional[str]:
        """执行工具循环，返回模型最终文本。

        无可用工具或 LLM/整体异常时返回 None，调用方据此降级为普通对话。
        """
        if not self.registry.has_enabled_tools():
            return None

        messages: List[Dict[str, Any]] = [dict(m) for m in base_messages]
        seen: Set[Tuple[str, str]] = set()
        rounds = 0

        try:
            async with asyncio.timeout(self.overall_timeout):
                while True:
                    message = await self._request(messages)
                    tool_calls = message.get("tool_calls") or []
                    if not tool_calls:
                        return message.get("content") or None

                    messages.append(self._assistant_tool_call_message(message, tool_calls))
                    for call in tool_calls:
                        messages.append(await self._execute_one(call, seen))

                    rounds += 1
                    if rounds >= self.max_rounds:
                        # 达轮数上限：最后一轮不带 tools，强制模型给出最终文本
                        final = await self._request(messages, include_tools=False)
                        return final.get("content") or None
        except TimeoutError:
            logger.warning("ToolLoop 整体超时（{}s），降级普通对话", self.overall_timeout)
            return None
        except Exception as exc:  # noqa: BLE001 - 任何异常不得带崩对话
            logger.warning("ToolLoop 失败，降级普通对话 err={}", exc)
            return None

    async def _request(
        self,
        messages: List[Dict[str, Any]],
        include_tools: bool = True,
    ) -> Dict[str, Any]:
        """发起一次模型请求；include_tools=False 时不携带 tools（用于轮数上限收尾）。"""
        tools: Optional[List[Dict[str, Any]]] = None
        if include_tools:
            tools = [spec.to_openai_schema() for spec in self.registry.list() if not spec.disabled]
        return await self.llm.chat(messages, tools=tools, tool_choice="auto")

    def _assistant_tool_call_message(
        self,
        message: Dict[str, Any],
        tool_calls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """把模型返回的 assistant 消息（含 tool_calls）转成可回传的对话消息。"""
        return {
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": [
                {
                    "id": call.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": call.get("name") or "",
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    },
                }
                for call in tool_calls
            ],
        }

    async def _execute_one(
        self,
        call: Dict[str, Any],
        seen: Set[Tuple[str, str]],
    ) -> Dict[str, Any]:
        """执行单个工具调用并返回 role:"tool" 消息。

        幂等约束（ADR-006）：写工具同工具同参数（规范化 JSON）不重复执行，
        防止死循环/重复写入；只读工具（read_only=True）天然幂等，允许同参重调
        （如改后复查 list_events 验证更新结果），防绕仍由轮数≤4 + 整体超时兜底
        （2026-08-30 复查细化）。
        """
        name = call.get("name") or ""
        arguments = call.get("arguments") or {}
        spec = self.registry.get(name)
        allow_repeat = spec is not None and spec.read_only
        key = (name, json.dumps(arguments, sort_keys=True, ensure_ascii=False))
        if not allow_repeat and key in seen:
            payload = _no_progress_error()
            logger.warning("ToolLoop 拒绝无进展重试 name={}", name)
        else:
            seen.add(key)
            try:
                payload = await asyncio.wait_for(
                    self.registry.execute(name, arguments), timeout=self.call_timeout
                )
                logger.info("ToolLoop 工具调用 name={} args={}", name, arguments)
            except TimeoutError:
                payload = {"isError": True, "code": "tool_timeout", "user_message": "工具执行超时，请稍后重试"}
                logger.warning("ToolLoop 工具超时 name={}", name)
            except ToolError as exc:
                payload = {"isError": True, "code": exc.code, "user_message": exc.user_message}
                if exc.code == "tool_source_unavailable":
                    # 进程级故障：该来源工具标记不可用，后续调用快速失败（不反复重连）
                    self.registry.disable_names([name])
                logger.warning("ToolLoop 工具错误 name={} code={}", name, exc.code)
            except Exception as exc:  # noqa: BLE001 - 注册表已归一化，此处兜底
                logger.exception("ToolLoop 工具执行异常 name={} err={}", name, exc)
                payload = {"isError": True, "code": "tool_execution_error", "user_message": "工具执行失败，请稍后重试"}

        return {
            "role": "tool",
            "tool_call_id": call.get("id") or "",
            "name": name,
            "content": json.dumps(payload, ensure_ascii=False),
        }
