# USC Swimming Pool → Google Calendar

把 [USC RecSports 泳池开放时间](https://recsports.usc.edu/rec-facilities/operating-hours/)
自动同步到 Google 日历，并把跟你日程不冲突的场次推送到 Discord。

网站每周更新一次 Rec Swim 时间，且只显示滚动的 7 天。这个程序一天跑三次，
有变化才动日历、才推送。

## 工作流程

```
WordPress REST API 读 modified 时间戳
  └─ 与上次相同 → 直接退出（不下载、不调 LLM、不写日历）

抓取页面 (135KB)
  └─ 确定性切片 → Rec Swim 区块 (573B)
        ├─ DeepSeek 抽取 ──┐
        └─ 正则解析 ───────┴─ 交叉验算 → 硬校验 → 时段列表
                                              │
                        指纹未变 → 退出 ──────┤
                                              │
                        幂等同步到 Google 日历 ┤
                        读主日历忙闲 → 推荐   ┤
                        Discord 推送变更 ─────┘
```

## 设计要点

**双路解析互相验算。** LLM 和正则每次都跑，比对结果：一致就高置信通过；
不一致用 LLM 结果并告警；一方失败用另一方；双方都失败才中止。
这样兜底路径不会因为长期不走而悄悄腐烂。

**永不在数据可疑时删事件。** 抽取为空、校验不过、过滤后无剩余 —— 任何一种情况
都直接中止，日历原样不动。这是最容易出事的地方：网站改版 → 解析出空数组 →
脚本"忠实地"把日历清空。

**同步是幂等的。** 每个时段有确定性 UID（`uscpool-dive-pool-20260831-0600`），
事件带 `source=usc-pool-bot` 私有标记。同步只在 `[今天, 最后一天]` 窗口内、
只对带标记的事件操作 —— 你手动建的东西和历史记录都不会被碰。

**所有时间都用场地时区。** 见 `src/clock.py`。GitHub runner 是 UTC，
洛杉矶傍晚后 UTC 已是第二天；用错时区会导致每天傍晚静默删掉当天的事件。

**日历客观、推荐个性化。** 日历收录全部开放时间（它是公开的，别人也在看）；
6-8am 之类你不会去的场次只在推荐里排除，不从日历里删。

## 本地运行

```bash
python -m venv .venv && ./.venv/Scripts/pip install -r requirements.txt

export DEEPSEEK_API_KEY=...
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
export GCAL_CALENDAR_ID=xxx@group.calendar.google.com
export GCAL_BUSY_CALENDAR_ID=you@example.com
export DISCORD_WEBHOOK=https://discord.com/api/webhooks/...

python -m src.main --dry-run     # 只打印计划，不写任何东西
python -m src.main               # 真的执行
python -m src.main --force       # 忽略变更检测
pytest tests/ -q
```

## GitHub Secrets

| Secret | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | platform.deepseek.com |
| `GCP_SA_JSON` | 服务账号 JSON 的**全部内容** |
| `GCAL_CALENDAR_ID` | 目标日历 ID（需共享给服务账号，权限「更改活动」）|
| `GCAL_BUSY_CALENDAR_ID` | 你的主日历（共享给服务账号，权限「仅查看空闲/忙碌」）|
| `DISCORD_WEBHOOK` | Discord 频道 webhook URL |

密钥只以 `${{ secrets.X }}` 形式出现在 workflow 里，不进代码。
`.gitignore` 已挡掉 `*-*.json` 等服务账号密钥的常见文件名。

## 配置

`config.yml` 控制启用哪些池子、日历的过滤规则（`filters`）、
以及推荐的偏好（`recommend`）。两组过滤器是独立的 —— 前者决定日历内容，
后者决定推给你什么。

## 已知边界

- 网站只提供滚动 7 天，所以日历里最多有未来一周的数据
- GitHub cron 高峰期会延迟 5–30 分钟甚至跳过，所以一天跑三次而不是掐点跑一次
- 仓库 60 天无活动 GitHub 会停用 schedule；每次回写 `state.json` 兼作心跳
