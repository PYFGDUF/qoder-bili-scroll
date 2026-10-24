#!/usr/bin/env python3
"""一轮结束时必须收回发言许可。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-round-end-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import gate  # noqa: E402
import mcp_server_safe as core  # noqa: E402
import state  # noqa: E402


def main():
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "", "buvid3": "", "dedeuserid": "1"})
    config.save_config({"autoComment": True, "mentionName": "owner", "perDay": 5, "dmMode": "off"})

    bogus = core._round_end_sync("bogus", "ok", "")
    assert bogus["ok"] is False and bogus["report"] is None, bogus

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    config.write_json(
        config.STATE_FILE,
        {
            "schemaVersion": 1,
            "day": yesterday,
            "count": 0,
            "runs": [
                {
                    "run_id": "midnight",
                    "status": "running",
                    "startedAt": f"{yesterday}T23:59:00+08:00",
                    "mode": "scan",
                    "quota": 1,
                }
            ],
        },
    )
    midnight = core._round_end_sync("midnight", "ok", "")
    assert midnight["ok"] is True, midnight
    assert Path(midnight["report"]["file"]).name == f"{yesterday}.md", midnight

    reservation = state.reserve_round("scan", "manual")
    gate.write_reply_policy([], reservation["run_id"], allow_new=True)
    assert gate.read_policy()["allowNewComment"] is True
    ok, why = gate.policy_fresh()
    assert ok, why

    # 没有对应 running run 的孤儿策略 -> 拒
    gate.write_reply_policy([], "orphan", allow_new=True)
    ok, why = gate.policy_fresh()
    assert not ok and "进行中" in why, (ok, why)

    gate.write_reply_policy([], reservation["run_id"], allow_new=True)
    core._round_end_sync(reservation["run_id"], "ok", "test")
    policy = gate.read_policy()
    assert policy["allow"] == [] and policy["allowNewComment"] is False, policy
    assert policy["runId"].endswith("-ended"), policy
    ok, why = gate.policy_fresh()
    assert not ok, (ok, why)

    print("round end policy reset OK")


if __name__ == "__main__":
    main()
