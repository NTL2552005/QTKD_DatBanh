from functools import wraps

from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied


def role_required(role, login_url=None, redirect_field_name=REDIRECT_FIELD_NAME):
    allowed_roles = (
        set(role)
        if isinstance(role, (tuple, list, set, frozenset))
        else {role}
    )

    def check_role(user):
        if not user.is_authenticated:
            return False
        if user.role not in allowed_roles:
            raise PermissionDenied
        return True

    return user_passes_test(
        check_role,
        login_url=login_url,
        redirect_field_name=redirect_field_name,
    )
