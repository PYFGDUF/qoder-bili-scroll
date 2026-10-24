from __future__ import annotations

import functools
import os
import re
import threading
import uuid
from datetime import datetime

from config import HOME, SEEN_FILE, ensure_home

_LOCK = threading.RLock()


def _locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return func(*args, **kwargs)

    return wrapper

HEADER = """# 刷B站判重表

## 看过的视频

| 日期 | BV | 标题 | 结论 | 理由 |
| --- | --- | --- | --- | --- |

## 评论记录

| BV | rpid | root | 时间 | 正文前20字 |
| --- | --- | --- | --- | --- |
"""

BV_PATTERN = r"BV[0-9A-Za-z]{10}"
BV_RE = re.compile(rf"^{BV_PATTERN}$")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?$")
OLD_VIDEO_HEADER = "| 日期 | BV | 标题 | 结论 | 理由 |"
NEW_VIDEO_HEADER = "| 日期 | bvid | 标题 | UP | 状态 |"
VIDEO_SEPARATOR = "| --- | --- | --- | --- | --- |"


def _write(text: str) -> None:
    ensure_home()
    tmp = SEEN_FILE.with_name(f".{SEEN_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, SEEN_FILE)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        os.chmod(SEEN_FILE, 0o600)
    except OSError:
        pass


def _read() -> str:
    ensure_home()
    try:
        return SEEN_FILE.read_text(encoding="utf-8")
    except OSError:
        _write(HEADER)
        return HEADER


def _section(text: str, title: str) -> tuple[int, int]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if re.match(r"^#{1,6}\s", line) and title in line:
            start = index
            break
    if start is None:
        return -1, -1
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^#{1,6}\s", lines[index]):
            end = index
            break
    return start, end


def _escape(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()[:160]


def _split_row(line: str) -> list:
    cells = []
    current = []
    escaped = False
    for char in line.strip().strip("|"):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def _norm_label(value) -> str:
    return re.sub(r"[\s*`]", "", str(value or "")).lower()


def _header_layout(line: str) -> str | None:
    if not str(line or "").strip().startswith("|"):
        return None
    cells = _split_row(line)
    labels = [_norm_label(cell) for cell in cells]
    if not labels or labels[0] != "日期":
        return None
    joined = "|".join(labels)
    if "状态" in joined or "up" in labels:
        return "new"
    if "结论" in joined or "理由" in joined:
        return "old"
    if "bvid" in labels:
        return "new"
    if "bv" in labels:
        return "old"
    return "old"


def _latest_header(lines: list, start: int, end: int) -> tuple[int, str] | None:
    found = None
    for index in range(start, end):
        layout = _header_layout(lines[index])
        if layout:
            found = (index, layout)
    return found


def _trim_end(lines: list, start: int, end: int) -> int:
    at = end
    while at > start and not lines[at - 1].strip():
        at -= 1
    return at


def _table_has_rows(lines: list, header_index: int, end: int) -> bool:
    for index in range(header_index + 1, end):
        line = lines[index]
        if _header_layout(line):
            return False
        if not line.strip().startswith("|"):
            continue
        if any(BV_RE.fullmatch(cell) for cell in _split_row(line)):
            return True
    return False


def _insert_row(lines: list, start: int, end: int, row: str, layout: str) -> list:
    header = _latest_header(lines, start, end)
    header_line = NEW_VIDEO_HEADER if layout == "new" else OLD_VIDEO_HEADER

    if not header:
        insert_at = _trim_end(lines, start, end)
        block = [header_line, VIDEO_SEPARATOR, row]
        if insert_at > start and lines[insert_at - 1].strip():
            block.insert(0, "")
        lines[insert_at:insert_at] = block
        return lines

    header_index, current = header
    if current == layout:
        lines.insert(_trim_end(lines, start, end), row)
        return lines

    if not _table_has_rows(lines, header_index, end):
        lines[header_index] = header_line
        lines.insert(_trim_end(lines, start, end), row)
        return lines

    insert_at = _trim_end(lines, start, end)
    lines[insert_at:insert_at] = ["", header_line, VIDEO_SEPARATOR, row]
    return lines


def _compose_status(verdict, reason) -> str:
    base = str(verdict or "").strip() or "跳过"
    note = str(reason or "").strip()
    if note and note not in base:
        return f"{base}（{note}）"
    return base


@_locked
def add(bvid: str, title: str = "", verdict: str = "跳过", reason: str = "", up: str = "", status: str = "") -> dict:
    bv = str(bvid or "").strip()
    if not re.fullmatch(BV_PATTERN, bv):
        return {"ok": False, "error": "BV 号格式不对"}
    text = _read()
    start, end = _section(text, "看过的视频")
    if start < 0:
        text = text.rstrip() + "\n\n" + HEADER.split("## 看过的视频", 1)[1]
        _write(text)
        start, end = _section(text, "看过的视频")
    lines = text.splitlines()
    scope = "\n".join(lines[start:end])
    if re.search(re.escape(bv), scope):
        return {"ok": True, "already": True, "bvid": bv}

    day = datetime.now().strftime("%Y-%m-%d")
    if str(up or "").strip() or str(status or "").strip():
        status_cell = str(status or "").strip() or _compose_status(verdict, reason)
        row = f"| {day} | {bv} | {_escape(title)} | {_escape(up)} | {_escape(status_cell)} |"
        layout = "new"
    else:
        row = f"| {day} | {bv} | {_escape(title)} | {_escape(verdict)} | {_escape(reason)} |"
        layout = "old"

    lines = _insert_row(lines, start, end, row, layout)
    _write("\n".join(lines) + "\n")
    return {"ok": True, "already": False, "bvid": bv, "row": row}


def check(bvid: str) -> dict:
    bv = str(bvid or "").strip()
    if not re.fullmatch(BV_PATTERN, bv):
        return {"ok": False, "bvid": bv, "seen": False, "error": "BV 号格式不对"}
    text = _read()
    start, end = _section(text, "看过的视频")
    scope = "\n".join(text.splitlines()[start:end]) if start >= 0 else ""
    return {"ok": True, "bvid": bv, "seen": bool(re.search(re.escape(bv), scope))}


def _date_cell(value) -> tuple[str, str]:
    text = str(value or "").strip()
    match = DATE_RE.match(text)
    return (match.group(1), text) if match else ("", text)


def _looks_verdict(value) -> bool:
    text = str(value or "").strip()
    return bool(
        re.match(
            r"^(?:值得|跳过|跳了|没挑|可以看|可以|不值|推荐|挑|收|选|评论|私信|已发|发给)",
            text,
        )
    )


def _looks_status(value) -> bool:
    return bool(
        re.search(
            r"挑|收|选|值得看|可以看|跳过|跳了|没挑|不值当|评论|私信|已发|发给",
            str(value or ""),
        )
    )


def _row_layout(cells: list, bv_index: int, header_layout: str | None) -> str:
    if header_layout == "new":
        if len(cells) > bv_index + 3:
            middle = cells[bv_index + 2]
            last = cells[-1]
            if middle and last and _looks_verdict(middle) and not _looks_status(middle) and not _looks_status(last):
                return "old"
        return "new"
    if header_layout == "old":
        if len(cells) > bv_index + 3:
            middle = cells[bv_index + 2]
            last = cells[-1]
            if middle and last and not _looks_verdict(middle) and _looks_status(last):
                return "new"
        return "old"
    if len(cells) > bv_index + 3:
        middle = cells[bv_index + 2]
        last = cells[-1]
        if last and _looks_status(last) and not _looks_verdict(middle):
            return "new"
    return "old"


def _derive_verdict(status: str) -> str:
    text = str(status or "").strip()
    if "不值当" in text:
        return "不值当"
    for tier in ("值得看", "可以看"):
        if re.search(rf"(?<!不){tier}", text):
            return tier
    match = re.match(
        r"^(挑|收|选|跳过|跳了|没挑|值得|不值得|推荐|评论过|私信已发|私信|已发|发给)",
        text,
    )
    return match.group(1) if match else text


def _reason_from_status(status: str) -> str:
    text = str(status or "").strip()
    match = re.search(r"[：:]\s*(.+)$", text)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()[:300]
    match = re.search(r"[（(]([^）)]+)[）)]", text)
    if match:
        inner = match.group(1).strip()
        if inner not in ("值得看", "可以看", "不值当"):
            return inner[:300]
    return ""


def _picked(value) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.search(r"跳过|跳了|没挑|不值|不看", text):
        return False
    return bool(re.search(r"挑|收|选|值得|可以看|推荐", text))


def _delivered_bvs(text: str) -> set:
    found = set()
    for line in str(text or "").splitlines():
        match = re.search(rf"\|\s*({BV_PATTERN})\s*\|\s*(\d{{6,}})\s*\|", line)
        if match:
            found.add(match.group(1))
    return found


def _note_exists(bvid: str) -> bool:
    try:
        return next((HOME / "reports").glob(f"{bvid}-*.md"), None) is not None
    except OSError:
        return False


def list_rows(limit: int = 50) -> dict:
    text = _read()
    start, end = _section(text, "看过的视频")
    delivered_bvs = _delivered_bvs(text)
    rows = []
    layout = None
    if start >= 0:
        for line in text.splitlines()[start:end]:
            if not line.strip().startswith("|"):
                continue
            header_layout = _header_layout(line)
            if header_layout:
                layout = header_layout
                continue
            cells = _split_row(line)
            if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            bv_index = next(
                (index for index, cell in enumerate(cells) if BV_RE.fullmatch(cell)),
                -1,
            )
            if bv_index < 0:
                continue

            bvid = cells[bv_index]
            date = ""
            if bv_index > 0:
                date, _ = _date_cell(cells[bv_index - 1])
            if not date:
                first = cells[0] if cells else ""
                date, _ = _date_cell(first)
                if not date and first != bvid:
                    date = first
            title = cells[bv_index + 1] if bv_index + 1 < len(cells) else ""

            row_layout = _row_layout(cells, bv_index, layout)
            up = ""
            status = ""
            if row_layout == "new":
                up = cells[bv_index + 2] if bv_index + 2 < len(cells) else ""
                status = cells[-1] if len(cells) > bv_index + 2 else ""
                verdict = _derive_verdict(status)
                reason = _reason_from_status(status)
            else:
                verdict = cells[bv_index + 2] if bv_index + 2 < len(cells) else ""
                reason = cells[-1] if len(cells) > bv_index + 3 else ""

            signal = f"{status} {verdict}".strip()
            rows.append(
                {
                    "date": date,
                    "bvid": bvid,
                    "title": title,
                    "verdict": verdict,
                    "reason": reason,
                    "up": up,
                    "status": status,
                    "picked": _picked(signal),
                    "delivered": bool(re.search(r"评论|私信|已发|发给", signal)) or bvid in delivered_bvs,
                    "note": _note_exists(bvid),
                    "url": f"https://www.bilibili.com/video/{bvid}",
                }
            )

    try:
        size = max(1, int(limit))
    except (TypeError, ValueError):
        size = 50
    return {"ok": True, "count": len(rows), "rows": rows[-size:][::-1]}
