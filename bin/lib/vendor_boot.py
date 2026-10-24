from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from config import CRED_FILE, HOME

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
VENDOR_FILE = PLUGIN_ROOT / "vendor" / "bilibili-mcp" / "mcp_server.py"


def load_vendor():
    os.environ["BILI_DUTY_HOME"] = str(HOME)
    name = "bili_vendor_mcp"
    if name in sys.modules:
        module = sys.modules[name]
    else:
        if not VENDOR_FILE.exists():
            raise RuntimeError(f"找不到 vendored MCP：{VENDOR_FILE}")
        spec = importlib.util.spec_from_file_location(name, VENDOR_FILE)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载 vendored MCP：{VENDOR_FILE}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

    module.CRED_FILE = CRED_FILE
    resolved = Path(getattr(module, "CRED_FILE")).resolve()
    if resolved != CRED_FILE.resolve():
        raise RuntimeError(f"vendored MCP 的凭证路径没有指向数据目录：{resolved}")
    return module
