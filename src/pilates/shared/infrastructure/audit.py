from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from pilates.shared.application.ports import AuditLog
from pilates.shared.infrastructure.orm import AuditLogModel


class SqlAuditLog(AuditLog):
    def __init__(self, session: Session) -> None:
        self._session = session

    def record(self, admin_id: UUID, action: str, details: dict[str, Any]) -> None:
        self._session.add(AuditLogModel(admin_id=admin_id, action=action, details=details, created_at=datetime.now(UTC)))
