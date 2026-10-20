from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pilates.identity.application.dto import UserSummary


class PasswordHasher(ABC):
    @abstractmethod
    def hash(self, raw_password: str) -> str: ...

    @abstractmethod
    def verify(self, raw_password: str, password_hash: str) -> bool: ...


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: UUID
    role: str
    session_id: UUID


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_at: datetime


class TokenService(ABC):
    @abstractmethod
    def issue(self, user_id: UUID, role: str, session_id: UUID) -> IssuedToken: ...

    @abstractmethod
    def decode(self, token: str) -> AccessTokenClaims: ...


class SecretTokenGenerator(ABC):
    """Genera tokens opacos de un solo uso y su hash para persistir."""

    @abstractmethod
    def generate(self) -> str: ...

    @abstractmethod
    def hash(self, raw_token: str) -> str: ...


class UserQueries(ABC):
    @abstractmethod
    def list_users(
        self, *, search: str | None, status: str | None, limit: int, offset: int
    ) -> list[UserSummary]: ...

    @abstractmethod
    def active_students(self) -> list[UserSummary]: ...
