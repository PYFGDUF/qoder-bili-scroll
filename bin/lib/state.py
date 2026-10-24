from __future__ import annotations

import functools
import threading
import uuid
from datetime import datetime

from config import day_key, load_config, now_iso, read_json, write_json, STATE_FILE

_LOCK = threading.RLock()


def _locked(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return func(*args, **kwargs)

    return wrapper


def load_state() -> dict:
    state = read_json(STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("schemaVersion", 1)
    state.setdefault("day", "")
    state.setdefault("count", 0)
    state.setdefault("runs", [])
    return state


def save_state(state: dict) -> dict:
    if len(state.get("runs", [])) > 100:
        state["runs"] = state["runs"][-100:]
    write_json(STATE_FILE, state)
    return state


def left_today() -> int:
    cfg = load_config()
    state = load_state()
    today = day_key()
    used = int(state.get("count") or 0) if state.get("day") == today else 0
    return max(0, max(0, int(cfg["perDay"])) - used)


@_locked
def reserve_round(mode: str = "scan", source: str = "manual") -> dict:
    cfg = load_config()
    state = load_state()
    now = datetime.now()
    today = day_key(now)
    used = int(state.get("count") or 0) if state.get("day") == today else 0
    per_day = max(0, int(cfg["perDay"]))
    per_round = max(1, int(cfg["perRound"]))

    if mode != "chat":
        if per_day <= 0 and source == "cron":
            return {
                "ok": True,
                "action": "stop",
                "reason": "「每天最多看几条」是 0 ⇒ 自动轮不跑；手动仍可跑。",
                "quota": 0,
                "leftToday": 0,
            }
        if per_day > 0 and used >= per_day:
            return {
                "ok": True,
                "action": "stop",
                "reason": f"今天的额度已用完（{used}/{per_day}）；明天再跑，或调大 perDay。",
                "quota": 0,
                "leftToday": 0,
            }
        quota = max(1, min(per_round, per_day - used)) if per_day > 0 else per_round
    else:
        quota = 0

    run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    if mode != "chat":
        state["day"] = today
        # perDay=0 表示自动轮关闭：手动轮照跑，但不消耗一个并不存在的自动额度。
        state["count"] = used + quota if per_day > 0 else used

    runs = state.get("runs")
    if not isinstance(runs, list):
        runs = []
    runs.append(
        {
            "run_id": run_id,
            "startedAt": now_iso(),
            "source": source,
            "mode": mode,
            "status": "running",
            "quota": quota,
        }
    )
    state["runs"] = runs
    state["lastRunAt"] = now_iso()
    state["lastRoundQuota"] = quota
    if mode == "chat":
        # chat 不扣视频额度；跨天时不能用昨天的 count。
        state["lastRoundLeft"] = max(0, per_day - used)
    else:
        state["lastRoundLeft"] = max(0, per_day - int(state.get("count") or 0))
    save_state(state)

    return {
        "ok": True,
        "action": "run",
        "run_id": run_id,
        "quota": quota,
        "leftToday": state["lastRoundLeft"],
        "mode": mode,
        "source": source,
    }


@_locked
def finish_round(run_id: str, status: str, summary: str = "") -> dict:
    state = load_state()
    runs = state.get("runs") or []
    found = None
    for run in runs:
        if run.get("run_id") == run_id:
            run["status"] = status
            run["finishedAt"] = now_iso()
            if summary:
                run["summary"] = str(summary)[:500]
            found = run
            break
    state["runs"] = runs
    state["lastRunStatus"] = status
    save_state(state)
    return {"ok": True, "run_id": run_id, "status": status, "found": bool(found)}
