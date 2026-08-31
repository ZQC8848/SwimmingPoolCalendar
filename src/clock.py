"""所有"现在/今天"的唯一来源。

绝不用裸的 datetime.now() / date.today()：本地开发机在洛杉矶，但 GitHub Actions
的 runner 是 UTC，两者差 7-8 小时。洛杉矶时间下午 5 点后 UTC 已是第二天，
用 UTC 的 today 会把当天整个排除出同步窗口，进而把当天的事件当垃圾删掉。
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo


def tz(cfg: dict) -> ZoneInfo:
    return ZoneInfo(cfg["source"]["timezone"])


def now(cfg: dict) -> dt.datetime:
    """场地所在时区的当前时间（naive，便于与 naive 的场次时间直接比较）。"""
    return dt.datetime.now(tz(cfg)).replace(tzinfo=None)


def today(cfg: dict) -> dt.date:
    return now(cfg).date()
