from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pilates.shared.domain.aggregate import AggregateRoot
from pilates.shared.domain.errors import BusinessRuleViolation, PermissionDenied
from pilates.identity.domain.events import UserBlocked, UserRegistered

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Role(StrEnum):
    ALUMNO = "alumno"
    ADMIN = "admin"


class UserStatus(StrEnum):
    ACTIVO = "activo"
    BLOQUEADO = "bloqueado"


@dataclass(frozen=True)
class Email:
    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _EMAIL_RE.match(normalized):
            raise BusinessRuleViolation("Email inválido", code="invalid_email")
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value


@dataclass(eq=False, kw_only=True)
class User(AggregateRoot):
    id: UUID
    email: Email
    full_name: str
    password_hash: str
    role: Role
    status: UserStatus
    created_at: datetime
    blocked_reason: str | None = None

    @classmethod
    def register(
        cls, *, email: Email, full_name: str, password_hash: str, now: datetime, role: Role = Role.ALUMNO
    ) -> User:
        full_name = full_name.strip()
        if not full_name:
            raise BusinessRuleViolation("El nombre es obligatorio", code="invalid_name")
        user = cls(
            id=uuid4(),
            email=email,
            full_name=full_name,
            password_hash=password_hash,
            role=role,
            status=UserStatus.ACTIVO,
            created_at=now,
        )
        user.record(UserRegistered(user_id=user.id))
        return user

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN

    @property
    def is_blocked(self) -> bool:
        return self.status is UserStatus.BLOQUEADO

    def ensure_active(self) -> None:
        if self.is_blocked:
            raise PermissionDenied("Usuario bloqueado. Contactá a recepción.", code="user_blocked")

    def change_password(self, new_hash: str) -> None:
        self.password_hash = new_hash

    def block(self, reason: str) -> None:
        if self.is_admin:
            raise BusinessRuleViolation("No se puede bloquear a un administrador", code="cannot_block_admin")
        if self.is_blocked:
            return
        self.status = UserStatus.BLOQUEADO
        self.blocked_reason = reason.strip() or None
        self.record(UserBlocked(user_id=self.id, reason=self.blocked_reason))

    def unblock(self) -> None:
        self.status = UserStatus.ACTIVO
        self.blocked_reason = None


@dataclass(eq=False, kw_only=True)
class PasswordResetToken(AggregateRoot):
    """Token de un solo uso. Sólo se persiste el hash del token."""

    id: UUID = field(default_factory=uuid4)
    user_id: UUID
    token_hash: str
    expires_at: datetime
    created_at: datetime
    used_at: datetime | None = None

    def is_valid(self, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at

    def consume(self, now: datetime) -> None:
        if not self.is_valid(now):
            raise BusinessRuleViolation("El enlace de recuperación es inválido o expiró", code="invalid_reset_token")
        self.used_at = now


@dataclass(eq=False, kw_only=True)
class Sesion(AggregateRoot):
    """Un login en un dispositivo. El JWT lleva su id (``sid``); el vencimiento lo controla el
    propio token, la sesión sirve para revocarlo antes (cerrar sesión, cambio de contraseña)."""

    id: UUID = field(default_factory=uuid4)
    user_id: UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoke_reason: str | None = None

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def revoke(self, now: datetime, reason: str) -> None:
        if not self.is_revoked:
            self.revoked_at, self.revoke_reason = now, reason
