from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from pilates.booking.domain.events import LugarLiberado, TurnoCancelado, TurnoReservado
from pilates.booking.domain.model import CanceladoPor
from pilates.identity.domain.events import PasswordResetRequested, UserInvited
from pilates.notifications.application import templates
from pilates.notifications.application.events import CorreoSolicitado
from pilates.notifications.application.ports import EmailMessage, EmailSender
from pilates.scheduling.domain.events import ClasesReabiertas, DiaSinClases, TurnosHabilitados
from pilates.shared.application.ports import Clock
from pilates.shared.application.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)
UowFactory = Callable[[], UnitOfWork]


def queue_email(uow: UnitOfWork, to: str, content: tuple[str, str]) -> None:
    """Encola un correo en el outbox (se envía tras el commit, uno por fila)."""
    subject, body = content
    uow.add_event(CorreoSolicitado(to=to, subject=subject, body=body))


class DeliverEmail:
    """Handler de CorreoSolicitado: el único que habla con el SMTP."""

    def __init__(self, sender: EmailSender) -> None:
        self._sender = sender

    def __call__(self, event: CorreoSolicitado) -> None:
        self._sender.send(EmailMessage(to=event.to, subject=event.subject, body=event.body))


class NotificationHandlers:
    """Reacciona a eventos de dominio encolando correos."""

    def __init__(
        self,
        uow: UowFactory,
        clock: Clock,
        frontend_url: str,
        reset_ttl_minutes: int,
        invite_ttl_hours: int,
    ) -> None:
        self._uow, self._clock = uow, clock
        self._frontend_url = frontend_url.rstrip("/")
        self._reset_ttl_minutes = reset_ttl_minutes
        self._invite_ttl_hours = invite_ttl_hours

    def on_turno_reservado(self, event: TurnoReservado) -> None:
        with self._uow() as uow:
            user, clase = uow.users.get(event.user_id), uow.clases.get(event.clase_id)
            if user and clase:
                template = templates.lugar_asignado if event.desde_lista_espera else templates.confirmacion
                queue_email(uow, str(user.email), template(user.full_name, clase.fecha, clase.start_time))
            uow.commit()

    def on_turno_cancelado(self, event: TurnoCancelado) -> None:
        """Aviso al alumno cuando cancela el gimnasio. El lugar liberado lo maneja PromoteFromWaitlist."""
        if event.por != CanceladoPor.GIMNASIO.value:
            return
        with self._uow() as uow:
            clase, user = uow.clases.get(event.clase_id), uow.users.get(event.user_id)
            if clase and user:
                template = templates.cancelacion_gimnasio if event.con_clase else templates.turno_cancelado_gimnasio
                queue_email(uow, str(user.email), template(user.full_name, clase.fecha, clase.start_time, event.motivo))
            uow.commit()

    def on_lugar_liberado(self, event: LugarLiberado) -> None:
        """Última hora antes de la clase: aviso a toda la lista de espera (gana el primero que reserva)."""
        now = self._clock.now()
        with self._uow() as uow:
            clase = uow.clases.get(event.clase_id)
            if clase is None or not clase.is_active or clase.starts_at(now.tzinfo) <= now:
                return
            if uow.turnos.count_occupied(clase.id) >= clase.max_capacity:
                return
            url = f"{self._frontend_url}/clases/{clase.id}"
            for entry in uow.waitlist.list_by_clase(clase.id):
                waiting = uow.users.get(entry.user_id)
                if waiting is None or waiting.is_blocked:
                    continue
                queue_email(
                    uow, str(waiting.email), templates.lugar_liberado(waiting.full_name, clase.fecha, clase.start_time, url)
                )
                entry.mark_notified(now)
                uow.waitlist.save(entry)
            uow.commit()

    def on_clases_reabiertas(self, event: ClasesReabiertas) -> None:
        """Avisa a quienes perdieron su turno por la cancelación que la clase volvió."""
        now = self._clock.now()
        with self._uow() as uow:
            per_user: dict = {}
            for clase_id in event.clase_ids:
                clase = uow.clases.get(clase_id)
                if clase is None or not clase.is_active or clase.starts_at(now.tzinfo) <= now:
                    continue
                for turno in uow.turnos.list_cancelled_with_class(clase_id):
                    per_user.setdefault(turno.user_id, []).append((clase.fecha, clase.start_time))
            url = f"{self._frontend_url}/reservar"
            for user_id, clases in per_user.items():
                user = uow.users.get(user_id)
                if user and not user.is_blocked:
                    queue_email(uow, str(user.email), templates.clases_reabiertas(user.full_name, clases, url))
            uow.commit()

    def on_turnos_habilitados(self, event: TurnosHabilitados) -> None:
        url = f"{self._frontend_url}/reservar"
        with self._uow() as uow:
            students = uow.user_queries.active_students()
            for s in students:
                queue_email(uow, s.email, templates.turnos_habilitados(s.full_name, event.fecha, url))
            uow.commit()
        logger.info("Apertura de turnos %s: %d correos encolados", event.fecha, len(students))

    def on_dia_sin_clases(self, event: DiaSinClases) -> None:
        excluded = set(event.excluir_user_ids)
        with self._uow() as uow:
            for s in uow.user_queries.active_students():
                if s.id in excluded:
                    continue
                queue_email(
                    uow, s.email, templates.dia_sin_clases(s.full_name, event.fecha, event.motivo, event.proximo_dia_habil)
                )
            uow.commit()

    def on_password_reset_requested(self, event: PasswordResetRequested) -> None:
        with self._uow() as uow:
            if user := uow.users.get(event.user_id):
                queue_email(
                    uow, str(user.email), templates.reset_password(user.full_name, event.reset_url, self._reset_ttl_minutes)
                )
            uow.commit()

    def on_user_invited(self, event: UserInvited) -> None:
        with self._uow() as uow:
            if user := uow.users.get(event.user_id):
                queue_email(
                    uow, str(user.email), templates.bienvenida(user.full_name, event.set_password_url, self._invite_ttl_hours)
                )
            uow.commit()


@dataclass(frozen=True)
class ReminderSchedule:
    """Cuándo se recuerda una clase: ``hours_before`` antes del inicio, pero nunca antes de
    ``earliest`` de ese día (nada de mails de madrugada). Si el alumno reservó después de ese
    momento no se recuerda: acaba de recibir la confirmación."""

    hours_before: timedelta
    earliest: time

    def remind_at(self, starts_at: datetime) -> datetime:
        return max(starts_at - self.hours_before, datetime.combine(starts_at.date(), self.earliest, starts_at.tzinfo))

    def is_due(self, *, now: datetime, starts_at: datetime, booked_at: datetime) -> bool:
        at = self.remind_at(starts_at)
        return booked_at < at <= now < starts_at


class SendClassReminders:
    """Tarea periódica (cada 10 min): recordatorio el día de la clase (reduce el no-show).

    Marca el turno y encola el correo en la misma transacción: un fallo de SMTP no hace que
    la próxima pasada reenvíe los recordatorios que ya salieron.
    """

    def __init__(self, uow: UowFactory, clock: Clock, schedule: ReminderSchedule) -> None:
        self._uow, self._clock, self._schedule = uow, clock, schedule

    def execute(self) -> int:
        now = self._clock.now()
        sent = 0
        with self._uow() as uow:
            # Superconjunto: remind_at nunca es anterior a inicio − hours_before.
            for turno in uow.turnos.list_pending_reminders(now, now + self._schedule.hours_before):
                user, clase = uow.users.get(turno.user_id), uow.clases.get(turno.clase_id)
                if user is None or clase is None:
                    continue
                if not self._schedule.is_due(now=now, starts_at=clase.starts_at(now.tzinfo), booked_at=turno.booked_at):
                    continue
                queue_email(uow, str(user.email), templates.recordatorio(user.full_name, clase.fecha, clase.start_time))
                turno.mark_reminder_sent(now)
                uow.turnos.save(turno)
                sent += 1
            uow.commit()
        return sent
