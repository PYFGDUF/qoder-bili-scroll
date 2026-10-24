from __future__ import annotations

import re
from pathlib import Path

import api
from config import LOG_FILE, OUT_DIR, day_key, load_config, now_iso


def report_path(day: str | None = None) -> Path:
    key = str(day or day_key())
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        key = day_key()
    return OUT_DIR / f"{key}.md"


def list_reports() -> list:
    if not OUT_DIR.exists():
        return []
    return sorted((path.name for path in OUT_DIR.glob("*.md")), reverse=True)


def _log_tail(lines: int = 40) -> str:
    try:
        content = LOG_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def ensure_report(day: str | None = None, run_id: str = "", status: str = "ok") -> dict:
    cfg = load_config()
    if str(cfg.get("reportLevel") or "full") == "off":
        return {"ok": True, "skipped": True, "reason": "reportLevel=off"}
    target = report_path(day)
    if target.exists():
        return {"ok": True, "exists": True, "file": str(target)}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tail = _log_tail()
    body = [
        f"# 刷B站日报 · {target.stem}",
        "",
        f"> 这一轮没有正常产出报告（run_id: {run_id or '未知'}，status: {status}）。",
        "> 下面附日志尾部，方便定位；数据目录里的 `duty.log` 有完整版。",
        "",
    ]
    if tail:
        body.extend(["```", tail, "```"])
    target.write_text("\n".join(body) + "\n", encoding="utf-8")
    return {"ok": True, "exists": False, "file": str(target), "fallback": True}


def show_report(day: str | None = None) -> dict:
    target = report_path(day)
    try:
        content = target.read_text(encoding="utf-8")
    except OSError:
        return {"ok": False, "error": f"没有 {target.name} 的报告", "reports": list_reports()}
    return {"ok": True, "file": str(target), "content": content, "reports": list_reports()}


def verify_comment(bvid: str, rpid: int, root: int = 0) -> dict:
    cred = api.load_credential()
    if not cred:
        return {"ok": False, "error": "未登录"}
    headers = api.headers_for(cred)
    meta = api.video_meta(bvid, headers)
    if not meta:
        return {"ok": False, "error": "取不到视频信息（可能已删/不可见）"}
    rid = int(rpid or 0)

    if root:
        thread = api.fetch_thread(bvid, int(root), headers)
        if not thread.get("ok"):
            return {"ok": True, "visible": None, "how": "楼中楼", "note": f"没查到（{thread.get('error')}）"}
        for row in thread.get("replies") or []:
            if int(row.get("rpid") or 0) == rid:
                return {"ok": True, "visible": True, "how": "楼中楼", "likes": row.get("like"), "note": "公开可见"}
        return {
            "ok": True,
            "visible": None,
            "how": "楼中楼",
            "note": "没查到（可能在审核，或被翻页挡住；不等于没发出去）",
        }

    data = api.jget(
        f"https://api.bilibili.com/x/v2/reply/main?type=1&oid={meta['aid']}&mode=2&next=0&ps=20",
        headers,
    )
    for row in ((data.get("data") or {}).get("replies") or []):
        if int(row.get("rpid") or 0) == rid:
            return {
                "ok": True,
                "visible": True,
                "how": "顶层按时间第一页",
                "likes": int(row.get("like") or 0),
                "note": "公开可见",
            }
    return {
        "ok": True,
        "visible": None,
        "how": "顶层按时间第一页",
        "note": "没查到（可能在审核，或被翻页挡住；不等于没发出去）",
    }


def verify_recent(records: list) -> dict:
    results = []
    for record in records or []:
        results.append(
            {
                "bv": record.get("bv"),
                "rpid": record.get("rpid"),
                **verify_comment(record.get("bv"), record.get("rpid"), record.get("root") or 0),
            }
        )
    return {"ok": True, "checked": len(results), "results": results, "at": now_iso()}
