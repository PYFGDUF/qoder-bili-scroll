#!/usr/bin/env python3
"""bili_reply 包装层测试：策略新鲜度、顶层评论开关、名单限制、发后记账。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-reply-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import gate  # noqa: E402
import ledger  # noqa: E402
import mcp_server_safe as core  # noqa: E402

BV = "BV1xx411c7mD"
RUN = "test-run"
NEW_RPID = 1900000001
counter = {"value": NEW_RPID}


def live_run(run_id: str = RUN) -> None:
    config.write_json(
        config.STATE_FILE,
        {
            "schemaVersion": 1,
            "day": config.day_key(),
            "count": 0,
            "runs": [
                {
                    "run_id": run_id,
                    "status": "running",
                    "startedAt": config.now_iso(),
                    "mode": "scan",
                    "quota": 2,
                }
            ],
        },
    )


async def fake_reply(bvid, text, rpid=0, root=0):
    counter["value"] += 1
    return json.dumps({"success": True, "rpid": counter["value"], "message": "ok"}, ensure_ascii=False)


def call(text, rpid=0, root=0):
    return json.loads(asyncio.run(core.bili_reply(BV, text, rpid=rpid, root=root)))


def reset():
    ledger.write_ledger({"sent": [], "threads": {}, "owner": {}, "me": {}})
    core._send_state.update({"runId": "__reset__", "new": 0, "replies": 0})
    live_run(RUN)
    try:
        config.POLICY_FILE.unlink()
    except OSError:
        pass


def main():
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "", "buvid3": "", "dedeuserid": "100"})
    config.save_config({"autoComment": True, "mentionName": "owner", "perDay": 5, "dmMode": "off"})
    core.UP.bili_reply = fake_reply
    core.report_mod.verify_comment = lambda bvid, rpid, root=0: {
        "ok": True,
        "visible": True,
        "how": "test",
        "note": "公开可见",
    }

    # 1) 没有策略 -> 拒
    reset()
    result = call("@owner 好耶")
    assert result.get("blocked") and "reply-policy" in result.get("reason", ""), result

    # 2) 有策略但不允许顶层评论 -> 拒
    reset()
    config.save_config({"autoComment": False})
    gate.write_reply_policy([], run_id=RUN)
    result = call("@owner 好耶")
    assert result.get("blocked") and "顶层评论" in result.get("reason", ""), result

    # 3) 允许顶层评论 -> 发，并记账
    reset()
    config.save_config({"autoComment": True})
    gate.write_reply_policy([], run_id=RUN)
    result = call("@owner 好耶")
    assert result.get("success") and result.get("ledger_recorded"), result
    assert result.get("verify", {}).get("visible") is True, result
    book = ledger.read_ledger()
    assert any(row["rpid"] == counter["value"] for row in book["sent"]), book
    assert book["sent"][-1]["root"] == 0, book["sent"][-1]
    # 同一轮第二条顶层评论 -> 被每轮上限挡住
    again = call("@owner 再来一条")
    assert again.get("blocked") and "本轮最多发 1 条" in again.get("reason", ""), again
    # 模拟另一个 MCP 进程：本地缓存清零，但持久计数应把它拦回来
    core._send_state["new"] = 0
    again2 = call("@owner 再来一条")
    assert again2.get("blocked") and "本轮最多发 1 条" in again2.get("reason", ""), again2

    # 顶层评论没有 @ 配置里的本人 -> 拒
    reset()
    gate.write_reply_policy([], run_id=RUN)
    no_mention = call("好耶")
    assert no_mention.get("blocked") and "必须 @ 配置里的使用者" in no_mention.get("reason", ""), no_mention

    # 4) 回复不在名单里 -> 拒
    reset()
    gate.write_reply_policy([{"bv": BV, "root": 111, "via": "thread", "from": {"rpid": 222}}], run_id=RUN)
    result = call("收到", rpid=333, root=111)
    assert result.get("blocked") and "名单" in result.get("reason", ""), result

    # 5) 回复在名单里 -> 发，并记 sent + threads
    reset()
    gate.write_reply_policy([{"bv": BV, "root": 111, "via": "thread", "from": {"rpid": 222}}], run_id=RUN)
    wrong_root = call("收到", rpid=222, root=999)
    assert wrong_root.get("blocked") and "root" in wrong_root.get("reason", ""), wrong_root
    before = counter["value"]
    result = call("收到", rpid=222, root=0)
    assert result.get("success"), result
    book = ledger.read_ledger()
    assert any(row["rpid"] == before + 1 for row in book["sent"]), book
    thread = book["threads"].get(f"{BV}:111")
    assert thread and thread["turns"] == 1 and thread["lastTheirRpid"] == 222, book["threads"]
    # 同一轮第二条回复 -> 被每轮上限挡住
    again = call("再回一句", rpid=222, root=111)
    assert again.get("blocked") and "本轮最多回 1 条" in again.get("reason", ""), again

    # 6) 文本检查仍然生效
    reset()
    gate.write_reply_policy([], run_id=RUN)
    result = call("@owner 看 https://example.com")
    assert result.get("blocked") and "链接" in result.get("reason", ""), result

    # 7) 同一视频第二条顶层评论 -> 拒（即使换了新一轮）
    reset()
    live_run("round-1")
    gate.write_reply_policy([], run_id="round-1")
    first = call("@owner 第一条")
    assert first.get("success"), first
    live_run("round-2")
    gate.write_reply_policy([], run_id="round-2")
    second = call("@owner 第二条")
    assert second.get("blocked") and "同一视频" in second.get("reason", ""), second

    # 8) dmMode=only：机器层禁止公开评论
    reset()
    config.save_config({"dmMode": "only"})
    gate.write_reply_policy([], run_id=RUN, allow_new=True)
    only_dm = call("@owner 这条不该发")
    assert only_dm.get("blocked") and "只私信" in only_dm.get("reason", ""), only_dm
    config.save_config({"dmMode": "off"})

    print("reply wrapper OK")


if __name__ == "__main__":
    main()
