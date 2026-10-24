"""B站私信：只读会话 / 消息，生成收件箱与「只回本人」策略；不直接发送。

私信接口（均为 GET，需要登录 Cookie）：
  · 列会话：session_svr/v1/session_svr/get_sessions
  · 读消息：svr_sync/v1/svr_sync/fetch_session_msgs
  · 发消息不在这里做，由调用方在策略允许时另行发送。
"""
from __future__ import annotations

import functools
import json
import re
import threading
from datetime import datetime, timezone

import api
import config
import state

UA = api.UA

INBOX_FILE = config.HOME / "dm-inbox.json"
POLICY_FILE = config.HOME / "dm-policy.json"
SEEN_FILE = config.HOME / "dm-seen.json"

MAX_RUN_AGE_HOURS = 2.0
MAX_SEEN_KEYS = 200
_INTERNAL_TERMS = ("dmMode", "aiReply", "白名单", "复核", "token", "prompt", "only")
# 摘要标题/理由来自公开报告，token 是正常内容，不能当内部词抹掉；回复正文仍按 _INTERNAL_TERMS 拦。
_TITLE_TERMS = tuple(term for term in _INTERNAL_TERMS if term.lower() != "token")
_TITLE_RE = re.compile("|".join(re.escape(term) for term in _TITLE_TERMS), re.I)
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_VIDEO_LINK_RE = re.compile(
    r"https?://(?:www\.|m\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10})",
    re.I,
)
_NEW_HEAD_RE = re.compile(r"^###\s+(BV[0-9A-Za-z]{10})(?:\s|$)")
_OLD_HEAD_RE = re.compile(r"^##\s+\d+\s*[.)]\s*(.*)$")
_HEAD_RE = re.compile(r"^#{1,6}\s")
_TOP_HEAD_RE = re.compile(r"^#{1,2}\s")
_META_RE = re.compile(
    r"^(?:UP主?|作者|上传者|链接|地址|播放量|播放|时长|档位|发布时间|日期|封面|简介|标签)\s*[:：]",
    re.I,
)

_LOCK = threading.RLock()


def _locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return func(*args, **kwargs)

    return wrapper


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_object(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def headers(cred) -> dict:
    c = cred if isinstance(cred, dict) else {}
    cookie = (
        f"SESSDATA={c.get('sessdata') or ''}; "
        f"bili_jct={c.get('bili_jct') or ''}; "
        f"buvid3={c.get('buvid3') or ''}"
    )
    return {
        "User-Agent": UA,
        "Referer": "https://message.bilibili.com/",
        "Origin": "https://message.bilibili.com",
        "Cookie": cookie,
    }


def list_sessions(cred) -> list:
    """会话列表：[{uid, unread, lastTs, preview}]。失败时返回空列表。"""
    url = (
        "https://api.vc.bilibili.com/session_svr/v1/session_svr/get_sessions"
        "?session_type=1&group_id=0&Build=0&mobi_app=web"
    )
    try:
        data = api.jget(url, headers(cred))
    except Exception as exc:
        config.log("私信会话读取失败：" + str(exc))
        return []
    if not isinstance(data, dict) or _as_int(data.get("code")) != 0:
        return []
    payload = data.get("data") or {}
    rows = payload.get("session_list") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    sessions = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        uid = _as_int(row.get("talker_id"))
        if uid <= 0:
            continue
        last = row.get("last_msg") if isinstance(row.get("last_msg"), dict) else {}
        parsed = _json_object(last.get("content"))
        preview = ""
        if isinstance(parsed, dict):
            preview = str(parsed.get("content") or parsed.get("text") or "")[:80]
        sessions.append(
            {
                "uid": uid,
                "unread": _as_int(row.get("unread_count")),
                "lastTs": _as_int(last.get("timestamp")),
                "preview": preview,
            }
        )
    return sessions


def fetch_messages(cred, talker_id: int, size: int = 10) -> list:
    """读某个会话最近的 n 条，按时间正序返回。失败时返回空列表。"""
    limit = max(1, min(50, _as_int(size, 10)))
    url = (
        "https://api.vc.bilibili.com/svr_sync/v1/svr_sync/fetch_session_msgs"
        f"?talker_id={_as_int(talker_id)}&session_type=1&size={limit}"
        "&build=0&mobi_app=web"
    )
    try:
        data = api.jget(url, headers(cred))
    except Exception as exc:
        config.log("私信消息读取失败：" + str(exc))
        return []
    if not isinstance(data, dict) or _as_int(data.get("code")) != 0:
        return []
    payload = data.get("data") or {}
    rows = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []

    cred_uid = api.our_mid(cred if isinstance(cred, dict) else {})
    messages = []
    # 接口给的是倒序（新 → 旧），翻成时间正序。
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        msg_type = _as_int(row.get("msg_type"), 1) or 1
        parsed = _json_object(row.get("content"))
        raw = row.get("content")
        bvid = ""
        is_card = False

        if (
            msg_type in (7, 8)
            and isinstance(parsed, dict)
            and any(parsed.get(key) for key in ("jump_url", "bvid", "author", "title"))
        ):
            is_card = True
            card_bvid = str(parsed.get("bvid") or "")
            match = _BV_RE.search(card_bvid) or _BV_RE.search(str(parsed.get("jump_url") or ""))
            bvid = match.group(0) if match else ""
            title = str(parsed.get("title") or parsed.get("headline") or parsed.get("desc") or "")[:80]
            author = str(parsed.get("author") or "")[:30]
            text = "[分享视频] " + (title or "(无标题)")
            if author:
                text += " / " + author
            if bvid:
                text += " " + bvid
        else:
            if isinstance(parsed, dict):
                text = str(parsed.get("content") or parsed.get("text") or "")
            elif isinstance(raw, str):
                text = raw
            else:
                text = ""
            if not text:
                text = "[非文字消息]"

        messages.append(
            {
                "key": str(row.get("msg_key") or row.get("msg_seqno") or row.get("timestamp") or ""),
                "ts": _as_int(row.get("timestamp")),
                "fromMe": cred_uid > 0 and _as_int(row.get("sender_uid")) == cred_uid,
                "kind": msg_type if is_card else 1,
                "text": text[:500],
                "bvid": bvid,
            }
        )
    return messages


def _empty_result(error: str = "") -> dict:
    return {"ok": False, "items": [], "text": "", "strangers": 0, "error": error}


def _publish(items: list, text: str, strangers: int) -> dict:
    inbox = {
        "items": items,
        "text": text,
        "strangers": strangers,
        "at": config.now_iso(),
    }
    config.write_json(INBOX_FILE, inbox, 0o600)
    return {"ok": True, "items": items, "text": text, "strangers": strangers, "error": ""}


def _seen_state(owner: int):
    data = config.read_json(SEEN_FILE, {})
    if not isinstance(data, dict):
        data = {}
    entry = data.get(str(owner))
    if not isinstance(entry, dict):
        entry = {}
    raw_keys = entry.get("keys")
    if isinstance(raw_keys, str):
        raw_keys = [raw_keys]
    elif not isinstance(raw_keys, list):
        raw_keys = []
    keys = [str(key) for key in raw_keys if key is not None and str(key)]
    return data, _as_int(entry.get("lastTs")), keys


def poll_owner(owner_uid: int, my_uid: int, run_id: str, max_messages: int = 3) -> dict:
    """轮询本人会话：只收本人发来的新消息；陌生人只计数，不读正文。"""
    owner = _as_int(owner_uid)
    me = _as_int(my_uid)
    if owner <= 0:
        return _empty_result("还没配置主人的 mentionUid —— 昵称不唯一，插件不猜人；先填你接收私信的 B 站 UID")
    if owner == me:
        return _empty_result("配置的本人就是当前登录的 B站 账号，不能给自己发私信；换一个账号，或改用评论投递")

    cred = api.load_credential()
    if not cred:
        return _empty_result("还没扫码登录 B站，读不了私信")

    sessions = list_sessions(cred)
    strangers = sum(
        1
        for row in sessions
        if _as_int(row.get("uid")) != owner and _as_int(row.get("unread")) > 0
    )
    match = next((row for row in sessions if _as_int(row.get("uid")) == owner), None)
    if not match or _as_int(match.get("unread")) <= 0:
        return _publish([], "", strangers)

    messages = fetch_messages(cred, owner, size=20)
    if not messages:
        return _publish([], "", strangers)

    limit = max(0, min(20, _as_int(max_messages, 3)))
    recent = [row for row in messages if not row.get("fromMe")]
    recent.sort(key=lambda row: _as_int(row.get("ts")))
    if limit:
        recent = recent[-limit:]
    else:
        recent = []

    seen_data, last_ts, seen_keys = _seen_state(owner)
    key_set = set(seen_keys)
    items = [
        row
        for row in recent
        if row.get("key") not in key_set and _as_int(row.get("ts")) > last_ts
    ]
    # 提示词只拿到 text，所以 key 必须跟着内容一起给，模型才能用 bili_dm_reply。
    text = "\n".join(f"[{row.get('key') or '?'}] {row.get('text') or ''}" for row in items)

    # 水位 = 已经取到过的最新时间；keys 用来挡住同一时间戳/补拉到的旧消息。
    newest_ts = max([last_ts] + [_as_int(row.get("ts")) for row in messages])
    merged = list(seen_keys)
    for row in messages:
        key = str(row.get("key") or "")
        if key and key not in merged:
            merged.append(key)
    seen_data[str(owner)] = {
        "lastTs": newest_ts,
        "keys": merged[-MAX_SEEN_KEYS:],
    }
    config.write_json(SEEN_FILE, seen_data, 0o600)

    if items:
        config.log(f"私信轮 {run_id or '?'}：本人发来 {len(items)} 条新消息")
    return _publish(items, text, strangers)


# ---------------------------------------------------------------- 日报摘要


def _scrub(text: str) -> str:
    value = _TITLE_RE.sub("", str(text or ""))
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" \t\r\n-—:：|_=*`")
    value = re.sub(r"^[，。；、,.;:]+", "", value)
    return value.strip()


def _clean_markdown(line: str) -> str:
    value = str(line or "").strip()
    value = re.sub(r"^#{1,6}\s*", "", value)
    value = re.sub(r"^>\s*", "", value)
    value = re.sub(r"^[-*+]\s+", "", value)
    value = re.sub(r"^\d+[.)、]\s*", "", value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    value = re.sub(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)", r"\1", value)
    return _scrub(value)


def _shorten(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _strip_label(text: str, labels: str) -> str:
    # 只去掉“标题：/ 标题 ”这种标签；不能把「标题1」「标题党」这类正文开头吃掉。
    return re.sub(r"^(?:" + labels + r")\s*(?:[:：]\s*|\s+)", "", str(text or ""), flags=re.I).strip()


def _section_end(lines: list, start: int) -> int:
    index = start + 1
    while index < len(lines):
        stripped = lines[index].strip()
        if _NEW_HEAD_RE.match(stripped) or _OLD_HEAD_RE.match(stripped) or _TOP_HEAD_RE.match(stripped):
            return index
        index += 1
    return len(lines)


def _parse_section(body: list, heading_title: str = ""):
    title = ""
    title_index = -1
    for index, line in enumerate(body):
        stripped = line.strip()
        if not stripped or _HEAD_RE.match(stripped):
            continue
        if _META_RE.match(_clean_markdown(stripped)):
            continue
        if _VIDEO_LINK_RE.search(stripped):
            continue
        if "为什么值得" in stripped:
            continue
        cleaned = _strip_label(_clean_markdown(stripped), r"标题|视频标题")
        if cleaned:
            title = cleaned
            title_index = index
            break

    if not title:
        title = _strip_label(_clean_markdown(heading_title), r"标题|视频标题")

    reason = ""
    for line in body:
        if "为什么值得" not in line:
            continue
        cleaned = _clean_markdown(line)
        match = re.search(r"为什么值得[^:：]*[:：]\s*(.*)$", cleaned)
        if not match:
            match = re.search(r"为什么值得\s+(.*)$", cleaned)
        reason = (match.group(1) if match else "").strip()
        if reason:
            break

    if not reason:
        for index, line in enumerate(body):
            if index == title_index:
                continue
            stripped = line.strip()
            if not stripped or _HEAD_RE.match(stripped):
                continue
            if _VIDEO_LINK_RE.search(stripped) or _META_RE.match(_clean_markdown(stripped)):
                continue
            if "为什么值得" in stripped:
                continue
            reason = _clean_markdown(stripped)
            if reason:
                break

    return title, _shorten(reason.splitlines()[0] if reason else "", 100)


def _report_items(report_text: str) -> list:
    lines = str(report_text or "").splitlines()
    items = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        new_head = _NEW_HEAD_RE.match(stripped)
        old_head = _OLD_HEAD_RE.match(stripped)
        if not new_head and not old_head:
            index += 1
            continue

        end = _section_end(lines, index)
        body = lines[index + 1 : end]
        if new_head:
            bvid = new_head.group(1)
            link = f"https://www.bilibili.com/video/{bvid}"
            heading_title = ""
        else:
            heading_title = old_head.group(1) or ""
            match = None
            for line in body:
                match = _VIDEO_LINK_RE.search(line)
                if match:
                    break
            if not match:
                match = _VIDEO_LINK_RE.search(heading_title)
            link = f"https://www.bilibili.com/video/{match.group(1)}" if match else ""
            if not link:
                heading_bv = _BV_RE.search(heading_title)
                link = f"https://www.bilibili.com/video/{heading_bv.group(0)}" if heading_bv else ""
            if not link:
                index = end
                continue

        title, reason = _parse_section(body, heading_title)
        if not title:
            title = bvid if new_head else (link.rsplit("/", 1)[-1] if link else "")
        items.append(
            {
                "title": _shorten(_scrub(title), 80) or "这条视频",
                "link": link,
                "reason": _shorten(_scrub(reason), 100),
            }
        )
        index = end
    return items


def _compose_summary(count: int, blocks: list, footer: bool) -> str:
    text = f"今天挑到 {count} 条：\n" + "\n".join(blocks)
    if footer:
        text += "\n其余见日报。"
    return text


def build_summary(report_text: str, max_items: int = 4, max_chars: int = 480) -> str:
    """把日报压成私信里能发的一段话；只保留标题 / 链接 / 一句话理由。"""
    max_items = _as_int(max_items, 4)
    max_chars = _as_int(max_chars, 480)
    if max_items <= 0 or max_chars <= 0:
        return ""

    parsed = _report_items(report_text)
    if not parsed:
        return ""

    selected = parsed[:max_items]
    blocks = []
    for index, item in enumerate(selected, 1):
        block = f"{index}. {item['title']}\n   {item['link']}"
        if item["reason"]:
            block += f"\n   {item['reason']}"
        blocks.append(block)

    truncated = len(parsed) > len(selected)
    full = _compose_summary(len(selected), blocks, truncated)
    if len(full) <= max_chars and not truncated:
        return full

    # 放不下（或还有没放进去的）→ 逐条减少，给「其余见日报。」留出位置。
    count = len(blocks)
    while count > 0:
        footer = truncated or count < len(parsed)
        candidate = _compose_summary(count, blocks[:count], footer)
        if len(candidate) <= max_chars:
            return candidate
        count -= 1
    return ""


# ---------------------------------------------------------------- 回复策略


@_locked
def write_policy(
    owner_uid: int,
    run_id: str,
    allow_keys=None,
    allow_send: bool = False,
    max_replies: int = 1,
    max_per_day: int = 3,
) -> dict:
    """写本轮私信策略。allowKeys 是本轮允许回复的消息 key（默认空 = 只能读）。"""
    if isinstance(allow_keys, str):
        allow_keys = [allow_keys]
    keys = []
    for value in allow_keys or []:
        key = str(value or "").strip()
        if key and key not in keys:
            keys.append(key)

    policy = {
        "runId": str(run_id or ""),
        "ownerUid": max(0, _as_int(owner_uid)),
        "allowKeys": keys,
        "allowSend": bool(allow_send),
        "maxReplies": max(0, _as_int(max_replies, 1)),
        "maxPerDay": max(0, _as_int(max_per_day, 3)),
        "replyUsed": 0,
        "summaryUsed": 0,
        "builtAt": config.now_iso(),
        "mode": "dm",
        "note": "由 bili-scroll 插件写入；只允许回复本人，且仅本轮有效。",
    }
    config.write_json(POLICY_FILE, policy, 0o600)
    result = dict(policy)
    result["ok"] = True
    return result


def read_policy() -> dict:
    data = config.read_json(POLICY_FILE, {})
    return data if isinstance(data, dict) else {}


def _parse_iso(value):
    if not value:
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


def policy_fresh(max_age_hours: float = 12.0):
    """dm-policy.json 是否仍然有效；语义与 gate.policy_fresh 一样，失败即关闭。"""
    policy = read_policy()
    if not policy:
        return False, "缺少 dm-policy.json（先开一轮，让插件把策略写好）"

    built = _parse_iso(policy.get("builtAt"))
    if built is None:
        return False, "dm-policy.json 缺少有效的 builtAt；先重新开一轮"
    age = (datetime.now(timezone.utc) - built.astimezone(timezone.utc)).total_seconds() / 3600
    if age > float(max_age_hours):
        return False, f"dm-policy.json 已过期（{age:.1f} 小时前写入），先重新开一轮"

    run_id = str(policy.get("runId") or "")
    run = next(
        (
            row
            for row in (state.load_state().get("runs") or [])
            if isinstance(row, dict) and row.get("run_id") == run_id
        ),
        None,
    )
    if not run or run.get("status") != "running":
        return False, f"当前没有进行中的一轮（策略来自 {run_id or '未知轮次'}）；先开一轮"

    started = _parse_iso(run.get("startedAt"))
    if started is None:
        return False, "这一轮缺少有效的开始时间；为安全起见先重新开一轮"
    run_age = (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds() / 3600
    if run_age > MAX_RUN_AGE_HOURS:
        return False, f"这一轮已超过 {MAX_RUN_AGE_HOURS:.0f} 小时未结束（可能崩溃）；先重新开一轮"
    return True, ""


@_locked
def note_reply_sent(key: str) -> dict:
    """回复成功后核销一个 allowKey；不在名单里则拒绝并返回当前策略。"""
    current = read_policy()
    if not current:
        return {"ok": False, "error": "缺少 dm-policy.json；先开一轮"}

    wanted = str(key or "")
    raw_allowed = current.get("allowKeys")
    if isinstance(raw_allowed, str):
        raw_allowed = [raw_allowed]
    elif not isinstance(raw_allowed, list):
        raw_allowed = []
    allowed = [str(value) for value in raw_allowed if str(value)]
    if not wanted or wanted not in allowed:
        result = dict(current)
        result["ok"] = False
        result["error"] = "这条消息不在本轮允许回复的名单里"
        return result

    updated = dict(current)
    updated["allowKeys"] = [value for value in allowed if value != wanted]
    updated["replyUsed"] = _as_int(current.get("replyUsed")) + 1
    config.write_json(POLICY_FILE, updated, 0o600)
    result = dict(updated)
    result["ok"] = True
    return result


@_locked
def mark_summary_sent() -> dict:
    """摘要发出去后关掉 allowSend，并累计 summaryUsed。"""
    current = read_policy()
    if not current:
        return {"ok": False, "error": "缺少 dm-policy.json；先开一轮"}

    updated = dict(current)
    updated["allowSend"] = False
    updated["summaryUsed"] = _as_int(current.get("summaryUsed")) + 1
    config.write_json(POLICY_FILE, updated, 0o600)
    result = dict(updated)
    result["ok"] = True
    return result


def check_reply_text(text: str, max_chars: int = 100):
    """私信回复正文的机器闸：短、无链接、无 @、无内部术语、无长数字串。"""
    body = str(text or "").strip()
    if not body:
        return False, "回复是空的"
    limit = _as_int(max_chars, 100)
    if limit <= 0 or len(body) > limit:
        return False, f"回复 {len(body)} 字，超过 {max(0, limit)} 字上限"
    if re.search(r"https?://|www\.|b23\.tv|bilibili\.com", body, re.I):
        return False, "回复里不能带链接"
    if "@" in body or "＠" in body:
        return False, "回复里不能 @ 任何人"
    lowered = body.lower()
    for term in _INTERNAL_TERMS:
        if term.lower() in lowered:
            return False, f"回复里不能出现内部术语（{term}）"
    if re.search(r"\d{9,}", body):
        return False, "回复里不能带长数字串（UID / 消息号等）"
    if "\x00" in body:
        return False, "回复里包含非法字符"
    return True, ""
