from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pilates.bootstrap import Container
from pilates.identity.application.dto import UserDTO
from pilates.identity.application.ports import AccessTokenClaims
from pilates.shared.domain.errors import AuthenticationError, PermissionDenied

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def access_claims(
    c: ContainerDep, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> AccessTokenClaims:
    if creds is None:
        raise AuthenticationError("No autenticado", code="not_authenticated")
    return c.token_service.decode(creds.credentials)


ClaimsDep = Annotated[AccessTokenClaims, Depends(access_claims)]


def current_user(c: ContainerDep, claims: ClaimsDep) -> UserDTO:
    """Valida en cada request que la sesión siga abierta y el usuario activo."""
    return c.get_current_user.execute(claims.user_id, claims.session_id)


def require_admin(user: Annotated[UserDTO, Depends(current_user)]) -> UserDTO:
    if user.role != "admin":
        raise PermissionDenied("Requiere rol administrador")
    return user


CurrentUser = Annotated[UserDTO, Depends(current_user)]
AdminUser = Annotated[UserDTO, Depends(require_admin)]
