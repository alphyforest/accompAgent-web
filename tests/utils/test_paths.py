"""资源路径解析单元测试。"""

import os
import sys

from src.utils.paths import resource_path


def test_resource_path_source_mode():
    """源码运行：返回规范化后的相对路径。"""
    assert str(resource_path("static")) == os.path.normpath("static")
    assert str(resource_path("./src/config/roles")) == os.path.normpath("src/config/roles")


def test_resource_path_meipass_mode(monkeypatch):
    """打包运行：指向 sys._MEIPASS 解压目录。"""
    monkeypatch.setattr(sys, "_MEIPASS", "C:/bundle", raising=False)
    assert os.path.normpath(str(resource_path("static"))) == os.path.normpath(os.path.join("C:/bundle", "static"))
    assert os.path.normpath(str(resource_path("./src/config/roles"))) == os.path.normpath(
        os.path.join("C:/bundle", "src", "config", "roles")
    )
