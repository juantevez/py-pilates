"""Errores de dominio. La capa HTTP los traduce a códigos de estado."""


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class BusinessRuleViolation(DomainError):
    code = "business_rule_violation"


class AuthenticationError(DomainError):
    code = "authentication_failed"


class PermissionDenied(DomainError):
    code = "permission_denied"
