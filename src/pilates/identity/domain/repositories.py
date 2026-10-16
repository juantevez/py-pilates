from abc import ABC, abstractmethod
from uuid import UUID

from pilates.identity.domain.model import Email, PasswordResetToken, User


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
