from __future__ import annotations

import json
import socket
import time
import urllib.parse
import urllib.request

from config import CRED_FILE, read_json

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# 这台机器的 IPv6 出口不通：getaddrinfo 默认把 AAAA 排在前面，
# urllib / aiohttp 会先连 IPv6 然后卡到超时。统一优先 IPv4，只有拿不到 A 记录时才回退。
_ORIG_GETADDRINFO = socket.getaddrinfo


def _ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
    if family in (0, socket.AF_UNSPEC):
        try:
            infos = _ORIG_GETADDRINFO(host, port, socket.AF_INET, type, proto, flags)
            if infos:
                return infos
        except socket.gaierror:
            pass
    return _ORIG_GETADDRINFO(host, port, family, type, proto, flags)


socket.getaddrinfo = _ipv4_first


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def load_credential():
    data = read_json(CRED_FILE, None)
    if not isinstance(data, dict) or not data.get("sessdata"):
        return None
    return data


def headers_for(cred) -> dict:
    cookie = f"SESSDATA={cred.get('sessdata', '')}; bili_jct={cred.get('bili_jct', '')}; buvid3={cred.get('buvid3', '')}"
    return {
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
        "Cookie": cookie,
    }


def jget(url: str, headers: dict | None = None, timeout: int = 20) -> dict:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"code": -999, "message": "not json"}
    except Exception as exc:
        return {"code": -998, "message": str(exc)}


def our_mid(cred) -> int:
    value = str((cred or {}).get("dedeuserid") or (cred or {}).get("DedeUserID") or "")
    return int(value) if value.isdigit() else 0


def own_account(headers: dict):
    data = jget("https://api.bilibili.com/x/web-interface/nav", headers)
    if data.get("code") != 0 or not (data.get("data") or {}).get("isLogin"):
        return None
    payload = data["data"]
    return {"mid": int(payload.get("mid") or 0), "uname": str(payload.get("uname") or "")}


def search_user(name: str, headers: dict):
    """按昵称搜 B 站用户；返回 data.result 列表（失败返回 []）。"""
    url = (
        "https://api.bilibili.com/x/web-interface/search/type?search_type=bili_user&page=1&keyword="
        + urllib.parse.quote(str(name or "").strip())
    )
    data = jget(url, {**headers, "Referer": "https://search.bilibili.com/"})
    if data.get("code") != 0:
        return []
    result = (data.get("data") or {}).get("result")
    return result if isinstance(result, list) else []


def video_meta(bvid: str, headers: dict, cache: dict | None = None):
    key = f"bv:{bvid}"
    if cache is not None and key in cache:
        return cache[key]
    data = jget(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", headers)
    meta = None
    if data.get("code") == 0 and isinstance(data.get("data"), dict):
        payload = data["data"]
        meta = {
            "aid": int(payload.get("aid") or 0),
            "mid": int((payload.get("owner") or {}).get("mid") or 0),
            "title": str(payload.get("title") or ""),
        }
    if cache is not None:
        cache[key] = meta
    sleep(0.3)
    return meta


def bvid_of_aid(aid: int, headers: dict, cache: dict | None = None) -> str:
    key = f"aid:{int(aid or 0)}"
    if cache is not None and key in cache:
        return cache[key]
    data = jget(f"https://api.bilibili.com/x/web-interface/view?aid={int(aid or 0)}", headers)
    bvid = str((data.get("data") or {}).get("bvid") or "") if data.get("code") == 0 else ""
    if cache is not None:
        cache[key] = bvid
    sleep(0.25)
    return bvid


def fetch_at_feed(headers: dict):
    data = jget(
        "https://api.bilibili.com/x/msgfeed/at?platform=web&build=0&mobi_app=web",
        headers,
    )
    if data.get("code") != 0:
        return {
            "ok": False,
            "items": [],
            "raw": 0,
            "error": f"接口回 code={data.get('code', '?')} {data.get('message', '')}".strip(),
        }
    items = (data.get("data") or {}).get("items")
    items = items if isinstance(items, list) else []
    return {"ok": True, "items": items, "raw": len(items)}


def parse_at_uri(uri: str) -> dict:
    import re

    text = str(uri or "")
    bvid = (re.search(r"(BV[0-9A-Za-z]{10})", text) or [None, ""])[1]
    match = re.search(r"[#?&]reply(\d{5,})", text)
    root_match = re.search(r"(?:comment_root_id|root_id|root)=(\d{5,})", text)
    return {
        "bvid": bvid,
        "rpid": int(match.group(1)) if match else 0,
        "root": int(root_match.group(1)) if root_match else 0,
    }


def fetch_thread(bvid: str, root: int, headers: dict, cache: dict | None = None, ps: int = 20, max_pages: int = 5) -> dict:
    meta = video_meta(bvid, headers, cache)
    if not meta:
        return {"ok": False, "replies": [], "error": "取视频信息失败（视频可能已删/不可见）"}

    def fetch_page(page: int) -> dict:
        return jget(
            f"https://api.bilibili.com/x/v2/reply/reply?type=1&oid={meta['aid']}&root={int(root)}&ps={ps}&pn={page}",
            headers,
        )

    first = fetch_page(1)
    if first.get("code") != 0:
        return {
            "ok": False,
            "replies": [],
            "error": f"接口回 code={first.get('code', '?')} {first.get('message', '')}".strip(),
        }

    total = int(((first.get("data") or {}).get("page") or {}).get("count") or 0)
    last_page = max(1, (total + ps - 1) // ps) if total else 1
    max_pages = max(2, int(max_pages))
    if last_page <= max_pages:
        pages = set(range(1, last_page + 1))
    else:
        pages = set(range(1, max_pages))
        pages.add(last_page)

    rows = {}

    def sorted_rows():
        return sorted(rows.values(), key=lambda row: row["ctime"])

    for page in sorted(pages):
        data = first if page == 1 else fetch_page(page)
        if data.get("code") != 0:
            error = f"接口回 code={data.get('code', '?')} {data.get('message', '')}".strip()
            if page == 1:
                return {
                    "ok": False,
                    "partial": False,
                    "replies": [],
                    "error": error,
                }
            # 非首页失败：不能把截断结果当成完整串，否则会误判最后说话的人。
            return {
                "ok": False,
                "partial": True,
                "replies": sorted_rows(),
                "total": total,
                "pages": sorted(pages),
                "error": error,
            }
        for item in ((data.get("data") or {}).get("replies") or []):
            member = item.get("member") or {}
            content = item.get("content") or {}
            rpid = int(item.get("rpid") or 0)
            if not rpid or bool(item.get("invisible")):
                continue
            rows[rpid] = {
                "rpid": rpid,
                "root": int(item.get("root") or 0),
                "parent": int(item.get("parent") or 0),
                "mid": int(item.get("mid") or member.get("mid") or 0),
                "uname": str(member.get("uname") or ""),
                "text": str(content.get("message") or ""),
                "ctime": int(item.get("ctime") or 0),
                "like": int(item.get("like") or 0),
                "invisible": False,
            }
        if page != 1:
            sleep(0.2)

    replies = sorted_rows()
    return {
        "ok": True,
        "partial": False,
        "replies": replies,
        "total": total or len(replies),
        "pages": sorted(pages),
    }
