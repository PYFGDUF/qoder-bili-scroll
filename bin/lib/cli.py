#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

import api
import gate
import ledger
import report as report_mod
import seen as seen_mod
import mcp_server_safe as core


def emit(value, code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False))
    if code == 0 and isinstance(value, dict) and value.get("ok") is False:
        return 1
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bili", description="bili-scroll 调试/运维 CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    env = sub.add_parser("env")
    env_sub = env.add_subparsers(dest="env_command", required=True)
    env_sub.add_parser("check")

    login = sub.add_parser("login")
    login.add_argument("--no-open", action="store_true", help="不自动打开二维码图片")
    login.add_argument("--force", action="store_true", help="已登录时仍重新扫码（换号）")

    sub.add_parser("whoami")

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("list")
    get_parser = config_sub.add_parser("get")
    get_parser.add_argument("key")
    set_parser = config_sub.add_parser("set")
    set_parser.add_argument("key")
    set_parser.add_argument("value")
    set_parser.add_argument("--confirm", action="store_true")

    round_parser = sub.add_parser("round")
    round_sub = round_parser.add_subparsers(dest="round_command", required=True)
    begin = round_sub.add_parser("begin")
    begin.add_argument("--mode", default="scan", choices=["scan", "chat"])
    begin.add_argument("--source", default="manual", choices=["cron", "manual"])
    end = round_sub.add_parser("end")
    end.add_argument("--run-id", required=True)
    end.add_argument("--status", default="ok", choices=["ok", "failed", "skipped"])
    end.add_argument("--summary", default="")

    sub.add_parser("inbox")

    seen = sub.add_parser("seen")
    seen_sub = seen.add_subparsers(dest="seen_command", required=True)
    seen_add = seen_sub.add_parser("add")
    seen_add.add_argument("bvid")
    seen_add.add_argument("--title", default="")
    seen_add.add_argument("--verdict", default="跳过")
    seen_add.add_argument("--reason", default="")
    seen_check = seen_sub.add_parser("check")
    seen_check.add_argument("bvid")
    seen_sub.add_parser("list")

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    report_show = report_sub.add_parser("show")
    report_show.add_argument("--day", default="")
    report_sub.add_parser("list")
    report_ensure = report_sub.add_parser("ensure")
    report_ensure.add_argument("--day", default="")

    verify = sub.add_parser("verify")
    verify.add_argument("bvid")
    verify.add_argument("rpid", type=int)
    verify.add_argument("--root", type=int, default=0)

    claim = sub.add_parser("claim")
    claim.add_argument("bvid")
    claim.add_argument("rpid", type=int)

    sub.add_parser("selftest")
    sub.add_parser("version")

    args = parser.parse_args(argv)

    if args.command == "env":
        return emit(core._env_info())

    if args.command == "login":
        import login as login_mod

        return login_mod.run_cli(open_qr=not args.no_open, force=args.force)

    if args.command == "whoami":
        cred = api.load_credential()
        if not cred:
            return emit({"ok": False, "logged_in": False, "message": "未登录"}, 2)
        me = api.own_account(api.headers_for(cred))
        return emit({"ok": True, "logged_in": True, "me": me}, 0 if me else 2)

    if args.command == "config":
        import asyncio

        if args.config_command == "list":
            return emit(json.loads(asyncio.run(core.bili_config(action="list"))))
        if args.config_command == "get":
            return emit(json.loads(asyncio.run(core.bili_config(action="get", key=args.key))))
        return emit(
            json.loads(
                asyncio.run(
                    core.bili_config(
                        action="set",
                        key=args.key,
                        value=args.value,
                        confirm=args.confirm,
                    )
                )
            )
        )

    if args.command == "round":
        if args.round_command == "begin":
            return emit(core._round_begin_sync(args.mode, args.source))
        return emit(core._round_end_sync(args.run_id, args.status, args.summary))

    if args.command == "inbox":
        return emit(
            core.inbox_mod.build_inbox(
                force=True,
                view_only=True,
                allow_new_comment=False,
            )
        )

    if args.command == "seen":
        if args.seen_command == "add":
            return emit(seen_mod.add(args.bvid, args.title, args.verdict, args.reason))
        if args.seen_command == "check":
            return emit(seen_mod.check(args.bvid))
        return emit(seen_mod.list_rows())

    if args.command == "report":
        if args.report_command == "show":
            return emit(report_mod.show_report(args.day or None))
        if args.report_command == "list":
            return emit({"ok": True, "reports": report_mod.list_reports()})
        return emit(report_mod.ensure_report(args.day or None))

    if args.command == "verify":
        return emit(report_mod.verify_comment(args.bvid, args.rpid, args.root))

    if args.command == "claim":
        cred = api.load_credential()
        if not cred:
            return emit({"ok": False, "error": "未登录"}, 2)
        return emit(
            ledger.claim_comment(
                args.bvid,
                args.rpid,
                api.headers_for(cred),
                api.our_mid(cred),
                {},
            )
        )

    if args.command == "selftest":
        return emit(_selftest())

    if args.command == "version":
        from pathlib import Path

        payload = json.loads((Path(core.PLUGIN_ROOT) / ".qoder-plugin" / "plugin.json").read_text(encoding="utf-8"))
        return emit({"ok": True, "name": payload.get("name"), "version": payload.get("version")})

    return emit({"ok": False, "error": "未知命令"}, 3)


def _selftest() -> dict:
    checks = []
    try:
        env = core._env_info()
        checks.append({"name": "env", "ok": True, "data_dir": env["data_dir"], "python": env["python"]})
    except Exception as exc:
        return {"ok": False, "checks": [{"name": "env", "ok": False, "error": str(exc)}]}

    try:
        vendor_path = core.UP.__file__
        checks.append({"name": "vendor", "ok": True, "file": vendor_path, "cred_file": str(core.UP.CRED_FILE)})
    except Exception as exc:
        checks.append({"name": "vendor", "ok": False, "error": str(exc)})

    text_cases = [
        ("好耶", "", True),
        ("看这个 https://example.com", "", False),
        ("@" + ("某人" * 60), "", False),
        ("@" + "我", "我", True),
        ("＠我", "我", True),
        ("＠别人", "", False),
        ("1234567890123", "", False),
    ]
    for text, mention, expected in text_cases:
        ok, _ = gate.check_text(text, mention)
        checks.append({"name": f"gate:{text[:12]}", "ok": ok == expected, "got": ok})

    fixture = """## 评论记录
| BV | rpid | root | 时间 | 正文 |
| --- | --- | --- | --- | --- |
| BV1xx411c7mD | 1234567890 | 1234567890 | 2026-10-21 | hi |
"""
    rows = ledger.parse_seen(fixture)
    checks.append({"name": "seen-parse", "ok": len(rows) == 1 and rows[0]["rpid"] == 1234567890, "rows": len(rows)})

    return {"ok": all(item.get("ok") for item in checks), "checks": checks}


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
