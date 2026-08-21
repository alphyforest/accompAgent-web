"""资源路径解析：兼容 PyInstaller onefile 打包（sys._MEIPASS）与源码运行。"""

import os
import sys
from pathlib import Path
from typing import Optional


def resource_path(relative: str) -> Path:
    """将相对资源路径解析为可访问的路径。

    源码运行时返回当前工作目录下的相对路径；
    打包运行时静态资源解压到 ``sys._MEIPASS`` 临时目录，自动指向该目录。
    资源路径统一走此函数解析，禁止在业务代码中写死相对路径。
    """
    bundle_dir: Optional[str] = getattr(sys, "_MEIPASS", None)
    normalized = os.path.normpath(relative)
    if bundle_dir is not None:
        return Path(bundle_dir) / normalized
    return Path(normalized)
