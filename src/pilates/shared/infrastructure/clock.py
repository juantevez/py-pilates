from datetime import datetime
from zoneinfo import ZoneInfo

from pilates.shared.application.ports import Clock


class SystemClock(Clock):
    def __init__(self, tz: str) -> None:
        self._tz = ZoneInfo(tz)

    def now(self) -> datetime:
        return datetime.now(self._tz)
