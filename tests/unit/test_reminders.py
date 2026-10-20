from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from pilates.notifications.application.handlers import ReminderSchedule

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SCHEDULE = ReminderSchedule(hours_before=timedelta(hours=2), earliest=time(7, 0))


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)  # 19 = lunes, 20 = martes


@pytest.mark.parametrize(
    ("booked", "starts", "remind"),
    [
        (at(19, 20, 5), at(20, 8), at(20, 7)),  # nunca antes de las 07:00
        (at(19, 20, 5), at(20, 11), at(20, 9)),
        (at(19, 20, 5), at(20, 17), at(20, 15)),  # antes salía a las 05:00
        (at(20, 13, 30), at(20, 16), at(20, 14)),
    ],
)
def test_reminder_goes_out_on_class_day(booked, starts, remind):
    assert SCHEDULE.remind_at(starts) == remind
    assert not SCHEDULE.is_due(now=remind - timedelta(minutes=1), starts_at=starts, booked_at=booked)
    assert SCHEDULE.is_due(now=remind, starts_at=starts, booked_at=booked)
    assert SCHEDULE.is_due(now=remind + timedelta(minutes=10), starts_at=starts, booked_at=booked)
    assert not SCHEDULE.is_due(now=starts, starts_at=starts, booked_at=booked)  # la clase ya empezó


@pytest.mark.parametrize(
    ("booked", "starts"),
    [
        (at(20, 7, 30), at(20, 9)),  # reservó después de las 07:00: ya tiene la confirmación
        (at(20, 14, 30), at(20, 16)),
    ],
)
def test_no_reminder_when_booked_after_reminder_time(booked, starts):
    assert not SCHEDULE.is_due(now=starts - timedelta(minutes=30), starts_at=starts, booked_at=booked)
