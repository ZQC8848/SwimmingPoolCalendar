"""钉住几个容易静默出错的地方。"""
import datetime as dt
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
import yaml

from src.validate import validate, apply_filters, ValidationError, _resolve_year, Slot
from src.fallback import parse
from src.recommend import _free_windows, recommend
from src import clock

CFG = yaml.safe_load(open("config.yml", encoding="utf-8"))


def day(wd, m, d, slots=None, closed=False):
    return {"weekday": wd, "month": m, "day": d, "closed": closed,
            "slots": slots or []}


def payload(days_comp, days_dive):
    return {"updated_label": None, "pools": [
        {"name": "Comp Pool", "days": days_comp},
        {"name": "Dive Pool", "days": days_dive}]}


# ---------- 年份反推 ----------

def test_year_inferred_from_weekday():
    # 2026-08-31 是周一
    assert _resolve_year(8, 31, "Mon", dt.date(2026, 8, 31)) == dt.date(2026, 8, 31)


def test_year_rolls_over_new_year():
    """跨年那周：12/29 的运行看到 '1/2'，必须解析成下一年。"""
    today = dt.date(2026, 12, 29)          # 周二
    assert _resolve_year(1, 2, "Sat", today) == dt.date(2027, 1, 2)


def test_weekday_date_mismatch_rejected():
    """星期几与日期对不上 = 抽取出错，必须失败而不是猜。"""
    with pytest.raises(ValidationError):
        _resolve_year(8, 31, "Fri", dt.date(2026, 8, 31))


def test_date_far_outside_window_rejected():
    with pytest.raises(ValidationError):
        _resolve_year(3, 15, "Sun", dt.date(2026, 8, 31))


# ---------- 拒绝空数据（防止清空日历）----------

def test_empty_pools_rejected():
    with pytest.raises(ValidationError):
        validate({"pools": []}, CFG, today=dt.date(2026, 8, 31))


def test_all_closed_rejected():
    """全周关闭在真实世界会发生，但更可能是解析故障 —— 宁可告警也不清空日历。"""
    with pytest.raises(ValidationError):
        validate(payload([day("Mon", 8, 31, closed=True)],
                         [day("Mon", 8, 31, closed=True)]),
                 CFG, today=dt.date(2026, 8, 31))


def test_closed_with_slots_is_contradiction():
    with pytest.raises(ValidationError):
        validate(payload([day("Mon", 8, 31, closed=True,
                              slots=[{"start": "12:00", "end": "14:00"}])],
                         [day("Mon", 8, 31, slots=[{"start": "06:00", "end": "08:00"}])]),
                 CFG, today=dt.date(2026, 8, 31))


def test_end_before_start_rejected():
    with pytest.raises(ValidationError):
        validate(payload([day("Mon", 8, 31, slots=[{"start": "14:00", "end": "12:00"}])],
                         [day("Mon", 8, 31, slots=[{"start": "06:00", "end": "08:00"}])]),
                 CFG, today=dt.date(2026, 8, 31))


# ---------- 正则兜底 ----------

BLOCK = """Rec Swim Hours
* Updated 8/31/26
Comp Pool
Mon, 8/31: 12pm-2pm
Tue, 9/1: Closed
Dive Pool
Mon, 8/31: 6am-8am, 11am-12pm, & 4pm-6pm
Tue, 9/1: 6am-8am & 4pm-6pm
"""


def test_fallback_handles_mixed_separators():
    """'6am-8am, 11am-12pm, & 4pm-6pm' —— 逗号和 & 混用的三段式。"""
    got = parse(BLOCK)
    dive = [p for p in got["pools"] if p["name"] == "Dive Pool"][0]
    assert [(s["start"], s["end"]) for s in dive["days"][0]["slots"]] == [
        ("06:00", "08:00"), ("11:00", "12:00"), ("16:00", "18:00")]


def test_fallback_noon_midnight_conversion():
    """12pm=12:00 而不是 00:00，这是 12 小时制最经典的坑。"""
    comp = [p for p in parse(BLOCK)["pools"] if p["name"] == "Comp Pool"][0]
    assert comp["days"][0]["slots"] == [{"start": "12:00", "end": "14:00"}]


def test_fallback_closed():
    comp = [p for p in parse(BLOCK)["pools"] if p["name"] == "Comp Pool"][0]
    assert comp["days"][1]["closed"] is True


# ---------- 推荐：buffer 与空档 ----------

def test_buffer_expands_busy_block():
    """13:00-13:30 的会议 + 30 分钟 buffer 会吃掉 12:30-14:00。"""
    slot = Slot("Comp Pool", dt.date(2026, 8, 31), dt.time(12), dt.time(14))
    free = _free_windows(slot, [(13 * 60, 13 * 60 + 30)], 30)
    assert free == [(12 * 60, 12 * 60 + 30)]      # 只剩 12:00-12:30


def test_no_busy_gives_full_window():
    slot = Slot("Comp Pool", dt.date(2026, 8, 31), dt.time(12), dt.time(14))
    assert _free_windows(slot, [], 30) == [(12 * 60, 14 * 60)]


def test_early_morning_excluded_from_recommendations():
    """6-8am 不推荐，但仍应存在于日历（两者是不同的过滤器）。"""
    early = Slot("Dive Pool", dt.date(2027, 1, 4), dt.time(6), dt.time(8))
    later = Slot("Dive Pool", dt.date(2027, 1, 4), dt.time(16), dt.time(18))
    picks = recommend([early, later], {}, CFG,
                      now=dt.datetime(2027, 1, 4, 5, 0))
    assert [p.slot.start for p in picks] == [dt.time(16)]


def test_lead_time_skips_imminent_slot():
    """距开始不足 lead_time 就别推了，赶不过去。"""
    slot = Slot("Comp Pool", dt.date(2027, 1, 4), dt.time(12), dt.time(14))
    assert recommend([slot], {}, CFG, now=dt.datetime(2027, 1, 4, 13, 50)) == []


# ---------- 时区 ----------

def test_clock_is_venue_local_not_machine_local():
    """GitHub runner 是 UTC，洛杉矶傍晚后两者日期会差一天。"""
    assert clock.today(CFG) == dt.datetime.now(clock.tz(CFG)).date()
    # 洛杉矶相对 UTC 是 -7(PDT) 或 -8(PST)。两次 now() 之间有微秒级间隔，取整到分钟再比。
    offset = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - clock.now(CFG)
    hours = round(offset.total_seconds() / 3600)
    assert hours in (7, 8), f"意外的时区偏移 {hours}h —— clock.py 可能退化成了机器本地时间"
