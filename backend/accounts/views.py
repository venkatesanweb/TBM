from django.contrib.auth import logout
from django.contrib.auth.models import update_last_login
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsSuperAdmin
from accounts.serializers import (
    DashboardAccessSerializer,
    LoginSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
)
from audit.services import record_audit


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class SensitiveAdminThrottle(ScopedRateThrottle):
    scope = "sensitive_admin"


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [LoginThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        record_audit(actor=user, action="auth.login", instance=user)
        return Response(
            {"token": token.key, "user": UserSerializer(user).data},
            status=status.HTTP_200_OK,
        )


class LogoutView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        record_audit(actor=request.user, action="auth.logout", instance=request.user)
        request.auth.delete()
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentUserView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class UserListCreateView(generics.ListCreateAPIView):
    queryset = User.objects.order_by("username")
    permission_classes = [IsSuperAdmin]
    throttle_classes = [SensitiveAdminThrottle]
    throttle_scope = "sensitive_admin"

    def get_serializer_class(self):
        if self.request.method == "POST":
            return UserCreateSerializer
        return UserSerializer

    @transaction.atomic
    def perform_create(self, serializer):
        user = serializer.save()
        record_audit(
            actor=self.request.user,
            action="user.created",
            instance=user,
            after={"username": user.username, "email": user.email, "role": user.role},
        )


class UserDetailView(generics.RetrieveUpdateAPIView):
    queryset = User.objects.all()
    permission_classes = [IsSuperAdmin]
    http_method_names = ["get", "patch", "head", "options"]
    throttle_classes = [SensitiveAdminThrottle]
    throttle_scope = "sensitive_admin"

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return UserUpdateSerializer
        return UserSerializer

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def perform_update(self, serializer):
        target = serializer.instance
        before = {"role": target.role, "is_active": target.is_active}
        user = serializer.save()
        record_audit(
            actor=self.request.user,
            action="user.updated",
            instance=user,
            before=before,
            after={"role": user.role, "is_active": user.is_active},
        )
        if not user.is_active:
            Token.objects.filter(user=user).delete()


class UserDashboardAccessView(APIView):
    permission_classes = [IsSuperAdmin]
    throttle_classes = [SensitiveAdminThrottle]
    throttle_scope = "sensitive_admin"

    @transaction.atomic
    def patch(self, request, pk):
        target = User.objects.select_for_update().get(pk=pk)
        if target.role != User.Role.ADMIN:
            return Response(
                {"detail": "Dashboard access can only be assigned to an Admin."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = DashboardAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        before = {
            "dashboard_sections": target.dashboard_sections,
            "can_export_dashboard_reports": target.can_export_dashboard_reports,
        }
        after = serializer.validated_data
        granted = sorted(set(after["dashboard_sections"]) - set(before["dashboard_sections"]))
        revoked = sorted(set(before["dashboard_sections"]) - set(after["dashboard_sections"]))
        export_changed = (
            before["can_export_dashboard_reports"]
            != after["can_export_dashboard_reports"]
        )
        target.dashboard_sections = after["dashboard_sections"]
        target.can_export_dashboard_reports = after["can_export_dashboard_reports"]
        target.save(
            update_fields=("dashboard_sections", "can_export_dashboard_reports")
        )
        record_audit(
            actor=request.user,
            action="dashboard.access.updated",
            instance=target,
            before=before,
            after={
                **after,
                "granted_sections": granted,
                "revoked_sections": revoked,
                "export_permission_changed": export_changed,
            },
        )
        return Response(UserSerializer(target).data)
