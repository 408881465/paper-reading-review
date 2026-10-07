"""让测试能直接 import scripts/ 下的脚本（脚本目录不是包）。"""

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_module(module):
    """按模块对象反向取回脚本模块，供测试直接访问其函数与常量。"""
    for name in ("build_review", "extract_pdf_text"):
        if sys.modules.get(name) is module:
            return module
    raise AssertionError(f"不是由 conftest 加载的模块：{module!r}")


build_review = _load("build_review")
extract_pdf_text = _load("extract_pdf_text")
