from django.contrib.auth import logout
from django.contrib.auth.models import update_last_login
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsSuperAdmin
from accounts.serializers import (
    LoginSerializer,
    UserCreateSerializer,
    UserSerializer,
    UserUpdateSerializer,
)
from audit.services import record_audit


class LoginThrottle(AnonRateThrottle):
    scope = "login"


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
