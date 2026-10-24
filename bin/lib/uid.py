"""B站 UID：只接受显式配置，不按昵称猜人。

主人身份只有两种合法来源：
  · 配置里的 mentionUid（推荐，私信收件人必须是它）；
  · 当前登录账号，且 mentionName 与登录昵称一致（只能用于评论区，不能私信自己）。

绝不按同名网友搜索结果推断 —— 昵称不唯一，猜错就会把私信和回复发给冒名者。
"""
from __future__ import annotations

import api
import config


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def my_uid(cred=None) -> int:
    """当前登录账号的 UID：凭证里的 dedeuserid，否则问 nav 接口。"""
    if cred is None:
        cred = api.load_credential()
        if not cred:
            return 0
    cred = cred if isinstance(cred, dict) else {}

    mid = api.our_mid(cred)
    if mid > 0:
        return mid
    try:
        account = api.own_account(api.headers_for(cred))
        return _as_int((account or {}).get("mid") if isinstance(account, dict) else 0)
    except Exception:
        return 0


def self_account(cred=None) -> dict:
    """当前登录账号：{mid, uname}；未登录或接口失败时字段可能为 0 / 空。"""
    if cred is None:
        cred = api.load_credential()
    if not cred:
        return {}
    cred = cred if isinstance(cred, dict) else {}
    mid = _as_int(api.our_mid(cred))
    uname = ""
    try:
        account = api.own_account(api.headers_for(cred))
        if isinstance(account, dict):
            mid = mid or _as_int(account.get("mid"))
            uname = str(account.get("uname") or "")
    except Exception:
        pass
    return {"mid": mid, "uname": uname} if mid or uname else {}


def owner_identity(cfg=None, me=None) -> tuple[int, str, str]:
    """返回 (mid, name, source)。source: config / self / none。"""
    cfg = cfg if isinstance(cfg, dict) else config.load_config()
    configured = _as_int(cfg.get("mentionUid"))
    name = str(cfg.get("mentionName") or "").strip()
    if configured > 0:
        return configured, name, "config"

    me = me if isinstance(me, dict) else self_account()
    if me and name and config.norm_name(me.get("uname")) == config.norm_name(name):
        mid = _as_int(me.get("mid"))
        if mid > 0:
            return mid, str(me.get("uname") or name), "self"
    return 0, name, "none"


def owner_uid(cfg=None) -> int:
    """配置里“本人”的 UID；没显式配置就是 0（登录号恰好是本人时除外）。"""
    return owner_identity(cfg)[0]


def owner_is_self(cfg=None) -> bool:
    """配置的本人就是当前登录账号吗？私信自己不可用，调用方要先拦。"""
    owner = owner_uid(cfg)
    return owner > 0 and owner == my_uid()
