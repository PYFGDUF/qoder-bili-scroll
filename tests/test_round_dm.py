#!/usr/bin/env python3
"""round_end：日报 -> 私信摘要候选 + 单条笔记 + digest。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-round-dm-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import dm  # noqa: E402
import mcp_server_safe as core  # noqa: E402
import seen  # noqa: E402
import state  # noqa: E402

BV = "BV1xx411c7mD"


def main():
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "j", "buvid3": "b", "dedeuserid": "100"})
    config.save_config(
        {
            "perDay": 5,
            "perRound": 2,
            "mentionUid": 200,
            "mentionName": "owner",
            "dmMode": "only",
            "dmReply": True,
            "reportLevel": "full",
            "videoNotes": True,
            "autoComment": False,
        }
    )
    reservation = state.reserve_round("scan", "manual")
    run_id = reservation["run_id"]
    day = config.day_key()
    report = config.OUT_DIR / f"{day}.md"
    report.write_text(
        f"# 刷B站日报 · {day}\n\n"
        f"### {BV}\n"
        "**档位**：值得看\n"
        "标题测试\n"
        "UP：某人\n"
        f"链接：https://www.bilibili.com/video/{BV}\n"
        "**为什么值得**：有实操细节\n\n"
        "因无字幕跳过 0 条\n",
        encoding="utf-8",
    )
    dm.write_policy(200, run_id, allow_keys=[], allow_send=True, max_replies=1, max_per_day=3)
    seen.add(BV, "标题测试", "值得看", "有实操细节")

    result = core._round_end_sync(run_id, "ok", "test")
    assert result["ok"], result
    assert result["report"]["exists"] is True, result
    assert result["dm_send"] and result["dm_send"]["ownerUid"] == 200, result
    text = result["dm_send"]["text"]
    assert "今天挑到 1 条" in text and "标题测试" in text and BV in text, text
    note = config.REPORTS_DIR / f"{BV}-{day}.md"
    assert note.exists(), note
    assert "标题测试" in note.read_text(encoding="utf-8")
    assert any(config.DIGEST_DIR.glob("*.md")), list(config.DIGEST_DIR.glob("*"))
    run = next(row for row in state.load_state()["runs"] if row["run_id"] == run_id)
    assert run["status"] == "ok", run
    print("round end DM/notes OK")


if __name__ == "__main__":
    main()
