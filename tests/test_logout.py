#!/usr/bin/env python3
"""退出登录：需要确认，删除凭证并清空账号缓存/发言许可。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-logout-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import gate  # noqa: E402
import ledger  # noqa: E402
import mcp_server_safe as core  # noqa: E402


def call(**kwargs):
    return json.loads(asyncio.run(core.bili_logout(**kwargs)))


def main():
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "", "buvid3": "", "dedeuserid": "1"})
    ledger.write_ledger({"sent": [], "threads": {}, "owner": {"mid": 2, "name": "owner"}, "me": {"mid": 1, "uname": "me"}})
    config.save_config({"autoComment": True, "mentionName": "owner", "dmMode": "off"})
    gate.write_reply_policy([], run_id="test", allow_new=True)
    assert gate.read_policy()["allowNewComment"] is True

    denied = call(confirm=False)
    assert denied["ok"] is False and denied["needs_confirm"] is True, denied
    assert config.CRED_FILE.exists()

    done = call(confirm=True)
    assert done["ok"] is True, done
    assert not config.CRED_FILE.exists()
    book = ledger.read_ledger()
    assert book["me"] == {} and book["owner"] == {}, book
    policy = gate.read_policy()
    assert policy["allow"] == [] and policy["allowNewComment"] is False, policy
    print("logout OK")


if __name__ == "__main__":
    main()
