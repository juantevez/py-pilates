from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from pilates.booking.domain.events import TurnoCancelado, TurnoReservado
from pilates.booking.domain.model import CanceladoPor
from pilates.identity.domain.events import PasswordResetRequested, UserInvited
from pilates.notifications.application import templates
from pilates.notifications.application.ports import EmailMessage, EmailSender
from pilates.scheduling.domain.events import ClasesReabiertas, DiaSinClases, TurnosHabilitados
from pilates.shared.application.ports import Clock
from pilates.shared.application.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)
UowFactory = Callable[[], UnitOfWork]


class NotificationHandlers:
    """Reacciona a eventos de dominio enviando correos."""

    def __init__(
        self,
        uow: UowFactory,
        sender: EmailSender,
        clock: Clock,
        frontend_url: str,
        reset_ttl_minutes: int,
        invite_ttl_hours: int,
    ) -> None:
        self._uow, self._sender, self._clock = uow, sender, clock
        self._frontend_url = frontend_url.rstrip("/")
        self._reset_ttl_minutes = reset_ttl_minutes
        self._invite_ttl_hours = invite_ttl_hours

    def _send(self, to: str, content: tuple[str, str]) -> None:
        subject, body = content
        self._sender.send(EmailMessage(to=to, subject=subject, body=body))

    def on_turno_reservado(self, event: TurnoReservado) -> None:
        with self._uow() as uow:
            user, clase = uow.users.get(event.user_id), uow.clases.get(event.clase_id)
            if user and clase:
                self._send(str(user.email), templates.confirmacion(user.full_name, clase.fecha, clase.start_time))

    def on_turno_cancelado(self, event: TurnoCancelado) -> None:
        now = self._clock.now()
        with self._uow() as uow:
            clase = uow.clases.get(event.clase_id)
            if clase is None:
                return
            if event.por == CanceladoPor.GIMNASIO.value and (user := uow.users.get(event.user_id)):
                template = templates.cancelacion_gimnasio if event.con_clase else templates.turno_cancelado_gimnasio
                self._send(str(user.email), template(user.full_name, clase.fecha, clase.start_time, event.motivo))
            # Aviso de liberación a la lista de espera (gana el primero que reserva).
            if not clase.is_active or clase.starts_at(now.tzinfo) <= now:
                return
            if uow.turnos.count_active(clase.id) >= clase.max_capacity:
                return
            url = f"{self._frontend_url}/clases/{clase.id}"
            for entry in uow.waitlist.list_by_clase(clase.id):
                waiting = uow.users.get(entry.user_id)
                if waiting is None or waiting.is_blocked:
                    continue
                self._send(str(waiting.email), templates.lugar_liberado(waiting.full_name, clase.fecha, clase.start_time, url))
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
                    self._send(str(user.email), templates.clases_reabiertas(user.full_name, clases, url))

    def on_turnos_habilitados(self, event: TurnosHabilitados) -> None:
        with self._uow() as uow:
            students = uow.user_queries.active_students()
        url = f"{self._frontend_url}/reservar"
        for s in students:
            self._send(s.email, templates.turnos_habilitados(s.full_name, event.fecha, url))
        logger.info("Apertura de turnos %s notificada a %d alumnos", event.fecha, len(students))

    def on_dia_sin_clases(self, event: DiaSinClases) -> None:
        with self._uow() as uow:
            students = uow.user_queries.active_students()
        for s in students:
            self._send(s.email, templates.dia_sin_clases(s.full_name, event.fecha, event.motivo, event.proximo_dia_habil))

    def on_password_reset_requested(self, event: PasswordResetRequested) -> None:
        with self._uow() as uow:
            user = uow.users.get(event.user_id)
        if user:
            self._send(str(user.email), templates.reset_password(user.full_name, event.reset_url, self._reset_ttl_minutes))

    def on_user_invited(self, event: UserInvited) -> None:
        with self._uow() as uow:
            user = uow.users.get(event.user_id)
        if user:
            self._send(str(user.email), templates.bienvenida(user.full_name, event.set_password_url, self._invite_ttl_hours))


class SendClassReminders:
    """Tarea periódica: recordatorio N horas antes de la clase (reduce el no-show)."""

    def __init__(self, uow: UowFactory, sender: EmailSender, clock: Clock, hours_before: int) -> None:
        self._uow, self._sender, self._clock = uow, sender, clock
        self._window = timedelta(hours=hours_before)

    def execute(self) -> int:
        now = self._clock.now()
        sent = 0
        with self._uow() as uow:
            for turno in uow.turnos.list_pending_reminders(now, now + self._window):
                user, clase = uow.users.get(turno.user_id), uow.clases.get(turno.clase_id)
                if user is None or clase is None:
                    continue
                subject, body = templates.recordatorio(user.full_name, clase.fecha, clase.start_time)
                self._sender.send(EmailMessage(to=str(user.email), subject=subject, body=body))
                turno.mark_reminder_sent(now)
                uow.turnos.save(turno)
                sent += 1
            uow.commit()
        return sent
