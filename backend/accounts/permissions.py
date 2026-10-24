from rest_framework.permissions import BasePermission


class IsSuperAdmin(BasePermission):
    message = "Only a Super Admin may perform this action."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.is_active
            and request.user.role == request.user.Role.SUPER_ADMIN
        )
