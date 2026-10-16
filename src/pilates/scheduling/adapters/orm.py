import uuid
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Time, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pilates.scheduling.domain.calendar import TimeBlock
from pilates.scheduling.domain.model import ClaseStatus, TipoCierre
from pilates.shared.infrastructure.db import Base


def _enum(e, name: str) -> Enum:
    return Enum(e, name=name, native_enum=False, create_constraint=True, values_callable=lambda x: [m.value for m in x], length=32)


class ClaseModel(Base):
    __tablename__ = "clases"
    __table_args__ = (UniqueConstraint("fecha", "start_time", name="uq_clases_fecha_start_time"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    bloque: Mapped[TimeBlock] = mapped_column(_enum(TimeBlock, "clase_bloque"))
    max_capacity: Mapped[int] = mapped_column(Integer)
    instructor_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[ClaseStatus] = mapped_column(_enum(ClaseStatus, "clase_status"))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))


class CierreModel(Base):
    __tablename__ = "cierres_gimnasio"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    fecha: Mapped[date] = mapped_column(Date, unique=True)
    tipo: Mapped[TipoCierre] = mapped_column(_enum(TipoCierre, "tipo_cierre"))
    motivo: Mapped[str | None] = mapped_column(String(500))
    creado_por: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
