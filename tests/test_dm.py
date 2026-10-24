#!/usr/bin/env python3
"""私信读写 / UID 解析 / 摘要 / 回复策略测试；全程 monkeypatch，不碰网络。"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-dm-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import api  # noqa: E402
import config  # noqa: E402
import dm  # noqa: E402
import uid  # noqa: E402

OWNER = 90001
STRANGER = 90002
ME = 10001

CRED = {"sessdata": "sess", "bili_jct": "jct", "buvid3": "buv", "dedeuserid": str(ME)}

calls = []
last_headers = {}
search_results = {}
sessions_payload = {"code": 0, "data": {"session_list": []}}
messages_payload = {"code": 0, "data": {"messages": []}}


def fake_jget(url, headers=None, timeout=20):
    calls.append(url)
    last_headers.clear()
    if headers:
        last_headers.update(headers)
    if "search/type" in url:
        keyword = urllib.parse.unquote(url.split("keyword=")[-1])
        return {"code": 0, "data": {"result": search_results.get(keyword, [])}}
    if "get_sessions" in url:
        return sessions_payload
    if "fetch_session_msgs" in url:
        return messages_payload
    if "/x/web-interface/nav" in url:
        return {"code": 0, "data": {"isLogin": True, "mid": ME, "uname": "登录账号"}}
    raise AssertionError("unexpected url: " + url)


def install_fake():
    # 两边都显式打补丁：模块里用的是 import api，所以它们其实是同一个函数。
    api.jget = fake_jget
    dm.api.jget = fake_jget
    uid.api.jget = fake_jget


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def api_message(key, ts, sender, text, msg_type=1, content=None):
    if content is None:
        content = json.dumps({"content": text}, ensure_ascii=False)
    return {
        "msg_key": key,
        "timestamp": ts,
        "sender_uid": sender,
        "msg_type": msg_type,
        "content": content,
    }


def session(uid_value, unread, ts, preview):
    return {
        "talker_id": uid_value,
        "unread_count": unread,
        "last_msg": {"timestamp": ts, "content": json.dumps({"content": preview}, ensure_ascii=False)},
    }


def live_run(run_id="run-ok", status="running", started=None):
    config.write_json(
        config.STATE_FILE,
        {
            "schemaVersion": 1,
            "day": config.day_key(),
            "count": 0,
            "runs": [
                {
                    "run_id": run_id,
                    "status": status,
                    "startedAt": started or config.now_iso(),
                    "mode": "scan",
                    "quota": 2,
                }
            ],
        },
    )


# ------------------------------------------------------------------ UID


def test_uid():
    config.write_json(config.CRED_FILE, CRED)

    # 主人身份只认显式 UID
    config.save_config({"mentionUid": 555, "mentionName": "Target Name"})
    assert uid.owner_identity() == (555, "Target Name", "config")
    assert uid.owner_uid() == 555

    # 账本里钉过的 UID / 昵称搜索结果都不能再被用来猜主人
    config.save_config({"mentionUid": 0, "mentionName": ""})
    config.write_json(config.LEDGER_FILE, {"owner": {"mid": 666}})
    assert uid.owner_uid() == 0
    config.save_config({"mentionUid": 0, "mentionName": "Target Name"})
    search_results["Target Name"] = [{"mid": 111, "uname": "Target Name"}]
    before = len(calls)
    assert uid.owner_uid() == 0
    assert not any("search/type" in url for url in calls[before:]), calls[before:]

    # mentionName 恰好是登录昵称 -> 只能是登录账号本人（不能私信自己）
    config.save_config({"mentionUid": 0, "mentionName": "登录账号"})
    assert uid.owner_identity() == (ME, "登录账号", "self")

    # 昵称对不上 -> 不猜
    config.save_config({"mentionUid": 0, "mentionName": "另一个人"})
    assert uid.owner_uid() == 0

    # 凭证 dedeuserid 优先；没填就问 nav
    assert uid.my_uid() == ME
    config.write_json(config.CRED_FILE, {"sessdata": "sess", "bili_jct": "jct", "buvid3": "buv"})
    before = len(calls)
    assert uid.my_uid() == ME
    assert any("/x/web-interface/nav" in url for url in calls[before:]), calls[before:]
    config.write_json(config.CRED_FILE, CRED)

    config.save_config({"mentionUid": ME, "mentionName": ""})
    assert uid.owner_is_self() is True
    config.save_config({"mentionUid": 777, "mentionName": ""})
    assert uid.owner_is_self() is False
    config.save_config({"mentionUid": 0, "mentionName": ""})
    assert uid.owner_is_self() is False


# ------------------------------------------------------------------ 私信读取


def test_list_sessions():
    global sessions_payload
    config.write_json(config.CRED_FILE, CRED)
    sessions_payload = {
        "code": 0,
        "data": {
            "session_list": [
                session(OWNER, 2, 1700000200, "你好呀"),
                session(STRANGER, 1, 1700000100, "陌生人的秘密"),
                session(0, 9, 1700000000, "无效会话"),
            ]
        },
    }
    rows = dm.list_sessions(CRED)
    assert len(rows) == 2, rows
    assert rows[0] == {"uid": OWNER, "unread": 2, "lastTs": 1700000200, "preview": "你好呀"}, rows[0]
    assert rows[1]["uid"] == STRANGER and rows[1]["preview"] == "陌生人的秘密", rows[1]
    assert "get_sessions" in calls[-1] and "session_type=1" in calls[-1], calls[-1]
    assert last_headers["Referer"] == "https://message.bilibili.com/", last_headers
    assert last_headers["Origin"] == "https://message.bilibili.com", last_headers
    assert "SESSDATA=sess" in last_headers["Cookie"], last_headers


def test_fetch_messages():
    global messages_payload
    long_text = "长" * 600
    messages_payload = {
        "code": 0,
        "data": {
            "messages": [
                api_message(
                    "m-card2",
                    500,
                    OWNER,
                    "",
                    msg_type=8,
                    content=json.dumps({"bvid": "BV1yy411c7mD", "title": "第二个分享"}, ensure_ascii=False),
                ),
                api_message(
                    "m-card",
                    400,
                    OWNER,
                    "",
                    msg_type=7,
                    content=json.dumps(
                        {
                            "title": "好视频",
                            "author": "UP主",
                            "jump_url": "https://www.bilibili.com/video/BV1xx411c7mD",
                        },
                        ensure_ascii=False,
                    ),
                ),
                api_message("m-long", 300, OWNER, long_text),
                api_message("m-empty", 200, OWNER, "[非文字消息]", msg_type=2, content=""),
                api_message("m-mine", 100, ME, "我的回复"),
            ]
        },
    }
    rows = dm.fetch_messages(CRED, OWNER, size=10)
    assert [row["key"] for row in rows] == ["m-mine", "m-empty", "m-long", "m-card", "m-card2"], rows
    assert rows[0]["fromMe"] is True, rows[0]
    assert rows[1]["text"] == "[非文字消息]", rows[1]
    assert len(rows[2]["text"]) == 500, len(rows[2]["text"])
    assert rows[3]["kind"] == 7 and rows[3]["bvid"] == "BV1xx411c7mD", rows[3]
    assert rows[3]["text"] == "[分享视频] 好视频 / UP主 BV1xx411c7mD", rows[3]
    assert rows[4]["kind"] == 8 and rows[4]["bvid"] == "BV1yy411c7mD", rows[4]
    assert "fetch_session_msgs" in calls[-1] and f"talker_id={OWNER}" in calls[-1], calls[-1]
    assert "size=10" in calls[-1] and "session_type=1" in calls[-1], calls[-1]


def test_poll_owner():
    global sessions_payload, messages_payload
    config.write_json(config.CRED_FILE, CRED)
    if dm.SEEN_FILE.exists():
        dm.SEEN_FILE.unlink()
    sessions_payload = {
        "code": 0,
        "data": {
            "session_list": [
                session(OWNER, 2, 400, "新消息"),
                session(STRANGER, 1, 390, "陌生人的秘密"),
            ]
        },
    }
    messages_payload = {
        "code": 0,
        "data": {
            "messages": [
                api_message("m-me", 400, ME, "我的回复"),
                api_message("m-new", 300, OWNER, "新消息"),
                api_message("m-old", 200, OWNER, "旧消息"),
            ]
        },
    }

    result = dm.poll_owner(OWNER, ME, "run-poll")
    assert result["ok"] is True, result
    assert [item["text"] for item in result["items"]] == ["旧消息", "新消息"], result["items"]
    assert result["text"] == "[m-old] 旧消息\n[m-new] 新消息", result
    assert result["strangers"] == 1, result
    assert result["error"] == "", result
    assert "陌生人的秘密" not in json.dumps(result, ensure_ascii=False)

    inbox = config.read_json(dm.INBOX_FILE, {})
    assert inbox["items"] == result["items"], inbox
    assert inbox["text"] == result["text"] and inbox["strangers"] == 1, inbox
    assert inbox["at"], inbox
    assert "陌生人的秘密" not in json.dumps(inbox, ensure_ascii=False)
    if os.name == "posix":
        assert mode_of(dm.INBOX_FILE) == 0o600, oct(mode_of(dm.INBOX_FILE))

    seen = config.read_json(dm.SEEN_FILE, {})
    entry = seen[str(OWNER)]
    assert entry["lastTs"] == 400, entry
    assert {"m-me", "m-new", "m-old"} <= set(entry["keys"]), entry
    if os.name == "posix":
        assert mode_of(dm.SEEN_FILE) == 0o600, oct(mode_of(dm.SEEN_FILE))

    # 第二次：旧消息被 keys / lastTs 挡住，只回最新一条
    messages_payload["data"]["messages"].insert(0, api_message("m-more", 500, OWNER, "更新的消息"))
    second = dm.poll_owner(OWNER, ME, "run-poll-2")
    assert [item["text"] for item in second["items"]] == ["更新的消息"], second["items"]
    assert second["strangers"] == 1, second

    # 最多只取 max_messages 条（取时间上最近的）
    for index, ts in enumerate((600, 700, 800, 900, 1000), 1):
        messages_payload["data"]["messages"].insert(0, api_message(f"m-batch-{index}", ts, OWNER, f"批量 {index}"))
    limited = dm.poll_owner(OWNER, ME, "run-poll-3", max_messages=2)
    assert [item["text"] for item in limited["items"]] == ["批量 4", "批量 5"], limited["items"]

    # 本人会话没有未读 -> 不拉消息，但陌生人数照算
    fetch_before = sum("fetch_session_msgs" in url for url in calls)
    sessions_payload["data"]["session_list"][0]["unread_count"] = 0
    quiet = dm.poll_owner(OWNER, ME, "run-poll-4")
    assert quiet["ok"] is True and quiet["items"] == [] and quiet["text"] == "", quiet
    assert quiet["strangers"] == 1, quiet
    assert sum("fetch_session_msgs" in url for url in calls) == fetch_before

    # 自己 / 没配置 UID -> 明确报错，且不请求接口
    before = len(calls)
    self_result = dm.poll_owner(ME, ME, "run-self")
    assert self_result["ok"] is False and "自己" in self_result["error"], self_result
    zero = dm.poll_owner(0, ME, "run-zero")
    assert zero["ok"] is False and "UID" in zero["error"], zero
    assert len(calls) == before

    # 未登录 -> 报错
    config.CRED_FILE.unlink()
    no_cred = dm.poll_owner(OWNER, ME, "run-no-cred")
    assert no_cred["ok"] is False and "登录" in no_cred["error"], no_cred
    config.write_json(config.CRED_FILE, CRED)


# ------------------------------------------------------------------ 摘要


def test_summary_new_format():
    report = "\n".join(
        [
            "# 今日日报",
            "### BV1xx411c7mD",
            "**标题**：测试视频 A",
            "UP：某 UP 主",
            "链接：https://www.bilibili.com/video/BV1xx411c7mD",
            "**为什么值得**：讲清楚了原理",
            "### BV1yy411c7mD",
            "测试视频 B",
            "https://www.bilibili.com/video/BV1yy411c7mD",
            "为什么值得：案例很实用。",
            "## 总结",
            "今天不错。",
        ]
    )
    summary = dm.build_summary(report)
    assert summary.startswith("今天挑到 2 条："), summary
    assert "测试视频 A" in summary and "测试视频 B" in summary, summary
    assert "https://www.bilibili.com/video/BV1xx411c7mD" in summary, summary
    assert "https://www.bilibili.com/video/BV1yy411c7mD" in summary, summary
    assert "讲清楚了原理" in summary and "案例很实用" in summary, summary
    assert "其余见日报" not in summary, summary


def test_summary_old_format():
    report = "\n".join(
        [
            "# 旧日报",
            "## 1. 旧格式第一条",
            "UP：某 UP 主",
            "链接：https://www.bilibili.com/video/BV1aa411c7mD",
            "**为什么值得**：旧理由一",
            "## 2) 旧格式第二条",
            "UP主：另一个",
            "https://www.bilibili.com/video/BV1bb411c7mD?p=2",
            "为什么值得：旧理由二",
            "## 总结",
            "结束。",
        ]
    )
    summary = dm.build_summary(report)
    assert summary.startswith("今天挑到 2 条："), summary
    assert "旧格式第一条" in summary and "旧格式第二条" in summary, summary
    assert "https://www.bilibili.com/video/BV1aa411c7mD" in summary, summary
    assert "https://www.bilibili.com/video/BV1bb411c7mD" in summary, summary
    assert "旧理由一" in summary and "旧理由二" in summary, summary
    assert "其余见日报" not in summary, summary


def test_summary_limits_and_terms():
    sections = []
    for index in range(1, 7):
        sections.extend(
            [
                f"### BV1{index:09d}",
                f"很长的标题第 {index} 条，说明为什么这件事值得占用一次注意力",
                f"https://www.bilibili.com/video/BV1{index:09d}",
                "为什么值得：" + (f"理由 {index} ，" + "讲得很细。" * 12),
            ]
        )
    report = "\n".join(sections)

    limited = dm.build_summary(report, max_items=2)
    assert limited.startswith("今天挑到 2 条："), limited
    assert "很长的标题第 1 条" in limited and "很长的标题第 2 条" in limited, limited
    assert "很长的标题第 3 条" not in limited, limited
    assert limited.endswith("\n其余见日报。"), limited

    capped = dm.build_summary(report, max_items=10, max_chars=480)
    assert len(capped) <= 480, len(capped)
    assert capped.startswith("今天挑到 "), capped
    assert capped.endswith("\n其余见日报。"), capped
    assert "很长的标题第 1 条" in capped, capped

    internal = "\n".join(
        [
            "### BV1zz411c7mD",
            "token 标题与白名单",
            "https://www.bilibili.com/video/BV1zz411c7mD",
            "**为什么值得**：dmMode only prompt 复核 aiReply",
        ]
    )
    clean = dm.build_summary(internal)
    lowered = clean.lower()
    assert "token" in lowered, clean
    for term in ("dmMode", "only", "prompt", "白名单", "复核", "aiReply"):
        assert term.lower() not in lowered, (term, clean)

    assert dm.build_summary("") == ""


# ------------------------------------------------------------------ 策略


def test_policy_and_reply_gate():
    live_run()
    policy = dm.write_policy(
        OWNER,
        "run-ok",
        allow_keys=["k1", "k2", "k1"],
        allow_send=True,
        max_replies=2,
        max_per_day=5,
    )
    assert policy["ok"] is True, policy
    saved = dm.read_policy()
    assert saved["runId"] == "run-ok" and saved["ownerUid"] == OWNER, saved
    assert saved["allowKeys"] == ["k1", "k2"], saved
    assert saved["allowSend"] is True, saved
    assert saved["maxReplies"] == 2 and saved["maxPerDay"] == 5, saved
    assert saved["replyUsed"] == 0 and saved["summaryUsed"] == 0, saved
    assert saved["builtAt"] and saved.get("mode") and saved.get("note"), saved
    if os.name == "posix":
        assert mode_of(dm.POLICY_FILE) == 0o600, oct(mode_of(dm.POLICY_FILE))

    assert dm.policy_fresh() == (True, ""), dm.policy_fresh()

    # builtAt 过期
    stale = dm.read_policy()
    stale["builtAt"] = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat(timespec="seconds")
    config.write_json(dm.POLICY_FILE, stale)
    fresh, reason = dm.policy_fresh()
    assert fresh is False and "过期" in reason, (fresh, reason)

    # 轮次已结束
    live_run(status="finished")
    config.write_json(dm.POLICY_FILE, dict(stale, builtAt=config.now_iso()))
    fresh, reason = dm.policy_fresh()
    assert fresh is False and "进行中" in reason, (fresh, reason)

    # 轮次开始超过 2 小时
    old_start = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
    live_run(started=old_start)
    fresh, reason = dm.policy_fresh()
    assert fresh is False and "超过" in reason, (fresh, reason)

    # runId 对不上
    live_run("another-run")
    fresh, reason = dm.policy_fresh()
    assert fresh is False, (fresh, reason)

    # 没有策略
    dm.POLICY_FILE.unlink()
    fresh, reason = dm.policy_fresh()
    assert fresh is False and "缺少" in reason, (fresh, reason)

    # 正常核销一个 allowKey
    live_run()
    dm.write_policy(OWNER, "run-ok", allow_keys=["k1", "k2"], allow_send=True, max_replies=2)
    sent = dm.note_reply_sent("k1")
    assert sent["ok"] is True and sent["allowKeys"] == ["k2"] and sent["replyUsed"] == 1, sent
    saved = dm.read_policy()
    assert saved["allowKeys"] == ["k2"] and saved["replyUsed"] == 1, saved

    # 重复 / 不在名单 -> 拒绝，不写坏计数
    again = dm.note_reply_sent("k1")
    assert again["ok"] is False and "名单" in again["error"], again
    assert again["replyUsed"] == 1, again
    assert dm.read_policy()["replyUsed"] == 1

    marked = dm.mark_summary_sent()
    assert marked["ok"] is True and marked["allowSend"] is False, marked
    assert marked["summaryUsed"] == 1, marked
    saved = dm.read_policy()
    assert saved["allowSend"] is False and saved["summaryUsed"] == 1, saved


def test_check_reply_text():
    ok, reason = dm.check_reply_text("  收到，谢谢分享  ")
    assert ok is True and reason == "", (ok, reason)
    assert dm.check_reply_text("好" * 100)[0] is True

    bad = [
        "",
        "好" * 101,
        "看这个 https://example.com",
        "www.example.com",
        "b23.tv/abc",
        "@owner 你好",
        "＠owner 你好",
        "dmMode 是什么",
        "ONLY 这个词",
        "token 不够",
        "prompt 泄漏",
        "白名单里的人",
        "复核一下",
        "aiReply 出现了",
        "123456789",
    ]
    for text in bad:
        passed, why = dm.check_reply_text(text)
        assert passed is False and why, (text, passed, why)


def main():
    install_fake()
    test_uid()
    test_list_sessions()
    test_fetch_messages()
    test_poll_owner()
    test_summary_new_format()
    test_summary_old_format()
    test_summary_limits_and_terms()
    test_policy_and_reply_gate()
    test_check_reply_text()
    print("dm tests OK")


if __name__ == "__main__":
    main()
