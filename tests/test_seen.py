#!/usr/bin/env python3
"""判重表写入/查询测试。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-seen-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import ledger  # noqa: E402
import seen  # noqa: E402


def main():
    first = seen.add("BV1xx411c7mD", "一个视频", "值得", "有实操细节")
    assert first["ok"] and first["already"] is False, first
    assert seen.check("BV1xx411c7mD")["seen"] is True
    invalid = seen.check("")
    assert invalid["ok"] is False and invalid["seen"] is False, invalid
    duplicate = seen.add("BV1xx411c7mD", "另一个标题", "跳过", "重复")
    assert duplicate["ok"] and duplicate["already"] is True, duplicate
    second = seen.add("BV1yy411c7mD", "第二个 | 视频", "跳过", "无字幕")
    assert second["ok"] and second["already"] is False, second

    rows = seen.list_rows()["rows"]
    assert len(rows) == 2 and rows[0]["bvid"] == "BV1yy411c7mD", rows
    assert rows[0]["title"] == "第二个 | 视频", rows
    assert config.SEEN_FILE.stat().st_mode & 0o777 == 0o600
    assert not list(TMP.glob(".*.tmp")), list(TMP.glob(".*.tmp"))
    text = config.SEEN_FILE.read_text(encoding="utf-8")
    before_comments = text.split("## 评论记录")[0]
    assert "BV1xx411c7mD" in before_comments and "BV1yy411c7mD" in before_comments
    # 视频记录不能被当成评论记录
    assert ledger.parse_seen(text) == [], ledger.parse_seen(text)

    # 新旧两种表混在一起：日期带时间、标题带转义竖线、状态里写投递/跳过
    fixture = """# 刷B站判重表

## 看过的视频

| 日期 | BV | 标题 | 结论 | 理由 |
| --- | --- | --- | --- | --- |
| 2026-10-20 18:51 | BV1aa411c7mD | 旧格式 \\| 标题 | 值得 | 旧理由 |
| 2026-10-21T09:03:12 | BV1bb411c7mD | 旧跳过 | 跳过 | 无字幕 |

| 日期 | bvid | 标题 | UP | 状态 |
| --- | --- | --- | --- | --- |
| 2026-10-22 20:00 | BV1cc411c7mD | 新格式 \\| 标题 | 某UP | 挑（值得看） |
| 2026-10-23 | BV1dd411c7mD | 评论过 | 某UP | 挑（可以看） |
| 2026-10-24 | BV1ee411c7mD | 跳过的 | 某UP | 跳过（无字幕） |

## 评论记录

| BV | rpid | root | 时间 | 正文前20字 |
| --- | --- | --- | --- | --- |
| BV1dd411c7mD | 1234567890 | 0 | 2026-10-23 | 你好 |
"""
    config.SEEN_FILE.write_text(fixture, encoding="utf-8")
    config.SEEN_FILE.chmod(0o600)

    report = config.HOME / "reports" / "BV1cc411c7mD-2026-10-22.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("# 新格式标题\n", encoding="utf-8")

    parsed = seen.list_rows(limit=50)
    assert parsed["count"] == 5 and len(parsed["rows"]) == 5, parsed
    index = {row["bvid"]: row for row in parsed["rows"]}

    old = index["BV1aa411c7mD"]
    assert old["date"] == "2026-10-20", old
    assert old["title"] == "旧格式 | 标题", old
    assert old["verdict"] == "值得" and old["picked"] is True, old
    assert old["up"] == "" and old["status"] == "" and old["reason"] == "旧理由", old
    assert old["delivered"] is False and old["note"] is False, old
    assert old["url"] == "https://www.bilibili.com/video/BV1aa411c7mD", old

    skipped = index["BV1bb411c7mD"]
    assert skipped["date"] == "2026-10-21", skipped
    assert skipped["verdict"] == "跳过" and skipped["picked"] is False, skipped
    assert skipped["reason"] == "无字幕", skipped

    fresh = index["BV1cc411c7mD"]
    assert fresh["date"] == "2026-10-22", fresh
    assert fresh["title"] == "新格式 | 标题" and fresh["up"] == "某UP", fresh
    assert fresh["status"] == "挑（值得看）" and fresh["verdict"] == "值得看", fresh
    assert fresh["picked"] is True and fresh["delivered"] is False, fresh
    assert fresh["note"] is True, fresh
    assert fresh["url"] == "https://www.bilibili.com/video/BV1cc411c7mD", fresh

    delivered = index["BV1dd411c7mD"]
    assert delivered["picked"] is True and delivered["delivered"] is True, delivered
    assert delivered["verdict"] == "可以看", delivered
    assert delivered["note"] is False, delivered

    rejected = index["BV1ee411c7mD"]
    assert rejected["verdict"] == "跳过", rejected
    assert rejected["picked"] is False and rejected["delivered"] is False, rejected
    assert rejected["reason"] == "无字幕", rejected

    limited = seen.list_rows(limit=2)["rows"]
    assert [row["bvid"] for row in limited] == ["BV1ee411c7mD", "BV1dd411c7mD"], limited
    assert parsed["rows"][0]["bvid"] == "BV1ee411c7mD", parsed["rows"]

    # v2 接口：带 UP / 状态时写新表头，读回来也要对
    v2 = seen.add("BV1ff411c7mD", "新接口", "值得", "有细节", up="UP主", status="挑（值得看）")
    assert v2["ok"] and v2["already"] is False, v2
    assert seen.check("BV1ff411c7mD")["seen"] is True
    fresh_rows = {row["bvid"]: row for row in seen.list_rows()["rows"]}
    added = fresh_rows["BV1ff411c7mD"]
    assert added["up"] == "UP主" and added["status"] == "挑（值得看）", added
    assert added["verdict"] == "值得看" and added["picked"] is True, added
    assert added["date"] == config.day_key(), added

    # 旧接口随后追加：不能让旧行被新表头误读
    legacy = seen.add("BV1gg411c7mD", "旧接口二", "跳过", "重复")
    assert legacy["ok"] and legacy["already"] is False, legacy
    all_rows = {row["bvid"]: row for row in seen.list_rows(limit=20)["rows"]}
    assert all_rows["BV1gg411c7mD"]["verdict"] == "跳过", all_rows
    assert all_rows["BV1gg411c7mD"]["reason"] == "重复", all_rows
    assert all_rows["BV1gg411c7mD"]["up"] == "", all_rows["BV1gg411c7mD"]
    assert all_rows["BV1aa411c7mD"]["verdict"] == "值得", all_rows["BV1aa411c7mD"]

    assert config.SEEN_FILE.stat().st_mode & 0o777 == 0o600
    assert not list(TMP.glob(".*.tmp")), list(TMP.glob(".*.tmp"))
    print("seen tests OK")


if __name__ == "__main__":
    main()
