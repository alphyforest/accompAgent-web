"""气氛值计算。"""

from typing import Dict

MOOD_MIN = -100
MOOD_MAX = 100
DELTA_LIMIT = 15
DECAY_RATE = 2


class MoodSystem:
    """气氛值计算。"""

    POSITIVE_KEYWORDS: Dict[str, int] = {
        "开心": 8,
        "高兴": 8,
        "喜欢": 6,
        "爱": 10,
        "好棒": 7,
        "谢谢": 4,
        "感恩": 5,
        "温暖": 6,
        "幸福": 8,
        "哈哈": 5,
    }

    NEGATIVE_KEYWORDS: Dict[str, int] = {
        "难过": -8,
        "伤心": -8,
        "累": -5,
        "疲惫": -5,
        "讨厌": -6,
        "烦": -6,
        "生气": -8,
        "愤怒": -10,
        "恨": -10,
        "失望": -7,
    }

    def __init__(self, initial_mood: int = 0):
        self.mood = max(MOOD_MIN, min(MOOD_MAX, initial_mood))
        self._decay_rate = DECAY_RATE

    def update(self, user_input: str) -> int:
        """更新气氛值，返回变化量。"""
        delta = 0
        for word, score in self.POSITIVE_KEYWORDS.items():
            if word in user_input:
                delta += score
        for word, score in self.NEGATIVE_KEYWORDS.items():
            if word in user_input:
                delta += score
        delta = max(-DELTA_LIMIT, min(DELTA_LIMIT, delta))

        # 自然衰减：向 0 靠拢
        if self.mood > 0:
            self.mood = max(0, self.mood - self._decay_rate)
        elif self.mood < 0:
            self.mood = min(0, self.mood + self._decay_rate)

        self.mood = max(MOOD_MIN, min(MOOD_MAX, self.mood + delta))
        return delta

    def get_label(self) -> str:
        """获取气氛值状态标签（英文，用于前端展示与 prompt 注入）。"""
        if self.mood > 50:
            return "happy"
        if self.mood > 20:
            return "greet"
        if self.mood > -20:
            return "idle"
        if self.mood > -50:
            return "sad"
        return "sad"

    def reset(self) -> None:
        """重置气氛值为 0。"""
        self.mood = 0
