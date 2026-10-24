from unittest.mock import patch

from django.core.cache import cache
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog


class AccountPermissionTests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="owner",
            email="owner@example.test",
            password="secure-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="worker",
            email="worker@example.test",
            password="secure-password-123",
            role=User.Role.ADMIN,
        )

    def test_user_management_requires_super_admin(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/users/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_super_admin_can_list_users(self):
        self.client.force_authenticate(self.super_admin)
        response = self.client.get("/api/users/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_user_creation_hashes_password_and_audits_action(self):
        self.client.force_authenticate(self.super_admin)
        response = self.client.post(
            "/api/users/",
            {
                "username": "new-admin",
                "email": "new-admin@example.test",
                "password": "another-secure-password",
                "role": User.Role.ADMIN,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(username="new-admin")
        self.assertTrue(created.check_password("another-secure-password"))
        self.assertEqual(created.role, User.Role.ADMIN)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.super_admin,
                action="user.created",
                object_id=str(created.pk),
            ).exists()
        )

    def test_admin_cannot_create_users(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/users/",
            {
                "username": "blocked",
                "email": "blocked@example.test",
                "password": "another-secure-password",
                "role": User.Role.ADMIN,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_super_admin_can_deactivate_user_and_audit_change(self):
        self.client.force_authenticate(self.super_admin)
        token = Token.objects.create(user=self.admin)
        response = self.client.patch(
            f"/api/users/{self.admin.pk}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())
        log = AuditLog.objects.get(action="user.updated", object_id=str(self.admin.pk))
        self.assertEqual(log.before, {"role": User.Role.ADMIN, "is_active": True})
        self.assertEqual(log.after, {"role": User.Role.ADMIN, "is_active": False})

    def test_last_active_super_admin_cannot_be_demoted(self):
        self.client.force_authenticate(self.super_admin)
        response = self.client.patch(
            f"/api/users/{self.super_admin.pk}/",
            {"role": User.Role.ADMIN},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.super_admin.refresh_from_db()
        self.assertEqual(self.super_admin.role, User.Role.SUPER_ADMIN)

    def test_login_creates_token_and_audit_event(self):
        response = self.client.post(
            "/api/auth/login/",
            {"username": self.admin.username, "password": "secure-password-123"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token", response.data)
        self.assertEqual(response.data["user"]["role"], User.Role.ADMIN)
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.admin,
                action="auth.login",
                object_id=str(self.admin.pk),
            ).exists()
        )

    def test_login_is_rate_limited(self):
        cache.clear()
        self.addCleanup(cache.clear)
        credentials = {
            "username": self.admin.username,
            "password": "secure-password-123",
        }
        with patch("accounts.views.LoginThrottle.THROTTLE_RATES", {"login": "1/minute"}):
            first = self.client.post("/api/auth/login/", credentials)
            second = self.client.post("/api/auth/login/", credentials)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_admin_cannot_read_audit_log(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/audit-logs/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
