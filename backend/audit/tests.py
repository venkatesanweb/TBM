from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog
from audit.services import record_audit


class AuditLogTests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="auditor",
            email="auditor@example.test",
            password="secure-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="operator",
            email="operator@example.test",
            password="secure-password-123",
            role=User.Role.ADMIN,
        )

    def test_helper_records_actor_action_object_and_snapshots(self):
        log = record_audit(
            actor=self.super_admin,
            action="inventory.adjusted",
            instance=self.admin,
            before={"quantity": 4},
            after={"quantity": 7},
        )
        self.assertEqual(log.actor, self.super_admin)
        self.assertEqual(log.content_object, self.admin)
        self.assertEqual(log.before, {"quantity": 4})
        self.assertEqual(log.after, {"quantity": 7})
        self.assertIsNotNone(log.timestamp)

    def test_super_admin_can_filter_audit_logs(self):
        record_audit(
            actor=self.super_admin,
            action="user.updated",
            instance=self.admin,
            before={"is_active": True},
            after={"is_active": False},
        )
        self.client.force_authenticate(self.super_admin)
        response = self.client.get("/api/audit-logs/?action=user.updated")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["action"], "user.updated")

    def test_audit_log_filters_reject_invalid_datetime(self):
        self.client.force_authenticate(self.super_admin)
        response = self.client.get("/api/audit-logs/?created_after=not-a-date")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
