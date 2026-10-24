#!/usr/bin/env python3
"""确认 bili_inbox 是 view-only：只读名单，不授权发送。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-inbox-tool-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import mcp_server_safe as core  # noqa: E402

captured = {}


def fake_build(**kwargs):
    captured.update(kwargs)
    return {"ok": True, "items": [], "text": "", "error": "", "scanned": 0, "why": {}}


def main():
    core.inbox_mod.build_inbox = fake_build
    result = json.loads(asyncio.run(core.bili_inbox()))
    assert result["ok"] is True, result
    assert captured.get("view_only") is True, captured
    assert captured.get("force") is True, captured
    assert captured.get("allow_new_comment") is False, captured
    print("inbox tool view-only OK")


if __name__ == "__main__":
    main()
