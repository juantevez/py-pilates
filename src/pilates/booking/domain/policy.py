from dataclasses import dataclass
from datetime import datetime, timedelta

from pilates.booking.domain.model import ClassSnapshot
from pilates.shared.domain.errors import BusinessRuleViolation, ConflictError

_BLOQUES = {"manana": "la mañana", "tarde": "la tarde"}


@dataclass(frozen=True)
class BookingPolicy:
    """Reglas de negocio para reservar y cancelar turnos."""

    max_per_day: int
    max_per_block: int
    min_advance: timedelta
    cancel_min_notice: timedelta

    def ensure_can_book(
        self,
        *,
        now: datetime,
        clase: ClassSnapshot,
        within_booking_window: bool,
        active_bookings: int,
        user_bookings_same_day: list[ClassSnapshot],
    ) -> None:
        self._ensure_reservable(now, clase, within_booking_window)
        self._ensure_has_capacity(clase, active_bookings)
        if len(user_bookings_same_day) >= self.max_per_day:
            raise BusinessRuleViolation(
                f"Ya alcanzaste el máximo de {self.max_per_day} clase(s) por día", code="daily_limit_reached"
            )
        same_block = [c for c in user_bookings_same_day if c.bloque == clase.bloque]
        if len(same_block) >= self.max_per_block:
            raise BusinessRuleViolation(
                f"Ya tenés {self.max_per_block} clase(s) reservada(s) por {_BLOQUES.get(clase.bloque, clase.bloque)} ese día",
                code="block_limit_reached",
            )

    def ensure_admin_can_book(self, *, clase: ClassSnapshot, active_bookings: int) -> None:
        """El gimnasio reserva por un alumno salteando ventana, anticipación y límites: sólo respeta el cupo."""
        if not clase.is_active:
            raise BusinessRuleViolation("La clase no está disponible", code="class_not_available")
        self._ensure_has_capacity(clase, active_bookings)

    def ensure_can_join_waitlist(
        self, *, now: datetime, clase: ClassSnapshot, within_booking_window: bool, active_bookings: int
    ) -> None:
        self._ensure_reservable(now, clase, within_booking_window)
        if active_bookings < clase.capacity:
            raise BusinessRuleViolation("La clase tiene lugares libres: reservá directamente", code="class_not_full")

    def can_book(self, *, now: datetime, clase: ClassSnapshot, within_booking_window: bool) -> bool:
        try:
            self._ensure_reservable(now, clase, within_booking_window)
        except BusinessRuleViolation:
            return False
        return True

    def can_cancel(self, *, now: datetime, class_starts_at: datetime) -> bool:
        return now < class_starts_at - self.cancel_min_notice

    def _ensure_has_capacity(self, clase: ClassSnapshot, active_bookings: int) -> None:
        if active_bookings >= clase.capacity:
            raise ConflictError("La clase está completa. Podés anotarte en la lista de espera.", code="class_full")

    def _ensure_reservable(self, now: datetime, clase: ClassSnapshot, within_booking_window: bool) -> None:
        if not clase.is_active:
            raise BusinessRuleViolation("La clase no está disponible", code="class_not_available")
        if not within_booking_window:
            raise BusinessRuleViolation(
                "Sólo se pueden reservar las clases del siguiente día hábil, una vez habilitados los turnos",
                code="outside_booking_window",
            )
        if now >= clase.starts_at - self.min_advance:
            raise BusinessRuleViolation("La reserva para esta clase ya cerró", code="booking_closed")
