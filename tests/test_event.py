"""事件系统单元测试。"""

import random


def test_load_config(events):
    assert "coquettish_s1" in events.nodes
    assert "excited" in events.nodes
    assert events.nodes["coquettish_s1"].type == "event"
    assert events.nodes["coquettish_s2_accept"].type == "chain"


def test_match_event_mood_in_range(events, monkeypatch):
    # coquettish_s1 需要 mood >= 50；固定随机数保证概率命中
    monkeypatch.setattr(random, "random", lambda: 0.0)
    node_id = events.match_event(mood=80)
    assert node_id is not None


def test_match_event_probability_miss(events, monkeypatch):
    # 概率判定不命中（random 返回 1.0 恒大于所有 probability）
    monkeypatch.setattr(random, "random", lambda: 1.0)
    assert events.match_event(mood=80) is None


def test_match_event_mood_out_of_range(events):
    # mood 低于 30 时 excited 也不应触发
    assert events.match_event(mood=0) is None


def test_force_trigger(events):
    node = events.force_trigger("coquettish_s1")
    assert node is not None
    assert node.id == "coquettish_s1"
    assert events.force_trigger("不存在") is None


def test_start_event(events):
    node = events.start_event("coquettish_s1")
    assert node is not None
    assert events.active_node == "coquettish_s1"


def test_process_branch_accept(events):
    events.start_event("coquettish_s1")
    node = events.process_response("好呀")
    # chain[0] 为同意分支 coquettish_s2_accept
    assert node is not None
    assert node.id == "coquettish_s2_accept"


def test_process_branch_reject(events):
    events.start_event("coquettish_s1")
    node = events.process_response("不要")
    # chain[1] 为拒绝分支 coquettish_s3_reject
    assert node is not None
    assert node.id == "coquettish_s3_reject"


def test_process_branch_unclear(events):
    events.start_event("coquettish_s1")
    # 不明确的回应，返回 None，链保持激活
    assert events.process_response("今天天气不错") is None
    assert events.active_node == "coquettish_s1"


def test_process_chain_end(events):
    events.start_event("coquettish_s1")
    events.process_response("好呀")  # -> s2_accept（无 chain）
    # s2_accept 无 chain，处理响应后链结束
    result = events.process_response("继续")
    assert result is None
    assert events.active_node is None
