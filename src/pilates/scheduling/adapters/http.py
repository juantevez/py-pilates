from datetime import date
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from pilates.scheduling.application.dto import (
    AdminClassDTO,
    CalendarDayDTO,
    CierreDTO,
    GenerationResultDTO,
    StudentCalendarDayDTO,
)
from pilates.scheduling.domain.model import TipoCierre
from pilates.shared.http.deps import AdminUser, ContainerDep, CurrentUser

router = APIRouter(prefix="/admin", tags=["admin: grilla y cierres"])
student_router = APIRouter(tags=["alumno: calendario"])


class GenerateRequest(BaseModel):
    fecha: date | None = Field(default=None, description="Por defecto, el siguiente día hábil")
    notificar: bool = False


class UpdateClaseRequest(BaseModel):
    max_capacity: int | None = Field(default=None, ge=1, le=50)
    instructor_name: str | None = Field(default=None, max_length=200)


class CancelClaseRequest(BaseModel):
    motivo: str = Field(min_length=1, max_length=500)


class CreateCierreRequest(BaseModel):
    desde: date
    hasta: date | None = Field(default=None, description="Opcional, para cargar un rango (ej. vacaciones)")
    tipo: TipoCierre
    motivo: str | None = Field(default=None, max_length=500)


class UpdateCierreRequest(BaseModel):
    tipo: TipoCierre
    motivo: str | None = Field(default=None, max_length=500)


@router.get("/clases", response_model=list[AdminClassDTO])
def list_classes(fecha: date, c: ContainerDep, _: AdminUser):
    return c.list_classes_for_date.execute(fecha=fecha)


@router.post("/clases/generar", response_model=GenerationResultDTO)
def generate_classes(body: GenerateRequest, c: ContainerDep, _: AdminUser):
    return c.generate_daily_classes.execute(fecha=body.fecha, notify=body.notificar)


@router.patch("/clases/{clase_id}", status_code=status.HTTP_204_NO_CONTENT)
def update_class(clase_id: UUID, body: UpdateClaseRequest, c: ContainerDep, admin: AdminUser) -> None:
    c.update_clase.execute(
        admin_id=admin.id, clase_id=clase_id, max_capacity=body.max_capacity, instructor_name=body.instructor_name
    )


@router.post("/clases/{clase_id}/cancelar")
def cancel_class(clase_id: UUID, body: CancelClaseRequest, c: ContainerDep, admin: AdminUser) -> dict:
    affected = c.cancel_clase.execute(admin_id=admin.id, clase_id=clase_id, motivo=body.motivo)
    return {"turnos_cancelados": affected}


@router.post("/clases/{clase_id}/reactivar", status_code=status.HTTP_204_NO_CONTENT)
def reactivate_class(clase_id: UUID, c: ContainerDep, admin: AdminUser) -> None:
    """Deshace la cancelación de una clase puntual y avisa a los alumnos que habían perdido el turno."""
    c.reactivate_clase.execute(admin_id=admin.id, clase_id=clase_id)


@router.get("/cierres", response_model=list[CierreDTO])
def list_closures(desde: date, hasta: date, c: ContainerDep, _: AdminUser):
    return c.list_cierres.execute(desde=desde, hasta=hasta)


@router.post("/cierres", status_code=status.HTTP_201_CREATED, response_model=list[CierreDTO])
def create_closures(body: CreateCierreRequest, c: ContainerDep, admin: AdminUser):
    return c.create_cierres.execute(
        admin_id=admin.id, desde=body.desde, hasta=body.hasta, tipo=body.tipo, motivo=body.motivo
    )


@router.put("/cierres/{cierre_id}", response_model=CierreDTO)
def update_closure(cierre_id: UUID, body: UpdateCierreRequest, c: ContainerDep, admin: AdminUser):
    return c.update_cierre.execute(admin_id=admin.id, cierre_id=cierre_id, tipo=body.tipo, motivo=body.motivo)


@router.delete("/cierres")
def delete_closures_range(desde: date, hasta: date, c: ContainerDep, admin: AdminUser) -> dict:
    """Reabre un rango de días cerrados (ej. levantar vacaciones) de una sola vez."""
    cierres, clases = c.delete_cierre.execute_range(admin_id=admin.id, desde=desde, hasta=hasta)
    return {"cierres_eliminados": cierres, "clases_reabiertas": clases}


@router.delete("/cierres/{cierre_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_closure(cierre_id: UUID, c: ContainerDep, admin: AdminUser) -> None:
    c.delete_cierre.execute(admin_id=admin.id, cierre_id=cierre_id)


@router.get("/calendario", response_model=list[CalendarDayDTO])
def calendar(desde: date, hasta: date, c: ContainerDep, _: AdminUser):
    return c.get_calendar.execute(desde=desde, hasta=hasta)


@student_router.get("/calendario", response_model=list[StudentCalendarDayDTO])
def student_calendar(c: ContainerDep, _: CurrentUser, desde: date | None = None, hasta: date | None = None):
    """Días con y sin clases (feriados, vacaciones, cierres). Por defecto: hoy + 30 días."""
    return c.get_student_calendar.execute(desde=desde, hasta=hasta)
