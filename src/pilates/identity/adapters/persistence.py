from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from pilates.booking.adapters.orm import TurnoModel
from pilates.booking.domain.model import TurnoStatus
from pilates.identity.adapters.orm import PasswordResetTokenModel, SesionModel, UserModel
from pilates.identity.application.dto import UserSummary
from pilates.identity.application.ports import UserQueries
from pilates.identity.domain.model import Email, PasswordResetToken, Role, Sesion, User, UserStatus
from pilates.identity.domain.repositories import PasswordResetTokenRepository, SesionRepository, UserRepository
from pilates.shared.application.unit_of_work import UnitOfWork


def _user_to_domain(m: UserModel) -> User:
    return User(
        id=m.id,
        email=Email(m.email),
        full_name=m.full_name,
        password_hash=m.password_hash,
        role=m.role,
        status=m.status,
        created_at=m.created_at,
        blocked_reason=m.blocked_reason,
    )


def _user_to_model(u: User) -> UserModel:
    return UserModel(
        id=u.id,
        email=str(u.email),
        full_name=u.full_name,
        password_hash=u.password_hash,
        role=u.role,
        status=u.status,
        created_at=u.created_at,
        blocked_reason=u.blocked_reason,
    )


class SqlUserRepository(UserRepository):
    def __init__(self, session: Session, uow: UnitOfWork) -> None:
        self._s, self._uow = session, uow

    def add(self, user: User) -> None:
        self._s.add(_user_to_model(user))
        self._s.flush()  # sin relationship() el orden de INSERT no está garantizado (FK de reset tokens)
        self._uow.track(user)

    def save(self, user: User) -> None:
        self._s.merge(_user_to_model(user))
        self._uow.track(user)

    def get(self, user_id: UUID, *, for_update: bool = False) -> User | None:
        stmt = select(UserModel).where(UserModel.id == user_id)
        if for_update:
            stmt = stmt.with_for_update()
        m = self._s.execute(stmt).scalar_one_or_none()
        return _user_to_domain(m) if m else None

    def get_by_email(self, email: Email) -> User | None:
        m = self._s.execute(select(UserModel).where(UserModel.email == str(email))).scalar_one_or_none()
        return _user_to_domain(m) if m else None


def _token_to_model(t: PasswordResetToken) -> PasswordResetTokenModel:
    return PasswordResetTokenModel(
        id=t.id, user_id=t.user_id, token_hash=t.token_hash, expires_at=t.expires_at, created_at=t.created_at,
        used_at=t.used_at,
    )


class SqlPasswordResetTokenRepository(PasswordResetTokenRepository):
    def __init__(self, session: Session, uow: UnitOfWork) -> None:
        self._s, self._uow = session, uow

    def add(self, token: PasswordResetToken) -> None:
        self._s.add(_token_to_model(token))
        self._uow.track(token)

    def save(self, token: PasswordResetToken) -> None:
        self._s.merge(_token_to_model(token))
        self._uow.track(token)

    def get_by_hash(self, token_hash: str) -> PasswordResetToken | None:
        m = self._s.execute(
            select(PasswordResetTokenModel).where(PasswordResetTokenModel.token_hash == token_hash).with_for_update()
        ).scalar_one_or_none()
        if m is None:
            return None
        return PasswordResetToken(
            id=m.id, user_id=m.user_id, token_hash=m.token_hash, expires_at=m.expires_at, created_at=m.created_at,
            used_at=m.used_at,
        )

    def invalidate_all_for_user(self, user_id: UUID) -> None:
        self._s.execute(
            update(PasswordResetTokenModel)
            .where(PasswordResetTokenModel.user_id == user_id, PasswordResetTokenModel.used_at.is_(None))
            .values(used_at=func.now())
        )


def _sesion_to_model(x: Sesion) -> SesionModel:
    return SesionModel(
        id=x.id, user_id=x.user_id, created_at=x.created_at, expires_at=x.expires_at, revoked_at=x.revoked_at,
        revoke_reason=x.revoke_reason,
    )


class SqlSesionRepository(SesionRepository):
    def __init__(self, session: Session, uow: UnitOfWork) -> None:
        self._s, self._uow = session, uow

    def add(self, sesion: Sesion) -> None:
        self._s.add(_sesion_to_model(sesion))
        self._uow.track(sesion)

    def save(self, sesion: Sesion) -> None:
        self._s.merge(_sesion_to_model(sesion))
        self._uow.track(sesion)

    def get(self, sesion_id: UUID) -> Sesion | None:
        m = self._s.get(SesionModel, sesion_id)
        if m is None:
            return None
        return Sesion(
            id=m.id, user_id=m.user_id, created_at=m.created_at, expires_at=m.expires_at, revoked_at=m.revoked_at,
            revoke_reason=m.revoke_reason,
        )

    def revoke_all_for_user(self, user_id: UUID, now: datetime, reason: str) -> int:
        result = self._s.execute(
            update(SesionModel)
            .where(SesionModel.user_id == user_id, SesionModel.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason=reason)
        )
        return result.rowcount

    def purge_expired(self, user_id: UUID, before: datetime) -> None:
        self._s.execute(delete(SesionModel).where(SesionModel.user_id == user_id, SesionModel.expires_at < before))


def _count_turnos(status: TurnoStatus):
    return (
        select(func.count(TurnoModel.id))
        .where(and_(TurnoModel.user_id == UserModel.id, TurnoModel.status == status))
        .correlate(UserModel)
        .scalar_subquery()
    )


class SqlUserQueries(UserQueries):
    def __init__(self, session: Session) -> None:
        self._s = session

    def list_users(self, *, search: str | None, status: str | None, limit: int, offset: int) -> list[UserSummary]:
        stmt = select(
            UserModel,
            _count_turnos(TurnoStatus.ASISTIO).label("asistencias"),
            _count_turnos(TurnoStatus.NO_ASISTIO).label("ausencias"),
            _count_turnos(TurnoStatus.CONFIRMADA).label("activas"),
        )
        if search:
            like = f"%{search.strip()}%"
            stmt = stmt.where(or_(UserModel.email.ilike(like), UserModel.full_name.ilike(like)))
        if status:
            stmt = stmt.where(UserModel.status == UserStatus(status))
        stmt = stmt.order_by(UserModel.full_name).limit(limit).offset(offset)
        return [
            UserSummary(
                id=u.id, email=u.email, full_name=u.full_name, role=u.role.value, status=u.status.value,
                created_at=u.created_at, blocked_reason=u.blocked_reason,
                asistencias=asis, ausencias=aus, reservas_activas=act,
            )
            for u, asis, aus, act in self._s.execute(stmt).all()
        ]

    def active_students(self) -> list[UserSummary]:
        rows = self._s.execute(
            select(UserModel).where(UserModel.role == Role.ALUMNO, UserModel.status == UserStatus.ACTIVO)
        ).scalars()
        return [
            UserSummary(id=u.id, email=u.email, full_name=u.full_name, role=u.role.value, status=u.status.value,
                        created_at=u.created_at)
            for u in rows
        ]
