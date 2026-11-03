from unittest.mock import patch

from django.core.cache import cache
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog


class AccountPermissionTests(APITestCase):
    def setUp(self):
        cache.clear()
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

    def test_optional_totp_applies_to_super_admin_but_not_admin(self):
        self.super_admin.set_password("secure-password-123")
        self.super_admin.save()
        self.admin.set_password("secure-password-123")
        self.admin.save()

        with patch("accounts.totp.pyotp.TOTP") as totp:
            totp.return_value.verify.return_value = True
            with self.settings(
                TWO_FACTOR_AUTHENTICATION_ENABLED=True,
                SUPER_ADMIN_TOTP_SECRETS={"owner": "JBSWY3DPEHPK3PXP"},
            ):
                owner_response = self.client.post(
                    "/api/auth/login/",
                    {
                        "username": "owner",
                        "password": "secure-password-123",
                        "second_factor_code": "123456",
                    },
                )
                worker_response = self.client.post(
                    "/api/auth/login/",
                    {"username": "worker", "password": "secure-password-123"},
                )

        self.assertEqual(owner_response.status_code, status.HTTP_200_OK)
        self.assertEqual(worker_response.status_code, status.HTTP_200_OK)
        totp.assert_called_once_with("JBSWY3DPEHPK3PXP")

    def test_enabled_totp_requires_a_code_for_super_admin(self):
        self.super_admin.set_password("secure-password-123")
        self.super_admin.save()
        with self.settings(
            TWO_FACTOR_AUTHENTICATION_ENABLED=True,
            SUPER_ADMIN_TOTP_SECRETS={"owner": "JBSWY3DPEHPK3PXP"},
        ):
            response = self.client.post(
                "/api/auth/login/",
                {"username": "owner", "password": "secure-password-123"},
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("second_factor_code", response.data)

    def test_admin_cannot_read_audit_log(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/audit-logs/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class DashboardAccessAssignmentTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="dashboard-owner",
            email="dashboard-owner@example.test",
            password="dashboard-test-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="dashboard-admin",
            email="dashboard-admin@example.test",
            password="dashboard-test-password-123",
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(self.owner)

    def test_new_admin_has_no_dashboard_sections_or_export_access(self):
        self.assertEqual(self.admin.dashboard_sections, [])
        self.assertFalse(self.admin.can_export_dashboard_reports)
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["dashboard_sections"], [])
        self.assertFalse(response.data["can_export_dashboard_reports"])
        self.client.force_authenticate(self.owner)

    def test_super_admin_can_grant_and_revoke_sections_with_audit(self):
        payload = {
            "dashboard_sections": ["production", "inventory"],
            "can_export_dashboard_reports": True,
        }
        response = self.client.patch(
            f"/api/users/{self.admin.pk}/dashboard-access/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.dashboard_sections, payload["dashboard_sections"])
        self.assertTrue(self.admin.can_export_dashboard_reports)
        grant_log = AuditLog.objects.get(action="dashboard.access.updated")
        self.assertEqual(grant_log.actor, self.owner)
        self.assertEqual(grant_log.before["dashboard_sections"], [])
        self.assertEqual(grant_log.after["granted_sections"], ["inventory", "production"])

        payload["dashboard_sections"] = ["inventory"]
        payload["can_export_dashboard_reports"] = False
        response = self.client.patch(
            f"/api/users/{self.admin.pk}/dashboard-access/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        revoke_log = AuditLog.objects.filter(action="dashboard.access.updated").order_by("-pk").first()
        self.assertEqual(revoke_log.after["revoked_sections"], ["production"])
        self.assertTrue(revoke_log.after["export_permission_changed"])

    def test_admin_cannot_assign_dashboard_access(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/users/{self.admin.pk}/dashboard-access/",
            {"dashboard_sections": ["sales_overview"], "can_export_dashboard_reports": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_invalid_or_duplicate_sections_are_rejected(self):
        for sections in (["unknown"], ["payments", "payments"]):
            with self.subTest(sections=sections):
                response = self.client.patch(
                    f"/api/users/{self.admin.pk}/dashboard-access/",
                    {"dashboard_sections": sections, "can_export_dashboard_reports": False},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
