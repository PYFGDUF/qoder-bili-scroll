#!/usr/bin/env python3
"""登录凭证校验与登出清理。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-login-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import api  # noqa: E402
import config  # noqa: E402
import login  # noqa: E402

ME = {"mid": 123, "uname": "me"}


class Cred:
    sessdata = "sess"
    bili_jct = "jct"
    buvid3 = "bv"
    dedeuserid = "123"


def main():
    api.jget = lambda url, headers=None, timeout=20: {"code": 0, "data": {"b_3": "bv"}}
    api.own_account = lambda headers: ME

    me = login._save_credential(Cred())
    assert me == ME, me
    assert config.CRED_FILE.exists()
    assert config.CRED_FILE.stat().st_mode & 0o777 == 0o600

    old = {"sessdata": "old", "bili_jct": "old", "buvid3": "", "dedeuserid": "1"}
    config.write_json(config.CRED_FILE, old)
    bad = Cred()
    bad.sessdata = ""
    assert login._save_credential(bad) is None
    assert config.CRED_FILE.read_text(encoding="utf-8").find('"sessdata": "old"') >= 0

    api.own_account = lambda headers: None
    assert login._save_credential(Cred()) is None
    assert config.CRED_FILE.read_text(encoding="utf-8").find('"sessdata": "old"') >= 0

    login._session = object()
    login._qr_info = {"x": 1}
    result = login.logout()
    assert result["ok"] is True, result
    assert login._session is None and login._qr_info is None
    assert not config.CRED_FILE.exists()
    print("login credentials OK")


if __name__ == "__main__":
    main()
