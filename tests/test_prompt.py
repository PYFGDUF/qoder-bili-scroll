#!/usr/bin/env python3
"""prompt 组装：模式覆盖、日报关闭、perDay=0 与 commentPerRound=0。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-prompt-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import config  # noqa: E402
import gate  # noqa: E402
import mcp_server_safe as core  # noqa: E402
import prompt  # noqa: E402


def read(path):
    return Path(path).read_text(encoding="utf-8")


def main():
    config.save_config({"commentPerRound": 0, "mentionName": "owner", "autoComment": True, "dmMode": "off"})

    chat = prompt.build_prompt("run-chat", "chat", 0, 5, {"text": "（有一條在等）"})
    chat_text = read(chat["prompt_file"])
    assert "本模式只执行「私信 / 评论区回话」" in chat_text, chat_text[:800]
    assert "本模式不写" in chat_text
    assert "日报写到：" not in chat_text
    assert "本轮不允许新开顶层评论" in chat_text
    assert chat["report_file"] == "", chat

    config.save_config({"reportLevel": "off"})
    off = prompt.build_prompt("run-off", "scan", 2, 3, None)
    off_text = read(off["prompt_file"])
    assert "本档不写日报" in off_text
    assert "日报写到：" not in off_text
    assert off["report_file"] == "", off

    config.save_config(
        {"reportLevel": "full", "commentPerRound": 2, "autoComment": True, "mentionName": "owner", "perDay": 5}
    )
    full = prompt.build_prompt("run-full", "scan", 2, 3, None)
    full_text = read(full["prompt_file"])
    assert "日报写到：" in full_text
    assert "本轮最多 @ 2 条" in full_text
    assert "本模式覆盖规则" not in full_text
    assert full["report_file"], full

    # 人设：文字 / 文件路径
    config.save_config({"personaPrompt": "用极客口吻说话"})
    persona = prompt.build_prompt("run-persona", "scan", 2, 5, None)
    persona_text = read(persona["prompt_file"])
    assert "用极客口吻说话" in persona_text and "人设来源：personaPrompt" in persona_text
    persona_file = TMP / "persona.md"
    persona_file.write_text("你是测试人设", encoding="utf-8")
    config.save_config({"personaPrompt": str(persona_file)})
    persona2 = prompt.build_prompt("run-persona2", "scan", 2, 5, None)
    assert "你是测试人设" in read(persona2["prompt_file"])
    config.save_config({"personaPrompt": "", "workspace": ""})

    # 私信提示词：自动摘要、主人私信、报告格式、dmMode=only 禁公开评论
    config.save_config(
        {
            "dmMode": "only",
            "dmReply": True,
            "mentionName": "owner",
            "mentionUid": 123456,
            "autoComment": True,
            "commentPerRound": 2,
            "perDay": 5,
            "reportLevel": "full",
        }
    )
    dm = prompt.build_prompt(
        "run-dm",
        "scan",
        2,
        5,
        None,
        dm_inbox={"text": "主人：在吗", "items": [{"key": "k1"}]},
        dm_owner_uid=123456,
        dm_self=False,
    )
    dm_text = read(dm["prompt_file"])
    assert "私信投递：本轮结束后由插件自动" in dm_text
    assert "主人：在吗" in dm_text
    assert "bili_dm_reply" in dm_text
    assert "### BV号" in dm_text and "**档位**" in dm_text
    assert "不允许新开顶层评论" in dm_text

    dm_bad = prompt.build_prompt("run-dm2", "scan", 2, 5, None, dm_owner_uid=0, dm_self=False)
    dm_bad_text = read(dm_bad["prompt_file"])
    assert "私信投递：**不可用**" in dm_bad_text and "不发公开评论" in dm_bad_text

    config.save_config({"dmMode": "off", "dmReply": False})

    # perDay=0 时手动轮也不授权顶层 @，prompt 必须和机器闸一致
    config.write_json(config.CRED_FILE, {"sessdata": "x", "bili_jct": "", "buvid3": "", "dedeuserid": "1"})
    config.save_config({"perDay": 0, "perRound": 1, "autoComment": True, "mentionName": "owner", "dmMode": "off", "dmReply": False})
    result = core._round_begin_sync("scan", "manual")
    assert result["next"]["action"] == "run", result
    assert gate.read_policy()["allowNewComment"] is False, gate.read_policy()
    blocked_text = read(result["prompt_file"])
    assert "本轮不允许新开顶层评论" in blocked_text, blocked_text[:1200]

    # 额度用完的 stop 路径必须立刻作废旧策略
    config.save_config({"perDay": 1, "perRound": 1, "autoComment": True, "mentionName": "owner", "dmMode": "off", "dmReply": False})
    first = core._round_begin_sync("scan", "manual")
    assert first["next"]["action"] == "run", first
    second = core._round_begin_sync("scan", "manual")
    assert second["next"]["action"] == "stop", second
    policy = gate.read_policy()
    assert policy["allow"] == [] and policy["allowNewComment"] is False, policy
    assert gate.policy_fresh()[0] is False, gate.policy_fresh()

    print("prompt tests OK")


if __name__ == "__main__":
    main()
