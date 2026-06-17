from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import jdatetime

TEHRAN = ZoneInfo("Asia/Tehran")
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def tehran_now() -> datetime:
    return utc_now().astimezone(TEHRAN)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def to_tehran(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    dt = parse_iso(value) if isinstance(value, str) else value
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TEHRAN)


def jalali_date(value: str | datetime | None) -> str:
    dt = to_tehran(value)
    if dt is None:
        return "—"
    jd = jdatetime.datetime.fromgregorian(datetime=dt)
    return jd.strftime("%Y/%m/%d")


def jalali_datetime(value: str | datetime | None) -> str:
    dt = to_tehran(value)
    if dt is None:
        return "—"
    jd = jdatetime.datetime.fromgregorian(datetime=dt)
    return jd.strftime("%Y/%m/%d - %H:%M")


def tehran_date_key(value: str | datetime | None = None) -> str:
    dt = to_tehran(value) if value else tehran_now()
    jd = jdatetime.date.fromgregorian(date=dt.date())
    return jd.strftime("%Y-%m-%d")


def jalali_week_start_key(value: str | datetime | None = None) -> str:
    dt = to_tehran(value) if value else tehran_now()
    jd = jdatetime.date.fromgregorian(date=dt.date())
    # jdatetime weekday: Saturday=0 ... Friday=6
    start = jd - timedelta(days=jd.weekday())
    return start.strftime("%Y-%m-%d")


def tehran_days_between(start_iso: str | None, end: datetime | None = None) -> int | None:
    start = to_tehran(start_iso)
    if start is None:
        return None
    end_dt = end.astimezone(TEHRAN) if end else tehran_now()
    return (end_dt.date() - start.date()).days


def jalali_date_diff_days(old_iso: str | None, new_dt: datetime | None = None) -> int | None:
    old = to_tehran(old_iso)
    if old is None:
        return None
    new = new_dt.astimezone(TEHRAN) if new_dt else tehran_now()
    return (new.date() - old.date()).days
