"""Políticas de grilla y calendario (servicios de dominio sin estado)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum


class TimeBlock(StrEnum):
    MANANA = "manana"
    TARDE = "tarde"


@dataclass(frozen=True)
class Slot:
    start: time
    block: TimeBlock


@dataclass(frozen=True)
class GridTemplate:
    """Grilla diaria fija: bloques de mañana y tarde, lunes a viernes."""

    slots: tuple[Slot, ...]
    duration: timedelta
    default_capacity: int

    @classmethod
    def from_times(
        cls, morning: list[time], afternoon: list[time], duration: timedelta, default_capacity: int
    ) -> GridTemplate:
        slots = [Slot(t, TimeBlock.MANANA) for t in morning] + [Slot(t, TimeBlock.TARDE) for t in afternoon]
        return cls(slots=tuple(sorted(slots, key=lambda s: s.start)), duration=duration, default_capacity=default_capacity)

    def end_of(self, start: time) -> time:
        return (datetime.combine(date.min, start) + self.duration).time()


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while not is_weekday(d):
        d += timedelta(days=1)
    return d


def previous_weekday(d: date) -> date:
    d -= timedelta(days=1)
    while not is_weekday(d):
        d -= timedelta(days=1)
    return d


@dataclass(frozen=True)
class BookingCalendar:
    """Horizonte deslizante de 1 día hábil.

    - La fecha objetivo es siempre el siguiente día hábil (lunes a viernes).
    - Los turnos de esa fecha se habilitan a ``opening_time`` del día hábil anterior
      (ej. el viernes a las 20:00 se abren los del lunes).
    - Opcionalmente se permite reservar el mismo día si quedan lugares.
    """

    opening_time: time
    allow_same_day: bool = False

    def target_date(self, now: datetime) -> date:
        return next_weekday(now.date())

    def opening_at(self, fecha: date, now: datetime) -> datetime:
        return datetime.combine(previous_weekday(fecha), self.opening_time, tzinfo=now.tzinfo)

    def is_bookable_date(self, fecha: date, now: datetime) -> bool:
        if not is_weekday(fecha):
            return False
        if fecha == now.date():
            return self.allow_same_day
        return fecha == self.target_date(now) and now >= self.opening_at(fecha, now)

    def bookable_dates(self, now: datetime) -> list[date]:
        dates = [now.date()] if self.allow_same_day and is_weekday(now.date()) else []
        return dates + [self.target_date(now)]
