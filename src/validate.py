"""LLM 输出的守门人。这里的每一条规则都假设 LLM 会出错。

只有通过全部校验的数据才允许流向日历。任何一条失败 -> 抛错 -> 上游中止，
绝不在数据可疑时写日历（尤其绝不删事件）。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

WEEKDAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
MIN_SLOT_MIN = 15
MAX_SLOT_MIN = 8 * 60
WINDOW_DAYS = 10          # 抓到的日期必须落在今天 ±10 天内
MAX_SLOTS_PER_DAY = 6


class ValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Slot:
    pool: str
    date: dt.date
    start: dt.time
    end: dt.time

    @property
    def duration_min(self) -> int:
        a = self.start.hour * 60 + self.start.minute
        b = self.end.hour * 60 + self.end.minute
        return b - a

    def uid(self) -> str:
        """确定性 UID —— 幂等同步的地基。同一时段永远算出同一个 id。"""
        pool = self.pool.lower().replace(" ", "-")
        return f"uscpool-{pool}-{self.date:%Y%m%d}-{self.start:%H%M}"


def _resolve_year(month: int, day: int, weekday: str, today: dt.date) -> dt.date:
    """网站只写 'Mon, 8/31' 不写年份。用星期几反推年份，跨年周才不会错。

    同时这是一道免费的校验：如果没有任何候选年份能让该日期落在标注的星期几上，
    说明抽取结果和页面对不上，直接判失败。
    """
    want = WEEKDAYS.get(weekday)
    if want is None:
        raise ValidationError(f"unknown weekday {weekday!r}")

    hits = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            cand = dt.date(year, month, day)
        except ValueError:
            continue  # 2/29 之类
        if cand.weekday() == want and abs((cand - today).days) <= WINDOW_DAYS:
            hits.append(cand)

    if not hits:
        raise ValidationError(
            f"{weekday} {month}/{day} 在今天({today})±{WINDOW_DAYS}天内没有匹配的年份 "
            f"— 星期几与日期不符，或页面数据超出预期窗口"
        )
    if len(hits) > 1:
        raise ValidationError(f"{weekday} {month}/{day} 年份不唯一: {hits}")
    return hits[0]


def _parse_hhmm(s: str, ctx: str) -> dt.time:
    try:
        h, m = s.split(":")
        return dt.time(int(h), int(m))
    except Exception:
        raise ValidationError(f"{ctx}: 时间格式非法 {s!r}，应为 HH:MM")


def validate(data: dict, cfg: dict, today: dt.date | None = None) -> list[Slot]:
    from .clock import today as _today
    today = today or _today(cfg)   # 必须用场地时区，见 clock.py

    if not isinstance(data, dict) or not isinstance(data.get("pools"), list):
        raise ValidationError("顶层结构不对：缺少 pools 数组")
    if not data["pools"]:
        raise ValidationError("pools 为空 —— 拒绝以空数据触发日历同步")

    enabled = {k for k, v in cfg["pools"].items() if v.get("enabled")}
    seen_pools: set[str] = set()
    slots: list[Slot] = []

    for pool in data["pools"]:
        name = (pool.get("name") or "").strip()
        if not name:
            raise ValidationError("pool 缺少 name")
        if name in seen_pools:
            raise ValidationError(f"pool 重复: {name}")
        seen_pools.add(name)
        if name not in enabled:
            continue

        days = pool.get("days") or []
        if not days:
            raise ValidationError(f"{name}: days 为空")
        if len(days) > 14:
            raise ValidationError(f"{name}: days 数量异常 ({len(days)})")

        seen_dates: set[dt.date] = set()
        for d in days:
            ctx = f"{name} {d.get('weekday')} {d.get('month')}/{d.get('day')}"
            date = _resolve_year(int(d["month"]), int(d["day"]), d["weekday"], today)
            if date in seen_dates:
                raise ValidationError(f"{name}: 日期重复 {date}")
            seen_dates.add(date)

            if d.get("closed"):
                if d.get("slots"):
                    raise ValidationError(f"{ctx}: 标记 closed 却带了时段")
                continue

            day_slots = d.get("slots") or []
            if not day_slots:
                raise ValidationError(f"{ctx}: 非 closed 但没有时段")
            if len(day_slots) > MAX_SLOTS_PER_DAY:
                raise ValidationError(f"{ctx}: 时段数量异常 ({len(day_slots)})")

            prev_end = None
            for s in day_slots:
                start = _parse_hhmm(s["start"], ctx)
                end = _parse_hhmm(s["end"], ctx)
                slot = Slot(name, date, start, end)
                if slot.duration_min <= 0:
                    raise ValidationError(f"{ctx}: end 不晚于 start ({start}-{end})")
                if not MIN_SLOT_MIN <= slot.duration_min <= MAX_SLOT_MIN:
                    raise ValidationError(
                        f"{ctx}: 时长 {slot.duration_min} 分钟不合理"
                    )
                if prev_end and start < prev_end:
                    raise ValidationError(f"{ctx}: 时段乱序或重叠 ({start} < {prev_end})")
                prev_end = end
                slots.append(slot)

    if not slots:
        raise ValidationError("校验后没有任何时段 —— 拒绝以空数据触发日历同步")

    missing = enabled - seen_pools
    if missing:
        raise ValidationError(f"配置里启用的池子未出现在抽取结果中: {sorted(missing)}")

    return sorted(slots, key=lambda s: (s.date, s.start, s.pool))


def apply_filters(slots: list[Slot], cfg: dict) -> list[Slot]:
    """校验之后才过滤。过滤是偏好，不是正确性 —— 两者必须分开。"""
    f = cfg.get("filters") or {}
    lo = _parse_hhmm(f.get("earliest", "00:00"), "filters.earliest")
    hi = _parse_hhmm(f.get("latest", "23:59"), "filters.latest")
    min_dur = int(f.get("min_duration_minutes", 0))
    return [
        s for s in slots
        if s.duration_min >= min_dur and s.start >= lo and s.end <= hi
    ]
