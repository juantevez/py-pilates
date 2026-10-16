"""Raíz de composición: conecta puertos con adaptadores y arma los casos de uso."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine

from pilates.booking.application.queries import GetAttendanceSheet, GetAvailability, GetMyBookings
from pilates.booking.application.services import (
    BookClass,
    CancelBooking,
    CancelUpcomingBookingsOfBlockedUser,
    JoinWaitlist,
    LeaveWaitlist,
    MarkAttendance,
)
from pilates.booking.domain.events import TurnoCancelado, TurnoReservado
from pilates.booking.domain.policy import BookingPolicy
from pilates.config import Settings
from pilates.identity.adapters.security import BcryptPasswordHasher, JwtTokenService, Sha256SecretTokenGenerator
from pilates.identity.application import services as identity
from pilates.identity.domain.events import PasswordResetRequested, UserBlocked
from pilates.notifications.adapters.email import ConsoleEmailSender, SmtpEmailSender
from pilates.notifications.application.handlers import NotificationHandlers, SendClassReminders
from pilates.notifications.application.ports import EmailSender
from pilates.scheduling.application import services as scheduling
from pilates.scheduling.domain.calendar import BookingCalendar, GridTemplate
from pilates.scheduling.domain.events import ClasesReabiertas, DiaSinClases, TurnosHabilitados
from pilates.shared.application.ports import Clock, EventBus
from pilates.shared.infrastructure.clock import SystemClock
from pilates.shared.infrastructure.db import make_engine, make_session_factory
from pilates.shared.infrastructure.event_bus import CeleryEventBus, EventRegistry, SyncEventBus
from pilates.shared.infrastructure.unit_of_work import SqlAlchemyUnitOfWork


@dataclass
class Container:
    settings: Settings
    engine: Engine
    clock: Clock
    registry: EventRegistry
    email_sender: EmailSender
    token_service: JwtTokenService
    # identity
    register_user: identity.RegisterUser
    login: identity.Login
    get_current_user: identity.GetCurrentUser
    request_password_reset: identity.RequestPasswordReset
    reset_password: identity.ResetPassword
    block_user: identity.BlockUser
    unblock_user: identity.UnblockUser
    list_users: identity.ListUsers
    # scheduling
    generate_daily_classes: scheduling.GenerateDailyClasses
    list_classes_for_date: scheduling.ListClassesForDate
    update_clase: scheduling.UpdateClase
    cancel_clase: scheduling.CancelClase
    reactivate_clase: scheduling.ReactivateClase
    create_cierres: scheduling.CreateCierres
    update_cierre: scheduling.UpdateCierre
    delete_cierre: scheduling.DeleteCierre
    list_cierres: scheduling.ListCierres
    get_calendar: scheduling.GetCalendar
    get_student_calendar: scheduling.GetStudentCalendar
    # booking
    get_availability: GetAvailability
    book_class: BookClass
    cancel_booking: CancelBooking
    join_waitlist: JoinWaitlist
    leave_waitlist: LeaveWaitlist
    my_bookings: GetMyBookings
    attendance_sheet: GetAttendanceSheet
    mark_attendance: MarkAttendance
    # notifications
    send_reminders: SendClassReminders


def _email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "console":
        return ConsoleEmailSender()
    return SmtpEmailSender(
        host=settings.smtp_host, port=settings.smtp_port, sender=settings.email_from,
        username=settings.smtp_user, password=settings.smtp_password, use_tls=settings.smtp_use_tls,
    )


def _default_event_bus(settings: Settings, registry: EventRegistry) -> EventBus:
    if settings.event_bus == "sync":
        return SyncEventBus(registry)
    from pilates.worker.celery_app import celery_app

    return CeleryEventBus(registry, celery_app.send_task)


def build_container(
    settings: Settings,
    *,
    engine: Engine | None = None,
    clock: Clock | None = None,
    email_sender: EmailSender | None = None,
    event_bus: EventBus | None = None,
) -> Container:
    logging.basicConfig(level=settings.log_level)
    tz = ZoneInfo(settings.timezone)
    engine = engine or make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    clock = clock or SystemClock(settings.timezone)
    email_sender = email_sender or _email_sender(settings)
    registry = EventRegistry()
    bus = event_bus or _default_event_bus(settings, registry)

    def uow() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory, bus, tz)

    hasher = BcryptPasswordHasher()
    tokens = JwtTokenService(settings.jwt_secret, settings.jwt_algorithm, timedelta(minutes=settings.jwt_expires_minutes))
    secrets = Sha256SecretTokenGenerator()
    grid = GridTemplate.from_times(
        settings.morning_times,
        settings.afternoon_times,
        timedelta(minutes=settings.class_duration_minutes),
        settings.class_capacity,
    )
    calendar = BookingCalendar(opening_time=settings.booking_opening_time, allow_same_day=settings.allow_same_day_booking)
    policy = BookingPolicy(
        max_per_day=settings.max_bookings_per_day,
        max_per_block=settings.max_bookings_per_block,
        min_advance=timedelta(minutes=settings.min_booking_advance_minutes),
        cancel_min_notice=timedelta(minutes=settings.cancel_min_notice_minutes),
    )

    notifications = NotificationHandlers(
        uow, email_sender, clock, settings.frontend_url, settings.password_reset_ttl_minutes
    )
    registry.subscribe(TurnoReservado, "notifications.confirmacion", notifications.on_turno_reservado)
    registry.subscribe(TurnoCancelado, "notifications.cancelacion_y_lista_espera", notifications.on_turno_cancelado)
    registry.subscribe(TurnosHabilitados, "notifications.apertura", notifications.on_turnos_habilitados)
    registry.subscribe(ClasesReabiertas, "notifications.clases_reabiertas", notifications.on_clases_reabiertas)
    registry.subscribe(DiaSinClases, "notifications.dia_sin_clases", notifications.on_dia_sin_clases)
    registry.subscribe(PasswordResetRequested, "notifications.reset_password", notifications.on_password_reset_requested)
    registry.subscribe(UserBlocked, "booking.cancelar_turnos_bloqueado", CancelUpcomingBookingsOfBlockedUser(uow, clock))

    return Container(
        settings=settings,
        engine=engine,
        clock=clock,
        registry=registry,
        email_sender=email_sender,
        token_service=tokens,
        register_user=identity.RegisterUser(uow, hasher, clock),
        login=identity.Login(uow, hasher, tokens),
        get_current_user=identity.GetCurrentUser(uow),
        request_password_reset=identity.RequestPasswordReset(
            uow, secrets, clock, settings.reset_url_template, timedelta(minutes=settings.password_reset_ttl_minutes)
        ),
        reset_password=identity.ResetPassword(uow, secrets, hasher, clock),
        block_user=identity.BlockUser(uow),
        unblock_user=identity.UnblockUser(uow),
        list_users=identity.ListUsers(uow),
        generate_daily_classes=scheduling.GenerateDailyClasses(uow, clock, grid, calendar, settings.notify_closed_day),
        list_classes_for_date=scheduling.ListClassesForDate(uow),
        update_clase=scheduling.UpdateClase(uow),
        cancel_clase=scheduling.CancelClase(uow, clock),
        reactivate_clase=scheduling.ReactivateClase(uow, clock),
        create_cierres=scheduling.CreateCierres(uow, clock),
        update_cierre=scheduling.UpdateCierre(uow),
        delete_cierre=scheduling.DeleteCierre(uow, clock, grid, calendar),
        list_cierres=scheduling.ListCierres(uow),
        get_calendar=scheduling.GetCalendar(uow),
        get_student_calendar=scheduling.GetStudentCalendar(uow, clock),
        get_availability=GetAvailability(uow, clock, calendar, policy),
        book_class=BookClass(uow, clock, calendar, policy),
        cancel_booking=CancelBooking(uow, clock, policy),
        join_waitlist=JoinWaitlist(uow, clock, calendar, policy),
        leave_waitlist=LeaveWaitlist(uow),
        my_bookings=GetMyBookings(uow, clock, policy),
        attendance_sheet=GetAttendanceSheet(uow, clock),
        mark_attendance=MarkAttendance(uow, clock),
        send_reminders=SendClassReminders(uow, email_sender, clock, settings.reminder_hours_before),
    )
