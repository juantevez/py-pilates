import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pilates.booking.domain.model import ReservadoPor, TurnoStatus
from pilates.shared.infrastructure.db import Base


class TurnoModel(Base):
    __tablename__ = "turnos"
    __table_args__ = (
        UniqueConstraint("user_id", "clase_id", name="uq_turnos_user_clase"),
        Index("ix_turnos_clase_status", "clase_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    clase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clases.id"))
    status: Mapped[TurnoStatus] = mapped_column(
        Enum(TurnoStatus, name="turno_status", native_enum=False, create_constraint=True, values_callable=lambda x: [m.value for m in x], length=32)
    )
    booked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(500))
    attendance_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    booked_by: Mapped[ReservadoPor] = mapped_column(
        Enum(ReservadoPor, name="reservado_por", native_enum=False, create_constraint=True, values_callable=lambda x: [m.value for m in x], length=32),
        server_default=ReservadoPor.ALUMNO.value,
    )


class WaitlistModel(Base):
    __tablename__ = "lista_espera"
    __table_args__ = (UniqueConstraint("user_id", "clase_id", name="uq_lista_espera_user_clase"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    clase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
