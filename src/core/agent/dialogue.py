"""对话引擎：流式生成、情绪标签解析、主动触发器、语料预热、语义化长期记忆。

第二阶段（doc/PHASE2_DEV_PLAN.md）扩展：
- 惰性总结：session_buffer / session_activity + 空闲超时或条数阈值触发，后台总结不阻塞热路径
- 结构化抽取：LLM 以 JSON 输出会话摘要与用户画像，失败降级保留 buffer
- 记忆注入：按 importance 取 top-k 拼入 system prompt，推测信息标注
- 三档清除：session / history / all

「五件事」改造（doc/04-专项设计/dialogue engine changing.txt）：
- 角色卡驱动：情绪标签/默认情绪/解析正则/多段分隔符/立绘映射/主动触发器均从角色卡读取，代码不硬编码
- 事件系统解耦：引擎不再直接驱动事件；主动说话由触发器（流水线内检查 + 后台调度器）承担
"""

import asyncio
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.core.agent.mood import MoodSystem
from src.core.agent.tool_loop import ToolLoop
from src.core.agent.triggers import InitiativeTriggerMatcher
from src.core.character.card import CharacterCard, InitiativeTrigger
from src.core.llm.client import LLMClient
from src.core.llm.prompt_builder import build_system_prompt
from src.core.memory.long_term import LongTermMemory
from src.core.memory.short_term import ShortTermMemory
from src.core.tools.registry import ToolRegistry
from src.core.tools.runtime import ToolRuntime
from src.core.tools.time_context import current_time_context
from src.utils.logger import logger

# 流式响应中情绪标记的前缀（前端据此切换立绘；非角色个性化，保持模块级）
EMOTION_MARK_PREFIX = "[[EMOTION:"

# 未接入角色卡时的回退默认值（正常流程始终由 dependencies 注入角色卡）
DEFAULT_EMOTIONS = {"happy", "sad", "idle", "surprised", "embarrassed", "greet", "sharing"}
DEFAULT_EMOTION = "idle"
DEFAULT_TAG_PATTERN = r"\[情绪[:：]?\s*([A-Za-z]+)\]"
DEFAULT_SEGMENT_SEPARATOR = "---"

# 抽取允许的画像分类
FACT_CATEGORIES = {"profile", "interest", "fact", "boundary", "need"}

# 即时抽取关键字预筛默认集（命中任一即触发函数调用抽取；可通过配置覆盖）
DEFAULT_INSTANT_KEYWORDS = [
    "我叫",
    "我是",
    "我的",
    "我喜欢",
    "我不喜欢",
    "我讨厌",
    "我住在",
    "我在",
    "我正在",
    "我最近",
    "我养",
    "我有",
    "我准备",
    "我在准备",
    "我打算",
    "我计划",
    "我有点",
    "我想",
    "我想要",
    "我工作",
    "我学",
    "我家",
    "可以叫我",
    "请别",
    "不要这样",
]

# 惰性总结的抽取提示词：要求 JSON 结构化输出（rules.md §14.2）
MEMORY_EXTRACTION_SYSTEM = """你是对话记忆抽取器。根据对话抽取结构化记忆，只输出一个 JSON 对象，不要输出任何其他文字。
JSON 结构：
{
  "topics": ["本次会话的主要话题"],
  "open_plans": ["跨会话需要跟进的约定/计划"],
  "user_emotional_state": "用户整体情绪（无则 null）",
  "key_facts": [
    {"category": "profile|interest|fact|boundary|need", "key": "字段名", "value": "值", "importance": 5}
  ]
}
规则：
- 只抽取用户明确表达的信息，不确定的不要写，禁止编造。
- key_facts 的 category：profile（身份/性格）、interest（喜好）、
  fact（生活事实）、boundary（禁忌/雷区）、need（情感需求）。
- importance 取 1~10，越重要越高。
- 没有的内容用空数组或 null。"""

# 即时抽取（方案 B：function calling 实时写入）的指令提示词
INSTANT_EXTRACTION_SYSTEM = (
    "你是用户信息抽取器。用户的对话中可能包含其个人信息。"
    "找到用户明确表达的身份、喜好、生活事实、边界/雷区、情感需求时，"
    "调用 save_user_memory 工具保存成用户画像/事实，供长期记忆跨会话复用。"
    "规则：只保存用户明确表达的内容，禁止编造或过度推断；"
    "角色自发起的话题（[主动]）不当作用户事实来源；没有可保存的信息就不要调用工具。"
)


class DialogueEngine:
    """对话引擎，协调 LLM、短期记忆、长期记忆、气氛值与主动触发器。"""

    def __init__(
        self,
        llm_client: LLMClient,
        memory: ShortTermMemory,
        mood: MoodSystem,
        card: Optional[CharacterCard] = None,
        matcher: Optional[InitiativeTriggerMatcher] = None,
        system_prompt: str = "",
        corpus: str = "",
        long_term: Optional[LongTermMemory] = None,
        user_id: str = "default",
        idle_timeout: float = 3600.0,
        segment_max: int = 30,
        inject_top_k: int = 8,
        forget_days: int = 30,
        forget_decay: int = 2,
        instant_enabled: bool = True,
        instant_keywords: Optional[List[str]] = None,
        tool_registry: Optional[ToolRegistry] = None,
        tool_runtime: Optional[ToolRuntime] = None,
        tool_rounds: int = 4,
        tool_call_timeout: float = 30.0,
        tool_overall_timeout: float = 120.0,
    ):
        self.llm = llm_client
        self.memory = memory
        self.mood = mood
        self.card = card
        # 角色卡初始状态：初始情绪（init_state.emotion）供前端展示/角色切换复位（蓝图 §3.1）
        self.init_emotion = (card.init_state.emotion if card is not None else None) or DEFAULT_EMOTION
        # 情绪解析配置：未提供角色卡时回退到默认值，保证引擎可用
        protocol = card.output_protocol if card is not None else None
        self._emotion_pattern = re.compile((protocol.tag_pattern if protocol else None) or DEFAULT_TAG_PATTERN)
        self._valid_emotions = set(protocol.emotions if protocol and protocol.emotions else DEFAULT_EMOTIONS)
        self._default_emotion = (protocol.default_emotion if protocol else None) or DEFAULT_EMOTION
        self._segment_separator = (protocol.segment_separator if protocol else None) or DEFAULT_SEGMENT_SEPARATOR

        self.matcher = matcher
        # 未显式注入匹配器时，从角色卡 initiative_triggers 自动组装
        if self.matcher is None and card is not None and card.initiative_triggers:
            self.matcher = InitiativeTriggerMatcher(card.initiative_triggers)

        self.system_prompt = system_prompt
        self.corpus = corpus
        # 第二阶段：语义化长期记忆
        self.long_term = long_term
        self.user_id = user_id
        self.idle_timeout = idle_timeout
        self.segment_max = segment_max
        self.inject_top_k = inject_top_k
        self.forget_days = forget_days
        self.forget_decay = forget_decay
        # 方案 B：关键字触发的即时抽取
        self.instant_enabled = instant_enabled
        self._instant_keywords = list(instant_keywords or DEFAULT_INSTANT_KEYWORDS)
        self._preheated = False
        self._session_buffer: Dict[str, List[Dict[str, str]]] = {}
        self._session_activity: Dict[str, float] = {}
        self._summary_tasks: List[asyncio.Task[None]] = []
        # 单进程多协程仅访问 dict，短操作原子；总结/落库关键段加锁
        self._memory_lock = asyncio.Lock()
        self._last_forget_run = 0.0
        self._instant_tasks: List[asyncio.Task[None]] = []

        # 第三阶段：工具引擎（ToolLoop 运行时，规格 §5）
        # 注册表为空/全部 disabled 时 _tool_loop 仍可构造，但 run() 返回 None 走普通对话
        self.tool_registry = tool_registry
        self._tool_runtime = tool_runtime
        self._tool_loop: Optional[ToolLoop] = None
        if tool_registry is not None:
            self._tool_loop = ToolLoop(
                llm_client=llm_client,
                registry=tool_registry,
                max_rounds=tool_rounds,
                call_timeout=tool_call_timeout,
                overall_timeout=tool_overall_timeout,
            )

    async def chat_stream(self, user_input: str, session_id: str = "default") -> AsyncGenerator[str, None]:
        """处理一轮对话，流式产出回复文本。

        流式输出结构：
        1. 首段为情绪标记 ``[[EMOTION:xxx]]``（前端据此切换立绘）
        2. 后续为剥离情绪标签后的正文（可能由 ``---`` 分隔多段）

        事件已与引擎解耦，主动说话由触发器承担：气氛值更新后先做流水线内
        触发器检查，命中则以引导式回复取代普通回复（第一步，[[EMOTION: 前缀标记）。
        """
        # 0. 惰性总结检查：空闲超时/条数达标则后台总结上一段（不阻塞本次响应）
        await self._check_summarize(session_id)

        # 1. 更新气氛值
        self.mood.update(user_input)

        # 2. 流水线内主动触发器检查（改动四·第一步）：仅"可接管回复"类触发器才取代普通回复
        trigger = self._match_initiative_trigger(session_id)
        await self.memory_add(session_id, "user", user_input)
        # 2.5 关键字触发的即时抽取（方案 B）：命中则后台异步落库，不阻塞本次回复
        self._maybe_instant_extract(session_id, user_input)
        if trigger is not None and trigger.interrupt_reply:
            async for chunk in self._generate_with_initiative(trigger, session_id):
                yield chunk
            return

        # 3. 预热
        if not self._preheated:
            await self._preheat()

        # 4. 普通对话流式生成
        async for chunk in self._generate_reply(user_input, session_id):
            yield chunk

    # ---------------------------------------------------------------- 主动触发器

    def _match_initiative_trigger(self, session_id: str = "default") -> Optional[InitiativeTrigger]:
        """流水线内主动触发器检查（改动四·第一步）：气氛/静默/消息数满足且未冷却则命中。"""
        if self.matcher is None or self.matcher.empty:
            return None
        return self.matcher.check(self.trigger_context(session_id))

    def trigger_context(self, session_id: str = "default") -> Dict[str, Any]:
        """构建触发器判定上下文（流水线与后台调度器共用，保证两路径语义一致，rules.md §15.2）。

        返回 mood / silence_seconds / message_count 三个白名单变量（缺失默认 0）。
        """
        silence = max(0.0, time.time() - self.last_activity(session_id))
        return {
            "mood": self.mood.mood,
            "silence_seconds": silence,
            "message_count": len(self.memory.get_history(session_id)),
        }

    async def generate_initiative(self, trigger: InitiativeTrigger, session_id: str = "default") -> str:
        """按触发器主动开口（后台调度器用），返回带情绪标记的完整发言文本。"""
        emotion, body = await self._run_trigger(trigger, session_id)
        return f"{EMOTION_MARK_PREFIX}{emotion}]]{body}"

    def last_activity(self, session_id: str) -> float:
        """返回会话最近活跃时间戳（调度器据此判定静默时长）。

        无活动记录的会话视为"刚活跃"（返回当前时间）：避免从未交互/冷启动
        会话被当作"长期静默"，从而在用户发第一句话前误触发主动发言。
        """
        return self._session_activity.get(session_id, time.time())

    async def _generate_with_initiative(self, trigger: InitiativeTrigger, session_id: str) -> AsyncGenerator[str, None]:
        """用触发器的提示词/情绪生成引导式回复，流式输出。"""
        emotion, body = await self._run_trigger(trigger, session_id)
        yield f"{EMOTION_MARK_PREFIX}{emotion}]]"
        yield body

    async def _run_trigger(self, trigger: InitiativeTrigger, session_id: str) -> tuple[str, str]:
        """生成并落库一次主动发言：(情绪标签, 正文)；情绪优先用触发器指定值。"""
        messages = await self._build_trigger_messages(trigger, session_id)
        full_response = ""
        async for chunk in self.llm.stream(messages):
            full_response += chunk
        if trigger.emotion:
            emotion = trigger.emotion
            body = self._strip_all_emotions(full_response)
        else:
            emotion, body = self._parse_response(full_response)
        await self.memory_add(session_id, "assistant", body, source="initiative")
        return emotion, body

    async def _build_trigger_messages(self, trigger: InitiativeTrigger, session_id: str) -> List[Dict[str, str]]:
        """构建主动发言的消息：system + 触发器指令 + 历史。"""
        system = await self._build_system_prompt(session_id)
        system += f"\n\n[系统主动指令] {trigger.prompt}"
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        history = self.memory.get_history(session_id)
        messages.extend(history[-self.memory.max_history :])
        return messages

    # ---------------------------------------------------------------- 普通对话

    async def _generate_reply(self, user_input: str, session_id: str) -> AsyncGenerator[str, None]:
        """生成普通回复，解析情绪标签并流式输出。

        工具引擎可用时先走 ToolLoop（带 tools 的模型请求循环，规格 §5）：
        - 模型请求工具 → 注册表执行 → role:"tool" 结果回填 → 重试，直至输出最终文本
        - 无可用工具 / 循环降级返回 None → 回退到原有流式路径（行为与现有一致）
        情绪解析始终在最终文本之后执行，[[EMOTION:xx]] 协议与伪流式结构不变。
        """
        await self._sync_tools()
        messages = await self._build_messages(user_input or "", session_id)
        final = await self._try_tool_loop(messages)
        if final is not None:
            emotion, body = self._parse_response(final)
            if body.strip():
                yield f"{EMOTION_MARK_PREFIX}{emotion}]]"
                yield body
                await self.memory_add(session_id, "assistant", body)
                return

        full_response = ""
        async for chunk in self.llm.stream(messages):
            full_response += chunk

        emotion, body = self._parse_response(full_response)
        yield f"{EMOTION_MARK_PREFIX}{emotion}]]"
        yield body

        await self.memory_add(session_id, "assistant", body)

    async def _sync_tools(self) -> None:
        """首次对话前同步工具来源（懒连接；失败仅禁用来源，不阻塞对话）。"""
        if self._tool_runtime is not None:
            try:
                await self._tool_runtime.sync()
            except Exception as exc:  # noqa: BLE001 - 来源同步异常不得带崩对话
                logger.warning("工具来源同步异常，继续普通对话 err={}", exc)

    async def _try_tool_loop(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """工具循环入口：返回最终文本；无工具/异常降级返回 None（走普通对话）。"""
        if self._tool_loop is None:
            return None
        try:
            return await self._tool_loop.run(messages)
        except Exception as exc:  # noqa: BLE001 - 工具循环异常不得带崩对话，降级普通对话
            logger.warning("工具循环异常，降级普通对话 err={}", exc)
            return None

    def _parse_response(self, text: str) -> tuple[str, str]:
        """解析完整回复，返回 (情绪标签, 剥离标签后的正文)。

        支持多段发言（``标识符`` 分隔）：逐段剥离各自的情绪标签，
        最终情绪取最后一段的标签（最后一段是用户最后看到的内容）。
        """
        segments = text.split(self._segment_separator)
        last_emotion: Optional[str] = None
        cleaned: List[str] = []

        for segment in segments:
            emotion, body = self._parse_segment(segment)
            if emotion is not None:
                last_emotion = emotion
            cleaned.append(body)

        # 多段用换行重新拼接，保证前端可读
        body = "\n".join(seg.strip() for seg in cleaned if seg.strip())
        return (last_emotion or self._default_emotion), body

    def _parse_segment(self, segment: str) -> tuple[Optional[str], str]:
        """解析单段发言，返回 (情绪标签或 None, 剥离标签后的正文)。

        合法标签集合由角色卡 ``output_protocol.emotions`` 定义（改动二：配置驱动）。
        """
        match = self._emotion_pattern.search(segment)
        if match:
            emotion = match.group(1).lower()
            if emotion in self._valid_emotions:
                body = self._emotion_pattern.sub("", segment).strip()
                return emotion, body
        return None, segment.strip()

    def _extract_emotion(self, text: str) -> Optional[str]:
        """从回复中提取情绪标签（取最后一段的标签，无标签返回 None）。"""
        segments = text.split(self._segment_separator)
        for segment in reversed(segments):
            emotion, _ = self._parse_segment(segment)
            if emotion is not None:
                return emotion
        return None

    def _strip_emotion(self, text: str) -> str:
        """移除回复中的所有情绪标签，返回干净正文。"""
        _, body = self._parse_response(text)
        return body

    def _strip_all_emotions(self, text: str) -> str:
        """移除所有情绪标签（用于触发器指定情绪时保留正文原样）。"""
        return self._emotion_pattern.sub("", text).strip()

    # ---------------------------------------------------------------- 长期记忆：惰性总结

    async def _check_summarize(self, session_id: str) -> None:
        """惰性总结检查：空闲超时或条数达标则后台总结上一段，不阻塞当前请求。"""
        if self.long_term is None:
            return
        self._summary_tasks = [t for t in self._summary_tasks if not t.done()]
        snapshot: Optional[List[Dict[str, str]]] = None
        async with self._memory_lock:
            if self._should_summarize(session_id):
                snapshot = list(self._session_buffer[session_id])
                del self._session_buffer[session_id]
                self._session_activity.pop(session_id, None)
        if snapshot is not None:
            task = asyncio.create_task(self._run_summarize(session_id, snapshot))
            self._summary_tasks.append(task)

    def _should_summarize(self, session_id: str) -> bool:
        """判定是否需要总结当前 buffer（空闲超时或条数达阈值）。"""
        messages = self._session_buffer.get(session_id, [])
        if not messages:
            return False
        now = time.time()
        last_active = self._session_activity.get(session_id, now)
        if now - last_active >= self.idle_timeout:
            return True
        return len(messages) >= self.segment_max

    async def _run_summarize(self, session_id: str, snapshot: List[Dict[str, str]]) -> None:
        """对一段会话做 LLM 总结与画像抽取并落库；失败保留 buffer 下次重试（rules.md §14.2）。"""
        try:
            data = await self._extract_memory(snapshot, session_id)
            if not data:
                raise RuntimeError("结构化抽取返回为空")
            await self._persist_memory(data, session_id)
        except Exception as exc:
            logger.warning("记忆抽取失败 session={} err={}，buffer 保留下次重试", session_id, exc)
            async with self._memory_lock:
                self._session_buffer[session_id] = snapshot + self._session_buffer.get(session_id, [])
                self._session_activity[session_id] = time.time()

    async def _extract_memory(self, snapshot: List[Dict[str, str]], session_id: str) -> Optional[Dict[str, Any]]:
        """调用 LLM 结构化抽取（JSON），返回解析后的字典。

        主动发言（source=initiative）在转写中标注 [主动]，提示抽取器其不含
        用户主动表达的信息，不作为用户画像事实来源（rules.md §15.5）。
        """
        lines = []
        for message in snapshot:
            prefix = "[主动] " if message.get("source") == "initiative" else ""
            lines.append(f"{message['role']}: {prefix}{message['content']}")
        transcript = "\n".join(lines)
        messages = [
            {"role": "system", "content": MEMORY_EXTRACTION_SYSTEM},
            {
                "role": "user",
                "content": (
                    "请总结并抽取以下对话（关注用户信息，角色发言仅作参考；"
                    "标注 [主动] 的角色发言为自发起话题，不包含用户主动表达的信息，不作为用户画像事实来源）："
                    f"\n\n{transcript}"
                ),
            },
        ]
        result = await self.llm.extract_json(messages)
        return result

    async def _persist_memory(self, data: Dict[str, Any], session_id: str) -> None:
        """将抽取结果写入会话摘要与用户记忆（含关系/里程碑/事实）。

        落库整体持锁串行：避免多段并发触发时 SQLite 写竞争。
        """
        if self.long_term is None:
            return
        async with self._memory_lock:
            topics = _as_str_list(data.get("topics"))
            open_plans = _as_str_list(data.get("open_plans"))
            emotional_state = _as_optional_str(data.get("user_emotional_state"))

            await self.long_term.save_summary(session_id, topics, open_plans, emotional_state)

            facts = data.get("key_facts")
            if isinstance(facts, list):
                for item in facts:
                    if not isinstance(item, dict):
                        continue
                    category = _as_str(item.get("category"))
                    if category not in FACT_CATEGORIES:
                        category = "fact"
                    key = _as_str(item.get("key"))
                    value = _as_str(item.get("value"))
                    importance = _as_int(item.get("importance"), default=5)
                    if key and value:
                        await self.long_term.save_memory(
                            self.user_id, category, key, value, importance=importance, source_session=session_id
                        )

            await self._maybe_forget()

    async def _maybe_forget(self) -> None:
        """遗忘维护（引用降权/删除），每小时至多执行一次。"""
        if self.long_term is None:
            return
        now = time.time()
        if now - self._last_forget_run < 3600:
            return
        self._last_forget_run = now
        result = await self.long_term.apply_forgetting(self.user_id, self.forget_days, self.forget_decay)
        if result["decayed"] or result["removed"]:
            logger.info("遗忘策略执行 user={} result={}", self.user_id, result)

    # ---------------------------------------------------------------- 长期记忆：即时抽取（方案 B）

    def _maybe_instant_extract(self, session_id: str, user_input: str) -> None:
        """关键字触发的即时抽取（后台异步，不阻塞回复）。

        用户消息命中用户信息关键字时，快照当前会话上下文，异步调 LLM 函数调用
        抽取画像/事实并即时入库；未命中/未启用/已达并发上限则直接跳过。
        """
        if not self.instant_enabled or self.long_term is None:
            return
        if not keyword_matches(user_input, self._instant_keywords):
            return
        self._instant_tasks = [t for t in self._instant_tasks if not t.done()]
        if len(self._instant_tasks) >= 5:
            # 突发消息并发上限，防止函数调用放大
            return
        task = asyncio.create_task(self._run_instant_extract(session_id))
        self._instant_tasks.append(task)

    async def _run_instant_extract(self, session_id: str) -> None:
        """对会话最近上下文做函数调用抽取，将用户画像/事实写入长期记忆。"""
        if self.long_term is None:
            return
        try:
            history = self.memory.get_history(session_id)
            recent = history[-self.memory.max_history :]
            transcript = "\n".join(f"{m['role']}: {m['content']}" for m in recent)
            messages = [
                {"role": "system", "content": INSTANT_EXTRACTION_SYSTEM},
                {
                    "role": "user",
                    "content": "请根据以下对话抽取用户信息"
                    "（仅用户明确表达的内容，不确定的不要保存）：\n\n" + transcript,
                },
            ]
            facts = await self.llm.extract_user_facts(messages)
            if not facts:
                return
            # 与惰性总结共用锁落库串行，避免并发 SQLite 写竞争
            async with self._memory_lock:
                for item in facts:
                    category = _as_str(item.get("category"))
                    if category not in FACT_CATEGORIES:
                        category = "fact"
                    key = _as_str(item.get("key"))
                    value = _as_str(item.get("value"))
                    importance = _as_int(item.get("importance"), default=5)
                    if key and value:
                        await self.long_term.save_memory(
                            self.user_id, category, key, value, importance=importance, source_session=session_id
                        )
        except Exception as exc:
            logger.warning("即时抽取失败 session={} err={}", session_id, exc)

    # ---------------------------------------------------------------- 长期记忆：注入

    async def _build_memory_context(self, session_id: str) -> str:
        """加载记忆上下文（本会话摘要 + top-k 用户记忆），引用时刷新 updated_at。"""
        if self.long_term is None:
            return ""
        await self._maybe_forget()
        lines: List[str] = []

        summary = await self.long_term.get_summary(session_id)
        if summary is not None:
            if summary.topics:
                lines.append(f"· 之前聊过：{'、'.join(summary.topics)}")
            if summary.open_plans:
                lines.append(f"· 我们的约定/计划：{'、'.join(summary.open_plans)}")
            if summary.emotional_state:
                lines.append(f"· 用户上次的情绪：{summary.emotional_state}（可能已变化）")

        records = await self.long_term.get_top_memory(self.user_id, self.inject_top_k)
        if records:
            for record in records:
                tag = "已确认" if record.confirmed else "推测"
                lines.append(
                    f"· {record.value}（{record.category}/{record.key}，{tag}，importance={record.importance}）"
                )
            await self.long_term.touch_memory([record.id for record in records])

        if not lines:
            return ""
        return "[关于用户你已知的信息]\n" + "\n".join(lines)

    # ---------------------------------------------------------------- 长期记忆：三档清除

    async def reset_memory(self, level: str, session_id: str = "default") -> None:
        """三档清除（rules.md §14.4）。

        - ``session``：短期 + buffer + 本会话摘要
        - ``history``：全部短期 + 全部摘要；保留用户画像/关系记忆
        - ``all``：全部清除（忘记我）
        """
        if self.long_term is None:
            self.memory.clear(session_id)
            return
        if level in ("session", "history", "all"):
            self.memory.clear(session_id)
            self._session_buffer.pop(session_id, None)
            self._session_activity.pop(session_id, None)
        if level == "session":
            await self.long_term.delete_summary(session_id)
        if level in ("history", "all"):
            self.memory.clear_all()
            self._session_buffer.clear()
            self._session_activity.clear()
            await self.long_term.clear_summaries()
        if level == "all":
            await self.long_term.clear_user_memory(self.user_id)

    async def reset_all(self, level: str, session_id: str = "default") -> None:
        """整体复位（改动五收拢）：清除记忆 + 复位气氛值 + 清空触发器冷却。"""
        await self.reset_memory(level, session_id)
        self.mood.reset()
        if self.matcher is not None:
            self.matcher.reset_cooldowns()

    # ---------------------------------------------------------------- Prompt 组装

    async def _build_messages(self, user_input: str, session_id: str) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": await self._build_system_prompt(session_id)}]
        history = self.memory.get_history(session_id)
        messages.extend(history[-self.memory.max_history :])
        messages.append({"role": "user", "content": user_input})
        return messages

    async def _build_system_prompt(self, session_id: str) -> str:
        """组装 system prompt：人设 + 气氛值 + 记忆上下文 + 工具时间注入（最小侵入）。"""
        memory_context = await self._build_memory_context(session_id)
        prompt = build_system_prompt(
            self.system_prompt, self.mood.mood, self.mood.get_label(), memory_context or None
        )
        # 规格 §8：工具可用时，system prompt 必带当前日期/时间/周几/时区（每次组装动态生成）
        if self.tool_registry is not None and self.tool_registry.has_enabled_tools():
            prompt += f"\n\n[当前时间] {current_time_context()}"
        return prompt

    async def _preheat(self) -> None:
        if self.corpus:
            await self.llm.simple_chat(f"请学习以下语料风格：\n{self.corpus}\n确认后回复'好的'")
        self._preheated = True

    async def memory_add(self, session_id: str, role: str, content: str, source: str = "reply") -> None:
        """保存短期记忆，并同步进惰性总结 buffer（含活动时间戳与消息来源）。

        ``source`` 标注消息来源（``reply``=对话回应 / ``initiative``=主动发言），
        供惰性总结时区分、避免把主动发言当作用户画像事实（rules.md §15.5）。
        """
        self.memory.add(session_id, role, content)
        buffer = self._session_buffer.setdefault(session_id, [])
        buffer.append({"role": role, "content": content, "source": source})
        # 未接长期记忆时 buffer 不触发总结，硬上限防止无限增长
        if len(buffer) > self.segment_max * 3:
            del buffer[: len(buffer) - self.segment_max * 3]
        self._session_activity[session_id] = time.time()


def _as_str(value: Any) -> str:
    """任意值转字符串（None 转空串）。"""
    return "" if value is None else str(value)


def _as_optional_str(value: Any) -> Optional[str]:
    """任意值转可空字符串：非字符串或空值返回 None。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _as_int(value: Any, default: int) -> int:
    """任意值转 int，失败用默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str_list(value: Any) -> List[str]:
    """任意值转字符串列表（仅接受 list，逐项转 str）。"""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def keyword_matches(text: str, keywords: List[str]) -> bool:
    """用户信息关键字预筛：命中任一关键字即返回 True（即时抽取触发判据）。"""
    if not text:
        return False
    return any(k and k in text for k in keywords)
