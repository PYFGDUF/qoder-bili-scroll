# 定时与手动

## 推荐：一条本地自动化（20:30）

Qoder 的插件本身没有常驻定时器，用 **Automations** 承担原插件 `index.js` 的定时器角色。

创建一个**本地**任务：

- 名称：`B站刷一轮`
- 计划：每天 `20:30`（读取 `config.json` 的 `dailyRunTime`），时区用 `timezone`（默认 `Asia/Shanghai`，cron `30 20 * * *`）
- cwd：`~/.qoder-bili-scroll`
- 权限：Full Access（需要运行 MCP 工具和读写数据目录）
- 位置：**local**（不要用 cloud：云端拿不到本地凭证、技能和数据目录）

prompt（固定薄壳，直接使用）：

```text
使用 bili-scroll 插件。调用 bili_round_begin(mode="scan", source="cron")。
（若工具没有直接暴露，先用 mcp_list 找到 bili-scroll 的 bili_round_begin，再用 mcp_call 调用。）
如果 next.action != "run"，只回一行原因并结束，不要搜视频、不要发言。
如果 next.action == "run"，读取返回的 prompt_file，严格按其中章程执行；
先看「主人的私信」名单并回（bili_dm_reply，≤100 字）；dmMode=only 时不要发公开评论；
本轮最多看 quota 条；如果 report_file 非空，日报写到 report_file（为空则不写日报）；
私信摘要由 bili_round_end 自动发，不要自己发私信；结束时调用
bili_round_end(run_id=..., status="ok" 或 "failed", summary="一句话")。
不要自行创建新的自动化。
```

`/bili-automation` 命令会展示上面这些参数，得到用户确认后通过 Qoder 的定时任务工具创建；
重复执行时应先查已有任务，避免建两条。

> 私信只在每轮开始/`/bili-dm` 时检查；上游的 1 分钟宿主轮询在 Qoder 里不可用，别假装实时。

## 手动

| 用户说 | 动作 |
|---|---|
| “刷一轮 B 站” | `bili_round_begin(mode="scan", source="manual")` |
| “现在看私信 / 回我私信” | `bili_round_begin(mode="chat", source="manual")` |
| “看谁在等我回话” | `bili_inbox`（评论区只读） |
| “刷过的视频 / 笔记” | `bili_seen(action="list")` + `bili_report(action="notes")` |
| “补跑今天漏掉的那轮” | 同手动 `scan` |

手动 `scan` 也会扣当天额度（`perDay`）；`chat` 不扣视频额度。
`perDay=0` 时自动轮不跑，但手动仍可跑。

## 额度

- `perDay`：每天最多看几条（默认 5）。
- `perRound`：一轮最多看几条（默认 2）。
- 额度在模型开跑前**预留**；中途崩溃也算用掉，避免下一轮重复看。
- 一轮的额度、剩余额度、日报路径都写在 `prompt_file` 开头。

## 漏跑与限制

- **Qoder 必须处于运行状态**；睡眠、退出、重启会漏跑。
- 漏跑用 `/bili-run` 补；`state.json` 里有 `lastRunAt` 和最近 100 轮记录。
- 本地自动化不支持“每隔几分钟轮询一次”的轻量 tick —— 每次触发都是一个模型会话，轮询会烧额度。
- 原插件的“峰谷时段省 token”与“把额度随机摊进窗口”不再适用（Qoder 计费模型不同），
  现在只保留“每天/每轮最多几条”的额度语义。
- 需要多条定时（如午间 + 晚间）时，复制上面的 prompt 建第二条即可；额度共用同一份 `state.json`。
