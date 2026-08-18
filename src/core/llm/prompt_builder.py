"""System Prompt 组装。"""


def build_system_prompt(base_prompt: str, mood: int, mood_label: str) -> str:
    """将角色基础人设与当前气氛值组装为 System Prompt。"""
    return f"{base_prompt}\n\n当前气氛值: {mood} ({mood_label})"
