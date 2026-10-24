#!/usr/bin/env python3
"""私信回复机器闸 + 摘要发送准备。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-dm-reply-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import dm  # noqa: E402
import mcp_server_safe as core  # noqa: E402

RUN = "dm-run"
OWNER = 200
ME = 100


async def fake_send(receiver_uid, text):
    return json.dumps({"success": True, "message": "ok", "data": {"text": text}}, ensure_ascii=False)


def live_run():
    config.write_json(
        config.STATE_FILE,
        {
            "schemaVersion": 2,
            "day": config.day_key(),
            "count": 0,
            "runs": [
                {
                    "run_id": RUN,
                    "status": "running",
                    "startedAt": config.now_iso(),
                    "mode": "chat",
                    "quota": 0,
                }
            ],
        },
    )


def policy(keys, allow_send=False):
    return dm.write_policy(OWNER, RUN, allow_keys=keys, allow_send=allow_send, max_replies=1, max_per_day=3)


def call(key, text):
    return json.loads(asyncio.run(core.bili_dm_reply(key, text)))


def main():
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "j", "buvid3": "b", "dedeuserid": str(ME)})
    config.save_config(
        {"dmMode": "only", "dmReply": True, "mentionName": "owner", "mentionUid": OWNER, "perDay": 5}
    )
    core.UP.bili_send_message = fake_send

    live_run()
    policy(["k1"])
    result = call("k1", "收到，谢谢")
    assert result.get("success") and result.get("sent"), result
    current = dm.read_policy()
    assert current["allowKeys"] == [] and current["replyUsed"] == 1, current
    quota = json.loads((config.HOME / "dm-quota.json").read_text())
    assert quota["count"] == 1, quota

    # 同一条消息只回一次：第一次成功后 allowKeys 已核销
    again = call("k1", "再回一次")
    assert again.get("blocked") and "名单" in again.get("reason", ""), again
    assert dm.read_policy()["allowKeys"] == []

    # 名单外
    live_run()
    policy(["k2"])
    outside = call("k9", "你好")
    assert outside.get("blocked") and "名单" in outside.get("reason", ""), outside

    # 内部术语
    live_run()
    policy(["k3"])
    bad = call("k3", "token 是什么")
    assert bad.get("blocked") and "内部术语" in bad.get("reason", ""), bad

    # 超长
    live_run()
    policy(["k4"])
    long = call("k4", "好" * 120)
    assert long.get("blocked") and "超过" in long.get("reason", ""), long

    # dmMode=off 机器层拒绝
    config.save_config({"dmMode": "off"})
    live_run()
    policy(["k5"])
    off = call("k5", "你好")
    assert off.get("blocked") and "dmMode=off" in off.get("reason", ""), off

    print("dm reply gate OK")


if __name__ == "__main__":
    main()
