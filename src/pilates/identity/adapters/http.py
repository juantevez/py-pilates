import hashlib
from uuid import UUID

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, EmailStr, Field

from pilates.identity.application.dto import UserDTO, UserSummary
from pilates.shared.domain.errors import AuthenticationError
from pilates.shared.http.deps import AdminUser, ClaimsDep, ContainerDep, CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(prefix="/admin/usuarios", tags=["admin: usuarios"])


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserDTO


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class CreateUserRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)


class BlockRequest(BaseModel):
    motivo: str = Field(default="", max_length=500)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def _email_key(email: str) -> str:
    # El email no se guarda en claro en Redis.
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserDTO)
def register(body: RegisterRequest, request: Request, c: ContainerDep):
    c.rate_limiter.hit(f"register:ip:{_client_ip(request)}", c.settings.registrations_per_ip_per_hour, 3600)
    return c.register_user.execute(email=body.email, full_name=body.full_name, password=body.password)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, c: ContainerDep):
    """Límites: intentos por IP por minuto, y fallos por email (contra fuerza bruta a una cuenta)."""
    s, limiter = c.settings, c.rate_limiter
    limiter.hit(f"login:ip:{_client_ip(request)}", s.login_attempts_per_ip_per_minute, 60)
    failures = f"login:fail:{_email_key(body.email)}"
    limiter.check(failures, s.login_failures_per_email)
    try:
        result = c.login.execute(email=body.email, password=body.password)
    except AuthenticationError:
        limiter.record(failures, s.login_failure_window_minutes * 60)
        raise
    limiter.reset(failures)
    return TokenResponse(access_token=result.access_token, user=result.user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(c: ContainerDep, _: CurrentUser, claims: ClaimsDep) -> None:
    """Cierra la sesión de este dispositivo: el token deja de servir aunque no haya vencido."""
    c.logout.execute(session_id=claims.session_id)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotPasswordRequest, request: Request, c: ContainerDep) -> dict:
    """Límites por IP y por email. El contador por email cuenta exista o no la cuenta,
    así un 429 no revela qué emails están registrados."""
    s, limiter = c.settings, c.rate_limiter
    limiter.hit(f"forgot:ip:{_client_ip(request)}", s.password_resets_per_ip_per_hour, 3600)
    limiter.hit(f"forgot:email:{_email_key(body.email)}", s.password_resets_per_email_per_hour, 3600)
    c.request_password_reset.execute(email=body.email)
    return {"detail": "Si el email está registrado, vas a recibir un enlace para restablecer la contraseña."}


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(body: ResetPasswordRequest, c: ContainerDep) -> None:
    c.reset_password.execute(token=body.token, new_password=body.new_password)


@router.get("/me", response_model=UserDTO)
def me(user: CurrentUser):
    return user


@admin_router.get("", response_model=list[UserSummary])
def list_users(
    c: ContainerDep,
    _: AdminUser,
    search: str | None = None,
    estado: str | None = Query(default=None, pattern="^(activo|bloqueado)$"),
    limit: int = Query(default=50, le=200),
    offset: int = 0,
):
    return c.list_users.execute(search=search, status=estado, limit=limit, offset=offset)


@admin_router.post("", status_code=status.HTTP_201_CREATED, response_model=UserDTO)
def create_user(body: CreateUserRequest, c: ContainerDep, admin: AdminUser):
    """Alta de un alumno por el gimnasio; recibe un email para definir su contraseña."""
    return c.create_user_by_admin.execute(admin_id=admin.id, email=body.email, full_name=body.full_name)


@admin_router.post("/{user_id}/bloquear", response_model=UserDTO)
def block_user(user_id: UUID, body: BlockRequest, c: ContainerDep, admin: AdminUser):
    return c.block_user.execute(admin_id=admin.id, user_id=user_id, reason=body.motivo)


@admin_router.post("/{user_id}/desbloquear", response_model=UserDTO)
def unblock_user(user_id: UUID, c: ContainerDep, admin: AdminUser):
    return c.unblock_user.execute(admin_id=admin.id, user_id=user_id)
