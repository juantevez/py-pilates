from datetime import date
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel

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
    return {"detail": "Te avisaremos por email si se libera un lugar."}


@router.delete("/clases/{clase_id}/lista-espera", status_code=status.HTTP_204_NO_CONTENT)
def leave_waitlist(clase_id: UUID, c: ContainerDep, user: CurrentUser) -> None:
    c.leave_waitlist.execute(user_id=user.id, clase_id=clase_id)


@admin_router.get("/asistencia", response_model=list[AttendanceClassDTO])
def attendance(c: ContainerDep, _: AdminUser, fecha: date | None = None):
    return c.attendance_sheet.execute(fecha=fecha)


@admin_router.post("/turnos/{turno_id}/asistencia", response_model=TurnoDTO)
def mark_attendance(turno_id: UUID, body: AttendanceRequest, c: ContainerDep, admin: AdminUser):
    return c.mark_attendance.execute(admin_id=admin.id, turno_id=turno_id, asistio=body.asistio)
