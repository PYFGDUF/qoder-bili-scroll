from __future__ import annotations

import contextlib
import functools
import os
import re
import threading
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # non-POSIX
    fcntl = None

from config import HOME, POLICY_FILE, load_config, norm_name, now_iso, read_json, write_json
from state import left_today, load_state

_LOCK = threading.RLock()
MAX_RUN_AGE_HOURS = 2.0


@contextlib.contextmanager
def send_lock():
    """跨进程串行化发送。POSIX 用 flock；其他平台退化为进程内锁。"""
    if fcntl is None:
        with _LOCK:
            yield
        return
    HOME.mkdir(parents=True, exist_ok=True)
    lock_path = HOME / ".reply.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return func(*args, **kwargs)

    return wrapper


@_locked
def write_reply_policy(items=None, run_id: str = "", allow_new: bool | None = None) -> dict:
    cfg = load_config()
    name = str(cfg["mentionName"] or "").strip()
    owner_mid = int(cfg.get("mentionUid") or 0)
    allow = []
    for item in items or []:
        source = item.get("from") or {}
        rpid = int(source.get("rpid") or 0)
        if not rpid:
            continue
        allow.append(
            {
                "bv": str(item.get("bv") or ""),
                "rpid": rpid,
                "root": int(item.get("root") or 0),
                "by": item.get("via") or "thread",
            }
        )
    if allow_new is None:
        allow_new = left_today() > 0
    per_round_new = max(0, int(cfg["commentPerRound"] or 0))
    can_new = bool(
        cfg["autoComment"]
        and name
        and allow_new
        and per_round_new > 0
        and str(cfg.get("dmMode") or "off") != "only"
    )
    policy = {
        "enabled": bool(cfg["replyOnlyMention"]),
        "owner": {"mid": owner_mid, "name": name},
        "allow": allow,
        "allowNewComment": can_new,
        "maxNewComments": per_round_new if can_new else 0,
        "maxReplies": max(1, int(cfg["replyPerRound"] or 1)),
        "dmMode": str(cfg.get("dmMode") or "off"),
        "newUsed": 0,
        "replyUsed": 0,
        "runId": run_id,
        "builtAt": now_iso(),
        "note": "由 bili-scroll 插件写入；供内置 MCP 的「只回本人」闸使用。",
    }
    write_json(POLICY_FILE, policy)
    return policy


@_locked
def bump_usage(policy: dict, new: int = 0, reply: int = 0) -> dict:
    current = read_policy()
    if not current or str(current.get("runId") or "") != str((policy or {}).get("runId") or ""):
        # 发送期间策略被新一轮/配置变更覆盖了：不要把旧 allow/runId 写回去。
        return current or {}
    updated = dict(current)
    updated["newUsed"] = int(current.get("newUsed") or 0) + int(new or 0)
    updated["replyUsed"] = int(current.get("replyUsed") or 0) + int(reply or 0)
    write_json(POLICY_FILE, updated)
    return updated


def read_policy() -> dict:
    data = read_json(POLICY_FILE, {})
    return data if isinstance(data, dict) else {}


def run_is_live(run_id: str):
    run_id = str(run_id or "")
    run = next(
        (row for row in (load_state().get("runs") or []) if row.get("run_id") == run_id),
        None,
    )
    if not run or run.get("status") != "running":
        return False, f"当前没有进行中的一轮（策略来自 {run_id or '未知轮次'}）；先 /bili-run 或开 chat 轮"
    started = run.get("startedAt")
    try:
        started_at = datetime.fromisoformat(str(started))
        if started_at.tzinfo is None:
            started_at = started_at.astimezone()
    except (TypeError, ValueError):
        return False, "这一轮缺少有效的开始时间；为安全起见先重新开一轮"
    run_age = (datetime.now(timezone.utc) - started_at.astimezone(timezone.utc)).total_seconds() / 3600
    if run_age > MAX_RUN_AGE_HOURS:
        return False, f"这一轮已超过 {MAX_RUN_AGE_HOURS:.0f} 小时未结束（可能崩溃）；先重新开一轮"
    return True, ""


def policy_fresh(max_age_hours: float = 12.0):
    policy = read_policy()
    if not policy:
        return False, "缺少 reply-policy.json（先开一轮，让插件把策略写好）"
    built = policy.get("builtAt")
    moment = None
    if built:
        try:
            moment = datetime.fromisoformat(str(built))
            if moment.tzinfo is None:
                moment = moment.astimezone()
        except ValueError:
            moment = None
    if moment is None:
        try:
            moment = datetime.fromtimestamp(POLICY_FILE.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return False, "读取 reply-policy.json 失败"
    age = (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds() / 3600
    if age > max_age_hours:
        return False, f"reply-policy.json 已过期（{age:.1f} 小时前写入），先重新开一轮"

    # 策略必须绑定到 state.json 里仍在 running 的轮次；配置变更/登录/收件箱/已结束轮次都不放行。
    return run_is_live(str(policy.get("runId") or ""))


def check_text(text: str, allow_mention: str = ""):
    body = str(text or "").strip()
    if not body:
        return False, "评论正文是空的"
    if len(body) > 100:
        return False, f"评论正文 {len(body)} 字，超过 100 字上限"
    if re.search(r"https?://|www\.|b23\.tv", body, re.I):
        return False, "评论里不能带链接"
    if re.search(r"\b\d{9,}\b", body):
        return False, "评论里不能带长数字串（rpid/UID 等）"
    allowed = norm_name(allow_mention)
    for name in re.findall(r"[@＠]([^\s@＠]+)", body):
        cleaned = re.sub(r"[，。！？、,.!?:：；;）)】\]]+$", "", name)
        if not allowed or norm_name(cleaned) != allowed:
            return False, f"评论里不能 @ 第三方（发现 @{cleaned}）"
    if "\x00" in body:
        return False, "评论正文包含非法字符"
    return True, ""
