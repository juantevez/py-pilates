from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from pilates.identity.domain.events import UserBlocked, UserRegistered
from pilates.identity.domain.model import Email, PasswordResetToken, Role, User
from pilates.shared.domain.errors import BusinessRuleViolation, PermissionDenied

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def test_email_is_normalized_and_validated():
    assert str(Email("  Ana@Mail.COM ")) == "ana@mail.com"
    with pytest.raises(BusinessRuleViolation):
        Email("no-es-un-email")


def test_block_user():
    user = User.register(email=Email("a@b.com"), full_name="Ana", password_hash="x", now=NOW)
    user.block("Moroso")
    with pytest.raises(PermissionDenied):
        user.ensure_active()
    assert [type(e) for e in user.pull_events()] == [UserRegistered, UserBlocked]


def test_admin_cannot_be_blocked():
    admin = User.register(email=Email("a@b.com"), full_name="Ana", password_hash="x", now=NOW, role=Role.ADMIN)
    with pytest.raises(BusinessRuleViolation):
        admin.block("x")


def test_reset_token_single_use_and_expiry():
    token = PasswordResetToken(user_id=uuid4(), token_hash="h", expires_at=NOW + timedelta(hours=1), created_at=NOW)
    token.consume(NOW)
    with pytest.raises(BusinessRuleViolation):
        token.consume(NOW)
    expired = PasswordResetToken(user_id=uuid4(), token_hash="h", expires_at=NOW, created_at=NOW)
    assert not expired.is_valid(NOW)
