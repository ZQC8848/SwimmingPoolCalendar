"""⭐ 推荐时段：列出所有跟你日程不冲突、且值得跑一趟的场次。

不做"每天只选一个"的排序 —— 你要的是候选清单，选哪个由你当天决定。
模型：把每个忙碌区间前后各撑开 buffer 分钟（换衣服 + 走路到 Uytengsu），
从泳池时段里减掉，剩下的连续空档就是"真正能游多久"。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .validate import Slot


@dataclass
class Pick:
    slot: Slot
    usable_start: dt.time
    usable_end: dt.time
    usable_min: int
    trimmed: bool          # True = 被日程挤掉了一部分，不是完整时段

    @property
    def note(self) -> str:
        return "受日程限制" if self.trimmed else "完整时段"


def _m(t: dt.time) -> int:
    return t.hour * 60 + t.minute


def _t(m: int) -> dt.time:
    m = max(0, min(24 * 60 - 1, m))
    return dt.time(m // 60, m % 60)


def _hhmm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def fetch_busy(svc, calendar_id: str, lo: dt.date, hi: dt.date, tz: str) -> dict:
    """返回 {date: [(start_min, end_min), ...]}。只含忙闲，不含任何事件细节。"""
    resp = svc.freebusy().query(body={
        "timeMin": dt.datetime.combine(lo, dt.time.min).isoformat() + "Z",
        "timeMax": dt.datetime.combine(hi + dt.timedelta(days=1), dt.time.min).isoformat() + "Z",
        "timeZone": tz,
        "items": [{"id": calendar_id}],
    }).execute()
    cal = resp["calendars"][calendar_id]
    if cal.get("errors"):
        raise RuntimeError(f"freebusy 读取失败: {cal['errors']}")

    out: dict[dt.date, list[tuple[int, int]]] = {}
    for b in cal.get("busy", []):
        s = dt.datetime.fromisoformat(b["start"])
        e = dt.datetime.fromisoformat(b["end"])
        cur = s
        while cur.date() <= e.date():          # 跨天的忙碌按天切开
            day = cur.date()
            day_end = min(e, dt.datetime.combine(day, dt.time.max, tzinfo=e.tzinfo))
            out.setdefault(day, []).append((_m(cur.time()), _m(day_end.time())))
            cur = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min,
                                      tzinfo=s.tzinfo)
    return out


def _free_windows(slot: Slot, busy: list[tuple[int, int]], buffer_min: int):
    """把 busy 前后撑开 buffer 后从时段里挖掉，返回剩余空档。"""
    lo, hi = _m(slot.start), _m(slot.end)
    blocked = sorted((max(lo, b - buffer_min), min(hi, e + buffer_min))
                     for b, e in busy if e + buffer_min > lo and b - buffer_min < hi)
    free, cursor = [], lo
    for b, e in blocked:
        if b > cursor:
            free.append((cursor, b))
        cursor = max(cursor, e)
    if cursor < hi:
        free.append((cursor, hi))
    return free


def recommend(slots: list[Slot], busy_by_day: dict, cfg: dict,
              now: dt.datetime | None = None) -> list[Pick]:
    rc = cfg.get("recommend") or {}
    if not rc.get("enabled"):
        return []

    buffer_min = int(rc.get("buffer_minutes", 30))
    min_swim = int(rc.get("min_swim_minutes", 45))
    earliest = _hhmm(rc.get("earliest", "00:00"))
    latest = _hhmm(rc.get("latest", "23:59"))

    # 来不及赶过去的场次不推荐
    from .clock import now as _now
    now = now or _now(cfg)         # 必须用场地时区，见 clock.py
    cutoff = now + dt.timedelta(minutes=int(rc.get("lead_time_minutes", 45)))

    picks: list[Pick] = []
    for slot in slots:
        if dt.datetime.combine(slot.date, slot.end) <= cutoff:
            continue
        if slot.start < earliest or slot.end > latest:
            continue

        busy = busy_by_day.get(slot.date, [])
        for fs, fe in _free_windows(slot, busy, buffer_min):
            # 空档起点也不能早于 earliest
            fs = max(fs, _m(earliest))
            if fe - fs < min_swim:
                continue
            picks.append(Pick(slot, _t(fs), _t(fe), fe - fs,
                              trimmed=(fs, fe) != (_m(slot.start), _m(slot.end))))

    return sorted(picks, key=lambda p: (p.slot.date, p.usable_start))
