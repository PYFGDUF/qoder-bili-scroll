#!/usr/bin/env python3
"""日报日期参数不能路径穿越。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-report-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import report  # noqa: E402


def main():
    shown = report.show_report("../../etc/passwd")
    assert shown["ok"] is False and ".." not in shown.get("file", ""), shown

    ensured = report.ensure_report("../../etc/passwd")
    path = Path(ensured["file"])
    assert path.parent == config.OUT_DIR, ensured
    assert path.name.count("-") == 2 and path.suffix == ".md", ensured
    print("report path traversal OK")


if __name__ == "__main__":
    main()
