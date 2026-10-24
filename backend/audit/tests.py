from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog
from audit.services import record_audit
from catalog.models import BOMLine, Component, Product, ProductRevision
from inventory.models import StockMovement
from workorders.models import WorkOrder


class AuditLogTests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="auditor",
            email="auditor@example.test",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="operator",
            email="operator@example.test",
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


class NotificationAPITests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="notification-owner",
            email="notification-owner@example.test",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="notification-admin",
            email="notification-admin@example.test",
            role=User.Role.ADMIN,
        )
        self.other_admin = User.objects.create_user(
            username="notification-other",
            email="notification-other@example.test",
            role=User.Role.ADMIN,
        )
        self.component = Component.objects.create(
            sku="NOTIFY-PART",
            name="Notification component",
            unit="piece",
        )
        product = Product.objects.create(
            product_number="NOTIFY-PRODUCT",
            name="Notification product",
            sku="NOTIFY-SKU",
        )
        self.revision = ProductRevision.create_next(product=product)
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component,
            quantity_per_unit=1,
        )
        self.own_order = self.make_order(product, self.admin)
        self.other_order = self.make_order(product, self.other_admin)

    def make_order(self, product, assigned_admin):
        return WorkOrder.objects.create(
            product=product,
            product_revision=self.revision,
            quantity=1,
            due_date="2026-10-25",
            assigned_admin=assigned_admin,
            created_by=self.super_admin,
            status=WorkOrder.Status.RESERVED,
        )

    def record_stock_event(self, actor, *, note=""):
        movement = StockMovement.objects.create(
            component=self.component,
            movement_type=StockMovement.Type.RECEIVE,
            quantity=1,
            delta_on_hand=1,
            user=actor,
            note=note,
        )
        return record_audit(
            actor=actor,
            action="inventory.stock.receive",
            instance=movement,
            before={"private": "before snapshot"},
            after={"private": "after snapshot", "ip_address": "192.0.2.1"},
        )

    def test_notifications_require_authentication(self):
        response = self.client.get("/api/notifications/?after=0")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_sees_only_own_work_orders_and_shared_stock_events(self):
        own_event = record_audit(
            actor=self.other_admin,
            action="workorder.started",
            instance=self.own_order,
        )
        other_event = record_audit(
            actor=self.other_admin,
            action="workorder.started",
            instance=self.other_order,
        )
        own_actor_event = self.record_stock_event(self.admin)
        shared_stock_event = self.record_stock_event(self.other_admin)
        super_admin_event = record_audit(
            actor=self.super_admin,
            action="user.updated",
            instance=self.other_admin,
        )

        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/notifications/?after=0")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item["id"] for item in response.data["results"]}
        self.assertIn(own_event.pk, returned_ids)
        self.assertIn(shared_stock_event.pk, returned_ids)
        self.assertNotIn(other_event.pk, returned_ids)
        self.assertNotIn(own_actor_event.pk, returned_ids)
        self.assertNotIn(super_admin_event.pk, returned_ids)

    def test_notifications_are_sanitized_and_exclude_private_admin_events(self):
        event = self.record_stock_event(self.other_admin, note="private note")
        record_audit(
            actor=self.other_admin,
            action="user.updated",
            instance=self.admin,
            before={"password": "do-not-return"},
            after={"email": self.admin.email, "ip": "192.0.2.2"},
        )
        self.client.force_authenticate(self.super_admin)

        response = self.client.get("/api/notifications/?after=0")

        row = next(item for item in response.data["results"] if item["id"] == event.pk)
        self.assertEqual(set(row), {"id", "time", "event_type", "message", "object"})
        self.assertEqual(row["object"], {"type": "component", "id": self.component.pk})
        encoded = str(response.data)
        for forbidden in ("before", "after", "ip", "private note", "do-not-return", self.admin.email):
            self.assertNotIn(forbidden, encoded)
        self.assertIn("user.updated", [item["event_type"] for item in response.data["results"]])

    def test_initial_cursor_and_after_cursor_behaviour(self):
        first = self.record_stock_event(self.other_admin)
        second = self.record_stock_event(self.other_admin)
        self.client.force_authenticate(self.admin)

        initial = self.client.get("/api/notifications/")
        following = self.client.get(f"/api/notifications/?after={first.pk}")

        self.assertEqual(initial.data["results"], [])
        self.assertEqual(initial.data["cursor"], second.pk)
        self.assertEqual([item["id"] for item in following.data["results"]], [second.pk])
        self.assertEqual(following.data["cursor"], second.pk)

    def test_notification_results_are_capped_and_cursor_continues(self):
        events = [self.record_stock_event(self.other_admin) for _ in range(55)]
        self.client.force_authenticate(self.admin)

        first_page = self.client.get("/api/notifications/?after=0")
        second_page = self.client.get(
            f"/api/notifications/?after={first_page.data['cursor']}"
        )

        self.assertEqual(len(first_page.data["results"]), 50)
        self.assertEqual(first_page.data["cursor"], events[49].pk)
        self.assertEqual(len(second_page.data["results"]), 5)
        self.assertEqual(
            second_page.data["results"][-1]["id"],
            events[-1].pk,
        )
