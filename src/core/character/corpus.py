"""语料预热。"""

from pathlib import Path
from typing import List


def load_corpus(phrases_dir: str) -> str:
    """加载语料库目录下所有纯文本内容，拼接为单个字符串。"""
    base = Path(phrases_dir)
    if not base.exists():
        return ""
    chunks: List[str] = []
    for path in sorted(base.glob("*.txt")):
        chunks.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(chunks)
