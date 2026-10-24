#!/usr/bin/env python3
"""配置写入校验与危险开关确认测试。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-config-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import gate  # noqa: E402
import mcp_server_safe as core  # noqa: E402


def cfg(**kwargs):
    return json.loads(asyncio.run(core.bili_config(**kwargs)))


def main():
    config.save_config({"dmMode": "off"})
    assert cfg(action="list")["ok"] is True
    assert cfg(action="set", key="perDay", value="-1")["ok"] is False
    assert cfg(action="set", key="perDay", value="3")["value"] == 3
    assert cfg(action="set", key="reportLevel", value="bad")["ok"] is False
    assert cfg(action="set", key="dailyRunTime", value="25:99")["ok"] is False
    assert cfg(action="set", key="keywords", value="AI, 大模型")["value"] == ["AI", "大模型"]
    bracket = cfg(action="set", key="keywords", value="[原神] 攻略")
    assert bracket["ok"] is True and bracket["value"] == ["[原神] 攻略"], bracket

    assert cfg(action="set", key="timezone", value="Not/AZone")["ok"] is False
    assert cfg(action="set", key="timezone", value="Asia/Shanghai")["ok"] is True
    assert cfg(action="set", key="mentionUid", value="-1")["ok"] is False
    assert cfg(action="set", key="mentionUid", value="123")["value"] == 123
    cfg(action="set", key="mentionUid", value="0")

    denied = cfg(action="set", key="replyOnlyMention", value="false")
    assert denied["ok"] is False and denied["needs_confirm"] is True, denied
    allowed = cfg(action="set", key="replyOnlyMention", value="false", confirm=True)
    assert allowed["ok"] is True and allowed["warnings"], allowed
    cfg(action="set", key="replyOnlyMention", value="true", confirm=True)

    denied = cfg(action="set", key="autoComment", value="true")
    assert denied["ok"] is False and denied["needs_confirm"] is True, denied
    cfg(action="set", key="mentionName", value="owner")
    allowed = cfg(action="set", key="autoComment", value="true", confirm=True)
    assert allowed["ok"] is True, allowed

    zero = cfg(action="set", key="commentPerRound", value="0")
    assert zero["ok"] is True and any("不会 @ 任何人" in w for w in zero["warnings"]), zero
    policy = gate.write_reply_policy([])
    assert policy["allowNewComment"] is False and policy["maxNewComments"] == 0, policy

    cfg(action="set", key="commentPerRound", value="2")
    policy = gate.write_reply_policy([])
    assert policy["allowNewComment"] is True and policy["maxNewComments"] == 2, policy

    # 关掉自动评论后，旧策略必须立刻失效
    cfg(action="set", key="autoComment", value="false")
    policy = gate.read_policy()
    assert policy["allowNewComment"] is False and policy["allow"] == [], policy

    # 发送期间策略被覆盖时，bump_usage 不能把旧 allow/runId 写回去
    config.save_config({"autoComment": True})
    gate.write_reply_policy([], run_id="round-a", allow_new=True)
    stale = gate.read_policy()
    assert stale["allowNewComment"] is True, stale
    gate.write_reply_policy([], run_id="round-b", allow_new=False)
    gate.bump_usage(stale, new=1)
    current = gate.read_policy()
    assert current["runId"] == "round-b", current
    assert current["allowNewComment"] is False, current
    assert current["newUsed"] == 0, current

    print("config tests OK")


if __name__ == "__main__":
    main()
