"""System Prompt 组装。"""

from typing import Optional


def build_system_prompt(base_prompt: str, mood: int, mood_label: str, memory_context: Optional[str] = None) -> str:
    """将角色基础人设、当前气氛值与记忆上下文组装为 System Prompt。

    memory_context 为第二阶段记忆注入内容（top-k 用户记忆 + 会话摘要），
    推测信息由调用方标注，此处补充使用约束。
    """
    prompt = f"{base_prompt}\n\n当前气氛值: {mood} ({mood_label})"
    if memory_context:
        prompt += (
            f"\n\n{memory_context}\n"
            "（以上是长期记忆。标注'推测'的信息不可当作事实向用户确认，"
            "只能作为温和的猜测；其余为已确认信息，可自然引用。）"
        )
    return prompt
