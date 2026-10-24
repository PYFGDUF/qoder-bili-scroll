#!/usr/bin/env python3
"""v1 -> v2 配置迁移：老评论用户不被突然改成私信。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-mig-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402


def run_case(name: str, raw: dict):
    home = TMP / name
    home.mkdir(parents=True, exist_ok=True)
    old_home, old_cfg = config.HOME, config.CONFIG_FILE
    config.HOME = home
    config.CONFIG_FILE = home / "config.json"
    try:
        config.CONFIG_FILE.write_text(json.dumps(raw), encoding="utf-8")
        cfg = config.load_config()
        backup = home / "config.json.v1.bak"
        assert backup.exists()
        return cfg, backup
    finally:
        config.HOME, config.CONFIG_FILE = old_home, old_cfg


def main():
    old_comments = {
        "schemaVersion": 1,
        "perDay": 30,
        "perRound": 5,
        "autoComment": True,
        "replyToComments": True,
        "mentionName": "example_owner",
        "mentionUid": 0,
        "reportLevel": "full",
    }
    cfg, backup = run_case("old-comments", old_comments)
    assert cfg["schemaVersion"] == 2
    assert cfg["dmMode"] == "off", cfg
    assert cfg["dmReply"] is False, cfg
    assert cfg["perDay"] == 30 and cfg["perRound"] == 5, cfg
    assert cfg["mentionName"] == "example_owner", cfg
    assert cfg["dmReplyPerRound"] == 1 and cfg["dmReplyPerDay"] == 3
    assert cfg["personaPrompt"] == "" and cfg["videoNotes"] is True
    assert json.loads(backup.read_text(encoding="utf-8"))["perDay"] == 30

    no_comments = {"schemaVersion": 1, "autoComment": False, "replyToComments": False}
    cfg2, _ = run_case("no-comments", no_comments)
    assert cfg2["dmMode"] == "only" and cfg2["dmReply"] is True, cfg2

    # 已迁移的配置再次 load 不应重复改值
    home = TMP / "old-comments"
    old_home, old_cfg = config.HOME, config.CONFIG_FILE
    config.HOME, config.CONFIG_FILE = home, home / "config.json"
    try:
        again = config.load_config()
        assert again["dmMode"] == "off" and again["schemaVersion"] == 2
    finally:
        config.HOME, config.CONFIG_FILE = old_home, old_cfg

    print("config migration OK")


if __name__ == "__main__":
    main()
