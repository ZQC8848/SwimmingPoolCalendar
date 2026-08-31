"""本地跑通 抓取 -> 切片 -> 抽取 -> 校验，把结果打出来给人眼验。"""
import json, sys, datetime as dt, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import yaml
from src.fetch import fetch_html, page_last_modified
from src.slice import extract_swim_block
from src.extract import extract
from src.validate import validate, apply_filters

cfg = yaml.safe_load(open("config.yml", encoding="utf-8"))

print("modified :", page_last_modified(cfg["source"]["wp_api"]))
html = fetch_html(cfg["source"]["url"])
block = extract_swim_block(html)
print(f"slice    : {len(block)} bytes\n")

raw = extract(block, cfg)
print("=== LLM 原始输出 ===")
print(json.dumps(raw, indent=2, ensure_ascii=False))

slots = validate(raw, cfg)
print(f"\n=== 校验通过: {len(slots)} 个时段 ===")
kept = apply_filters(slots, cfg)
dropped = [s for s in slots if s not in kept]

cur = None
for s in slots:
    if s.date != cur:
        cur = s.date
        print(f"\n{s.date:%a %Y-%m-%d}")
    mark = "  " if s in kept else " x"
    print(f" {mark} {s.start:%H:%M}-{s.end:%H:%M}  {s.pool:10s} {s.duration_min:3d}min  {s.uid()}")

print(f"\n过滤后保留 {len(kept)} / {len(slots)}（x = 被 config.yml 的 filters 排除）")
