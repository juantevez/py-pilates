"""Políticas de grilla y calendario (servicios de dominio sin estado)."""

from __future__ import annotations

from collections.abc import Collection
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


_MAX_SKIP = 90  # tope de días cerrados seguidos que se saltean (ej. vacaciones largas)


def next_open_weekday(d: date, closed: Collection[date]) -> date:
    """Siguiente día hábil después de ``d`` que no está cerrado."""
    candidate = next_weekday(d)
    for _ in range(_MAX_SKIP):
        if candidate not in closed:
            break
        candidate = next_weekday(candidate)
    return candidate


def previous_open_weekday(d: date, closed: Collection[date]) -> date:
    """Día hábil anterior a ``d`` que no está cerrado."""
    candidate = previous_weekday(d)
    for _ in range(_MAX_SKIP):
        if candidate not in closed:
            break
        candidate = previous_weekday(candidate)
    return candidate


@dataclass(frozen=True)
class BookingCalendar:
    """Horizonte deslizante de 1 día hábil abierto.

    - La fecha objetivo es el siguiente día hábil (lunes a viernes) sin cierre: si mañana el
      estudio está cerrado, se reserva para el día siguiente que abra.
    - Los turnos de esa fecha se habilitan a ``opening_time`` del día hábil abierto anterior
      (ej. el viernes a las 20:00 se abren los del lunes; si el martes está cerrado, el lunes
      a las 20:00 se abren los del miércoles).
    - Opcionalmente se permite reservar el mismo día si quedan lugares.

    ``closed`` son las fechas con cierre cargado (feriados, vacaciones).
    """

    opening_time: time
    allow_same_day: bool = False

    def target_date(self, now: datetime, closed: Collection[date] = ()) -> date:
        return next_open_weekday(now.date(), closed)

    def opening_at(self, fecha: date, now: datetime, closed: Collection[date] = ()) -> datetime:
        return datetime.combine(previous_open_weekday(fecha, closed), self.opening_time, tzinfo=now.tzinfo)

    def is_bookable_date(self, fecha: date, now: datetime, closed: Collection[date] = ()) -> bool:
        if not is_weekday(fecha):
            return False
        if fecha == now.date():
            return self.allow_same_day
        return fecha == self.target_date(now, closed) and now >= self.opening_at(fecha, now, closed)

    def bookable_dates(self, now: datetime, closed: Collection[date] = ()) -> list[date]:
        """Hoy (si se permite), los días hábiles cerrados intermedios (para avisar) y la fecha objetivo."""
        today = now.date()
        dates = [today] if self.allow_same_day and is_weekday(today) else []
        target = self.target_date(now, closed)
        day = next_weekday(today)
        while day < target:
            dates.append(day)  # sólo pueden ser días cerrados
            day = next_weekday(day)
        return dates + [target]
