from abc import ABC, abstractmethod
from datetime import date
from uuid import UUID

from pilates.scheduling.domain.model import CierreGimnasio, Clase


class ClaseRepository(ABC):
    @abstractmethod
    def add(self, clase: Clase) -> None: ...

    @abstractmethod
    def save(self, clase: Clase) -> None: ...

    @abstractmethod
    def get(self, clase_id: UUID, *, for_update: bool = False) -> Clase | None: ...

    @abstractmethod
    def list_by_date(self, fecha: date) -> list[Clase]: ...


class CierreRepository(ABC):
    @abstractmethod
    def add(self, cierre: CierreGimnasio) -> None: ...

    @abstractmethod
    def save(self, cierre: CierreGimnasio) -> None: ...

    @abstractmethod
    def delete(self, cierre: CierreGimnasio) -> None: ...

    @abstractmethod
    def get(self, cierre_id: UUID) -> CierreGimnasio | None: ...

    @abstractmethod
    def get_by_date(self, fecha: date) -> CierreGimnasio | None: ...

    @abstractmethod
    def list_between(self, desde: date, hasta: date) -> list[CierreGimnasio]: ...
