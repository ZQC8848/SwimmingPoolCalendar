"""Discord 推送。变更摘要交给 DeepSeek 写成人话，失败就退化成裸列表。"""
from __future__ import annotations

import os

import requests

COLOR_OK, COLOR_WARN, COLOR_ERR = 0x2ECC71, 0xF1C40F, 0xE74C3C


def _post(embed: dict) -> None:
    url = os.environ.get("DISCORD_WEBHOOK")
    if not url:
        print("[notify] DISCORD_WEBHOOK 未设置，跳过推送")
        return
    r = requests.post(url, json={"username": "USC Pool Bot", "embeds": [embed]},
                      timeout=20)
    if r.status_code not in (200, 204):
        print(f"[notify] Discord 返回 {r.status_code}: {r.text[:200]}")


def summarize(added: list[str], removed: list[str], cfg: dict) -> str:
    """让 LLM 把 diff 写成一两句人话。这是纯锦上添花，绝不能因此让整个流程失败。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    raw = ("新增:\n" + "\n".join(added or ["(无)"]) +
           "\n\n移除:\n" + "\n".join(removed or ["(无)"]))
    if not api_key:
        return raw
    try:
        r = requests.post(
            f"{cfg['llm']['base_url']}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": cfg["llm"]["model"],
                "messages": [
                    {"role": "system", "content":
                     "你是泳池时间播报助手。把下面的日程增删改写成 1-4 条中文要点，"
                     "每条一行，以 • 开头。只陈述事实，不要客套、不要标题、不要总结句。"
                     "把同一天的变化合并成一条。"},
                    {"role": "user", "content": raw},
                ],
                "temperature": 0.2,
                "max_tokens": 300,
            }, timeout=30)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[notify] 摘要生成失败，退化为裸列表: {e}")
    return raw


def changed(summary_text: str, plan_line: str, picks: list) -> None:
    fields = [{"name": "同步", "value": plan_line, "inline": False}]
    if picks:
        rec = "\n".join(
            f"`{p.slot.date:%m/%d %a}` {p.slot.pool} **{p.usable_start:%H:%M}–"
            f"{p.usable_end:%H:%M}** ({p.usable_min}min)" for p in picks)
        fields.append({"name": "⭐ 不冲突的场次", "value": rec[:1024],
                       "inline": False})
    _post({"title": "🏊 USC 泳池时间已更新", "description": summary_text[:4000],
           "color": COLOR_OK, "fields": fields})


def warn(title: str, detail: str) -> None:
    _post({"title": f"⚠️ {title}", "description": detail[:4000], "color": COLOR_WARN})


def error(title: str, detail: str) -> None:
    _post({"title": f"❌ {title}", "description": detail[:4000], "color": COLOR_ERR,
           "footer": {"text": "日历未被修改"}})
