from rest_framework.permissions import BasePermission, SAFE_METHODS

from accounts.models import User


class SuperAdminWriteAdminReadOnly(BasePermission):
    message = "Only a Super Admin may make changes."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated or not user.is_active:
            return False
        if request.method in SAFE_METHODS:
            return True
        return user.role == User.Role.SUPER_ADMIN
