from __future__ import annotations

import functools
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

HOME = Path(os.environ.get("BILI_SCROLL_HOME", str(Path.home() / ".qoder-bili-scroll"))).expanduser()

CONFIG_FILE = HOME / "config.json"
STATE_FILE = HOME / "state.json"
LEDGER_FILE = HOME / "conversation.json"
SEEN_FILE = HOME / "seen.md"
INBOX_FILE = HOME / "inbox.md"
POLICY_FILE = HOME / "reply-policy.json"
CRED_FILE = HOME / "bili_credential.json"
LOG_FILE = HOME / "duty.log"
OUT_DIR = HOME / "out"
ROUND_DIR = HOME / "round"
REPORTS_DIR = HOME / "reports"
DIGEST_DIR = HOME / "digest"
DM_INBOX_FILE = HOME / "dm-inbox.json"
DM_POLICY_FILE = HOME / "dm-policy.json"
DM_SEEN_FILE = HOME / "dm-seen.json"

DEFAULTS = {
    "perDay": 5,
    "perRound": 2,
    "keywords": [],
    "workspace": "",
    "reportLevel": "full",
    "autoComment": False,
    "commentPerRound": 1,
    "mentionName": "",
    "mentionUid": 0,
    "dmMode": "only",
    "dmReply": True,
    "dmReplyPerRound": 1,
    "dmReplyPerDay": 3,
    "personaPrompt": "",
    "videoNotes": True,
    "replyToComments": False,
    "replyPerRound": 1,
    "replyDepth": 5,
    "replyOnlyMention": True,
    "replyToMentions": True,
    "timezone": "Asia/Shanghai",
    "dailyRunTime": "20:30",
    "schemaVersion": 2,
}

_LOCK = threading.RLock()


def _locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return func(*args, **kwargs)

    return wrapper


def ensure_home() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(HOME, 0o700)
    except OSError:
        pass
    for sub in (OUT_DIR, ROUND_DIR, REPORTS_DIR, DIGEST_DIR, HOME / "tmp"):
        sub.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


@_locked
def write_json(path: Path, value, mode: int = 0o600) -> None:
    ensure_home()
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _normalize(src, base):
    out = dict(base)
    if not isinstance(src, dict):
        return out
    for key, default in base.items():
        value = src.get(key)
        if value is None:
            continue
        if isinstance(default, list):
            if isinstance(value, list):
                out[key] = [x for x in value if isinstance(x, str)]
        elif isinstance(default, bool):
            if isinstance(value, bool):
                out[key] = value
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif isinstance(default, str):
            if isinstance(value, str):
                out[key] = value
    return out


def _migrate_v1(raw: dict) -> dict:
    """把 0.1.x 配置一次性迁到 v2；老评论用户默认保持 dmMode=off，不突然改发私信。"""
    migrated = dict(raw)
    old_comments = bool(raw.get("autoComment")) or bool(raw.get("replyToComments"))
    migrated["dmMode"] = "off" if old_comments else "only"
    migrated["dmReply"] = not old_comments
    for key, default in (
        ("dmReplyPerRound", 1),
        ("dmReplyPerDay", 3),
        ("personaPrompt", ""),
        ("videoNotes", True),
    ):
        migrated.setdefault(key, default)
    migrated["schemaVersion"] = 2

    backup = CONFIG_FILE.with_name(CONFIG_FILE.name + ".v1.bak")
    try:
        if not backup.exists():
            shutil.copy2(CONFIG_FILE, backup)
    except OSError:
        pass
    write_json(CONFIG_FILE, _normalize(migrated, DEFAULTS))
    log(
        "配置迁移 v1→v2："
        + ("保留评论行为，dmMode=off、dmReply=false" if old_comments else "默认投递改为私信，dmMode=only")
        + f"；备份 {backup}"
    )
    return migrated


def load_config() -> dict:
    ensure_home()
    raw = read_json(CONFIG_FILE, {})
    if isinstance(raw, dict) and raw:
        try:
            version = int(raw.get("schemaVersion") or 1)
        except (TypeError, ValueError):
            version = 1
        if version < 2:
            raw = _migrate_v1(raw)
    return _normalize(raw, DEFAULTS)


@_locked
def save_config(patch: dict) -> dict:
    cfg = _normalize(patch, load_config())
    write_json(CONFIG_FILE, cfg)
    return cfg


def coerce_value(key: str, raw: str, current):
    if isinstance(current, bool):
        low = str(raw).strip().lower()
        if low in ("1", "true", "yes", "on", "开"):
            return True
        if low in ("0", "false", "no", "off", "关"):
            return False
        raise ValueError(f"{key} 需要 true/false")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, list):
        text = str(raw).strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                value = None
            if isinstance(value, list):
                return [str(x) for x in value]
            if value is not None:
                raise ValueError(f"{key} 需要字符串列表")
            # 例如 "[原神] 攻略" 不是 JSON，按普通文本切分
        return [x.strip() for x in re.split(r"[,，\n]", text) if x.strip()]
    return str(raw)


def day_key(dt: datetime | None = None) -> str:
    now = dt or datetime.now()
    return now.strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(message: str) -> None:
    ensure_home()
    line = f"{now_iso()}  {message}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def clip(text, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[:limit] + "…"


def norm_name(value) -> str:
    return re.sub(r"\s+", "", re.sub(r"^[@＠]+", "", str(value or ""))).lower()


def iso_time(seconds) -> str:
    try:
        sec = int(seconds or 0)
    except (TypeError, ValueError):
        sec = 0
    if sec <= 0:
        return ""
    return datetime.fromtimestamp(sec).astimezone().isoformat(timespec="seconds")
