from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

from pilates.identity.application.dto import AuthResult, UserDTO, UserSummary
from pilates.identity.application.ports import PasswordHasher, SecretTokenGenerator, TokenService
from pilates.identity.domain.events import PasswordResetRequested
from pilates.identity.domain.model import Email, PasswordResetToken, Role, User
from pilates.shared.application.ports import Clock
from pilates.shared.application.unit_of_work import UnitOfWork
from pilates.shared.domain.errors import (
    AuthenticationError,
    BusinessRuleViolation,
    ConflictError,
    NotFoundError,
)

UowFactory = Callable[[], UnitOfWork]
MIN_PASSWORD_LENGTH = 8


def to_dto(user: User) -> UserDTO:
    return UserDTO(
        id=user.id,
        email=str(user.email),
        full_name=user.full_name,
        role=user.role.value,
        status=user.status.value,
        created_at=user.created_at,
        blocked_reason=user.blocked_reason,
    )


def _validate_password(raw: str) -> None:
    if len(raw) < MIN_PASSWORD_LENGTH:
        raise BusinessRuleViolation(
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres", code="weak_password"
        )


class RegisterUser:
    def __init__(self, uow: UowFactory, hasher: PasswordHasher, clock: Clock) -> None:
        self._uow, self._hasher, self._clock = uow, hasher, clock

    def execute(self, *, email: str, full_name: str, password: str, role: Role = Role.ALUMNO) -> UserDTO:
        _validate_password(password)
        email_vo = Email(email)
        with self._uow() as uow:
            if uow.users.get_by_email(email_vo):
                raise ConflictError("Ya existe un usuario con ese email", code="email_taken")
            user = User.register(
                email=email_vo,
                full_name=full_name,
                password_hash=self._hasher.hash(password),
                now=self._clock.now(),
                role=role,
            )
            uow.users.add(user)
            uow.commit()
            return to_dto(user)


class Login:
    def __init__(self, uow: UowFactory, hasher: PasswordHasher, tokens: TokenService) -> None:
        self._uow, self._hasher, self._tokens = uow, hasher, tokens

    def execute(self, *, email: str, password: str) -> AuthResult:
        try:
            email_vo = Email(email)
        except BusinessRuleViolation:
            raise AuthenticationError("Credenciales inválidas") from None
        with self._uow() as uow:
            user = uow.users.get_by_email(email_vo)
            if user is None or not self._hasher.verify(password, user.password_hash):
                raise AuthenticationError("Credenciales inválidas")
            user.ensure_active()
            return AuthResult(access_token=self._tokens.issue(user.id, user.role.value), user=to_dto(user))


class GetCurrentUser:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, user_id: UUID) -> UserDTO:
        with self._uow() as uow:
            user = uow.users.get(user_id)
            if user is None:
                raise AuthenticationError("Usuario inexistente")
            user.ensure_active()
            return to_dto(user)


class RequestPasswordReset:
    """Genera un token de un solo uso y dispara el email. Nunca revela si el email existe."""

    def __init__(
        self,
        uow: UowFactory,
        tokens: SecretTokenGenerator,
        clock: Clock,
        reset_url_template: str,
        ttl: timedelta,
    ) -> None:
        self._uow, self._tokens, self._clock = uow, tokens, clock
        self._url_template, self._ttl = reset_url_template, ttl

    def execute(self, *, email: str) -> None:
        try:
            email_vo = Email(email)
        except BusinessRuleViolation:
            return
        with self._uow() as uow:
            user = uow.users.get_by_email(email_vo)
            if user is None or user.is_blocked:
                return
            now = self._clock.now()
            uow.reset_tokens.invalidate_all_for_user(user.id)
            raw = self._tokens.generate()
            token = PasswordResetToken(
                user_id=user.id, token_hash=self._tokens.hash(raw), expires_at=now + self._ttl, created_at=now
            )
            token.record(
                PasswordResetRequested(user_id=user.id, reset_url=self._url_template.format(token=raw))
            )
            uow.reset_tokens.add(token)
            uow.commit()


class ResetPassword:
    def __init__(self, uow: UowFactory, tokens: SecretTokenGenerator, hasher: PasswordHasher, clock: Clock) -> None:
        self._uow, self._tokens, self._hasher, self._clock = uow, tokens, hasher, clock

    def execute(self, *, token: str, new_password: str) -> None:
        _validate_password(new_password)
        with self._uow() as uow:
            stored = uow.reset_tokens.get_by_hash(self._tokens.hash(token))
            if stored is None:
                raise BusinessRuleViolation("El enlace de recuperación es inválido o expiró", code="invalid_reset_token")
            stored.consume(self._clock.now())
            user = uow.users.get(stored.user_id)
            if user is None:
                raise NotFoundError("Usuario inexistente")
            user.change_password(self._hasher.hash(new_password))
            uow.reset_tokens.save(stored)
            uow.users.save(user)
            uow.commit()


class BlockUser:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, admin_id: UUID, user_id: UUID, reason: str) -> UserDTO:
        with self._uow() as uow:
            user = uow.users.get(user_id, for_update=True)
            if user is None:
                raise NotFoundError("Usuario inexistente")
            user.block(reason)
            uow.users.save(user)
            uow.audit.record(admin_id, "user.blocked", {"user_id": str(user_id), "reason": reason})
            uow.commit()
            return to_dto(user)


class UnblockUser:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, admin_id: UUID, user_id: UUID) -> UserDTO:
        with self._uow() as uow:
            user = uow.users.get(user_id, for_update=True)
            if user is None:
                raise NotFoundError("Usuario inexistente")
            user.unblock()
            uow.users.save(user)
            uow.audit.record(admin_id, "user.unblocked", {"user_id": str(user_id)})
            uow.commit()
            return to_dto(user)


class ListUsers:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, search: str | None, status: str | None, limit: int, offset: int) -> list[UserSummary]:
        with self._uow() as uow:
            return uow.user_queries.list_users(search=search, status=status, limit=limit, offset=offset)
