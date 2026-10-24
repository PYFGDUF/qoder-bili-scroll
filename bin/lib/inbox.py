from __future__ import annotations

import time
from datetime import datetime

import api
import gate
import ledger
from config import (
    INBOX_FILE,
    clip,
    iso_time,
    load_config,
    norm_name,
    now_iso,
)


def _pick(obj, paths):
    for path in paths:
        current = obj
        found = True
        for key in str(path).split("."):
            if not isinstance(current, dict) or key not in current:
                found = False
                break
            current = current[key]
        if found and current is not None and current != "":
            return current
    return None


def normalize_at_item(raw) -> dict | None:
    item = (raw or {}).get("item") or {}
    user = (raw or {}).get("user") or {}
    uri = str(_pick(item, ["uri", "native_uri"]) or "")
    from_uri = api.parse_at_uri(uri)
    try:
        fallback_rpid = int(_pick(item, ["source_id", "target_id", "reply.rpid", "reply_id"]) or 0)
    except (TypeError, ValueError):
        fallback_rpid = 0
    rpid = int(from_uri.get("rpid") or 0) or fallback_rpid
    if not rpid:
        return None
    try:
        aid_raw = int(_pick(item, ["subject_id", "oid"]) or _pick(item, ["business_id"]) or 0)
    except (TypeError, ValueError):
        aid_raw = 0
    aid = aid_raw if aid_raw > 1_000_000 else 0
    return {
        "bvid": str(from_uri.get("bvid") or _pick(item, ["bvid"]) or ""),
        "aid": aid,
        "rpid": rpid,
        "root": int(_pick(item, ["root_id", "reply.root"]) or from_uri.get("root") or 0),
        "mid": int(_pick(user, ["mid", "uid", "id"]) or 0),
        "uname": str(_pick(user, ["nickname", "uname", "name"]) or ""),
        "text": str(
            _pick(
                item,
                ["source_content", "reply.content.message", "reply.content", "content.message", "nat_text"],
            )
            or ""
        ),
        "title": str(_pick(item, ["title"]) or ""),
        "at": int(_pick(raw, ["at_time", "item.at_time", "time"]) or 0),
        "uri": uri,
    }


def render_inbox(items: list) -> str:
    if not items:
        return "（现在没有人在等你回话）"
    lines = []
    for index, item in enumerate(items, 1):
        source = item.get("from") or {}
        via = ""
        if item.get("via") == "at":
            where = item.get("where")
            via = f"（⭐ 这是他在别的视频的评论区 @ 了你{('：《' + where + '》') if where else ''} —— 就在他这条评论下面回）"
        lines.append(f"### {index}) {item.get('bv')} · 这一串已经来回 {item.get('turns')} 轮，最多还能回 {item.get('left')} 轮{via}")
        lines.append(
            f'- 要有话说，就用 bili_reply 这么填：bvid="{item.get("bv")}"  rpid={source.get("rpid")}  root={item.get("root")}'
        )
        lines.append(f'- 说这话的人：{source.get("uname") or "（没名字）"}（{source.get("at")}）')
        lines.append(f'- 他说的：{source.get("text") or "（空的）"}')
        history = item.get("history") or []
        if history:
            lines.append("- 这段来回之前说过什么（按时间，最新的在最后）：")
            for message in history:
                who = "我" if message.get("who") == "me" else (message.get("uname") or "他")
                lines.append(f"  - {who}：{message.get('text')}")
        lines.append("")
    return "\n".join(lines)


def build_inbox(
    max_items: int | None = None,
    depth: int | None = None,
    only_name: str | None = None,
    include_at: bool | None = None,
    force: bool = False,
    view_only: bool = False,
    allow_new_comment: bool | None = None,
    run_id: str = "",
    log_fn=None,
) -> dict:
    cfg = load_config()
    why = {}

    def bump(key):
        why[key] = why.get(key, 0) + 1

    def note(message):
        if log_fn:
            log_fn(message)

    if not cfg.get("replyToComments") and not force:
        policy = gate.write_reply_policy([], run_id, allow_new=allow_new_comment)
        return {
            "ok": True,
            "items": [],
            "text": "（评论区来回对话关着）",
            "error": "",
            "scanned": 0,
            "why": why,
            "owner": policy.get("owner") or {},
            "me": {},
        }

    if cfg.get("replyOnlyMention") and not str(cfg.get("mentionName") or "").strip() and not int(
        cfg.get("mentionUid") or 0
    ) and (cfg.get("replyToComments") or view_only):
        if not view_only:
            gate.write_reply_policy([], run_id, allow_new=False)
        return {
            "ok": False,
            "items": [],
            "text": "",
            "error": (
                "「只回我 @ 的那个人」开着，但没配置 mentionUid —— "
                "昵称不唯一，插件不猜人，所以这一轮谁都不回。先填你回话用的 B 站 UID。"
            ),
            "scanned": 0,
            "why": why,
        }

    cred = api.load_credential()
    if not cred:
        return {
            "ok": False,
            "items": [],
            "text": "",
            "error": "还没扫码登录 —— 看评论区要用你自己的登录态",
            "scanned": 0,
            "why": why,
        }

    rows = ledger.parse_seen(ledger.read_seen())
    merged = ledger.merge_sent_records(rows)
    if merged:
        note(f"对话：从判重表并进 {merged} 条评论记录到账本")
    book = ledger.read_ledger()

    limit = max(1, int(max_items if max_items is not None else (cfg.get("replyPerRound") or 1)))
    # 名单只返回 limit 条，但扫描要为“最新那条最后是我们说的”留余量，去翻更旧的串。
    scan_limit = min(20, max(12, limit * 3))
    depth = max(1, int(depth if depth is not None else (cfg.get("replyDepth") or 5)))
    if only_name is None:
        only_name = str(cfg.get("mentionName") or "").strip() if cfg.get("replyOnlyMention") else ""
    if include_at is None:
        include_at = bool(cfg.get("replyToMentions"))

    headers = api.headers_for(cred)
    cache = {}
    mine = api.our_mid(cred)
    now = time.time()

    def _ts(value) -> float:
        if not value:
            return 0.0
        try:
            moment = datetime.fromisoformat(str(value))
            if moment.tzinfo is None:
                moment = moment.astimezone()
            return moment.timestamp()
        except (TypeError, ValueError):
            return 0.0

    # 候选串按“最久没被我们碰过”排序：刚回复过的串排到后面，
    # 避免它们占满 scan_limit，把更旧的、真正等人回话的串挤掉。
    candidates = {}

    def add_candidate(bvid, root, last_activity: float = 0.0):
        bv = str(bvid or "").strip()
        rt = int(root or 0)
        if not bv or not rt:
            return
        key = f"{bv}:{rt}"
        current = candidates.get(key)
        if current is None:
            candidates[key] = {"bv": bv, "root": rt, "key": key, "last": float(last_activity or 0.0)}
        else:
            current["last"] = max(current["last"], float(last_activity or 0.0))

    for sent in book.get("sent") or []:
        if sent.get("from") == "claim" and not int(sent.get("root") or 0):
            bump("认领的评论根楼层未知（不扫）")
            continue
        add_candidate(
            sent.get("bv"),
            int(sent.get("root") or sent.get("rpid") or 0),
            _ts(sent.get("at")),
        )
    for row in rows:
        add_candidate(row.get("bv"), int(row.get("root") or row.get("rpid") or 0), 0.0)
    for thread_key, thread_book in (book.get("threads") or {}).items():
        if not isinstance(thread_book, dict):
            continue
        add_candidate(thread_book.get("bv"), int(thread_book.get("root") or 0), _ts(thread_book.get("at")))

    threads = sorted(candidates.values(), key=lambda item: (item["last"], item["key"]))[:scan_limit]

    want = norm_name(only_name)
    only_owner = bool(cfg.get("replyOnlyMention"))
    configured_owner_mid = int(cfg.get("mentionUid") or 0)
    owner_mid = configured_owner_mid
    me = api.own_account(headers)
    owner_name = str(cfg.get("mentionName") or "").strip()
    if (
        want
        and not owner_mid
        and me
        and norm_name(me.get("uname")) == want
    ):
        owner_mid = int(me.get("mid") or 0)
        owner_name = str(me.get("uname") or owner_name)
    # 凭证缺 dedeuserid 时用 nav 结果兜底，避免 mine=0 导致“最后一句是我们说的”和深度计数失效。
    mine = mine or int((me or {}).get("mid") or 0) or int((book.get("me") or {}).get("mid") or 0)
    if me and (
        (book.get("me") or {}).get("uname") != me.get("uname")
        or int((book.get("me") or {}).get("mid") or 0) != int(me.get("mid") or 0)
    ):
        book["me"] = {"mid": me.get("mid"), "uname": me.get("uname"), "at": now_iso()}
        ledger.update_fields(me=book["me"])

    if not mine:
        return {
            "ok": False,
            "items": [],
            "text": "",
            "error": "认不出当前登录账号（缺少 dedeuserid 且 nav 失败）；为避免自问自答，这一轮不列名单，请重新 /bili-login。",
            "scanned": 0,
            "why": why,
        }
    if want and not me:
        return {
            "ok": False,
            "items": [],
            "text": "",
            "error": "拿不到当前登录账号信息（nav 失败），无法确认名单里谁是你；这一轮不列名单，请重新 /bili-login。",
            "scanned": 0,
            "why": why,
        }

    if me and configured_owner_mid and int(me.get("mid") or 0) == configured_owner_mid:
        return {
            "ok": False,
            "items": [],
            "text": "",
            "error": (
                f"设置里 mentionUid={configured_owner_mid} 就是这个插件登录的账号 —— "
                "它分不清“自己说的”和“你说的”，这样没法对话。请换一个账号，或改成你回话用的 UID。"
            ),
            "scanned": 0,
            "why": why,
        }

    if want and not configured_owner_mid and me and norm_name(me.get("uname")) == want:
        return {
            "ok": False,
            "items": [],
            "text": "",
            "error": (
                f"设置里「要 @ 谁」填的「{str(only_name).lstrip('@')}」就是这个插件登录的账号 —— "
                "它分不清“自己说的”和“你说的”，这样没法对话。请换一个账号，或改成你回话用的 UID。"
            ),
            "scanned": 0,
            "why": why,
        }

    if only_owner and not owner_mid:
        return {
            "ok": False,
            "items": [],
            "text": "",
            "error": (
                "「只回本人」需要明确的 mentionUid，不能只靠昵称猜 —— "
                "昵称不唯一，猜错会回到冒名者。先运行 /bili-config mentionUid=<你的B站UID>。"
            ),
            "scanned": 0,
            "why": why,
        }

    def is_owner(mid, uname):
        if not only_owner:
            # replyOnlyMention=false：用户明确关掉了“只回本人”，按原行为放开。
            return True
        return int(mid or 0) == owner_mid

    items = []
    at_keys = set()

    if include_at:
        feed = api.fetch_at_feed(headers)
        if not feed.get("ok"):
            bump("「@我」接口没读到")
            note(f"「收到的@」读不到（{feed.get('error')}）")
        for raw in feed.get("items") or []:
            parsed = normalize_at_item(raw)
            if not parsed:
                bump("@我那条解析不出评论号")
                continue
            if not is_owner(parsed["mid"], parsed["uname"]):
                bump("@我的不是本人（不理）")
                continue
            if parsed["at"] and now - parsed["at"] > 7 * 86400:
                bump("@我超过 7 天（不翻旧账）")
                continue
            bvid = parsed["bvid"]
            if not bvid and parsed["aid"]:
                bvid = api.bvid_of_aid(parsed["aid"], headers, cache)
            if not bvid:
                bump("不是视频评论区（动态/专栏暂不支持）")
                continue
            root = int(parsed["root"] or parsed["rpid"])
            key = f"{bvid}:{root}"
            existing = (book.get("threads") or {}).get(key) or {}
            if int(existing.get("lastTheirRpid") or 0) == int(parsed["rpid"]):
                bump("这条 @ 已经回过了")
                continue

            history = []
            our_count = 0
            thread = api.fetch_thread(bvid, root, headers, cache)
            if thread.get("ok") and thread.get("replies"):
                replies = thread["replies"]
                last_message = replies[-1]
                if mine and int(last_message.get("mid") or 0) == mine:
                    bump("最后一句是我们说的（@ 已接过）")
                    continue
                our_count = len([row for row in replies if int(row.get("mid") or 0) == mine]) if mine else 0
                history = [
                    {
                        "who": "me" if mine and int(row.get("mid") or 0) == mine else "them",
                        "uname": row.get("uname"),
                        "text": clip(row.get("text"), 200),
                        "at": iso_time(row.get("ctime")),
                    }
                    for row in replies[-6:]
                ]
            turns = max(our_count, int(existing.get("turns") or 0))
            if turns >= depth:
                bump("来回已经到顶")
                continue

            at_keys.add(key)
            items.append(
                {
                    "via": "at",
                    "bv": bvid,
                    "root": root,
                    "turns": turns,
                    "left": max(0, depth - turns),
                    "where": clip(parsed["title"], 80),
                    "from": {
                        "rpid": parsed["rpid"],
                        "uname": parsed["uname"],
                        "uid": parsed["mid"],
                        "text": clip(parsed["text"], 300),
                        "at": iso_time(parsed["at"]),
                    },
                    "history": history,
                }
            )
            if len(items) >= min(limit, 5):
                break

    for thread_info in threads:
        if len(items) >= limit:
            break
        if thread_info["key"] in at_keys:
            bump("已经在 @ 名单里了")
            continue
        thread = api.fetch_thread(thread_info["bv"], thread_info["root"], headers, cache)
        if not thread.get("ok"):
            bump("接口没读到")
            note(f"对话：{thread_info['bv']} root={thread_info['root']} 读不到（{thread.get('error')}）")
            time.sleep(0.4)
            continue
        messages = thread.get("replies") or []
        if not messages:
            bump("那一串是空的")
            time.sleep(0.4)
            continue

        last = messages[-1]
        if mine and int(last.get("mid") or 0) == mine:
            bump("最后一句是我们说的")
            time.sleep(0.4)
            continue
        if not is_owner(last.get("mid"), last.get("uname")):
            bump("不是本人（不理）")
            time.sleep(0.4)
            continue
        if last.get("ctime") and now - int(last["ctime"]) > 30 * 86400:
            bump("最后一句超过 30 天")
            time.sleep(0.4)
            continue

        existing = (book.get("threads") or {}).get(thread_info["key"]) or {}
        if int(existing.get("lastTheirRpid") or 0) == int(last.get("rpid") or 0):
            bump("这一条已经回过了")
            time.sleep(0.4)
            continue

        our_count = len([row for row in messages if mine and int(row.get("mid") or 0) == mine]) if mine else 0
        turns = max(our_count, int(existing.get("turns") or 0))
        if turns >= depth:
            bump("来回已经到顶")
            time.sleep(0.4)
            continue

        items.append(
            {
                "via": "thread",
                "bv": thread_info["bv"],
                "root": int(thread_info["root"]),
                "turns": turns,
                "left": max(0, depth - turns),
                "from": {
                    "rpid": int(last.get("rpid") or 0),
                    "uname": last.get("uname"),
                    "uid": last.get("mid"),
                    "text": clip(last.get("text"), 300),
                    "at": iso_time(last.get("ctime")),
                },
                "history": [
                    {
                        "who": "me" if mine and int(row.get("mid") or 0) == mine else "them",
                        "uname": row.get("uname"),
                        "text": clip(row.get("text"), 200),
                        "at": iso_time(row.get("ctime")),
                    }
                    for row in messages[-6:]
                ],
            }
        )
        time.sleep(0.6)

    by_from = {}
    final_items = []
    for item in items:
        key = str((item.get("from") or {}).get("rpid"))
        previous = by_from.get(key)
        if not previous:
            by_from[key] = item
            final_items.append(item)
            continue
        bump("同一条对方的话出现了两次（去重）")
        better = item.get("root") == (item.get("from") or {}).get("rpid") and previous.get("root") != (
            previous.get("from") or {}
        ).get("rpid")
        if better:
            final_items[final_items.index(previous)] = item
            by_from[key] = item

    text = render_inbox(final_items)
    try:
        INBOX_FILE.write_text(f"# 谁在等你回话 · {now_iso()}\n\n{text}\n", encoding="utf-8")
    except OSError:
        pass

    # 只看不发时，策略里不放行任何目标 —— 名单可以看，但机器闸仍然拒绝回复。
    # view-only（/bili-inbox）只读名单，不写策略、不授权任何发送，也不打断正在进行的轮次。
    if view_only:
        policy = gate.read_policy()
    else:
        policy = gate.write_reply_policy(final_items, run_id, allow_new=allow_new_comment)
    at_count = len([item for item in final_items if item.get("via") == "at"])
    note(
        f"对话收件箱：看了 {len(threads)} 条串，{len(final_items)} 条在等我回话（其中 {at_count} 条是 @ 我）"
        + (f"（其余：{why}）" if why else "")
    )
    return {
        "ok": True,
        "items": final_items,
        "text": text,
        "error": "",
        "scanned": len(threads) + at_count,
        "why": why,
        "onlyName": str(only_name).lstrip("@").strip() if want else "",
        "owner": {"mid": owner_mid, "name": owner_name} if owner_mid else {},
        "me": me or {},
        "policy": {"builtAt": policy.get("builtAt"), "allow": len(policy.get("allow") or []), "allowNewComment": policy.get("allowNewComment")},
    }
