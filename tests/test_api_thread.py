#!/usr/bin/env python3
"""楼中楼分页：必须读到最后一页，才能判断最后说话的人。"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="bili-api-thread-test-"))
os.environ["BILI_SCROLL_HOME"] = str(TMP)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin" / "lib"))

import api  # noqa: E402

PAGES = {
    1: [(1, 100), (2, 200)],
    2: [(3, 210)],
    3: [(45, 300)],
}


def fake_jget(url, headers=None, timeout=20):
    page = int(re.search(r"pn=(\d+)", url).group(1))
    rows = PAGES.get(page, [])
    replies = [
        {
            "rpid": rpid,
            "root": 111,
            "ctime": ctime,
            "member": {"mid": 200, "uname": "owner"},
            "content": {"message": f"m{rpid}"},
        }
        for rpid, ctime in rows
    ]
    if page == 1:
        replies.append(
            {
                "rpid": 99,
                "root": 111,
                "ctime": 1,
                "member": {"mid": 200, "uname": "owner"},
                "content": {"message": "hidden"},
                "invisible": True,
            }
        )
    return {"code": 0, "data": {"page": {"count": 45}, "replies": replies}}


def main():
    api.video_meta = lambda bvid, headers, cache=None: {"aid": 123, "mid": 1, "title": "t"}
    api.sleep = lambda seconds: None
    api.jget = fake_jget
    result = api.fetch_thread("BV1xx411c7mD", 111, {})
    assert result["ok"], result
    assert result["pages"] == [1, 2, 3], result
    assert result["total"] == 45, result
    assert [row["rpid"] for row in result["replies"]] == [1, 2, 3, 45], result["replies"]
    assert result["replies"][-1]["rpid"] == 45
    assert all(row["rpid"] != 99 for row in result["replies"]), result["replies"]

    def failing_jget(url, headers=None, timeout=20):
        page = int(re.search(r"pn=(\d+)", url).group(1))
        if page == 3:
            return {"code": -412, "message": "risk control"}
        return fake_jget(url, headers, timeout)

    api.jget = failing_jget
    partial = api.fetch_thread("BV1xx411c7mD", 111, {})
    assert partial["ok"] is False and partial["partial"] is True, partial
    assert partial["replies"], partial
    assert partial["replies"][-1]["rpid"] != 45
    print("thread pagination OK")


if __name__ == "__main__":
    main()
