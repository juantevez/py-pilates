from abc import ABC, abstractmethod
from datetime import date

from pilates.scheduling.application.dto import AdminClassDTO, CalendarDayDTO


class ScheduleQueries(ABC):
    @abstractmethod
    def classes_for_date(self, fecha: date) -> list[AdminClassDTO]: ...

    @abstractmethod
    def calendar(self, desde: date, hasta: date) -> list[CalendarDayDTO]: ...
