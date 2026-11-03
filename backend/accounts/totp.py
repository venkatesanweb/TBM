import pyotp
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from accounts.models import User


def verify_super_admin_totp(user, code):
    if user.role != User.Role.SUPER_ADMIN:
        return True
    secret = settings.SUPER_ADMIN_TOTP_SECRETS.get(user.get_username())
    if not secret:
        raise ImproperlyConfigured(
            "TOTP is enabled but no secret is configured for this Super Admin."
        )
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)
