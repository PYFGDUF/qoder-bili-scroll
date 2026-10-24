#!/usr/bin/env python3
"""额度语义：perDay=0 手动轮不消耗额度，chat 不消耗视频额度。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-state-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import state  # noqa: E402


def main():
    config.save_config({"perDay": 0, "perRound": 2})
    for _ in range(3):
        result = state.reserve_round("scan", "manual")
        assert result["action"] == "run", result
    assert state.load_state()["count"] == 0, state.load_state()

    # 调回 perDay=5 后应能正常跑，而不是被手动轮的历史计数锁死
    config.save_config({"perDay": 5, "perRound": 2})
    result = state.reserve_round("scan", "manual")
    assert result["action"] == "run" and result["quota"] == 2, result

    before = state.load_state()["count"]
    chat = state.reserve_round("chat", "manual")
    assert chat["action"] == "run" and chat["quota"] == 0, chat
    assert state.load_state()["count"] == before, state.load_state()

    # 跨天：chat 的 leftToday 不能沿用昨天的 count
    config.write_json(
        config.STATE_FILE,
        {"schemaVersion": 1, "day": "2000-01-01", "count": 5, "runs": []},
    )
    chat2 = state.reserve_round("chat", "manual")
    assert chat2["leftToday"] == 5, chat2
    print("state quota OK")


if __name__ == "__main__":
    main()
