"""幂等的 Google 日历同步。

三条铁律：
1. 只操作带 source=usc-pool-bot 标记的事件 —— 永不碰你手动建的东西
2. 只操作 [今天, 抓到的最后一天] 这个窗口 —— 窗口外的历史记录原样保留
3. desired 为空时直接拒绝执行 —— 防止解析故障把日历清空
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .validate import Slot

MARKER = "usc-pool-bot"
SCOPES = ["https://www.googleapis.com/auth/calendar"]


class SyncError(RuntimeError):
    pass


@dataclass
class SyncPlan:
    create: list[Slot] = field(default_factory=list)
    update: list[tuple[Slot, str]] = field(default_factory=list)   # (slot, event_id)
    delete: list[tuple[str, str]] = field(default_factory=list)    # (uid, event_id)
    unchanged: int = 0

    @property
    def is_noop(self) -> bool:
        return not (self.create or self.update or self.delete)

    def summary(self) -> str:
        return (f"+{len(self.create)} 新建 / ~{len(self.update)} 更新 / "
                f"-{len(self.delete)} 删除 / ={self.unchanged} 不变")


def credentials():
    """本地用文件路径，CI 用 GCP_SA_JSON 环境变量里的 JSON 内容。"""
    raw = os.environ.get("GCP_SA_JSON")
    if raw:
        return service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=SCOPES)
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        raise SyncError("需要 GCP_SA_JSON 或 GOOGLE_APPLICATION_CREDENTIALS")
    return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)


def service():
    return build("calendar", "v3", credentials=credentials(), cache_discovery=False)


def _body(slot: Slot, cfg: dict) -> dict:
    p = cfg["pools"][slot.pool]
    tz = cfg["source"]["timezone"]
    start = dt.datetime.combine(slot.date, slot.start)
    end = dt.datetime.combine(slot.date, slot.end)
    return {
        "summary": f"{p['emoji']} {slot.pool} Lap Swim",
        "location": p.get("location", ""),
        "description": (
            f"USC Rec Swim · {slot.pool}\n"
            f"{slot.start:%H:%M}–{slot.end:%H:%M} ({slot.duration_min} min)\n\n"
            f"Lanes first-come, first-served. Hours change weekly.\n"
            f"{cfg['source']['url']}"
        ),
        "start": {"dateTime": start.isoformat(), "timeZone": tz},
        "end": {"dateTime": end.isoformat(), "timeZone": tz},
        "transparency": "transparent",   # 不占用忙闲，否则会污染你的日程
        "extendedProperties": {"private": {"source": MARKER, "uid": slot.uid()}},
    }


def _same(existing: dict, want: dict) -> bool:
    """比较已存在事件与期望事件。只比会影响观感的字段，避免无谓的 API 写入。"""
    if existing.get("summary") != want["summary"]:
        return False
    if (existing.get("location") or "") != (want.get("location") or ""):
        return False
    for k in ("start", "end"):
        # Google 回传的 dateTime 带时区偏移(2026-08-31T12:00:00-07:00)，
        # 我们发的是 naive + timeZone 字段。只比到分钟的本地墙钟时间即可。
        a = (existing.get(k, {}).get("dateTime") or "")[:16]
        b = want[k]["dateTime"][:16]
        if a != b:
            return False
    return True


def plan(svc, cal_id: str, slots: list[Slot], cfg: dict,
         today: dt.date | None = None) -> SyncPlan:
    if not slots:
        raise SyncError("desired 集合为空 —— 拒绝同步（这几乎一定是解析故障）")

    from .clock import today as _today
    today = today or _today(cfg)   # 必须用场地时区，否则傍晚会误删当天事件
    window_start = today
    window_end = max(s.date for s in slots)
    tz = cfg["source"]["timezone"]

    desired = {s.uid(): s for s in slots if s.date >= window_start}

    existing: dict[str, dict] = {}
    token = None
    while True:
        resp = svc.events().list(
            calendarId=cal_id,
            privateExtendedProperty=f"source={MARKER}",
            timeMin=dt.datetime.combine(window_start, dt.time.min).isoformat() + "Z",
            timeMax=dt.datetime.combine(window_end + dt.timedelta(days=1),
                                        dt.time.min).isoformat() + "Z",
            singleEvents=True, maxResults=250, pageToken=token,
        ).execute()
        for e in resp.get("items", []):
            uid = e.get("extendedProperties", {}).get("private", {}).get("uid")
            if uid:
                existing[uid] = e
        token = resp.get("nextPageToken")
        if not token:
            break

    p = SyncPlan()
    for uid, slot in desired.items():
        if uid not in existing:
            p.create.append(slot)
        elif not _same(existing[uid], _body(slot, cfg)):
            p.update.append((slot, existing[uid]["id"]))
        else:
            p.unchanged += 1
    for uid, e in existing.items():
        if uid not in desired:
            p.delete.append((uid, e["id"]))
    return p


def apply(svc, cal_id: str, p: SyncPlan, cfg: dict) -> None:
    for slot in p.create:
        svc.events().insert(calendarId=cal_id, body=_body(slot, cfg)).execute()
    for slot, eid in p.update:
        svc.events().update(calendarId=cal_id, eventId=eid,
                            body=_body(slot, cfg)).execute()
    for _, eid in p.delete:
        svc.events().delete(calendarId=cal_id, eventId=eid).execute()
