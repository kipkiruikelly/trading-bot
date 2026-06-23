from datetime import datetime, timezone
from config import LONDON_START, LONDON_END, NY_START, NY_END


def _in_window(now_utc: datetime, start: tuple, end: tuple) -> bool:
    s = now_utc.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    e = now_utc.replace(hour=end[0], minute=end[1], second=0, microsecond=0)
    return s <= now_utc <= e


def in_kill_zone() -> bool:
    now = datetime.now(timezone.utc)
    return _in_window(now, LONDON_START, LONDON_END) or _in_window(now, NY_START, NY_END)


def active_session() -> str:
    now = datetime.now(timezone.utc)
    if _in_window(now, LONDON_START, LONDON_END):
        return "London"
    if _in_window(now, NY_START, NY_END):
        return "NY"
    return "None"
