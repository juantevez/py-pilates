from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class UserDTO:
    id: UUID
    email: str
    full_name: str
    role: str
    status: str
    created_at: datetime
    blocked_reason: str | None


@dataclass(frozen=True)
class UserSummary:
    id: UUID
    email: str
    full_name: str
    role: str
    status: str
    created_at: datetime
    blocked_reason: str | None = None
    asistencias: int = 0
    ausencias: int = 0
    reservas_activas: int = 0


@dataclass(frozen=True)
class AuthResult:
    access_token: str
    user: UserDTO
