"""正则兜底解析器，产出与 extract.py 完全相同的 JSON 结构。

平时不走这条路，所以它最大的风险是"腐烂了没人知道"。
对策：每次运行都让它跟 LLM 结果做交叉验算（见 pipeline），不一致就告警。
"""
from __future__ import annotations

import re

DAY_RE = re.compile(
    r"^(?P<wd>Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s*"
    r"(?P<m>\d{1,2})/(?P<d>\d{1,2}):\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm)", re.IGNORECASE)
POOL_RE = re.compile(r"^(.*\bPool)\s*$", re.IGNORECASE)
UPDATED_RE = re.compile(r"Updated\s+\d{1,2}/\d{1,2}/\d{2,4}", re.IGNORECASE)


class FallbackError(RuntimeError):
    pass


def _to24(h: int, mi: int, ap: str) -> str:
    ap = ap.lower()
    if ap == "am":
        h = 0 if h == 12 else h
    else:
        h = 12 if h == 12 else h + 12
    return f"{h:02d}:{mi:02d}"


def _parse_ranges(rest: str) -> list[dict]:
    if re.search(r"\b(closed|n/?a)\b", rest, re.IGNORECASE):
        return []
    slots = []
    # 先按 , 和 & 拆成若干区间，再在每段里找两个时间
    for part in re.split(r"[,&]", rest):
        times = list(TIME_RE.finditer(part))
        if len(times) != 2:
            if times:
                raise FallbackError(f"区间时间数不为 2: {part!r}")
            continue
        a, b = times
        slots.append({
            "start": _to24(int(a["h"]), int(a["mi"] or 0), a["ap"]),
            "end": _to24(int(b["h"]), int(b["mi"] or 0), b["ap"]),
        })
    if not slots:
        raise FallbackError(f"未能从 {rest!r} 解析出任何时段")
    return slots


def parse(block: str) -> dict:
    updated = UPDATED_RE.search(block)
    pools: list[dict] = []
    current: dict | None = None

    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue

        m = DAY_RE.match(line)
        if m:
            if current is None:
                raise FallbackError(f"出现日期行但还没遇到池子标题: {line!r}")
            slots = _parse_ranges(m["rest"])
            current["days"].append({
                "weekday": m["wd"][:3].title(),
                "month": int(m["m"]),
                "day": int(m["d"]),
                "closed": not slots,
                "slots": slots,
            })
            continue

        p = POOL_RE.match(line)
        if p and "Rec Swim" not in line:
            current = {"name": p.group(1).strip(), "days": []}
            pools.append(current)

    if not pools:
        raise FallbackError("未找到任何池子标题")
    return {"updated_label": updated.group(0) if updated else None, "pools": pools}
