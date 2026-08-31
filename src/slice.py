"""把 135KB 的整页确定性地切成 ~1KB 的泳池片段。

这一步刻意不用 LLM：切片是结构性操作，正则/DOM 完全胜任，
而且把送进 LLM 的内容压到最小能同时降低成本和幻觉概率。
"""
from __future__ import annotations

from bs4 import BeautifulSoup

# 泳池时间表所在区块的起止锚点
START_HEADING = "Rec Swim Hours"
STOP_HEADINGS = ("Open Rec Hours", "Helpful Links")


class SliceError(RuntimeError):
    pass


def extract_swim_block(html: str) -> str:
    """返回 Rec Swim 区块的纯文本，形如：

        Rec Swim Hours
        *Updated 8/31/26
        Comp Pool
        Mon, 8/31: 12pm-2pm
        ...
    """
    soup = BeautifulSoup(html, "html.parser")

    start = None
    for h in soup.find_all(["h2", "h3", "h4"]):
        if START_HEADING.lower() in h.get_text(strip=True).lower():
            start = h
            break
    if start is None:
        raise SliceError(f"anchor heading {START_HEADING!r} not found — 页面可能改版了")

    lines: list[str] = []
    for el in start.find_all_next(["h2", "h3", "h4", "p", "li"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name in ("h2", "h3", "h4") and any(
            s.lower() in text.lower() for s in STOP_HEADINGS
        ):
            break
        lines.append(text)
        if len(lines) > 200:  # 保险丝：锚点失效时不至于把整页塞进 LLM
            raise SliceError("swim block unexpectedly long — 停止锚点可能失效")

    block = "\n".join([start.get_text(strip=True)] + lines)
    if "Pool" not in block:
        raise SliceError("sliced block contains no pool heading")
    return block
