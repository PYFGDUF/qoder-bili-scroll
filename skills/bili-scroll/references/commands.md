# 工具与命令参考

## MCP 工具（插件自带，服务名 `bili-scroll`）

### 登录与账号

| 工具 | 作用 |
|---|---|
| `bili_login_start(force)` | 生成扫码登录二维码，返回 `qr_file`（PNG 路径）、`qr_text_file`、URL；`force=true` 用于换号 |
| `bili_login_poll` | 轮询扫码状态；成功后保存凭证并返回账号昵称 |
| `bili_logout(confirm)` | 退出登录：删除本地凭证、清空账号缓存与发言许可（需 `confirm=true`） |
| `bili_whoami` | 返回当前账号 mid / 昵称 |
| `bili_check_credential` | 用 B 站接口验证凭证是否有效 |
| `bili_env_check` | 检查 Python、依赖（含 missingDeps）、数据目录、凭证、owner UID、dmMode/dmReply、策略 |

### 私信（只发主人）

| 工具 | 作用 |
|---|---|
| `bili_dm_reply(message_key, text)` | 回主人一条私信：只能回本轮名单里的 key；≤100 字；无链接/@/内部术语；同一条只回一次；成功后核销 key、累计日额度 |
| `bili_round_begin(mode="chat")` | 拉主人未读私信（最多 3 条）并写 `dm-policy.json`；没有可回的就停 |
| `bili_round_end` | scan 轮结束时由插件生成并发送私信摘要（≤480 字、最多 4 条）；同时生成单条笔记和 digest |

> 私信收件人固定为 `mentionUid`。不按昵称搜索、不猜同名网友；登录账号恰好是主人时不能私信自己。
> 没有“发给任意 UID”的工具；陌生人私信只计数、不读正文、不回复。

### 只读采集

| 工具 | 作用 |
|---|---|
| `bili_search(keyword, num, order)` | 搜索视频；`order` = totalrank/click/pubdate/dm |
| `bili_subtitle(bvid)` | 读字幕原文；无字幕返回 `code=no_subtitle` |
| `bili_comments(bvid, num)` | 读热门评论（含少量楼中楼） |
| `bili_video_info(bvid)` | 视频详情 |
| `bili_received_at_and_likes()` | 收到的 @ / 点赞通知 |
| `bili_received_replies()` | 收到的回复通知 |
| `bili_unread_messages()` | 未读消息计数 |

### 编排

| 工具 | 作用 |
|---|---|
| `bili_round_begin(mode, source)` | 预留额度、刷新评论区收件箱/私信/策略、生成 `prompt_file`；`mode=scan/chat`，`source=cron/manual` |
| `bili_round_end(run_id, status, summary)` | 记录结果、日报兜底、单条笔记/digest、自动私信摘要；然后收回发言许可 |
| `bili_inbox()` | 计算评论区“谁在等我回话”，写 `inbox.md`；**view-only，不写 reply-policy，不授权发送** |
| `bili_config(action, key, value, confirm)` | list/get/set；危险开关（dmMode/dmReply/autoComment/replyToComments/replyOnlyMention）需要 `confirm=true` |
| `bili_seen(action, bvid, ..., up, status, limit)` | 判重表 add/check/list；兼容旧/新表格，返回档位、状态、是否已发、笔记、链接 |
| `bili_report(action, day)` | 日报 show/list/ensure/notes；`notes` 拆成 `reports/<BV>-<日期>.md` |
| `bili_verify(bvid, rpid, root)` | 复核评论是否公开可见 |
| `bili_claim_comment(bvid, rpid)` | 认领旧评论（B站 App 评论链接里的 BV + rpid） |

### 唯一的写操作

| 工具 | 作用 |
|---|---|
| `bili_reply(bvid, text, rpid=0, root=0)` | 发顶层评论（`rpid=0`，需 `allowNewComment` 且 `dmMode!=only`）或回复楼中楼；发前过文本检查 + 策略新鲜度 + 作者机器闸；成功后自动调用 `bili_verify` 复核公开可见性 |
| `bili_dm_reply(message_key, text)` | 回主人私信；只回本轮名单，≤100 字，发前过私信策略/文本/日额度闸 |

评论/回复成功后，插件会自动写入 `conversation.json` 的 `sent` / `threads` 账本；
`seen.md` 的「评论记录」只用于旧版本或手工迁移，不需要模型每轮手写。

## JSON 返回约定

MCP 工具返回 JSON 字符串。常见字段：

- `ok`：操作是否成功（不等于“发出去”）。
- `success`：仅 `bili_reply` 使用；`blocked=true` 表示机器闸拒绝。
- `next.action`：`run` / `stop`；`stop` 时看 `next.reason`。
- `run_id`：本轮 id；结束时原样传给 `bili_round_end`。

## 调试 CLI

插件自带 `bin/bili`（开发/排障用，日常优先走 MCP）：

```bash
bin/bili env check
bin/bili login
bin/bili whoami
bin/bili config list
bin/bili config set perDay 3
bin/bili round begin --mode scan --source manual
bin/bili round end --run-id <id> --status ok
bin/bili inbox
bin/bili seen check BV1xx411c7mD
bin/bili report show
bin/bili selftest
```

所有命令向 stdout 输出一个 JSON；日志写入 `~/.qoder-bili-scroll/duty.log`。
