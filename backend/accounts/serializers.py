from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password as validate_user_password
from django.core.exceptions import ImproperlyConfigured
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.module_loading import import_string
from rest_framework import serializers

from accounts.models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "role", "is_active", "date_joined")
        read_only_fields = ("id", "date_joined")


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=12)

    class Meta:
        model = User
        fields = ("id", "username", "email", "password", "role")
        read_only_fields = ("id",)

    def validate(self, attrs):
        candidate = User(
            username=attrs.get("username", ""),
            email=attrs.get("email", ""),
        )
        try:
            validate_user_password(attrs["password"], candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return attrs

    def create(self, validated_data):
        role = validated_data.pop("role", User.Role.ADMIN)
        password = validated_data.pop("password")
        user = User(**validated_data, role=role)
        user.set_password(password)
        user.is_staff = role == User.Role.SUPER_ADMIN
        user.save()
        return user


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("role", "is_active")

    def validate(self, attrs):
        target = self.instance
        active_super_admin_ids = []
        if target.role == User.Role.SUPER_ADMIN and target.is_active:
            active_super_admin_ids = list(
                User.objects.select_for_update()
                .filter(role=User.Role.SUPER_ADMIN, is_active=True)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
        if (
            target.role == User.Role.SUPER_ADMIN
            and (
                attrs.get("role", target.role) != User.Role.SUPER_ADMIN
                or attrs.get("is_active", target.is_active) is False
            )
            and len(active_super_admin_ids) <= 1
        ):
            raise serializers.ValidationError(
                "At least one active Super Admin must remain."
            )
        return attrs

    def update(self, instance, validated_data):
        instance.role = validated_data.get("role", instance.role)
        instance.is_active = validated_data.get("is_active", instance.is_active)
        instance.is_staff = instance.role == User.Role.SUPER_ADMIN
        instance.save(update_fields=("role", "is_active", "is_staff"))
        return instance


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    second_factor_code = serializers.CharField(required=False, write_only=True)

    def validate(self, attrs):
        request = self.context["request"]
        user = authenticate(
            request=request,
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None or not user.is_active:
            raise serializers.ValidationError("Invalid username or password.")

        if settings.TWO_FACTOR_AUTHENTICATION_ENABLED:
            verifier_path = settings.TWO_FACTOR_VERIFIER
            if not verifier_path:
                raise ImproperlyConfigured(
                    "Two-factor authentication is enabled but TWO_FACTOR_VERIFIER is not configured."
                )
            if "second_factor_code" not in attrs:
                raise serializers.ValidationError(
                    {"second_factor_code": "A second-factor code is required."}
                )
            verifier = import_string(verifier_path)
            if not verifier(user, attrs["second_factor_code"]):
                raise serializers.ValidationError("Invalid username or password.")

        attrs["user"] = user
        return attrs
