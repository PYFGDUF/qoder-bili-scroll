#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import functools
import json
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from config import (
    CONFIG_FILE,
    CRED_FILE,
    DEFAULTS,
    HOME,
    LOG_FILE,
    POLICY_FILE,
    SEEN_FILE,
    coerce_value,
    ensure_home,
    load_config,
    log,
    norm_name,
    read_json,
    save_config,
    write_json,
)
from vendor_boot import PLUGIN_ROOT, load_vendor

UP = load_vendor()

from mcp.server.fastmcp import FastMCP  # noqa: E402

import api  # noqa: E402
import dm as dm_mod  # noqa: E402
import gate  # noqa: E402
import inbox as inbox_mod  # noqa: E402
import ledger  # noqa: E402
import login as login_mod  # noqa: E402
import notes as notes_mod  # noqa: E402
import prompt as prompt_mod  # noqa: E402
import report as report_mod  # noqa: E402
import seen as seen_mod  # noqa: E402
import state as state_mod  # noqa: E402
import uid as uid_mod  # noqa: E402

mcp = FastMCP("bili-scroll")

# 进程内的发送计数：策略 runId 变化即视为新一轮；初值从策略里的持久计数恢复。
_send_state = {"runId": "", "new": 0, "replies": 0}
_reply_lock = asyncio.Lock()


def _serialized(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        async with _reply_lock:
            return await func(*args, **kwargs)

    return wrapper


def _file_serialized(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        with gate.send_lock():
            return await func(*args, **kwargs)

    return wrapper


def _send_state_for(run_id: str, policy: dict) -> dict:
    if _send_state["runId"] != run_id:
        _send_state.update(
            {
                "runId": run_id,
                "new": int(policy.get("newUsed") or 0),
                "replies": int(policy.get("replyUsed") or 0),
            }
        )
    else:
        # 另一个 MCP 进程可能已经发过并持久化了计数；取最大值，避免多窗口各自超发。
        _send_state["new"] = max(_send_state["new"], int(policy.get("newUsed") or 0))
        _send_state["replies"] = max(_send_state["replies"], int(policy.get("replyUsed") or 0))
    return _send_state


def j(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _dep_version(name: str) -> str:
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return ""


def _which_uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    for candidate in (
        Path.home() / ".local/bin/uv",
        Path.home() / ".cargo/bin/uv",
        Path("/opt/homebrew/bin/uv"),
        Path("/usr/local/bin/uv"),
    ):
        if candidate.exists():
            return str(candidate)
    return ""


def _missing_deps() -> list:
    missing = []
    for module, package in (
        ("bilibili_api", "bilibili-api-python"),
        ("mcp", "mcp"),
        ("qrcode", "qrcode"),
        ("PIL", "Pillow"),
    ):
        try:
            __import__(module)
        except Exception:
            missing.append(package)
    return missing


def _env_info() -> dict:
    ensure_home()
    cfg = load_config()
    cred = api.load_credential()
    my_mid = api.our_mid(cred) if cred else 0
    owner_mid = int(uid_mod.owner_uid(cfg) or 0)
    dm_mode = str(cfg.get("dmMode") or "off")
    dm_effective = dm_mode
    if dm_mode != "off" and (owner_mid <= 0 or (my_mid and owner_mid == my_mid)):
        dm_effective = "off"
    missing = _missing_deps()
    return {
        "ok": True,
        "plugin_root": str(PLUGIN_ROOT),
        "data_dir": str(HOME),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "uv": _which_uv(),
        "ffmpeg": shutil.which("ffmpeg") or "",
        "deps": {
            "bilibili-api-python": _dep_version("bilibili-api-python"),
            "mcp": _dep_version("mcp"),
            "qrcode": _dep_version("qrcode"),
            "Pillow": _dep_version("Pillow"),
        },
        "missingDeps": missing,
        "logged_in": bool(cred),
        "account": {"mid": my_mid},
        "owner": {
            "mid": owner_mid,
            "name": str(cfg.get("mentionName") or ""),
            "self": bool(owner_mid and my_mid and owner_mid == my_mid),
        },
        "dm": {
            "mode": dm_mode,
            "effective": dm_effective,
            "reply": bool(cfg.get("dmReply")),
            "replyPerDay": int(cfg.get("dmReplyPerDay") or 0),
            "policy": read_json(HOME / "dm-policy.json", {}),
        },
        "files": {
            "config": str(CONFIG_FILE),
            "credential": str(CRED_FILE),
            "credential_exists": CRED_FILE.exists(),
            "seen": str(SEEN_FILE),
            "policy": str(POLICY_FILE),
            "dm_policy": str(HOME / "dm-policy.json"),
            "log": str(LOG_FILE),
        },
        "config": cfg,
    }


DM_QUOTA_FILE = HOME / "dm-quota.json"


def _clear_dm_policy(run_id: str) -> None:
    try:
        dm_mod.write_policy(0, f"{run_id or 'ended'}-ended", allow_keys=[], allow_send=False)
    except Exception as exc:
        log(f"清理 dm-policy 失败：{exc}")


def _dm_quota(cfg, increment: bool = False):
    today = datetime.now().strftime("%Y-%m-%d")
    data = read_json(DM_QUOTA_FILE, {})
    used = int(data.get("count") or 0) if data.get("day") == today else 0
    limit = max(0, int(cfg.get("dmReplyPerDay") or 0))
    if limit > 0 and used >= limit:
        return False, used, limit
    if increment:
        write_json(DM_QUOTA_FILE, {"day": today, "count": used + 1})
    return True, used, limit


def _round_begin_sync(mode: str, source: str) -> dict:
    cfg = load_config()
    cred = api.load_credential()
    if not cred:
        gate.write_reply_policy([], "", allow_new=False)
        _clear_dm_policy("")
        return {
            "ok": False,
            "next": {"action": "stop", "reason": "还没扫码登录。先运行 /bili-login。"},
        }
    reservation = state_mod.reserve_round(mode=mode, source=source)
    if reservation.get("action") != "run":
        # 额度用完/未开跑：立刻作废旧轮的发言许可，避免崩溃轮的策略继续放行。
        gate.write_reply_policy([], "quota-stop", allow_new=False)
        _clear_dm_policy("quota-stop")
        return {
            "ok": True,
            "run_id": "",
            "next": {"action": "stop", "reason": reservation.get("reason", "本轮不跑")},
        }

    run_id = reservation["run_id"]
    dm_mode = str(cfg.get("dmMode") or "off")
    owner_uid = 0
    my_uid = 0
    if dm_mode != "off":
        try:
            owner_uid = int(uid_mod.owner_uid(cfg) or 0)
        except Exception as exc:
            log(f"解析主人 UID 失败：{exc}")
        try:
            my_uid = int(uid_mod.my_uid(cred) or 0)
        except Exception as exc:
            log(f"读取登录账号 UID 失败：{exc}")
    dm_self = bool(owner_uid and my_uid and owner_uid == my_uid)
    dm_effective = dm_mode
    if dm_mode != "off" and (owner_uid <= 0 or dm_self):
        dm_effective = "off"
        log(
            f"私信降级为 off：owner_uid={owner_uid} my_uid={my_uid}"
            + ("（登录号就是主人，不能私信自己）" if dm_self else "（没认出主人）")
        )

    dm_inbox = None
    if dm_mode != "off" and cfg.get("dmReply") and dm_effective != "off":
        try:
            dm_inbox = dm_mod.poll_owner(
                owner_uid,
                my_uid,
                run_id,
                max_messages=max(1, int(cfg.get("dmReplyPerRound") or 1)),
            )
        except Exception as exc:
            dm_inbox = {"ok": False, "items": [], "text": "", "error": str(exc)}
            log(f"私信收件箱失败：{exc}")

    dm_items = (dm_inbox or {}).get("items") or []
    allow_keys = [str(item.get("key")) for item in dm_items if item.get("key")] if cfg.get("dmReply") else []
    allow_send = bool(
        mode == "scan"
        and dm_effective != "off"
        and owner_uid > 0
        and not dm_self
        and str(cfg.get("reportLevel") or "full") != "off"
    )
    try:
        dm_mod.write_policy(
            owner_uid,
            run_id,
            allow_keys=allow_keys,
            allow_send=allow_send,
            max_replies=max(1, int(cfg.get("dmReplyPerRound") or 1)),
            max_per_day=max(0, int(cfg.get("dmReplyPerDay") or 0)),
        )
    except Exception as exc:
        log(f"写 dm-policy 失败：{exc}")

    inbox_result = None
    # perDay=0 表示自动轮关闭；dmMode=only 时机器层禁止公开评论。
    can_comment_new = (
        int(cfg.get("perDay") or 0) > 0
        and reservation["quota"] > 0
        and dm_mode != "only"
    )

    if mode == "chat":
        if cfg.get("replyToComments"):
            try:
                inbox_result = inbox_mod.build_inbox(
                    force=True, run_id=run_id, allow_new_comment=False
                )
            except Exception as exc:
                inbox_result = {"ok": False, "items": [], "text": "", "error": str(exc)}
            if not inbox_result.get("ok"):
                log(f"评论区收件箱失败（私信仍可回）：{inbox_result.get('error')}")
        if not dm_items and not (inbox_result and inbox_result.get("items")):
            reason = "现在没有人在等你回话。"
            if not cfg.get("replyToComments") and not cfg.get("dmReply"):
                reason = "私信回复和评论区回话都关着；没有可回的内容。"
            elif dm_inbox and dm_inbox.get("error"):
                reason = f"没有可回的内容（私信没读到：{dm_inbox['error']}）"
            state_mod.finish_round(run_id, "skipped", reason)
            gate.write_reply_policy([], run_id, allow_new=False)
            _clear_dm_policy(run_id)
            return {
                "ok": True,
                "run_id": run_id,
                "dm": {"waiting": 0, "error": (dm_inbox or {}).get("error", "")},
                "next": {"action": "stop", "reason": reason},
            }
    elif cfg.get("replyToComments"):
        inbox_result = inbox_mod.build_inbox(
            force=False,
            run_id=run_id,
            allow_new_comment=can_comment_new,
        )
        if not inbox_result.get("ok"):
            log(f"收件箱没拉到（这一轮继续刷视频）：{inbox_result.get('error')}")
            gate.write_reply_policy([], run_id, allow_new=can_comment_new)
    else:
        gate.write_reply_policy([], run_id, allow_new=can_comment_new)

    prompt_info = prompt_mod.build_prompt(
        run_id=run_id,
        mode=mode,
        quota=reservation["quota"],
        left_today=reservation["leftToday"],
        inbox=inbox_result,
        dm_inbox=dm_inbox,
        dm_owner_uid=owner_uid,
        dm_self=dm_self,
    )
    return {
        "ok": True,
        "run_id": run_id,
        "mode": mode,
        "source": source,
        "quota": reservation["quota"],
        "leftToday": reservation["leftToday"],
        "prompt_file": prompt_info["prompt_file"],
        "report_file": prompt_info["report_file"],
        "inbox": (
            {
                "waiting": len(inbox_result.get("items") or []),
                "error": inbox_result.get("error", ""),
            }
            if inbox_result
            else {"waiting": 0, "error": ""}
        ),
        "dm": {
            "mode": dm_mode,
            "effective": dm_effective,
            "ownerUid": owner_uid,
            "self": dm_self,
            "waiting": len(dm_items),
            "error": (dm_inbox or {}).get("error", ""),
        },
        "next": {"action": "run"},
    }


def _round_end_sync(run_id: str, status: str, summary: str) -> dict:
    run_id = str(run_id or "")
    state = state_mod.load_state()
    run = next((row for row in state.get("runs") or [] if row.get("run_id") == run_id), None)
    if not run:
        # 未知/空 run_id 不造日报、不改状态；只把发言许可收回。
        gate.write_reply_policy([], f"{run_id or 'unknown'}-ended", allow_new=False)
        _clear_dm_policy(run_id or "unknown")
        return {
            "ok": False,
            "error": "run_id 不存在（可能已过期或写错）；没有写日报",
            "run_id": run_id,
            "report": None,
        }

    report_result = None
    notes_result = None
    dm_send = None
    day = None
    try:
        day = datetime.fromisoformat(str(run.get("startedAt"))).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        day = None

    if run.get("mode") != "chat":
        report_result = report_mod.ensure_report(day=day, run_id=run_id, status=status)
        report_file = report_result.get("file") if isinstance(report_result, dict) else None
        if report_file:
            cfg = load_config()
            notes_info = {}
            if bool(cfg.get("videoNotes", True)):
                try:
                    notes_info["notes"] = notes_mod.sync_from_report(Path(report_file), day)
                except Exception as exc:
                    log(f"生成单条笔记失败：{exc}")
            try:
                rows = seen_mod.list_rows(limit=5000).get("rows") or []
                notes_info["digest"] = notes_mod.digest_from_seen(rows, (day or datetime.now().strftime("%Y-%m-%d"))[:7])
            except Exception as exc:
                log(f"生成 digest 失败：{exc}")
            notes_result = notes_info or None

        # 私信摘要在 run 结束前组装；真正发送由 async bili_round_end 做。
        if status == "ok" and report_file:
            try:
                ok, why = dm_mod.policy_fresh()
                if ok:
                    policy = dm_mod.read_policy()
                    if policy.get("allowSend") and int(policy.get("summaryUsed") or 0) == 0:
                        text = dm_mod.build_summary(Path(report_file).read_text(encoding="utf-8"))
                        if text:
                            dm_send = {
                                "ownerUid": int(policy.get("ownerUid") or 0),
                                "text": text,
                            }
                else:
                    log(f"私信摘要不发送：{why}")
            except Exception as exc:
                log(f"组装私信摘要失败：{exc}")

    finished = state_mod.finish_round(run_id, status, summary)
    gate.write_reply_policy([], f"{run_id}-ended", allow_new=False)
    return {
        "ok": True,
        **finished,
        "report": report_result,
        "notes": notes_result,
        "dm_send": dm_send,
    }


async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


# ---------------------------------------------------------------------------
# B站只读工具（白名单）
# ---------------------------------------------------------------------------


@mcp.tool()
async def bili_check_credential() -> str:
    """检查 B 站登录是否有效，返回当前账号信息。"""
    return await UP.bili_check_credential()


@mcp.tool()
async def bili_search(keyword: str, num: int = 10, order: str = "totalrank") -> str:
    """搜索 B 站视频。num 最多 50；order：totalrank/click/pubdate/dm。"""
    return await UP.bili_search(keyword, num, order)


@mcp.tool()
async def bili_subtitle(bvid: str) -> str:
    """读取视频字幕原文；没有字幕时返回明确的 no_subtitle 结果。"""
    raw = await UP.bili_subtitle(bvid)
    try:
        data = json.loads(raw)
    except Exception:
        return raw
    if data.get("message") == "该视频没有字幕":
        return j({"ok": False, "code": "no_subtitle", "bvid": bvid, "message": "该视频没有字幕"})
    return raw


@mcp.tool()
async def bili_comments(bvid: str, num: int = 30) -> str:
    """读取视频评论（含少量楼中楼）。"""
    return await UP.bili_comments(bvid, num)


@mcp.tool()
async def bili_video_info(bvid: str) -> str:
    """读取视频详情（标题、UP、播放/评论/收藏等）。"""
    return await UP.bili_video_info(bvid)


@mcp.tool()
async def bili_received_at_and_likes() -> str:
    """读取最近收到的 @ 和点赞通知（只读）。"""
    return await UP.bili_received_at_and_likes()


@mcp.tool()
async def bili_received_replies() -> str:
    """读取最近收到的评论回复通知（只读）。"""
    return await UP.bili_received_replies()


@mcp.tool()
async def bili_unread_messages() -> str:
    """读取未读消息计数（只读）。"""
    return await UP.bili_unread_messages()


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------


@mcp.tool()
async def bili_login_start(force: bool = False) -> str:
    """生成 B 站扫码登录二维码，返回 qr_file（PNG 路径）与二维码文本文件。

    已登录时默认返回 already_logged_in；要换号请传 force=true。
    """
    return j(await login_mod.start_login(force=force))


@mcp.tool()
async def bili_login_poll() -> str:
    """轮询扫码状态；成功后保存凭证并返回账号昵称。"""
    return j(await login_mod.poll_login())


@mcp.tool()
async def bili_logout(confirm: bool = False) -> str:
    """退出登录：删除本地凭证、清空账号缓存与发言许可。需要 confirm=true。"""
    if not confirm:
        return j(
            {
                "ok": False,
                "needs_confirm": True,
                "error": "退出登录会删除本地凭证；先向用户确认，再带 confirm=true 重试。",
            }
        )
    return j(await _to_thread(login_mod.logout))


@mcp.tool()
async def bili_whoami() -> str:
    """返回当前登录账号的 mid / 昵称（只读）。"""
    cred = api.load_credential()
    if not cred:
        return j({"ok": False, "logged_in": False, "message": "未登录，先运行 /bili-login"})
    me = await _to_thread(api.own_account, api.headers_for(cred))
    if not me:
        return j({"ok": False, "logged_in": False, "message": "凭证可能已过期；请重新 /bili-login。"})
    return j({"ok": True, "logged_in": True, "me": me, "credential_file": str(CRED_FILE)})


# ---------------------------------------------------------------------------
# 一轮的编排
# ---------------------------------------------------------------------------


@mcp.tool()
async def bili_round_begin(mode: str = "scan", source: str = "manual") -> str:
    """开始一轮：预留额度、刷新收件箱与回复策略、生成 prompt 文件。

    mode=scan 刷视频+写日报；mode=chat 只回话（不搜视频、不写日报）。
    返回 next.action=run 时，读取 prompt_file 并严格按其中章程执行。
    """
    if mode not in ("scan", "chat"):
        gate.write_reply_policy([], "invalid-request", allow_new=False)
        return j({"ok": False, "next": {"action": "stop", "reason": "mode 只能是 scan 或 chat"}})
    if source not in ("cron", "manual"):
        gate.write_reply_policy([], "invalid-request", allow_new=False)
        return j({"ok": False, "next": {"action": "stop", "reason": "source 只能是 cron 或 manual"}})
    result = await _to_thread(_round_begin_sync, mode, source)
    return j(result)


@mcp.tool()
async def bili_round_end(run_id: str, status: str = "ok", summary: str = "") -> str:
    """结束一轮：记录状态、写日报/笔记；私信摘要由插件在这里自动发送。status 用 ok 或 failed。"""
    if status not in ("ok", "failed", "skipped"):
        status = "failed"
    result = await _to_thread(_round_end_sync, run_id, status, summary)
    dm_send = result.pop("dm_send", None)
    dm_result = None
    if dm_send:
        owner = int(dm_send.get("ownerUid") or 0)
        text = str(dm_send.get("text") or "")
        if owner > 0 and text:
            try:
                raw = await UP.bili_send_message(owner, text)
                data = json.loads(raw) if isinstance(raw, str) else dict(raw)
                success = bool(data.get("success"))
                dm_result = {"sent": success, "chars": len(text)}
                if success:
                    try:
                        dm_mod.mark_summary_sent()
                    except Exception as exc:
                        log(f"标记私信摘要已发失败：{exc}")
                    log(f"私信摘要已发送：uid={owner} chars={len(text)}")
                else:
                    dm_result["error"] = data.get("error") or data.get("message") or "发送失败"
                    log(f"私信摘要发送失败：{dm_result['error']}")
            except Exception as exc:
                dm_result = {"sent": False, "error": str(exc)}
                log(f"私信摘要发送异常：{exc}")
    if dm_result:
        result["dm"] = dm_result
    try:
        dm_mod.write_policy(0, f"{run_id or 'unknown'}-ended", allow_keys=[], allow_send=False)
    except Exception as exc:
        log(f"结束轮次时清理 dm-policy 失败：{exc}")
    return j(result)


@mcp.tool()
async def bili_inbox() -> str:
    """计算并返回「谁在等我回话」。只读名单，不会发任何评论，也不会授权发送。"""
    run_id = f"inbox-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    result = await _to_thread(
        inbox_mod.build_inbox,
        force=True,
        view_only=True,
        allow_new_comment=False,
        run_id=run_id,
    )
    return j(result)


@mcp.tool()
@_serialized
@_file_serialized
async def bili_dm_reply(message_key: str, text: str) -> str:
    """回复主人的一条私信。只能回本轮名单里的 message_key；每条 ≤100 字。"""
    cfg = load_config()
    if str(cfg.get("dmMode") or "off") == "off":
        return j({"success": False, "blocked": True, "reason": "私信投递/回复关着（dmMode=off）"})
    if not cfg.get("dmReply"):
        return j({"success": False, "blocked": True, "reason": "私信回复关着（dmReply=false）"})
    ok, why = dm_mod.policy_fresh()
    if not ok:
        return j({"success": False, "blocked": True, "reason": why})
    policy = dm_mod.read_policy()
    key = str(message_key or "").strip()
    if key not in [str(x) for x in policy.get("allowKeys") or []]:
        return j({"success": False, "blocked": True, "reason": "这条 message_key 不在本轮私信名单里；不要回名单外的人"})
    owner = int(policy.get("ownerUid") or 0)
    cred = api.load_credential()
    if not cred:
        return j({"success": False, "blocked": True, "reason": "未登录"})
    if owner <= 0:
        return j({"success": False, "blocked": True, "reason": "没认出主人 UID"})
    try:
        mine = int(uid_mod.my_uid(cred) or 0)
    except Exception:
        mine = 0
    if mine and owner == mine:
        return j({"success": False, "blocked": True, "reason": "主人 UID 就是登录账号，不能私信自己"})
    ok, why = dm_mod.check_reply_text(text, 100)
    if not ok:
        return j({"success": False, "blocked": True, "reason": why})
    ok, used, limit = _dm_quota(cfg)
    if not ok:
        return j({"success": False, "blocked": True, "reason": f"今天私信回复已达上限（{used}/{limit}）"})

    raw = await UP.bili_send_message(owner, str(text).strip())
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        data = {"success": False, "raw": str(raw)}
    if data.get("success"):
        try:
            dm_mod.note_reply_sent(key)
        except Exception as exc:
            log(f"私信回复记账失败：{exc}")
        _dm_quota(cfg, increment=True)
        data["sent"] = True
        data["ownerUid"] = owner
        log(f"已回私信：uid={owner} key={key} chars={len(str(text))}")
    else:
        data["sent"] = False
        log(f"私信回复失败：{data.get('error') or data.get('message') or '未知'}")
    return j(data)


def _config_value_error(key: str, value):
    if key == "perDay" and value < 0:
        return "perDay 不能小于 0"
    if key in ("perRound", "replyPerRound", "replyDepth") and value < 1:
        return f"{key} 至少是 1"
    if key == "commentPerRound" and value < 0:
        return "commentPerRound 不能小于 0（0 = 一条都不 @）"
    if key == "mentionUid" and value < 0:
        return "mentionUid 必须是非负整数（0 = 未指定，私信和「只回本人」不可用）"
    if key == "dmMode" and value not in ("off", "only", "both"):
        return "dmMode 只能是 off / only / both"
    if key in ("dmReplyPerRound",) and value < 1:
        return f"{key} 至少是 1"
    if key == "dmReplyPerDay" and value < 0:
        return "dmReplyPerDay 不能小于 0（0 = 不限）"
    if key == "reportLevel" and value not in ("full", "brief", "off"):
        return "reportLevel 只能是 full / brief / off"
    if key == "dailyRunTime" and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", str(value)):
        return "dailyRunTime 需要 HH:MM"
    if key == "timezone":
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(str(value))
        except Exception:
            return "timezone 需要合法的 IANA 时区，如 Asia/Shanghai"
    return ""


def _config_needs_confirm(key: str, value) -> bool:
    if key in ("autoComment", "replyToComments"):
        return bool(value)
    if key == "replyOnlyMention":
        return value is False
    if key == "dmMode":
        return str(value) != "off"
    if key == "dmReply":
        return bool(value)
    return False


@mcp.tool()
async def bili_config(action: str = "list", key: str = "", value: str = "", confirm: bool = False) -> str:
    """读取或修改插件配置。action=list/get/set。

    开启 autoComment / replyToComments，或关闭 replyOnlyMention，
    都属于“拿你的账号说话”，必须由用户明确确认后，调用时带 confirm=true。
    """
    cfg = load_config()
    action = str(action or "list").lower()
    if action == "list":
        return j({"ok": True, "config": cfg, "data_dir": str(HOME), "config_file": str(CONFIG_FILE)})
    if action == "get":
        if key not in DEFAULTS:
            return j({"ok": False, "error": f"没有这个配置项：{key}"})
        return j({"ok": True, "key": key, "value": cfg.get(key)})
    if action != "set":
        return j({"ok": False, "error": "action 只能是 list/get/set"})
    if key not in DEFAULTS or key == "schemaVersion":
        return j({"ok": False, "error": f"不允许修改：{key}"})
    try:
        new_value = coerce_value(key, value, cfg.get(key))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return j({"ok": False, "error": f"值不合法：{exc}"})
    invalid = _config_value_error(key, new_value)
    if invalid:
        return j({"ok": False, "error": invalid})
    if _config_needs_confirm(key, new_value) and not confirm:
        return j(
            {
                "ok": False,
                "needs_confirm": True,
                "error": f"修改 {key} 会改变“用你的 B 站账号说话”的边界。先向用户明确确认，再带 confirm=true 重试。",
            }
        )
    warnings = []
    if key == "autoComment" and new_value and not str(cfg.get("mentionName") or "").strip():
        warnings.append("autoComment 开着但 mentionName 为空：不会发评论。先设置 mentionName。")
    if key == "autoComment" and new_value and int(cfg.get("commentPerRound") or 0) == 0:
        warnings.append("commentPerRound=0：即使开着自动评论，也不会 @ 任何人。")
    if key == "commentPerRound" and int(new_value or 0) == 0 and cfg.get("autoComment"):
        warnings.append("commentPerRound=0：即使开着自动评论，也不会 @ 任何人。")
    if key == "mentionName" and not str(new_value or "").strip() and cfg.get("autoComment"):
        warnings.append("mentionName 被清空，自动评论会停。")
    if key == "replyOnlyMention" and new_value is False:
        warnings.append("已关闭「只回本人」：名单里出现的其他人也可能被回复。")
    if key == "dmMode" and str(new_value) == "only" and cfg.get("autoComment"):
        warnings.append("dmMode=only 时即使 autoComment 开着，也不会发公开评论。")
    if key == "dmMode" and str(new_value) != "off" and int(cfg.get("mentionUid") or 0) <= 0:
        warnings.append("dmMode 不是 off，但 mentionUid=0：昵称不唯一，插件不猜人，私信不会发。")
    if key == "dmMode" and str(new_value) != "off" and str(cfg.get("reportLevel") or "full") == "off":
        warnings.append("reportLevel=off 时没有报告可组装私信摘要；私信不会发。")
    cfg = save_config({key: new_value})
    if key in ("mentionName", "mentionUid", "replyOnlyMention"):
        book = ledger.read_ledger()
        book["owner"] = {}
        ledger.write_ledger(book)
    # 任何配置改动都立刻作废旧的发言许可；下一次发言必须重新开一轮/刷新名单。
    gate.write_reply_policy([], run_id="config-change", allow_new=False)
    try:
        import dm as dm_mod

        dm_mod.write_policy(0, "config-change", allow_keys=[], allow_send=False)
    except Exception:
        pass
    return j({"ok": True, "key": key, "value": cfg.get(key), "config": cfg, "warnings": warnings})


@mcp.tool()
async def bili_seen(
    action: str = "check",
    bvid: str = "",
    title: str = "",
    verdict: str = "",
    reason: str = "",
    up: str = "",
    status: str = "",
    limit: int = 50,
) -> str:
    """判重表：action=add/check/list。add 会把 BV 写进 seen.md；list 支持 limit。"""
    action = str(action or "check").lower()
    if action == "add":
        return j(seen_mod.add(bvid, title, verdict, reason, up=up, status=status))
    if action == "check":
        return j(seen_mod.check(bvid))
    if action == "list":
        return j(seen_mod.list_rows(limit=limit))
    return j({"ok": False, "error": "action 只能是 add/check/list"})


@mcp.tool()
async def bili_report(action: str = "show", day: str = "") -> str:
    """日报：action=show/list/ensure/notes。notes 会把日报拆成 reports/<BV>-<日期>.md。"""
    action = str(action or "show").lower()
    if action == "show":
        return j(report_mod.show_report(day or None))
    if action == "list":
        return j({"ok": True, "reports": report_mod.list_reports()})
    if action == "ensure":
        return j(report_mod.ensure_report(day or None))
    if action == "notes":
        target = report_mod.report_path(day or None)
        if not target.exists():
            return j({"ok": False, "error": f"没有 {target.name} 的报告"})
        result = await _to_thread(notes_mod.sync_from_report, target, target.stem)
        return j(result)
    return j({"ok": False, "error": "action 只能是 show/list/ensure/notes"})


@mcp.tool()
async def bili_verify(bvid: str, rpid: int, root: int = 0) -> str:
    """复核一条评论是否公开可见。看不到不等于没发出去。"""
    return j(await _to_thread(report_mod.verify_comment, bvid, rpid, root))


@mcp.tool()
async def bili_claim_comment(bvid: str, rpid: int) -> str:
    """认领一条旧版本发过、账本里没有的评论（B站 App 的评论链接里的 BV + rpid）。"""
    cred = api.load_credential()
    if not cred:
        return j({"ok": False, "error": "未登录"})
    result = await _to_thread(
        ledger.claim_comment,
        bvid,
        rpid,
        api.headers_for(cred),
        api.our_mid(cred),
        {},
    )
    return j(result)


@mcp.tool()
async def bili_env_check() -> str:
    """检查运行环境、依赖、凭证与配置（只读）。"""
    return j(await _to_thread(_env_info))


# ---------------------------------------------------------------------------
# 发言（唯一的写操作）
# ---------------------------------------------------------------------------


@mcp.tool()
@_serialized
@_file_serialized
async def bili_reply(bvid: str, text: str, rpid: int = 0, root: int = 0) -> str:
    """发评论或回复评论。rpid=0 表示发顶层评论（必须允许 @）；rpid>0 表示回复。

    发之前有两道机器闸：策略必须新鲜；目标评论作者必须是配置里的本人。
    被拒绝时不会发出任何字，也不要重试。
    """
    cfg = load_config()
    allow_mention = str(cfg.get("mentionName") or "") if int(rpid or 0) == 0 else ""
    ok, why = gate.check_text(text, allow_mention)
    if not ok:
        log(f"拒发（文本检查）：{why}")
        return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})

    if int(rpid or 0) == 0:
        if str(cfg.get("dmMode") or "off") == "only":
            why = "当前是「只私信」模式（dmMode=only）—— 这一轮不发公开评论"
            log(f"拒发（只私信模式）：{why}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})
        name = str(cfg.get("mentionName") or "").strip()
        body = str(text or "")
        if name and (("@" not in body and "＠" not in body) or (norm_name(name) not in norm_name(body))):
            why = "顶层评论必须 @ 配置里的使用者（mentionName）"
            log(f"拒发（顶层评论未 @ 本人）：{why}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})

    ok, why = gate.policy_fresh()
    if not ok:
        log(f"拒发（策略）：{why}")
        return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})

    policy = gate.read_policy()
    target = int(rpid or 0)
    send_state = _send_state_for(str(policy.get("runId") or ""), policy)
    if target == 0:
        if not policy.get("allowNewComment"):
            why = "配置没有允许发新的顶层评论（autoComment 关着 / 没填 mentionName / 今天额度用完）"
            log(f"拒发（顶层评论策略）：{why}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})
        max_new = int(policy.get("maxNewComments") or 0)
        if send_state["new"] >= max_new:
            why = f"本轮最多发 {max_new} 条新评论，已经用完了"
            log(f"拒发（本轮新评论上限）：{why}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})
        if any(
            str(row.get("bv") or "") == str(bvid or "")
            and int(row.get("root") or 0) == 0
            and row.get("from") != "claim"
            for row in (ledger.read_ledger().get("sent") or [])
        ):
            why = "这个视频已经有一条顶层评论了；同一视频只评论一次"
            log(f"拒发（同视频重复评论）：{bvid}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})
    else:
        entry = next(
            (
                row
                for row in (policy.get("allow") or [])
                if isinstance(row, dict) and int(row.get("rpid") or 0) == target
            ),
            None,
        )
        if entry is None:
            why = "这条不在本轮「谁在等我回话」名单里；先开一轮 chat（/bili-inbox 只读，不授权发送），按名单回"
            log(f"拒发（不在名单）：rpid={target}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})
        allowed_root = int(entry.get("root") or 0)
        if allowed_root <= 0:
            why = "名单里这条缺少根评论 root，无法安全回复"
            log(f"拒发（root 缺失）：rpid={target}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})
        if int(root or 0) == 0:
            root = allowed_root
        elif int(root) != allowed_root:
            why = f"root={root} 与名单里的 root={allowed_root} 不一致"
            log(f"拒发（root 不一致）：rpid={target}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})
        max_replies = int(policy.get("maxReplies") or 0)
        if send_state["replies"] >= max_replies:
            why = f"本轮最多回 {max_replies} 条，已经用完了"
            log(f"拒发（本轮回复上限）：{why}")
            return j({"success": False, "blocked": True, "reason": why, "message": "没发出去：" + why})

    if target == 0:
        root = 0

    raw = await UP.bili_reply(bvid, text, rpid=target, root=int(root or 0))
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        data = {"success": False, "raw": str(raw)}

    if data.get("success"):
        if int(rpid or 0):
            send_state["replies"] += 1
            gate.bump_usage(policy, reply=1)
        else:
            send_state["new"] += 1
            gate.bump_usage(policy, new=1)
        actual_root = int(root or rpid or 0)
        if int(rpid or 0):
            ledger.note_reply_sent(bvid, actual_root, int(rpid))
        new_rpid = int(data.get("rpid") or 0)
        if new_rpid:
            ledger.note_comment_sent(bvid, new_rpid, actual_root, text)
            data["ledger_recorded"] = True
            try:
                data["verify"] = await _to_thread(
                    report_mod.verify_comment, bvid, new_rpid, actual_root
                )
            except Exception as exc:
                data["verify"] = {"ok": False, "error": str(exc)}
        else:
            data["ledger_recorded"] = False
            data["warning"] = "接口没有返回 rpid，账本无法记录；下一轮可能重复回话。"
        log(f"已发言：{bvid} rpid={new_rpid or '?'} root={actual_root}")
    elif data.get("blocked"):
        log(f"机器闸拒发：{data.get('reason')}")
    return j(data)


def main() -> None:
    ensure_home()
    log(f"MCP 启动：plugin={PLUGIN_ROOT} data={HOME}")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
