from rest_framework.permissions import BasePermission, SAFE_METHODS

from accounts.models import User


class WorkOrderPermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)

    def has_object_permission(self, request, view, obj):
        return (
            request.user.role == User.Role.SUPER_ADMIN
            or obj.assigned_admin_id == request.user.pk
        )


class SuperAdminOnly(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and user.role == User.Role.SUPER_ADMIN
        )
