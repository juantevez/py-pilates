from datetime import date
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from pilates.booking.application.dto import (
    AttendanceClassDTO,
    DayAvailabilityDTO,
    MyBookingsDTO,
    TurnoDTO,
)
from pilates.shared.http.deps import AdminUser, ContainerDep, CurrentUser

router = APIRouter(tags=["alumno: turnos"])
admin_router = APIRouter(prefix="/admin", tags=["admin: asistencia"])


class BookRequest(BaseModel):
    clase_id: UUID


class AdminBookRequest(BaseModel):
    user_id: UUID
    clase_id: UUID


class AdminCancelRequest(BaseModel):
    motivo: str = Field(default="", max_length=500)


class AttendanceRequest(BaseModel):
    asistio: bool


@router.get("/clases/disponibles", response_model=list[DayAvailabilityDTO])
def availability(c: ContainerDep, user: CurrentUser):
    """Grilla del siguiente día hábil con cupos y acciones habilitadas para el alumno."""
    return c.get_availability.execute(user_id=user.id)


@router.post("/turnos", status_code=status.HTTP_201_CREATED, response_model=TurnoDTO)
def book(body: BookRequest, c: ContainerDep, user: CurrentUser):
    return c.book_class.execute(user_id=user.id, clase_id=body.clase_id)


@router.post("/turnos/{turno_id}/cancelar", response_model=TurnoDTO)
def cancel(turno_id: UUID, c: ContainerDep, user: CurrentUser):
    return c.cancel_booking.execute(user_id=user.id, turno_id=turno_id)


@router.get("/turnos/mios", response_model=MyBookingsDTO)
def my_bookings(c: ContainerDep, user: CurrentUser):
    return c.my_bookings.execute(user_id=user.id)


@router.post("/clases/{clase_id}/lista-espera", status_code=status.HTTP_201_CREATED)
def join_waitlist(clase_id: UUID, c: ContainerDep, user: CurrentUser) -> dict:
    c.join_waitlist.execute(user_id=user.id, clase_id=clase_id)
    return {"detail": "Si se libera un lugar, te lo reservamos automáticamente y te avisamos por email."}


@router.delete("/clases/{clase_id}/lista-espera", status_code=status.HTTP_204_NO_CONTENT)
def leave_waitlist(clase_id: UUID, c: ContainerDep, user: CurrentUser) -> None:
    c.leave_waitlist.execute(user_id=user.id, clase_id=clase_id)


@admin_router.get("/asistencia", response_model=list[AttendanceClassDTO])
def attendance(c: ContainerDep, _: AdminUser, fecha: date | None = None):
    return c.attendance_sheet.execute(fecha=fecha)


@admin_router.post("/turnos", status_code=status.HTTP_201_CREATED, response_model=TurnoDTO)
def admin_book(body: AdminBookRequest, c: ContainerDep, admin: AdminUser):
    """Reserva en nombre de un alumno: valida el cupo y que la clase no haya terminado (sin ventana ni límites)."""
    return c.admin_book_class.execute(admin_id=admin.id, user_id=body.user_id, clase_id=body.clase_id)


@admin_router.post("/turnos/{turno_id}/cancelar", response_model=TurnoDTO)
def admin_cancel(turno_id: UUID, body: AdminCancelRequest, c: ContainerDep, admin: AdminUser):
    """Cancela un turno puntual sin ventana de anticipación (no en clases terminadas); avisa al alumno."""
    return c.admin_cancel_booking.execute(admin_id=admin.id, turno_id=turno_id, motivo=body.motivo.strip() or None)


@admin_router.post("/turnos/{turno_id}/asistencia", response_model=TurnoDTO)
def mark_attendance(turno_id: UUID, body: AttendanceRequest, c: ContainerDep, admin: AdminUser):
    return c.mark_attendance.execute(admin_id=admin.id, turno_id=turno_id, asistio=body.asistio)
