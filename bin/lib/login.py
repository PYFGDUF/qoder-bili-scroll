from __future__ import annotations

import asyncio
import http.cookiejar
import io
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

import api
import gate
import ledger
from config import CRED_FILE, HOME, ensure_home, now_iso, write_json

_session = None
_qr_info = None

GEN_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate?source=main-fe-header"
POLL_URL = (
    "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    "?qrcode_key={key}&source=main-fe-header"
)
OPEN_HDRS = {
    "User-Agent": api.UA,
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}


def _field(cred, *names) -> str:
    """兼容 Credential 对象、dict、cookies 字典三种形态。"""
    for name in names:
        if isinstance(cred, dict):
            value = cred.get(name)
        else:
            value = getattr(cred, name, None)
        if value:
            return str(value)
    return ""


def _save_credential(cred):
    ensure_home()
    payload = {
        "sessdata": _field(cred, "sessdata", "SESSDATA"),
        "bili_jct": _field(cred, "bili_jct"),
        "buvid3": _field(cred, "buvid3"),
        "dedeuserid": _field(cred, "dedeuserid", "DedeUserID"),
    }
    if not payload["sessdata"] or not payload["bili_jct"]:
        return None
    if not payload["buvid3"]:
        try:
            spi = api.jget("https://api.bilibili.com/x/frontend/finger/spi")
            b3 = (spi.get("data") or {}).get("b_3")
            if b3:
                payload["buvid3"] = str(b3)
        except Exception:
            pass
    # 先用新凭证做一次 nav 校验；不通就不要覆盖旧凭证。
    me = api.own_account(api.headers_for(payload))
    if not me:
        return None
    write_json(CRED_FILE, payload)
    try:
        os.chmod(CRED_FILE, 0o600)
    except OSError:
        pass
    return me


def _fetch_json(opener, url: str) -> dict:
    request = urllib.request.Request(url, headers=OPEN_HDRS)
    try:
        with opener.open(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
        return json.loads(body)
    except Exception as exc:
        return {"code": -998, "message": str(exc)}


def _begin_qr() -> dict | None:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = _fetch_json(opener, GEN_URL)
    if data.get("code") != 0:
        return None
    payload = data.get("data") or {}
    key = str(payload.get("qrcode_key") or "")
    url = str(payload.get("url") or "")
    if not key or not url:
        return None

    ensure_home()
    try:
        import qrcode  # 二维码图；依赖缺失时只影响图片，不影响轮询
    except Exception:
        qrcode = None

    qr_file = HOME / "qr.png"
    terminal = ""
    if qrcode is not None:
        try:
            qrcode.make(url).save(qr_file)
            os.chmod(qr_file, 0o600)
        except Exception:
            pass
        try:
            code = qrcode.QRCode(border=1)
            code.add_data(url)
            code.make(fit=True)
            buffer = io.StringIO()
            code.print_ascii(out=buffer, invert=True)
            terminal = buffer.getvalue()
        except Exception:
            terminal = ""

    qr_text_file = HOME / "qr.txt"
    try:
        qr_text_file.write_text(url, encoding="utf-8")
        os.chmod(qr_text_file, 0o600)
    except OSError:
        pass

    return {
        "key": key,
        "jar": jar,
        "opener": opener,
        "info": {
            "ok": True,
            "status": "qrcode_ready",
            "message": "用 B站 App 扫码；确认时选「记住我 / 长期」。",
            "qr_file": str(qr_file),
            "qr_text_file": str(qr_text_file),
            "terminal_chars": len(terminal),
            "qrcode_url": url,
            "next_step": "扫码后调用 bili_login_poll（每 3 秒一次，最多 60 次）。",
        },
    }


def _poll_once(session: dict) -> dict:
    url = POLL_URL.format(key=urllib.parse.quote(str(session["key"])))
    data = _fetch_json(session["opener"], url)
    inner = data.get("data") or {}
    code = inner.get("code")

    if code == 86101:
        return {"ok": True, "status": "waiting", "message": "等待扫码。"}
    if code == 86090:
        return {"ok": True, "status": "scanned", "message": "已扫码，等待手机确认。"}
    if code == 86038:
        return {"ok": False, "status": "timeout", "message": "二维码过期，请重新调用 bili_login_start。"}
    if data.get("code") == 0 and code == 0:
        # B 站改为 Set-Cookie 下发凭证；同时保留 url 查询串作为兜底。
        cookies = {}
        for cookie in session["jar"]:
            cookies[cookie.name] = cookie.value
        cross = str(inner.get("url") or "")
        query = urllib.parse.parse_qs(urllib.parse.urlparse(cross).query)
        payload = {
            "sessdata": cookies.get("SESSDATA") or (query.get("SESSDATA") or [""])[0],
            "bili_jct": cookies.get("bili_jct") or (query.get("bili_jct") or [""])[0],
            "buvid3": cookies.get("buvid3") or (query.get("buvid3") or [""])[0],
            "dedeuserid": cookies.get("DedeUserID") or (query.get("DedeUserID") or [""])[0],
        }
        return {"ok": True, "status": "done", "payload": payload}
    return {
        "ok": False,
        "status": "unknown",
        "message": f"未知状态：{code if code is not None else data.get('code')}",
        "raw": str(inner or data),
    }


async def start_login(force: bool = False) -> dict:
    global _session, _qr_info
    if force:
        _session = None
        _qr_info = None
    cred = api.load_credential()
    if cred and not force:
        return {"ok": True, "status": "already_logged_in", "message": "已登录，无需重复扫码；要换号请用 force=true。"}
    if _session is not None and _qr_info:
        return {"ok": True, **_qr_info}

    session = await asyncio.to_thread(_begin_qr)
    if not session:
        return {"ok": False, "status": "error", "message": "生成二维码失败，请稍后重试。"}
    _session = session
    _qr_info = session["info"]
    return dict(_qr_info)


async def poll_login() -> dict:
    global _session, _qr_info
    if _session is None:
        cred = api.load_credential()
        if cred:
            return {"ok": True, "status": "already_logged_in", "message": "已登录。"}
        return {"ok": False, "status": "no_session", "message": "没有进行中的登录，请先调用 bili_login_start。"}

    result = await asyncio.to_thread(_poll_once, _session)
    status = result.get("status")

    if status == "timeout":
        _session = None
        _qr_info = None
        return result

    if status == "done":
        payload = result.get("payload") or {}
        me = _save_credential(payload)
        _session = None
        _qr_info = None
        if not me:
            return {
                "ok": False,
                "status": "invalid_credential",
                "message": "登录返回的凭证不完整或无效；已保留旧凭证，请重新扫码。",
                "credential_file": str(CRED_FILE),
            }
        # 换号/重新登录后旧的 Owner UID 不再可信；重新配置 mentionUid 后才能继续。
        ledger.update_fields(
            owner={},
            me={"mid": me.get("mid"), "uname": me.get("uname"), "at": now_iso()},
        )
        gate.write_reply_policy([], run_id="login", allow_new=False)
        return {
            "ok": True,
            "status": "done",
            "message": "登录成功，凭证已保存。",
            "me": me,
            "credential_file": str(CRED_FILE),
        }

    return result


def logout() -> dict:
    global _session, _qr_info
    _session = None
    _qr_info = None
    try:
        CRED_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        return {"ok": False, "error": f"删除凭证失败：{exc}"}
    for name in ("qr.png", "qr.txt"):
        try:
            (HOME / name).unlink()
        except OSError:
            pass
    ledger.update_fields(me={}, owner={})
    gate.write_reply_policy([], run_id="logout", allow_new=False)
    return {"ok": True, "message": "已退出登录并清除本地凭证。", "credential_file": str(CRED_FILE)}


def _open_file(path: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def run_cli(open_qr: bool = True, force: bool = False) -> int:
    async def runner():
        started = await start_login(force=force)
        print(json.dumps(started, ensure_ascii=False))
        if started.get("status") == "already_logged_in":
            return 0
        qr_file = started.get("qr_file")
        if qr_file and open_qr:
            _open_file(qr_file)
        for _ in range(60):
            await asyncio.sleep(3)
            result = await poll_login()
            status = result.get("status")
            print(f"[login] {status}", file=sys.stderr)
            if status in ("done", "already_logged_in"):
                print(json.dumps(result, ensure_ascii=False))
                return 0
            if status in ("timeout", "no_session", "invalid_credential"):
                print(json.dumps(result, ensure_ascii=False))
                return 2
        print(json.dumps({"ok": False, "status": "timeout", "message": "等待超时，请重新登录。"}, ensure_ascii=False))
        return 2

    return asyncio.run(runner())
