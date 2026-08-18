"""情绪标签与 emoji 映射。"""

from typing import Dict

# 情绪标签 -> emoji 图标
EMOJI_MAP: Dict[str, str] = {
    "开心": "😊",
    "高兴": "😄",
    "害羞": "😳",
    "难过": "😢",
    "生气": "😠",
    "平静": "😌",
    "热烈": "🔥",
    "温暖": "🌞",
    "低沉": "🌧️",
    "冰冷": "❄️",
    "分享": "💬",
}

# 默认 emoji，用于未映射的情绪标签
DEFAULT_EMOJI = "🙂"


def get_emoji(label: str) -> str:
    """根据情绪标签返回对应 emoji。"""
    return EMOJI_MAP.get(label, DEFAULT_EMOJI)
