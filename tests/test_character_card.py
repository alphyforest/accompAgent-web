"""角色卡（Character Card）测试：加载器、字段默认与解析。"""

from src.core.character.card import load_character_card, parse_character_card

from tests.conftest import CHARACTER_CONFIG_DIR


def test_load_real_card():
    """真实角色卡能正确加载（含情绪/立绘/触发器）。"""
    card = load_character_card(CHARACTER_CONFIG_DIR)
    assert card.meta.id == "elysia"
    assert card.meta.name == "爱莉希雅"
    assert card.system_prompt_file == "system_prompt.txt"
    assert "happy" in card.output_protocol.emotions
    assert card.output_protocol.default_emotion == "idle"
    assert card.portrait_map["happy"].endswith("happy.png")
    assert len(card.initiative_triggers) >= 1
    trig = card.initiative_triggers[0]
    assert trig.id
    assert trig.condition.mood_min is not None


def test_parse_card_defaults():
    """空配置解析回退到模型默认值。"""
    card = parse_character_card({})
    assert card.meta.id == "default"
    assert card.meta.name == "角色"
    assert card.system_prompt_file == "system_prompt.txt"
    assert card.output_protocol.default_emotion == "idle"
    assert card.output_protocol.emotions == []
    assert card.portrait_map == {}
    assert card.initiative_triggers == []
    assert card.init_state.mood == 0


def test_parse_card_fields():
    """完整字段解析：元信息、输出协议、立绘映射、触发器、初始状态。"""
    data = {
        "meta": {"id": "m", "name": "小美", "description": "测试角色"},
        "system_prompt_file": "sp.txt",
        "output_protocol": {"emotions": ["a", "b"], "default_emotion": "a"},
        "portrait_map": {"a": "assets/a.png"},
        "initiative_triggers": [
            {
                "id": "t1",
                "condition": {"mood_min": 10, "mood_max": 90},
                "probability": 0.5,
                "cooldown_minutes": 2,
                "prompt": "提示",
                "emotion": "a",
            }
        ],
        "init_state": {"mood": 50, "emotion": "a"},
    }
    card = parse_character_card(data)
    assert card.meta.name == "小美"
    assert card.meta.description == "测试角色"
    assert card.system_prompt_file == "sp.txt"
    assert card.output_protocol.emotions == ["a", "b"]
    assert card.portrait_map["a"] == "assets/a.png"
    assert card.init_state.mood == 50
    trig = card.initiative_triggers[0]
    assert trig.id == "t1"
    assert trig.condition.mood_min == 10
    assert trig.condition.mood_max == 90
    assert trig.probability == 0.5
    assert trig.cooldown_minutes == 2.0
    assert trig.prompt == "提示"
    assert trig.emotion == "a"


def test_parse_card_partial_trigger():
    """触发器缺字段时使用默认值。"""
    card = parse_character_card({"initiative_triggers": [{"id": "t"}]})
    trig = card.initiative_triggers[0]
    assert trig.id == "t"
    assert trig.condition.mood_min is None
    assert trig.condition.mood_max is None
    assert trig.probability == 1.0
    assert trig.cooldown_minutes == 0.0
    assert trig.emotion == ""
