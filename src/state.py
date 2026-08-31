"""变更检测。省钱、也省你的耳朵 —— 页面没动就什么都不做。"""
from __future__ import annotations

import hashlib
import json
import pathlib

from .validate import Slot

PATH = pathlib.Path("state.json")


def load() -> dict:
    if PATH.exists():
        return json.loads(PATH.read_text(encoding="utf-8"))
    return {}


def save(state: dict) -> None:
    PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False,
                               sort_keys=True) + "\n", encoding="utf-8")


def fingerprint(slots: list[Slot]) -> str:
    joined = "|".join(sorted(s.uid() for s in slots))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def as_rows(slots: list[Slot]) -> list[str]:
    """存成人可读的行，diff 摘要要拿它喂给 LLM。"""
    return sorted(f"{s.date:%a %Y-%m-%d} {s.pool} {s.start:%H:%M}-{s.end:%H:%M}"
                  for s in slots)


def diff(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    o, n = set(old or []), set(new or [])
    return sorted(n - o), sorted(o - n)
