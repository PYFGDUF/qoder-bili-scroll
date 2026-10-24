#!/usr/bin/env python3
"""CLI 失败时返回非零退出码。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-cli-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import cli  # noqa: E402


def main():
    assert cli.main(["config", "set", "perDay", "-1"]) == 1
    assert cli.main(["report", "show", "--day", "2020-01-01"]) == 1
    assert cli.main(["config", "set", "perDay", "3"]) == 0
    print("cli exit codes OK")


if __name__ == "__main__":
    main()
