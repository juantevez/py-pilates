from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, EmailStr, Field

from pilates.identity.application.dto import UserDTO, UserSummary
from pilates.shared.http.deps import AdminUser, ContainerDep, CurrentUser

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


class BlockRequest(BaseModel):
    motivo: str = Field(default="", max_length=500)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserDTO)
def register(body: RegisterRequest, c: ContainerDep):
    return c.register_user.execute(email=body.email, full_name=body.full_name, password=body.password)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, c: ContainerDep):
    result = c.login.execute(email=body.email, password=body.password)
    return TokenResponse(access_token=result.access_token, user=result.user)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(body: ForgotPasswordRequest, c: ContainerDep) -> dict:
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


@admin_router.post("/{user_id}/bloquear", response_model=UserDTO)
def block_user(user_id: UUID, body: BlockRequest, c: ContainerDep, admin: AdminUser):
    return c.block_user.execute(admin_id=admin.id, user_id=user_id, reason=body.motivo)


@admin_router.post("/{user_id}/desbloquear", response_model=UserDTO)
def unblock_user(user_id: UUID, c: ContainerDep, admin: AdminUser):
    return c.unblock_user.execute(admin_id=admin.id, user_id=user_id)
