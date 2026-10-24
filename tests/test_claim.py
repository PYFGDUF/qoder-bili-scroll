#!/usr/bin/env python3
"""认领旧评论：顶层可扫、根楼层未知的不扫，且不误伤同视频顶层评论。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-claim-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import gate  # noqa: E402
import ledger  # noqa: E402
import mcp_server_safe as core  # noqa: E402

BV_FOUND = "BV1xx411c7mD"
BV_UNKNOWN = "BV1zz411c7mD"
BV_SENT = "BV1yy411c7mD"


def live_run(run_id: str) -> None:
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
    return json.dumps({"success": True, "rpid": 423456789}, ensure_ascii=False)


def call(text, bvid):
    return json.loads(asyncio.run(core.bili_reply(bvid, text)))


def main():
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "", "buvid3": "", "dedeuserid": "1"})
    config.save_config({"autoComment": True, "mentionName": "owner", "perDay": 5, "commentPerRound": 1, "dmMode": "off"})
    ledger.video_meta = lambda bvid, headers, cache=None: {"aid": 1, "title": "t"}

    ledger.jget = lambda url, headers: {
        "code": 0,
        "data": {"replies": [{"rpid": 123456789, "member": {"mid": 1}, "content": {"message": "hi"}}]},
    }
    found = ledger.claim_comment(BV_FOUND, 123456789, {}, mine=1)
    assert found["verified"] == "yes", found
    assert ledger.read_ledger()["sent"][0]["from"] == "claim-top"

    ledger.jget = lambda url, headers: {"code": 0, "data": {"replies": []}}
    unknown = ledger.claim_comment(BV_UNKNOWN, 223456789, {}, mine=1)
    assert unknown["verified"] == "unknown", unknown
    assert any(row["rpid"] == 223456789 and row["from"] == "claim" for row in ledger.read_ledger()["sent"])

    core.UP.bili_reply = fake_reply
    core.report_mod.verify_comment = lambda bvid, rpid, root=0: {"ok": True, "visible": True}

    # claim-top 说明该视频已有顶层评论 -> 应拒
    live_run("r-found")
    gate.write_reply_policy([], run_id="r-found", allow_new=True)
    blocked = call("@owner 新评论", BV_FOUND)
    assert blocked.get("blocked") and "同一视频" in blocked.get("reason", ""), blocked

    # 根楼层未知的 claim 不能误判成顶层评论 -> 应放行
    live_run("r-unknown")
    gate.write_reply_policy([], run_id="r-unknown", allow_new=True)
    allowed = call("@owner 新评论", BV_UNKNOWN)
    assert allowed.get("success"), allowed

    # 真正发过的顶层评论仍然阻止同视频第二条
    ledger.note_comment_sent(BV_SENT, 323456789, 0, "@owner old", source="sent")
    live_run("r-sent")
    gate.write_reply_policy([], run_id="r-sent", allow_new=True)
    blocked = call("@owner 新评论", BV_SENT)
    assert blocked.get("blocked") and "同一视频" in blocked.get("reason", ""), blocked

    print("claim tests OK")


if __name__ == "__main__":
    main()
