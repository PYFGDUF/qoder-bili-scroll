#!/usr/bin/env python3
"""单条笔记 / 月度归档测试（不出网）。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-notes-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import notes  # noqa: E402

REPORT = """# 刷B站日报 · 2026-10-22

### BV1aa411c7mD

**标题**：新格式标题
**UP**：测试UP
**链接**：https://www.bilibili.com/video/BV1aa411c7mD?share=1
**档位**：值得看
**为什么值得**：第一句理由。
第二句理由。

## 1. 旧格式标题

- 链接：https://www.bilibili.com/video/BV1bb411c7mD?p=2
- 结论：可以看
- 理由：旧格式的理由。

## 总结

这一轮就这些。
"""


def main():
    assert notes.REPORTS_DIR == TMP / "reports", notes.REPORTS_DIR
    assert notes.DIGEST_DIR == TMP / "digest", notes.DIGEST_DIR

    sections = notes.parse_report_sections(REPORT)
    assert len(sections) == 2, sections
    parsed = {section["bvid"]: section for section in sections}
    assert set(parsed) == {"BV1aa411c7mD", "BV1bb411c7mD"}, parsed

    fresh = parsed["BV1aa411c7mD"]
    assert fresh["title"] == "新格式标题", fresh
    assert fresh["tier"] == "值得看", fresh
    assert fresh["reason"] == "第一句理由。 第二句理由。", fresh
    assert fresh["url"] == "https://www.bilibili.com/video/BV1aa411c7mD", fresh

    legacy = parsed["BV1bb411c7mD"]
    assert legacy["title"] == "旧格式标题", legacy
    assert legacy["tier"] == "可以看", legacy
    assert legacy["reason"] == "旧格式的理由。", legacy
    assert legacy["url"] == "https://www.bilibili.com/video/BV1bb411c7mD", legacy

    # 旧标题序号可以是 `1)`，结论缺失时回落到段落
    fallback = notes.parse_report_sections(
        """## 2) 另一个旧标题

https://www.bilibili.com/video/BV1cc411c7mD
结论：值得

这是第一段理由。
"""
    )
    assert len(fallback) == 1, fallback
    assert fallback[0]["bvid"] == "BV1cc411c7mD", fallback
    assert fallback[0]["tier"] == "值得", fallback
    assert fallback[0]["reason"] == "这是第一段理由。", fallback

    report_file = TMP / "out" / "2026-10-22.md"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(REPORT, encoding="utf-8")

    synced = notes.sync_from_report(report_file, "2026-10-22")
    assert synced["ok"] is True and synced["created"] == 2 and synced["skipped"] == 0, synced

    fresh_note = notes.REPORTS_DIR / "BV1aa411c7mD-2026-10-22.md"
    legacy_note = notes.REPORTS_DIR / "BV1bb411c7mD-2026-10-22.md"
    assert fresh_note.exists() and legacy_note.exists()
    for path in (fresh_note, legacy_note):
        assert path.stat().st_mode & 0o777 == 0o600, path

    fresh_text = fresh_note.read_text(encoding="utf-8")
    assert fresh_text.startswith("# 新格式标题\n"), fresh_text
    assert "2026-10-22" in fresh_text, fresh_text
    assert "https://www.bilibili.com/video/BV1aa411c7mD" in fresh_text, fresh_text
    assert "档位：值得看" in fresh_text, fresh_text
    assert "第一句理由。 第二句理由。" in fresh_text, fresh_text
    assert str(report_file) in fresh_text, fresh_text

    legacy_text = legacy_note.read_text(encoding="utf-8")
    assert legacy_text.startswith("# 旧格式标题\n"), legacy_text
    assert "档位：可以看" in legacy_text, legacy_text
    assert "旧格式的理由。" in legacy_text, legacy_text

    # 已存在的笔记绝不覆盖
    fresh_note.write_text("# 手改过\n", encoding="utf-8")
    again = notes.sync_from_report(report_file, "2026-10-22")
    assert again["ok"] is True and again["created"] == 0 and again["skipped"] == 2, again
    assert fresh_note.read_text(encoding="utf-8") == "# 手改过\n"
    assert not list(notes.REPORTS_DIR.glob(".*.tmp")), list(notes.REPORTS_DIR.glob(".*.tmp"))

    missing = notes.sync_from_report(TMP / "out" / "nope.md", "2026-10-22")
    assert missing["ok"] is False and missing["created"] == 0, missing

    rows = [
        {
            "date": "2026-10-22",
            "bvid": "BV1aa411c7mD",
            "title": "标题 | 带竖线",
            "verdict": "值得看",
            "reason": "理由一",
            "url": "https://www.bilibili.com/video/BV1aa411c7mD",
        },
        {
            "date": "2026-10-22 18:51",
            "bvid": "BV1bb411c7mD",
            "title": "旧格式",
            "verdict": "跳过",
            "reason": "无字幕",
        },
        {
            "date": "2026-10-22",
            "bvid": "BV1aa411c7mD",
            "title": "同一行的重复",
            "verdict": "值得看",
            "reason": "重复",
        },
    ]
    first = notes.digest_from_seen(rows, month="2026-10")
    assert first["ok"] is True and first["added"] == 2, first
    digest_file = Path(first["path"])
    assert digest_file == notes.DIGEST_DIR / "2026-10.md", first
    assert digest_file.stat().st_mode & 0o777 == 0o600, digest_file

    digest_text = digest_file.read_text(encoding="utf-8")
    lines = [line for line in digest_text.splitlines() if line.strip()]
    assert len(lines) == 2, lines
    assert lines[0].startswith("- 2026-10-22 · BV1aa411c7mD · 标题 | 带竖线 · 值得看 · 理由一 · "), lines
    assert lines[1] == (
        "- 2026-10-22 · BV1bb411c7mD · 旧格式 · 跳过 · 无字幕 · "
        "https://www.bilibili.com/video/BV1bb411c7mD"
    ), lines

    second = notes.digest_from_seen(rows, month="2026-10")
    assert second["ok"] is True and second["added"] == 0, second
    assert digest_file.read_text(encoding="utf-8") == digest_text

    # 已有内容（哪怕是人手加的表头）必须保留，只往后追加
    digest_file.write_text("# 月度归档\n\n" + digest_text, encoding="utf-8")
    third = notes.digest_from_seen(
        [
            {
                "date": "2026-10-23",
                "bvid": "BV1cc411c7mD",
                "title": "第三条",
                "verdict": "可以看",
                "reason": "补一条",
            }
        ],
        month="2026-10",
    )
    assert third["ok"] is True and third["added"] == 1, third
    updated = digest_file.read_text(encoding="utf-8")
    assert updated.startswith("# 月度归档\n\n"), updated
    assert updated.count("- 2026-10-22 · BV1aa411c7mD") == 1, updated
    assert "- 2026-10-23 · BV1cc411c7mD · 第三条 · 可以看 · 补一条 · " in updated, updated

    empty = notes.digest_from_seen([], month=None)
    assert empty["ok"] is True and empty["added"] == 0, empty
    assert Path(empty["path"]).parent == notes.DIGEST_DIR, empty
    assert Path(empty["path"]).name == datetime.now().strftime("%Y-%m") + ".md", empty
    assert not list(notes.DIGEST_DIR.glob(".*.tmp")), list(notes.DIGEST_DIR.glob(".*.tmp"))

    assert config.HOME == TMP, config.HOME
    print("notes tests OK")


if __name__ == "__main__":
    main()
