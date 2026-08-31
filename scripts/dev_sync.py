"""Dry-run 日历同步：打印计划，除非带 --apply 否则一个字节都不写。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import yaml
from src.fetch import fetch_html
from src.slice import extract_swim_block
from src.extract import extract
from src.fallback import parse
from src.validate import validate, apply_filters
from src import gcal
import os

APPLY = "--apply" in sys.argv
cfg = yaml.safe_load(open("config.yml", encoding="utf-8"))
block = extract_swim_block(fetch_html(cfg["source"]["url"]))

llm = validate(extract(block, cfg), cfg)
rex = validate(parse(block), cfg)
agree = {s.uid() for s in llm} == {s.uid() for s in rex}
print(f"交叉验算 : {'一致 OK' if agree else '不一致 WARN'}  (LLM {len(llm)} / regex {len(rex)})")

slots = apply_filters(llm, cfg)
print(f"过滤后   : {len(slots)} 个时段\n")

svc = gcal.service()
cal = os.environ["GCAL_CALENDAR_ID"]
p = gcal.plan(svc, cal, slots, cfg)
print("同步计划 :", p.summary(), "\n")
for s in p.create:
    print(f"  + {s.date:%a %m/%d} {s.start:%H:%M}-{s.end:%H:%M}  {s.pool}")
for s, _ in p.update:
    print(f"  ~ {s.date:%a %m/%d} {s.start:%H:%M}-{s.end:%H:%M}  {s.pool}")
for uid, _ in p.delete:
    print(f"  - {uid}")

if APPLY:
    gcal.apply(svc, cal, p, cfg)
    print("\n已执行。")
else:
    print("\n(dry-run，未写入。加 --apply 才会真正执行)")
