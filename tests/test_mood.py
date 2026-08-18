"""气氛值系统单元测试。"""

from src.core.agent.mood import MoodSystem


def test_update_positive_keyword():
    mood = MoodSystem()
    delta = mood.update("今天很开心")
    assert delta > 0
    assert mood.mood > 0


def test_update_negative_keyword():
    mood = MoodSystem()
    delta = mood.update("今天很难过")
    assert delta < 0
    assert mood.mood < 0


def test_update_delta_limit():
    mood = MoodSystem()
    # 多个正向词累加，单次变化不超过 15
    delta = mood.update("开心高兴喜欢爱好棒谢谢温暖幸福哈哈")
    assert delta <= 15
    assert delta >= -15


def test_update_clamp_range():
    mood = MoodSystem(initial_mood=95)
    mood.update("开心高兴喜欢爱好棒谢谢温暖幸福哈哈")
    assert mood.mood <= 100


def test_decay_toward_zero():
    mood = MoodSystem(initial_mood=50)
    mood.update("（无关键词）")
    assert mood.mood == 48  # 衰减 2


def test_get_label():
    mood = MoodSystem(initial_mood=80)
    assert mood.get_label() == "happy"
    mood.mood = 30
    assert mood.get_label() == "greet"
    mood.mood = 0
    assert mood.get_label() == "idle"
    mood.mood = -60
    assert mood.get_label() == "sad"


def test_reset():
    mood = MoodSystem(initial_mood=80)
    mood.reset()
    assert mood.mood == 0
