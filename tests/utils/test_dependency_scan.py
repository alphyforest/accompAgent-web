"""PLAN-010 R0：依赖边界扫描（STD-010 §10 + R1 验收）。

用 ast 静态扫描 src/ 下每个模块的 import 边，断言禁用组合未新增。
历史耦合放入 ALLOWED 允许列表（每项注明拆除阶段）；新出现任何其他禁用边直接失败；
已无实际引用的允许项也必须移除（防止改完后残留白名单）。
"""

import ast
from pathlib import Path
from typing import List, Optional, Set, Tuple

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src"

# 禁用边规则：(from 前缀, to 前缀, 说明)
RULES: List[Tuple[str, str, str]] = [
    ("src.core.agent", "src.core.tools", "dialogue -> tool 实现（R2 拆出 ToolService 后移除）"),
    ("src.core.agent", "src.core.agent.tool_loop", "agent 持有 ToolLoop 实现（R2 迁入 src/core/tools 后移除）"),
    ("src.core.tools", "src.core.agent", "tool -> dialogue 实现（STD-010 §4 禁止）"),
    ("src.core.tools", "src.core.character", "tool -> character（STD-010 §4 禁止）"),
    ("src.core", "src.api", "core -> api（STD-010 §1 依赖方向禁止）"),
    ("src.core", "fastapi", "core -> fastapi（STD-010 §1 禁止）"),
    ("src.application", "src.core", "application 用例层 -> core 实现（仅 model_adapter 边界允许）"),
    ("src.application", "src.api", "application -> api（STD-010 §1 禁止）"),
    ("src.application", "fastapi", "application -> fastapi（STD-010 §1 禁止）"),
    ("src.core", "src.application", "core -> application（依赖方向倒挂）"),
    ("src.core", "src.bootstrap", "core -> bootstrap（依赖方向倒挂）"),
    ("src.bootstrap", "src.api", "bootstrap -> api（依赖倒挂/循环风险）"),
    ("src.bootstrap", "fastapi", "bootstrap -> fastapi（Composition Root 不得直接依赖 Web 框架）"),
]

# 允许列表：(from 模块精确, to 前缀) -> 说明（必须注明拆除阶段）
ALLOWED: dict[Tuple[str, str], str] = {
    ("src.application.model_adapter", "src.core.llm.client"): "SPEC-010 §4：边界适配器包装 LLMClient，R1 允许",
}


def _relative_base(module: str, level: int) -> str:
    """相对 import 的基准包：模块 src.a.b.mod 在 level=1 时基准为 src.a.b，level=2 时为 src.a。"""
    parts = module.split(".")
    return ".".join(parts[: len(parts) - level])


def _import_targets(module: str, tree: ast.Module) -> Set[str]:
    targets: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                base = _relative_base(module, node.level)
                if node.module:
                    base = f"{base}.{node.module}"
            else:
                base = node.module or ""
            if base:
                targets.add(base)
    return targets


def _collect_edges() -> List[Tuple[str, str]]:
    edges: List[Tuple[str, str]] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(SRC_ROOT.parent).with_suffix("")
        module = ".".join(rel.parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for target in _import_targets(module, tree):
            if target and target != module:
                edges.append((module, target))
    return edges


def _allowed_key(module: str, target: str) -> Optional[Tuple[str, str]]:
    for from_module, to_prefix in ALLOWED:
        if module == from_module and target.startswith(to_prefix):
            return (from_module, to_prefix)
    return None


def test_relative_import_resolution():
    assert _relative_base("src.core.agent.dialogue", 1) == "src.core.agent"
    assert _relative_base("src.core.agent.dialogue", 2) == "src.core"


def test_dependency_boundaries_no_new_violations():
    edges = _collect_edges()
    violations: List[str] = []
    covered: Set[Tuple[str, str]] = set()

    for module, target in edges:
        for from_prefix, to_prefix, note in RULES:
            if module.startswith(from_prefix) and target.startswith(to_prefix):
                key = _allowed_key(module, target)
                if key is not None:
                    covered.add(key)
                else:
                    violations.append(f"{module} -> {target}  ({note})")

    stale = [key for key in ALLOWED if key not in covered]

    assert stale == [], f"移除已无实际引用的 ALLOWED 项: {stale}"
    assert violations == [], "发现禁用 import 边（新增耦合）:\n" + "\n".join(violations)
