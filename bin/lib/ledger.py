from __future__ import annotations

import functools
import re
import threading

from config import LEDGER_FILE, SEEN_FILE, clip, now_iso, read_json, write_json
from api import jget, video_meta

_LOCK = threading.RLock()


def _locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return func(*args, **kwargs)

    return wrapper


def conversation_file():
    return LEDGER_FILE


def empty_ledger() -> dict:
    return {"sent": [], "threads": {}, "owner": {}, "me": {}}


def read_ledger() -> dict:
    data = read_json(LEDGER_FILE, None)
    if not isinstance(data, dict):
        return empty_ledger()
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    owner = owner if int(owner.get("mid") or 0) else {}
    me = data.get("me") if isinstance(data.get("me"), dict) else {}
    me = me if str(me.get("uname") or "").strip() else {}
    sent = []
    for row in data.get("sent") or []:
        if not isinstance(row, dict):
            continue
        if row.get("bv") and int(row.get("rpid") or 0):
            sent.append(row)
    threads = data.get("threads") if isinstance(data.get("threads"), dict) else {}
    return {"sent": sent, "threads": threads, "owner": owner, "me": me}


def write_ledger(ledger: dict) -> None:
    write_json(LEDGER_FILE, ledger)


@_locked
def update_fields(**fields) -> dict:
    """只更新指定字段：重读后再写，避免把并发发送刚写入的 sent 行覆盖掉。"""
    book = read_ledger()
    book.update(fields)
    write_ledger(book)
    return book


@_locked
def note_comment_sent(bv: str, rpid: int, root: int, text: str, source: str = "sent"):
    bvid = str(bv or "").strip()
    rid = int(rpid or 0)
    if not bvid or not rid:
        return None
    ledger = read_ledger()
    if any(int(row.get("rpid") or 0) == rid for row in ledger["sent"]):
        return None
    ledger["sent"].append(
        {
            "bv": bvid,
            "rpid": rid,
            "root": int(root or 0),
            "text": clip(text, 80),
            "at": now_iso(),
            "from": source,
        }
    )
    if len(ledger["sent"]) > 300:
        ledger["sent"] = ledger["sent"][-300:]
    write_ledger(ledger)
    return ledger["sent"][-1]


@_locked
def note_reply_sent(bv: str, root: int, their_rpid: int):
    key = f"{str(bv or '').strip()}:{int(root or 0)}"
    ledger = read_ledger()
    current = ledger["threads"].get(key) or {}
    ledger["threads"][key] = {
        "bv": str(bv or ""),
        "root": int(root or 0),
        "turns": int(current.get("turns") or 0) + 1,
        "lastTheirRpid": int(their_rpid or 0),
        "at": now_iso(),
    }
    write_ledger(ledger)
    return ledger["threads"][key]


@_locked
def merge_sent_records(records: list) -> int:
    if not records:
        return 0
    ledger = read_ledger()
    existing = {int(row.get("rpid") or 0) for row in ledger["sent"]}
    added = 0
    for row in records:
        bvid = str((row or {}).get("bv") or "").strip()
        rpid = int((row or {}).get("rpid") or 0)
        if not bvid or not rpid or rpid in existing:
            continue
        ledger["sent"].append(
            {
                "bv": bvid,
                "rpid": rpid,
                "root": int(row.get("root") or 0),
                "text": "",
                "at": now_iso(),
                "from": "seen",
            }
        )
        existing.add(rpid)
        added += 1
    if added:
        if len(ledger["sent"]) > 300:
            ledger["sent"] = ledger["sent"][-300:]
        write_ledger(ledger)
    return added


def parse_seen(text: str) -> list:
    raw = str(text or "")
    lines = raw.splitlines()
    scope = raw
    head = next(
        (index for index, line in enumerate(lines) if re.match(r"^#{1,6}\s", line) and "评论记录" in line),
        None,
    )
    if head is not None:
        end = len(lines)
        for index in range(head + 1, len(lines)):
            if re.match(r"^#{1,6}\s", lines[index]):
                end = index
                break
        scope = "\n".join(lines[head:end])

    rows = []
    for line in scope.splitlines():
        bv = re.search(r"BV[0-9A-Za-z]{10}", line)
        if not bv:
            continue
        numbers = [int(value) for value in re.findall(r"\b(\d{9,})\b", line)]
        if not numbers:
            continue
        rows.append(
            {
                "bv": bv.group(0),
                "rpid": numbers[0],
                "root": numbers[1] if len(numbers) > 1 else 0,
                "line": line.strip()[:160],
            }
        )
    return rows


def read_seen() -> str:
    try:
        return SEEN_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


@_locked
def claim_comment(bvid: str, rpid: int, headers: dict, mine: int = 0, cache: dict | None = None) -> dict:
    bv = str(bvid or "").strip()
    rid = int(rpid or 0)
    if not re.fullmatch(r"BV[0-9A-Za-z]{10}", bv) or rid < 1000:
        return {
            "ok": False,
            "error": "需要一条评论链接（同时包含 BV 号和 rpid）。B站 App 里那条评论的「复制链接」直接粘过来即可。",
        }
    meta = video_meta(bv, headers, cache)
    if not meta:
        return {"ok": False, "error": "取不到这个视频（可能已删 / 不可见）"}

    verified = "unknown"
    text = ""
    found = False
    data = jget(
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={meta['aid']}&mode=2&next=0&ps=20",
        headers,
    )
    for item in ((data.get("data") or {}).get("replies") or []):
        if int(item.get("rpid") or 0) == rid:
            found = True
            text = str((item.get("content") or {}).get("message") or "")
            verified = "yes" if int((item.get("member") or {}).get("mid") or 0) == int(mine or 0) else "no"
            break

    record = note_comment_sent(bv, rid, 0, text, source="claim-top" if found else "claim")
    note = {
        "yes": "核对过：这确实是这个账号发的，以后有人在这条下面说话，它会接。",
        "no": "核对到这条不是这个账号发的；已记账，但默认只回你本人，不会去答别人。",
        "unknown": "已记进账本，但第一页没核对到（被翻页/排序挡住，正常）—— 根楼层未知，自动收件箱不会扫这条；"
        "如果它是顶层评论，可以直接在它下面回。",
    }[verified]
    return {
        "ok": True,
        "bvid": bv,
        "rpid": rid,
        "root": 0,
        "verified": verified,
        "title": meta.get("title", ""),
        "text": clip(text, 60),
        "already": record is None,
        "note": note,
    }
