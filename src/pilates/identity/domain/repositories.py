from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from pilates.identity.domain.model import Email, PasswordResetToken, Sesion, User


class UserRepository(ABC):
    @abstractmethod
    def add(self, user: User) -> None: ...

    @abstractmethod
    def save(self, user: User) -> None: ...

    @abstractmethod
    def get(self, user_id: UUID, *, for_update: bool = False) -> User | None: ...

    @abstractmethod
    def get_by_email(self, email: Email) -> User | None: ...


class PasswordResetTokenRepository(ABC):
    @abstractmethod
    def add(self, token: PasswordResetToken) -> None: ...

    @abstractmethod
    def save(self, token: PasswordResetToken) -> None: ...

    @abstractmethod
    def get_by_hash(self, token_hash: str) -> PasswordResetToken | None: ...

    @abstractmethod
    def invalidate_all_for_user(self, user_id: UUID) -> None: ...


class SesionRepository(ABC):
    @abstractmethod
    def add(self, sesion: Sesion) -> None: ...

    @abstractmethod
    def save(self, sesion: Sesion) -> None: ...

    @abstractmethod
    def get(self, sesion_id: UUID) -> Sesion | None: ...

    @abstractmethod
    def revoke_all_for_user(self, user_id: UUID, now: datetime, reason: str) -> int: ...

    @abstractmethod
    def purge_expired(self, user_id: UUID, before: datetime) -> None:
        """Borra las sesiones del usuario vencidas antes de ``before`` (higiene de la tabla)."""
