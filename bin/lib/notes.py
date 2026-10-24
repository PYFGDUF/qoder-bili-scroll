from __future__ import annotations

import functools
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from config import HOME, day_key, ensure_home

REPORTS_DIR = HOME / "reports"
DIGEST_DIR = HOME / "digest"

BV_PATTERN = r"BV[0-9A-Za-z]{10}"
BV_RE = re.compile(rf"^{BV_PATTERN}$")
ANY_BV_RE = re.compile(rf"{BV_PATTERN}(?![0-9A-Za-z])")
LINK_RE = re.compile(
    rf"https?://(?:www\.|m\.)?bilibili\.com/video/({BV_PATTERN})(?![0-9A-Za-z])",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+")
NEW_HEADING_RE = re.compile(rf"^###\s*({BV_PATTERN})(?![0-9A-Za-z])(.*)$")
OLD_HEADING_RE = re.compile(r"^##\s*\d+\s*[.)）、．]\s*(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s")
FIELD_RE = re.compile(r"^\s*(?:[-*+]\s+)?(?:\*\*)?([^*：:\n]{1,24}?)(?:\*\*)?\s*[：:]\s*(.*)$")
BOLD_FIELD_RE = re.compile(r"^\*\*[^*\n]{1,30}\*\*\s*[：:]")
BOLD_LABEL_RE = re.compile(
    r"^\*\*(?:标题|title|档位|tier|结论|verdict|为什么值得|why|理由|reason|up|作者|链接|url)\*\*\s*$",
    re.IGNORECASE,
)
TIERS = ("值得看", "可以看", "不值当")
META_LABELS = {
    "标题",
    "title",
    "up",
    "作者",
    "链接",
    "url",
    "档位",
    "tier",
    "为什么值得",
    "why",
    "理由",
    "reason",
    "结论",
    "verdict",
    "播放量",
    "views",
    "一句话",
    "我的一句话",
    "一句话结论",
    "up主",
    "日期",
    "date",
    "bvid",
    "bv",
}

_LOCK = threading.RLock()


def _locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return func(*args, **kwargs)

    return wrapper


def _clean(value, limit: int = 0) -> str:
    text = str(value or "")
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit > 0 else text


def _norm_label(value) -> str:
    return re.sub(r"[\s*`]", "", str(value or "")).lower()


def _field(line: str) -> tuple[str, str]:
    match = FIELD_RE.match(str(line or ""))
    if not match:
        return "", ""
    return _norm_label(match.group(1)), match.group(2).strip()


def _field_value(text: str, labels: tuple) -> str:
    wanted = {str(label).lower() for label in labels}
    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        label, value = _field(line)
        if label not in wanted:
            continue
        parts = [value]
        for next_line in lines[index + 1 :]:
            stripped = next_line.strip()
            if not stripped:
                break
            if HEADING_RE.match(stripped) or stripped in ("---", "***", "___"):
                break
            next_label, _ = _field(stripped)
            if next_label in META_LABELS or BOLD_FIELD_RE.match(stripped) or BOLD_LABEL_RE.match(stripped):
                break
            parts.append(stripped)
        cleaned = _clean(" ".join(part for part in parts if part))
        if cleaned:
            return cleaned
    return ""


def _clean_heading_rest(rest: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", str(rest or ""))
    text = URL_RE.sub("", text).strip()
    text = re.sub(r"^[\s\-—–|:：.、]+", "", text)
    text = _clean(text).strip(" -—–|:：")
    if not text:
        return ""
    label, value = _field(text)
    if label in ("标题", "title") and value:
        return _clean(value)
    return text


def _is_meta_line(line: str) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return True
    if HEADING_RE.match(stripped) or stripped in ("---", "***", "___"):
        return True
    if stripped.startswith("http"):
        return True
    label, _ = _field(stripped)
    if label in META_LABELS or BOLD_FIELD_RE.match(stripped) or BOLD_LABEL_RE.match(stripped):
        return True
    if LINK_RE.search(stripped) and not URL_RE.sub("", stripped).strip(" -—–|:："):
        return True
    return False


def _parse_title(lines: list, heading_rest: str) -> tuple[str, int | None]:
    for index, line in enumerate(lines):
        label, value = _field(line)
        if label in ("标题", "title") and value:
            return _clean(value), index

    rest_title = _clean_heading_rest(heading_rest)
    if rest_title:
        return rest_title, None

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or HEADING_RE.match(stripped) or stripped in ("---", "***", "___"):
            continue
        if _is_meta_line(stripped):
            continue
        value = _clean(re.sub(r"^[-*+]\s+", "", stripped))
        value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
        if value and not URL_RE.fullmatch(value) and not BV_RE.fullmatch(value):
            return value, index
    return "", None


def _field_line_value(text: str, labels: tuple) -> str:
    wanted = {str(label).lower() for label in labels}
    for line in str(text or "").splitlines():
        label, value = _field(line)
        if label in wanted and value:
            return _clean(value)
    return ""


def _match_tier(value: str) -> str:
    for tier in TIERS:
        if re.search(rf"(?<!不){re.escape(tier)}", value):
            return tier
    return ""


def _parse_tier(text: str) -> str:
    value = _field_line_value(text, ("档位", "tier"))
    if value:
        tier = _match_tier(value)
        return tier or value[:20]
    value = _field_line_value(text, ("结论", "verdict"))
    if value:
        tier = _match_tier(value)
        return tier or value[:40]
    return ""


def _parse_reason(lines: list, title_index: int | None) -> str:
    text = "\n".join(lines)
    for labels in (("为什么值得", "why"), ("理由", "reason")):
        value = _field_value(text, labels)
        if value:
            return value[:300]

    chunks = []
    for index, line in enumerate(lines):
        if index == title_index:
            if chunks:
                break
            continue
        stripped = line.strip()
        if not stripped:
            if chunks:
                break
            continue
        if HEADING_RE.match(stripped) or stripped in ("---", "***", "___"):
            if chunks:
                break
            continue
        label, _ = _field(stripped)
        if label in META_LABELS or BOLD_FIELD_RE.match(stripped) or BOLD_LABEL_RE.match(stripped):
            if chunks:
                break
            continue
        if stripped.startswith("http"):
            if chunks:
                break
            continue
        value = _clean(re.sub(r"^[-*+]\s+", "", stripped))
        if value and not BV_RE.fullmatch(value):
            chunks.append(value)
    if chunks:
        return _clean(" ".join(chunks))[:300]

    # 整段只有一行正文时，这行既被当成标题，也别让“第一段”彻底丢掉
    if title_index is not None and 0 <= title_index < len(lines):
        line = lines[title_index].strip()
        label, _ = _field(line)
        if line and label not in META_LABELS and not line.startswith("http") and not HEADING_RE.match(line):
            value = _clean(re.sub(r"^[-*+]\s+", "", line))
            if value and not BV_RE.fullmatch(value):
                return value[:300]
    return ""


def parse_report_sections(text: str) -> list[dict]:
    """把日报拆成一条视频一段：兼容 v2 的 `### BV...` 和旧版 `## 1.` / `## 1)`。"""
    lines = str(text or "").splitlines()
    starts = []
    for index, line in enumerate(lines):
        match = NEW_HEADING_RE.match(line)
        if match:
            starts.append((index, 3, match.group(1), match.group(2)))
            continue
        match = OLD_HEADING_RE.match(line)
        if match:
            starts.append((index, 2, "", match.group(1)))

    start_indexes = {item[0] for item in starts}
    sections = []
    for index, level, heading_bv, heading_rest in starts:
        end = len(lines)
        for probe in range(index + 1, len(lines)):
            if probe in start_indexes:
                end = probe
                break
            heading = HEADING_RE.match(lines[probe])
            if heading and len(heading.group(1)) <= level:
                end = probe
                break

        body_lines = lines[index + 1 : end]
        body = "\n".join(body_lines)
        heading_line = lines[index]

        bvid = heading_bv
        if not bvid:
            match = LINK_RE.search(heading_line) or LINK_RE.search(body)
            if match:
                bvid = match.group(1)
            else:
                match = ANY_BV_RE.search(heading_line)
                if match:
                    bvid = match.group(0)
        if not bvid or not BV_RE.fullmatch(bvid):
            continue

        title, title_index = _parse_title(body_lines, heading_rest)
        sections.append(
            {
                "bvid": bvid,
                "title": title or bvid,
                "tier": _parse_tier(body),
                "reason": _parse_reason(body_lines, title_index),
                "url": f"https://www.bilibili.com/video/{bvid}",
            }
        )
    return sections


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _atomic_write(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _day_value(day) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(day or ""))
    return match.group(0) if match else day_key()


def _note_text(section: dict, day: str, source: Path) -> str:
    title = section.get("title") or section.get("bvid") or ""
    reason = section.get("reason") or "（日报里没写理由）"
    return "\n".join(
        [
            f"# {_clean(title)}",
            "",
            f"- 日期：{day}",
            f"- 链接：{section.get('url') or ''}",
            f"- 档位：{section.get('tier') or '未标注'}",
            f"- 来源：{_clean(source)}",
            "",
            "## 理由",
            "",
            reason,
            "",
        ]
    )


@_locked
def sync_from_report(report_path, day=None) -> dict:
    source = Path(report_path).expanduser()
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "created": 0, "skipped": 0, "error": f"读不到日报：{exc}"}

    sections = parse_report_sections(text)
    key = _day_value(day)
    ensure_home()
    _ensure_dir(REPORTS_DIR)

    created = 0
    skipped = 0
    for section in sections:
        bvid = section["bvid"]
        target = REPORTS_DIR / f"{bvid}-{key}.md"
        if target.exists():
            skipped += 1
            continue
        try:
            _atomic_write(target, _note_text(section, key, source))
        except OSError as exc:
            return {"ok": False, "created": created, "skipped": skipped, "error": str(exc)}
        created += 1
    return {"ok": True, "created": created, "skipped": skipped}


def _month_value(month) -> str:
    text = str(month or "").strip()
    match = re.match(r"^(\d{4})-(\d{2})(?:-\d{2})?(?:$|[ T])", text)
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{match.group(1)}-{match.group(2)}"
    return datetime.now().strftime("%Y-%m")


def _one_line(value, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _row_date(row: dict) -> str:
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(row.get("date") or row.get("day") or ""))
    return match.group(0) if match else ""


def _row_bvid(row: dict) -> str:
    text = str(row.get("bvid") or row.get("bv") or row.get("BVID") or "").strip()
    if BV_RE.fullmatch(text):
        return text
    match = ANY_BV_RE.search(str(row.get("url") or ""))
    return match.group(0) if match else ""


@_locked
def digest_from_seen(rows, month=None) -> dict:
    key = _month_value(month)
    ensure_home()
    _ensure_dir(DIGEST_DIR)
    target = DIGEST_DIR / f"{key}.md"

    try:
        existing = target.read_text(encoding="utf-8")
    except OSError:
        existing = ""

    known = set()
    for line in existing.splitlines():
        date_match = re.search(r"\d{4}-\d{2}-\d{2}", line)
        bv_match = ANY_BV_RE.search(line)
        if date_match and bv_match:
            known.add((date_match.group(0), bv_match.group(0)))

    additions = []
    if isinstance(rows, dict):
        rows = rows.get("rows") or []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        date = _row_date(row)
        bvid = _row_bvid(row)
        if not date or not bvid:
            continue
        marker = (date, bvid)
        if marker in known:
            continue
        known.add(marker)
        title = _one_line(row.get("title"))
        verdict = _one_line(row.get("verdict") or row.get("status"))
        reason = _one_line(row.get("reason"), 300)
        url = str(row.get("url") or "").strip()
        if not url.startswith("http"):
            url = f"https://www.bilibili.com/video/{bvid}"
        additions.append(f"- {date} · {bvid} · {title} · {verdict} · {reason} · {url}")

    if not additions:
        return {"ok": True, "added": 0, "path": str(target)}

    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n".join(additions) + "\n"
    try:
        _atomic_write(target, text)
    except OSError as exc:
        return {"ok": False, "added": 0, "path": str(target), "error": str(exc)}
    return {"ok": True, "added": len(additions), "path": str(target)}
