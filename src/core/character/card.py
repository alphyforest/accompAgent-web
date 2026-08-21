"""角色卡（Character Card）：角色所有个性化特征的外部配置。

代码只读取角色卡、不硬编码任何角色相关数据（doc/00-架构蓝图 §3.1）。
领域模型用 Pydantic（rules.md §15.1），存放：``src/config/roles/character.json``。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# 未配置 tag_pattern 时的默认情绪解析正则
DEFAULT_TAG_PATTERN = r"\[情绪[:：]?\s*([A-Za-z]+)\]"


class CardMeta(BaseModel):
    """角色基础信息。"""

    id: str = "default"
    name: str = "角色"
    description: str = ""


class OutputProtocol(BaseModel):
    """输出协议：情绪标签列表、默认情绪、解析正则、多段分隔符。"""

    emotions: List[str] = Field(default_factory=list)
    default_emotion: str = "idle"
    tag_pattern: str = DEFAULT_TAG_PATTERN
    segment_separator: str = "---"


class TriggerCondition(BaseModel):
    """触发器条件：气氛区间（可空表不限制）+ 表达式（简单 eval，蓝图允许）。"""

    mood_min: Optional[int] = None
    mood_max: Optional[int] = None
    expression: Optional[str] = None


class InitiativeTrigger(BaseModel):
    """主动说话触发器：条件 + 概率 + 冷却 + 提示词 + 情绪（id 为稳定标识，rules.md §15.4）。

    ``interrupt_reply``：为 True 时，该触发器命中可在用户消息流水线内"接管"普通回复
    （如紧急安抚）；为 False（默认）时仅由后台调度器在用户空闲时主动开口，
    不在对话中途劫持对用户问题的正常回答。
    """

    id: str
    condition: TriggerCondition = Field(default_factory=TriggerCondition)
    probability: float = 1.0
    cooldown_minutes: float = 0.0
    prompt: str = ""
    emotion: str = ""
    interrupt_reply: bool = False


class InitState(BaseModel):
    """初始状态（气氛值 / 情绪）。"""

    mood: int = 0
    emotion: str = "idle"


class CharacterCard(BaseModel):
    """完整的角色卡。"""

    meta: CardMeta = Field(default_factory=CardMeta)
    system_prompt_file: str = "system_prompt.txt"
    output_protocol: OutputProtocol = Field(default_factory=OutputProtocol)
    portrait_map: Dict[str, str] = Field(default_factory=dict)
    initiative_triggers: List[InitiativeTrigger] = Field(default_factory=list)
    init_state: InitState = Field(default_factory=InitState)


def load_character_card(config_dir: str) -> CharacterCard:
    """从配置目录加载角色卡（character.json）。字段缺失时使用模型默认值。"""
    path = Path(config_dir) / "character.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return parse_character_card(data)


def parse_character_card(data: Dict[str, Any]) -> CharacterCard:
    """把角色卡 dict 解析为 CharacterCard（供配置文件与单测共用）。"""
    meta = data.get("meta") or {}
    protocol = data.get("output_protocol") or {}
    init_state = data.get("init_state") or {}
    triggers = [_parse_trigger(item, index) for index, item in enumerate(list(data.get("initiative_triggers", [])))]

    return CharacterCard(
        meta=CardMeta(
            id=_str(meta.get("id")) or "default",
            name=_str(meta.get("name")) or "角色",
            description=_str(meta.get("description")),
        ),
        system_prompt_file=_str(data.get("system_prompt_file")) or "system_prompt.txt",
        output_protocol=OutputProtocol(
            emotions=[_str(item) for item in protocol.get("emotions", [])],
            default_emotion=_str(protocol.get("default_emotion")) or "idle",
            tag_pattern=_str(protocol.get("tag_pattern")) or DEFAULT_TAG_PATTERN,
            segment_separator=_str(protocol.get("segment_separator")) or "---",
        ),
        portrait_map={_str(k): _str(v) for k, v in (data.get("portrait_map") or {}).items() if k and v},
        initiative_triggers=triggers,
        init_state=InitState(
            mood=_int(init_state.get("mood"), default=0),
            emotion=_str(init_state.get("emotion")) or "idle",
        ),
    )


def _parse_trigger(item: Any, index: int) -> InitiativeTrigger:
    """解析单个触发器条目；缺 id 时用可复现的序号派生（禁止 id()，rules.md §15.4）。"""
    item = item if isinstance(item, dict) else {}
    condition = item.get("condition") or {}
    return InitiativeTrigger(
        id=_str(item.get("id")) or f"trigger_{index}",
        condition=TriggerCondition(
            mood_min=_opt_int(condition.get("mood_min")),
            mood_max=_opt_int(condition.get("mood_max")),
            expression=_opt_str(condition.get("expression")),
        ),
        probability=_float(item.get("probability"), default=1.0),
        cooldown_minutes=_float(item.get("cooldown_minutes") or item.get("cooldownMinutes"), default=0.0),
        prompt=_str(item.get("prompt")),
        emotion=_str(item.get("emotion")),
        interrupt_reply=_bool(item.get("interrupt_reply"), default=False),
    )


def _str(value: Any) -> str:
    """任意值转字符串（None 转空串）。"""
    return "" if value is None else str(value)


def _opt_str(value: Any) -> Optional[str]:
    """可空字符串。"""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _opt_int(value: Any) -> Optional[int]:
    """可空整数（非数值返回 None）。"""
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any, default: int) -> int:
    """转 int，失败用默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    """转 float，失败用默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any, default: bool) -> bool:
    """转布尔：None 用默认；bool 原样；字符串按 1/true/yes/on 判真。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
