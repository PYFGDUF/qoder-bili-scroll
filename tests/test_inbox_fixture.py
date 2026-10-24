#!/usr/bin/env python3
"""收件箱 fixture 测试：不联网、不发言。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-scroll-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import api  # noqa: E402
import config  # noqa: E402
import gate  # noqa: E402
import inbox  # noqa: E402
import ledger  # noqa: E402

NOW = int(time.time())
ME = 100
OWNER = 200
OTHER = 999


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


def reply(rpid, mid, uname, text, age=10):
    return {
        "rpid": rpid,
        "root": 0,
        "parent": 0,
        "mid": mid,
        "uname": uname,
        "text": text,
        "ctime": NOW - age,
        "like": 0,
        "invisible": False,
    }


THREADS = {
    ("BV1xx411c7mD", 111): [  # 最后一句是本人 -> 应进名单
        reply(1001, ME, "me", "收到"),
        reply(1002, OWNER, "owner", "你好"),
        reply(1003, OWNER, "owner", "再聊一句", 5),
    ],
    ("BV1xx411c7mD", 222): [  # 最后一句是我们 -> 不进
        reply(2001, ME, "me", "我先说"),
    ],
    ("BV1xx411c7mD", 333): [  # 已到顶 -> 不进
        reply(3001, OWNER, "owner", "第五轮"),
    ],
    ("BV1xx411c7mD", 444): [  # 别人 -> 不进
        reply(4001, OTHER, "other", "别人在说话"),
    ],
    ("BV1xx411c7mD", 1500000001): [  # @ 起头 -> 进
        reply(1500000001, OWNER, "owner", "在别的视频 @ 你"),
    ],
    ("BV1yy411c7mD", 1600000001): [  # 另一条 @
        reply(1600000001, OWNER, "owner", "另一条 @"),
    ],
    ("BV1zz411c7mD", 1500000001): [  # 与 5001 同 rpid -> 去重
        reply(1500000001, OWNER, "owner", "重复的 @"),
    ],
}


def fake_thread(bvid, root, headers=None, cache=None, ps=20, pn=1):
    return {"ok": True, "replies": THREADS.get((bvid, int(root)), []), "total": 1}


def fake_at_feed(headers):
    return {
        "ok": True,
        "raw": 3,
        "items": [
            {
                "item": {
                    "uri": "https://www.bilibili.com/video/BV1xx411c7mD?comment_on=1#reply1500000001",
                    "title": "某个视频",
                    "subject_id": 123456789,
                },
                "user": {"mid": OWNER, "nickname": "owner"},
                "at_time": NOW - 20,
            },
            {
                "item": {
                    "uri": "https://www.bilibili.com/video/BV1xx411c7mD?comment_on=1#reply1400000001",
                    "title": "别人的 @",
                    "subject_id": 123456789,
                },
                "user": {"mid": OTHER, "nickname": "other"},
                "at_time": NOW - 20,
            },
            {
                "item": {
                    "uri": "https://www.bilibili.com/video/BV1yy411c7mD?comment_on=1#reply1600000001",
                    "title": "重复 @",
                    "subject_id": 123456789,
                },
                "user": {"mid": OWNER, "nickname": "owner"},
                "at_time": NOW - 20,
            },
            {
                "item": {
                    "uri": "https://www.bilibili.com/video/BV1zz411c7mD?comment_on=1#reply1500000001",
                    "title": "同一条 @ 被挂到另一个视频",
                    "subject_id": 123456789,
                },
                "user": {"mid": OWNER, "nickname": "owner"},
                "at_time": NOW - 20,
            },
        ],
    }


def main():
    parsed = api.parse_at_uri(
        "https://www.bilibili.com/video/BV1xx411c7mD?comment_on=1&comment_root_id=1400000001#reply1500000001"
    )
    assert parsed["root"] == 1400000001 and parsed["rpid"] == 1500000001, parsed
    config.save_config(
        {
            "dmMode": "off",
            "replyToComments": True,
            "replyOnlyMention": True,
            "mentionUid": OWNER,
            "mentionName": "owner",
            "replyPerRound": 8,
            "replyDepth": 5,
            "autoComment": False,
        }
    )
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "", "buvid3": "", "dedeuserid": str(ME)})
    ledger.write_ledger(
        {
            "sent": [
                {"bv": "BV1xx411c7mD", "rpid": 111, "root": 111, "text": "", "at": ""},
                {"bv": "BV1xx411c7mD", "rpid": 222, "root": 222, "text": "", "at": ""},
                {"bv": "BV1xx411c7mD", "rpid": 333, "root": 333, "text": "", "at": ""},
            ],
            "threads": {"BV1xx411c7mD:333": {"turns": 5, "lastTheirRpid": 0}},
            "owner": {},
            "me": {},
        }
    )
    config.SEEN_FILE.write_text(
        "# 判重表\n\n## 评论记录\n\n| BV | rpid | root | 时间 | 正文 |\n| --- | --- | --- | --- | --- |\n"
        "| BV1xx411c7mD | 444 | 444 | 2026-10-21 | old |\n",
        encoding="utf-8",
    )

    api.load_credential = lambda: {"sessdata": "x", "dedeuserid": str(ME)}
    api.headers_for = lambda cred: {}
    api.our_mid = lambda cred: ME
    api.own_account = lambda headers: {"mid": ME, "uname": "me_account"}
    api.fetch_thread = fake_thread
    api.fetch_at_feed = fake_at_feed
    api.bvid_of_aid = lambda aid, headers=None, cache=None: ""
    inbox.time.sleep = lambda seconds: None

    live_run("live-round")
    result = inbox.build_inbox(force=True, run_id="live-round")
    assert result["ok"], result
    items = result["items"]
    rpids = {item["from"]["rpid"] for item in items}
    assert rpids == {1003, 1500000001, 1600000001}, rpids
    assert all(item["from"]["uname"] == "owner" for item in items), items
    by_rpid = {item["from"]["rpid"]: item for item in items}
    assert by_rpid[1003]["via"] == "thread"
    assert by_rpid[1003]["turns"] == 1 and by_rpid[1003]["left"] == 4
    assert by_rpid[1500000001]["via"] == "at"
    assert by_rpid[1600000001]["via"] == "at"

    policy = json.loads(config.POLICY_FILE.read_text())
    assert policy["owner"]["mid"] == OWNER, policy
    assert len(policy["allow"]) == 3, policy
    assert policy["allowNewComment"] is False, policy
    assert gate.policy_fresh()[0] is True

    # 只看不发：view-only 不写策略、不授权发送，也不打断已有轮次
    config.save_config({"autoComment": True, "commentPerRound": 1})
    gate.write_reply_policy([], run_id="live-round", allow_new=True)
    before = config.POLICY_FILE.read_text()
    inbox.build_inbox(force=True, view_only=True)
    after = config.POLICY_FILE.read_text()
    assert before == after, (before, after)

    # 自动评论开 + 有昵称 + 有额度 -> allowNewComment
    config.save_config({"autoComment": True})
    gate.write_reply_policy(items)
    policy = json.loads(config.POLICY_FILE.read_text())
    assert policy["allowNewComment"] is True, policy

    # 只看最近 1 条时，仍要翻过“最新一条是我们说的”的串，找到更旧的本人留言
    narrow = inbox.build_inbox(force=True, max_items=1, include_at=False)
    assert narrow["ok"] and [item["from"]["rpid"] for item in narrow["items"]] == [1003], narrow

    # 扫描期间并发发送：build_inbox 的 owner/me 写回不能覆盖刚写入的 sent 行
    original_thread = api.fetch_thread
    inserted = {"done": False}

    def racing_thread(*args, **kwargs):
        if not inserted["done"]:
            ledger.note_comment_sent("BV1race411c7", 999999999, 111, "race", source="sent")
            inserted["done"] = True
        return original_thread(*args, **kwargs)

    api.fetch_thread = racing_thread
    ledger.update_fields(owner={}, me={})
    inbox.build_inbox(force=True, run_id="live-round")
    assert any(row.get("rpid") == 999999999 for row in ledger.read_ledger()["sent"])

    # 凭证缺 dedeuserid 时用 nav mid 兜底，仍能识别“最后一句是我们说的”
    original_our_mid = api.our_mid
    api.our_mid = lambda cred: 0
    fallback = inbox.build_inbox(force=True, run_id="live-round", max_items=8)
    assert fallback["ok"] and {item["from"]["rpid"] for item in fallback["items"]} == {
        1003,
        1500000001,
        1600000001,
    }, fallback
    api.our_mid = original_our_mid

    # 默认配置（replyToComments=false、mentionName 为空）下，view-only 也不能把所有人列出来
    config.save_config(
        {"mentionUid": 0, "replyToComments": False, "replyOnlyMention": True, "mentionName": ""}
    )
    view_guard = inbox.build_inbox(force=True, view_only=True)
    assert view_guard["ok"] is False and "mentionUid" in view_guard["error"], view_guard

    # mentionUid 恰好是插件登录的账号 -> 明确拒绝
    config.save_config(
        {"mentionUid": ME, "mentionName": "owner", "replyToComments": True, "replyOnlyMention": True}
    )
    same_account = inbox.build_inbox(force=True)
    assert same_account["ok"] is False and "mentionUid" in same_account["error"], same_account
    config.save_config({"mentionUid": 0})

    # 配置 mentionUid 时，UID 优先，昵称可以留空
    config.save_config(
        {"mentionUid": OWNER, "mentionName": "", "replyToComments": True, "replyOnlyMention": True}
    )
    by_uid = inbox.build_inbox(force=True)
    assert by_uid["ok"], by_uid
    assert by_uid["items"] and all(item["from"]["uname"] == "owner" for item in by_uid["items"]), by_uid

    # mentionUid 优先：昵称对不上也不能清掉配置的 UID
    config.save_config({"mentionUid": OWNER, "mentionName": "different-name"})
    by_uid2 = inbox.build_inbox(force=True)
    assert by_uid2["ok"] and by_uid2["items"], by_uid2
    assert all(item["from"]["uname"] == "owner" for item in by_uid2["items"]), by_uid2
    config.save_config({"mentionUid": 0, "mentionName": ""})

    # “只回本人”开着但没有显式 UID -> 明确拒绝，不能按昵称猜人
    config.save_config({"mentionUid": 0, "mentionName": "", "replyToComments": True, "replyOnlyMention": True})
    guarded = inbox.build_inbox(force=True)
    assert guarded["ok"] is False and "mentionUid" in guarded["error"], guarded

    # 只填昵称、不填 UID -> 同样拒绝（昵称不唯一）
    config.save_config({"mentionUid": 0, "mentionName": "owner", "replyToComments": True, "replyOnlyMention": True})
    name_only = inbox.build_inbox(force=True)
    assert name_only["ok"] is False and "mentionUid" in name_only["error"], name_only

    print("inbox fixture OK:", sorted(rpids))
    print("why:", result.get("why"))
    print("text head:", result["text"].splitlines()[:3])


if __name__ == "__main__":
    main()
