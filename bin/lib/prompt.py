from __future__ import annotations

import re
from pathlib import Path

from config import (
    HOME,
    INBOX_FILE,
    OUT_DIR,
    ROUND_DIR,
    SEEN_FILE,
    clip,
    day_key,
    load_config,
    now_iso,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
CHARTER_FILE = PLUGIN_ROOT / "skills" / "bili-scroll" / "references" / "charter.md"

REPORT_LEVELS = {"full": "完整", "brief": "薄版", "off": "不写"}


def _reply_voice(cfg) -> str:
    if not cfg.get("replyToComments"):
        return "评论区来回对话：关着（一个字都不要回任何人，就算有人回复你那条评论也不要理）。"
    only = str(cfg.get("mentionName") or "").strip() if cfg.get("replyOnlyMention") else ""
    only_uid = int(cfg.get("mentionUid") or 0) if cfg.get("replyOnlyMention") else 0
    line = (
        f"评论区来回对话：开着 —— 每轮最多回 {max(1, int(cfg.get('replyPerRound') or 1))} 条；"
        f"同一条串最多来回 {max(1, int(cfg.get('replyDepth') or 5))} 轮，到顶就收住。"
    )
    if only or only_uid:
        label = only or f"UID {only_uid}"
        line += f" 只回「{label}」这一个人：名单里只会有他；别人回复到你那条评论下面，一律不理。"
    elif cfg.get("replyOnlyMention"):
        line += " 只回本人，但 mentionName/mentionUid 都没填：认不出谁，名单会是空的。"
    else:
        line += " 已设成「回复所有人」：名单里可能有别人，回的时候更要守红线。"
    if cfg.get("replyToMentions"):
        line += " 「收到的 @」也一起看：他在这条评论下面起头，就在他这条评论下面回，不要新开一层。"
    else:
        line += " 「别人在别的评论区 @ 它」这一路关着，只看它自己发过评论的那几串。"
    return line


def _persona_block(cfg):
    """返回 (人设文本, 来源说明)。personaPrompt 支持文字或文件/目录路径。"""
    raw = str(cfg.get("personaPrompt") or "").strip()
    if raw:
        looks_path = (
            "\n" not in raw
            and (
                raw.startswith("~/")
                or raw.startswith("/")
                or raw.startswith("\\\\")
                or bool(re.match(r"^[A-Za-z]:[\\/]", raw))
            )
        )
        if looks_path:
            path = Path(raw).expanduser()
            if path.is_dir():
                path = path / "AGENTS.md"
            try:
                return clip(path.read_text(encoding="utf-8"), 6000), f"读自 {path}"
            except OSError:
                pass
        return clip(raw, 6000), "personaPrompt"
    workspace = str(cfg.get("workspace") or "").strip()
    if workspace:
        path = Path(workspace).expanduser() / "AGENTS.md"
        try:
            return clip(path.read_text(encoding="utf-8"), 6000), f"读自 {path}"
        except OSError:
            return f"（工作区 {workspace} 里没有 AGENTS.md；按中性口吻来。）", ""
    return "（没有设置人设；按中性口吻来。）", ""


def build_prompt(
    run_id: str,
    mode: str,
    quota: int,
    left_today: int,
    inbox: dict | None,
    dm_inbox: dict | None = None,
    dm_owner_uid: int = 0,
    dm_self: bool = False,
) -> dict:
    cfg = load_config()
    today = day_key()
    report_file = OUT_DIR / f"{today}.md"
    mode_text = "只回话" if mode == "chat" else "刷一轮"
    keywords = cfg.get("keywords") or []
    keywords_text = "、".join(keywords) if keywords else "（空，使用章程内置的默认关键词）"
    reply_state = "开" if cfg.get("replyToComments") else "关"
    mention = str(cfg.get("mentionName") or "").strip() or "（没填，不发评论）"
    mention_name = str(cfg.get("mentionName") or "").strip()
    workspace = str(cfg.get("workspace") or "").strip() or "（未设置）"
    inbox_text = (inbox or {}).get("text") or "（这一轮没拉名单）"
    if inbox and inbox.get("error"):
        inbox_text = f"（收件箱没拉到：{inbox['error']}）"
    dm_text = (dm_inbox or {}).get("text") or "（没有新的主人私信）"
    if dm_inbox and dm_inbox.get("error"):
        dm_text = f"（私信没读到：{dm_inbox['error']}）"

    dm_mode = str(cfg.get("dmMode") or "off")
    dm_effective = dm_mode
    if dm_mode != "off" and (int(dm_owner_uid or 0) <= 0 or dm_self):
        dm_effective = "off"

    per_day = int(cfg.get("perDay") or 0)
    report_level = str(cfg.get("reportLevel") or "full")
    if mode == "chat":
        quota_line = "- 本轮：只回话（不搜视频、不写日报、不消耗视频额度）"
        left_line = f"- 今天还剩视频额度：{left_today}（本模式不扣）"
        report_line = "- 日报：本模式不写"
        mode_note = (
            "⚠️ 本模式只执行「私信 / 评论区回话」；"
            "忽略章程里的「搜一轮」「日报」「判重视频」，不要调用 bili_search / bili_subtitle。"
        )
    else:
        quota_line = f"- 本轮最多看几条：{quota}"
        left_line = (
            f"- 本轮之后还剩额度：{left_today}"
            if per_day > 0
            else "- 今天额度：perDay=0（自动轮关闭；手动仍可跑）"
        )
        report_line = f"- 日报文件：{report_file}" if report_level != "off" else "- 日报：本轮不写（判重表照写）"
        mode_note = "⚠️ 本档不写日报；忽略章程里写日报的步骤，但判重表照写。" if report_level == "off" else ""

    mode_block = f"## 本模式覆盖规则\n\n{mode_note}\n\n" if mode_note else ""

    comment_per_round = max(0, int(cfg.get("commentPerRound") or 0))
    can_new = bool(
        mode != "chat"
        and per_day > 0
        and quota > 0
        and cfg.get("autoComment")
        and mention_name
        and comment_per_round > 0
        and dm_mode != "only"
    )
    if can_new:
        comment_line = (
            f"- 自动评论并 @ 你：开；本轮最多 @ {comment_per_round} 条；要 @ 谁：{mention}\n"
            "  （“最多 @”是上限，不是任务：没意思的就一条都别发。）"
        )
    else:
        if mode == "chat":
            reason = "只回话模式"
        elif dm_mode == "only":
            reason = "dmMode=only（只私信，不发公开评论）"
        elif per_day == 0:
            reason = "perDay=0"
        elif not cfg.get("autoComment"):
            reason = "自动评论关着"
        elif not mention_name:
            reason = "mentionName 为空"
        elif comment_per_round == 0:
            reason = "commentPerRound=0"
        else:
            reason = "本轮额度为 0"
        comment_line = (
            f"- 自动评论并 @ 你：**本轮不允许新开顶层评论**（{reason}）。"
            "只处理名单内的楼中楼回复；不要尝试发顶层评论。"
        )

    if dm_mode == "off":
        dm_line = "- 私信投递：关（只写报告）。"
    elif dm_effective == "off":
        dm_line = (
            "- 私信投递：**不可用**（没认出主人 UID / 登录号就是主人）⇒ "
            "本轮不发私信，也**不发公开评论**；只写报告。"
        )
    else:
        dm_line = (
            f"- 私信投递：本轮结束后由插件自动把报告摘要发到主人 UID {int(dm_owner_uid)}；"
            "**你只写报告，不要自己发私信**。"
        )
        if dm_mode == "both":
            dm_line += " 公开评论仍按下面的评论开关执行。"

    if not cfg.get("dmReply"):
        dm_reply_line = "- 私信回复：关（不要回任何私信）。"
    else:
        dm_reply_line = (
            f"- 私信回复：开（本轮最多回 {max(1, int(cfg.get('dmReplyPerRound') or 1))} 条）—— "
            "只回「主人的私信」名单里的 message_key；用 bili_dm_reply(message_key=..., text=...)，每条 ≤100 字。"
        )

    report_rules = ""
    if mode != "chat" and report_level != "off":
        report_rules = """
## 日报格式（机器要读）

- 每挑中一条，**第一行必须是 `### BV号`**（例：`### BV1xxxxxxx`）。
- 紧跟一行 `**档位**：值得看` / `可以看` / `不值当`（三选一，不打分）。
- 再写标题 / UP / 链接 / 为什么值得（两三句）/ 你自己的一句话。
- 末尾写「因无字幕跳过 N 条」（N=0 也写）。
- 看过的每一条用 `bili_seen(action="add", ...)` 落盘；插件会据此生成归档和单条笔记。
"""

    persona_text, persona_source = _persona_block(cfg)

    header = f"""# 刷B站 · 这一轮

- 日期：{today}
- 模式：{mode_text}（run_id: {run_id}）
{quota_line}
{left_line}
- 数据目录：{HOME}
{report_line}
- 判重表：{SEEN_FILE}
- 收件箱：{INBOX_FILE}
{dm_line}
{dm_reply_line}
- 工具体系：使用本插件提供的 MCP 工具（bili_search / bili_subtitle / bili_comments / bili_video_info / bili_reply / bili_dm_reply / bili_seen / bili_report / bili_verify）。
  不要用 Bash 自己拼 B 站接口；工具失败两次就按“跳过”处理。

## 这一轮的设置

- 关注关键词：{keywords_text}
- 工作区路径：{workspace}
- 人设来源：{persona_source or "中性"}
  （如果工作区里有 `memories.md`，可以 Read 扫一眼，只为判断“什么跟他有关”；不要把日记内容抄进报告或评论。）
- 日报形态：{REPORT_LEVELS.get(str(cfg.get('reportLevel')), '完整')}
- 自动评论并 @ 你：{comment_line.removeprefix('- 自动评论并 @ 你：')}
- 评论区来回对话：{reply_state}
- {_reply_voice(cfg)}

## 谁在等你回话（评论区；只含筛选后的名单）

{inbox_text}

## 主人的私信（只含本人；最多 {max(1, int(cfg.get('dmReplyPerRound') or 1))} 条）

{dm_text}
{report_rules}
## 人设（按这个来）

{persona_text}

---

{mode_block}---

# 章程

"""

    try:
        charter = CHARTER_FILE.read_text(encoding="utf-8")
    except OSError:
        charter = "# 章程缺失\n\n请按设置完成本轮：搜视频、读字幕、筛选、写日报；不要越界发言。"

    if mode == "chat":
        footer = f"""

---

## 这一轮的收尾（必须做）

- **本模式只回话**：不搜视频、不写日报、不往判重表写视频。
- **先回主人私信**（「主人的私信」名单）：用 `bili_dm_reply(message_key=..., text=...)`；只回名单里的 key，陌生人一律不理，每条 ≤100 字。
- 再处理「谁在等你回话」的评论区名单；名单为空就跳过。
- 评论区回复要带名单给的 `bvid` / `rpid` / `root`，**不要新开顶层评论**。
- 本轮结束时调用 `bili_round_end(run_id="{run_id}", status="ok", summary="一句话")`。
- 如果中途失败，也要调用 `bili_round_end(run_id="{run_id}", status="failed", summary="失败原因")`。
- 不要在聊天里输出隐私、账号、路径或内部细节。
"""
    elif report_level == "off":
        footer = f"""

---

## 这一轮的收尾（必须做）

- **本档不写日报**（连保底那一行也不写）；但**判重表照写**。
- `reportLevel=off` 没有报告可组装私信摘要 ⇒ 本轮不发私信；也不要改发公开评论。
- 判重表用 `bili_seen(action="add", ...)` 落盘，不要只在回复里说“我看过了”。
- 本轮结束时调用 `bili_round_end(run_id="{run_id}", status="ok", summary="一句话")`。
- 如果中途失败，也要调用 `bili_round_end(run_id="{run_id}", status="failed", summary="失败原因")`。
- 不要在聊天里输出隐私、账号、路径或内部细节。
"""
    else:
        footer = f"""

---

## 这一轮的收尾（必须做）

- 日报写到：`{report_file}`；格式按上面的 `### BV` + `**档位**`。
- **不要自己发私信**：如果 `dmMode` 不是 off 且认出了主人，插件会在 `round_end` 自动生成并发送摘要。
- `dmMode=only` 时**一条公开评论都不许发**；`both` 才允许按评论开关发。
- 判重表用 `bili_seen(action="add", ...)` 落盘；单条笔记和 digest 由插件在 `round_end` 生成，不需要你写。
- 本轮结束时调用 `bili_round_end(run_id="{run_id}", status="ok", summary="一句话")`。
- 如果中途失败，也要调用 `bili_round_end(run_id="{run_id}", status="failed", summary="失败原因")`。
- 无论成功失败，都不要在聊天里输出隐私、账号、路径或内部细节。
"""

    text = header + charter + footer
    ROUND_DIR.mkdir(parents=True, exist_ok=True)
    prompt_file = ROUND_DIR / f"prompt-{run_id}.md"
    prompt_file.write_text(text, encoding="utf-8")
    try:
        prompt_file.chmod(0o600)
    except OSError:
        pass
    return {
        "prompt_file": str(prompt_file),
        "report_file": "" if (mode == "chat" or report_level == "off") else str(report_file),
        "chars": len(text),
        "generatedAt": now_iso(),
    }
