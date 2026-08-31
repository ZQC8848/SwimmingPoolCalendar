"""编排。失败一律走 Discord 告警 + 非零退出，且保证不在数据可疑时动日历。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback

import yaml

from . import gcal, notify, state
from .extract import extract
from .fallback import parse
from .fetch import fetch_html, page_last_modified
from .recommend import fetch_busy, recommend
from .slice import extract_swim_block
from .validate import validate, apply_filters


def run(force: bool = False, dry_run: bool = False) -> int:
    cfg = yaml.safe_load(open("config.yml", encoding="utf-8"))
    st = state.load()

    # 1) 便宜的变更探测：WordPress modified 没变就整个跳过
    modified = page_last_modified(cfg["source"]["wp_api"])
    if modified and not force and modified == st.get("wp_modified"):
        print(f"[skip] 页面未变更 (modified={modified})")
        return 0
    print(f"[fetch] modified={modified}")

    # 2) 抓取 + 切片
    block = extract_swim_block(fetch_html(cfg["source"]["url"]))
    print(f"[slice] {len(block)} bytes")

    # 3) 双路解析 + 交叉验算。两条都失败才算真失败。
    llm_slots = rex_slots = None
    llm_err = rex_err = None
    try:
        llm_slots = validate(extract(block, cfg), cfg)
    except Exception as e:
        llm_err = e
    try:
        rex_slots = validate(parse(block), cfg)
    except Exception as e:
        rex_err = e

    if llm_slots is None and rex_slots is None:
        raise RuntimeError(f"两条解析路径都失败\nLLM: {llm_err}\nregex: {rex_err}")

    slots = llm_slots if llm_slots is not None else rex_slots
    if llm_slots is not None and rex_slots is not None:
        a = {s.uid() for s in llm_slots}
        b = {s.uid() for s in rex_slots}
        if a == b:
            print(f"[parse] 双路一致，{len(slots)} 个时段")
        else:
            print("[parse] 双路不一致，采用 LLM 结果")
            notify.warn("解析双路不一致",
                        f"仅 LLM: `{sorted(a - b)}`\n仅正则: `{sorted(b - a)}`\n"
                        "已采用 LLM 结果。网站可能改版，或正则该更新了。")
    elif llm_slots is None:
        print(f"[parse] LLM 失败，走正则兜底: {llm_err}")
        notify.warn("LLM 抽取失败，已用正则兜底", str(llm_err))
    else:
        print(f"[parse] 正则失败，采用 LLM: {rex_err}")
        notify.warn("正则解析器失效，已用 LLM", f"{rex_err}\n正则该更新了。")

    slots = apply_filters(slots, cfg)
    if not slots:
        raise RuntimeError("过滤后无剩余时段 —— 检查 config.yml 的 filters")
    print(f"[filter] 保留 {len(slots)} 个时段")

    # 4) 指纹比对：内容没变就不写日历、不推送
    fp = state.fingerprint(slots)
    rows = state.as_rows(slots)
    if fp == st.get("fingerprint") and not force:
        print(f"[skip] 内容指纹未变 ({fp})")
        st["wp_modified"] = modified
        state.save(st)
        return 0

    # 5) 同步日历
    svc = gcal.service()
    cal_id = os.environ["GCAL_CALENDAR_ID"]
    p = gcal.plan(svc, cal_id, slots, cfg)
    print(f"[plan] {p.summary()}")

    if dry_run:
        for s in p.create:
            print(f"  + {s.date:%a %m/%d} {s.start:%H:%M}-{s.end:%H:%M} {s.pool}")
        for s, _ in p.update:
            print(f"  ~ {s.date:%a %m/%d} {s.start:%H:%M}-{s.end:%H:%M} {s.pool}")
        for uid, _ in p.delete:
            print(f"  - {uid}")
        print("[dry-run] 未写入")
        return 0

    gcal.apply(svc, cal_id, p, cfg)
    print("[sync] 完成")

    # 6) 智能推荐（读主日历忙闲，失败不影响主流程）
    picks = []
    busy_cal = os.environ.get("GCAL_BUSY_CALENDAR_ID")
    if busy_cal and cfg.get("recommend", {}).get("enabled"):
        try:
            busy = fetch_busy(svc, busy_cal, min(s.date for s in slots),
                              max(s.date for s in slots), cfg["source"]["timezone"])
            picks = recommend(slots, busy, cfg)
            print(f"[recommend] {len(picks)} 个不冲突场次，覆盖 "
                  f"{len({p.slot.date for p in picks})} 天")
        except Exception as e:
            print(f"[recommend] 失败（不影响同步）: {e}")
            notify.warn("智能推荐失败", str(e))

    # 7) 推送变更
    added, removed = state.diff(st.get("rows", []), rows)
    if added or removed:
        notify.changed(notify.summarize(added, removed, cfg), p.summary(), picks)
    else:
        print("[notify] 无实质增删，跳过推送")

    st.update({"wp_modified": modified, "fingerprint": fp, "rows": rows,
               "synced_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    state.save(st)
    return 0


def main() -> int:
    try:
        return run(force="--force" in sys.argv, dry_run="--dry-run" in sys.argv)
    except Exception as e:
        traceback.print_exc()
        notify.error(f"同步失败: {type(e).__name__}", str(e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
