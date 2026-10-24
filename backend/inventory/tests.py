from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog
from catalog.models import Component
from inventory.models import StockAdjustment, StockBalance, StockMovement
from inventory.services import (
    approve_adjustment,
    consume,
    receive,
    reject_adjustment,
    release,
    request_adjustment,
    reserve,
    return_stock,
)


class InventoryAPITests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="inventory-owner",
            email="inventory-owner@example.test",
            password="secure-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="inventory-reader",
            email="inventory-reader@example.test",
            password="secure-password-123",
            role=User.Role.ADMIN,
        )
        self.component = Component.objects.create(
            sku="INV-001",
            name="Inventory test component",
            unit="piece",
            reorder_level=Decimal("3"),
        )
        self.client.force_authenticate(self.super_admin)

    def test_receive_stock_updates_ledger_balance_and_audit(self):
        response = self.client.post(
            "/api/inventory/receive/",
            {
                "component_id": self.component.pk,
                "quantity": "10",
                "note": "Initial receipt",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["movement_type"], StockMovement.Type.RECEIVE)
        balance = StockBalance.objects.get(component=self.component)
        self.assertEqual(balance.on_hand, Decimal("10"))
        self.assertEqual(balance.reserved, Decimal("0"))
        self.assertEqual(balance.available, Decimal("10"))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                action="inventory.stock.receive",
                object_id=str(response.data["id"]),
            ).exists()
        )

    def test_admin_can_view_available_stock_but_cannot_change_it(self):
        receive(
            component_id=self.component.pk,
            quantity=Decimal("10"),
            actor=self.super_admin,
        )
        self.client.force_authenticate(self.admin)
        listed = self.client.get("/api/inventory/")
        detail = self.client.get(f"/api/inventory/{self.component.pk}/")
        receive_denied = self.client.post(
            "/api/inventory/receive/",
            {"component_id": self.component.pk, "quantity": "1"},
            format="json",
        )
        adjustment_denied = self.client.post(
            "/api/inventory/adjustments/",
            {
                "component_id": self.component.pk,
                "delta": "-1",
                "reason": "Damage",
            },
            format="json",
        )
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(listed.data[0]["available"], Decimal("10"))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["balance"]["available"], Decimal("10.000"))
        self.assertEqual(receive_denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(adjustment_denied.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.get("/api/inventory/adjustments/").status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_pending_adjustment_does_not_change_counts_until_approved(self):
        receive(
            component_id=self.component.pk,
            quantity=Decimal("8"),
            actor=self.super_admin,
        )
        response = self.client.post(
            "/api/inventory/adjustments/",
            {
                "component_id": self.component.pk,
                "delta": "-2",
                "reason": "Damaged during handling",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], StockAdjustment.Status.PENDING)
        self.assertEqual(StockBalance.objects.get(component=self.component).on_hand, Decimal("8"))
        approved = self.client.post(
            f"/api/inventory/adjustments/{response.data['id']}/approve/",
            format="json",
        )
        self.assertEqual(approved.status_code, status.HTTP_200_OK)
        self.assertEqual(approved.data["status"], StockAdjustment.Status.APPROVED)
        balance = StockBalance.objects.get(component=self.component)
        self.assertEqual(balance.on_hand, Decimal("6"))
        self.assertEqual(balance.available, Decimal("6"))

    def test_adjustment_requires_reason_and_can_be_rejected(self):
        missing_reason = self.client.post(
            "/api/inventory/adjustments/",
            {"component_id": self.component.pk, "delta": "3", "reason": "  "},
            format="json",
        )
        self.assertEqual(missing_reason.status_code, status.HTTP_400_BAD_REQUEST)
        request = request_adjustment(
            component_id=self.component.pk,
            delta=Decimal("3"),
            reason="Recount found three extra",
            actor=self.super_admin,
        )
        rejected = self.client.post(
            f"/api/inventory/adjustments/{request.pk}/reject/",
            format="json",
        )
        self.assertEqual(rejected.status_code, status.HTTP_200_OK)
        self.assertEqual(rejected.data["status"], StockAdjustment.Status.REJECTED)
        self.assertFalse(StockBalance.objects.filter(component=self.component).exists())

    def test_negative_adjustment_cannot_approve_below_zero(self):
        request = request_adjustment(
            component_id=self.component.pk,
            delta=Decimal("-1"),
            reason="Damage",
            actor=self.super_admin,
        )
        response = self.client.post(
            f"/api/inventory/adjustments/{request.pk}/approve/",
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        request.refresh_from_db()
        self.assertEqual(request.status, StockAdjustment.Status.PENDING)
        self.assertFalse(StockMovement.objects.exists())

    def test_low_stock_alerts_are_super_admin_only(self):
        receive(
            component_id=self.component.pk,
            quantity=Decimal("2"),
            actor=self.super_admin,
        )
        response = self.client.get("/api/inventory/low-stock/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["component"]["sku"], self.component.sku)
        self.client.force_authenticate(self.admin)
        denied = self.client.get("/api/inventory/low-stock/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)


class InventoryServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="inventory-service",
            email="inventory-service@example.test",
            password="secure-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        self.component = Component.objects.create(
            sku="INV-SVC",
            name="Service component",
            unit="piece",
        )

    def test_reserve_release_consume_return_movements_keep_balance_consistent(self):
        receive(
            component_id=self.component.pk,
            quantity=Decimal("10"),
            actor=self.user,
        )
        reserve(
            component_id=self.component.pk,
            quantity=Decimal("6"),
            actor=self.user,
            work_order_reference="WO-100",
        )
        balance = StockBalance.objects.get(component=self.component)
        self.assertEqual((balance.on_hand, balance.reserved, balance.available), (
            Decimal("10"), Decimal("6"), Decimal("4")
        ))
        with self.assertRaises(ValidationError):
            consume(component_id=self.component.pk, quantity=Decimal("5"), actor=self.user)
        consume(
            component_id=self.component.pk,
            quantity=Decimal("2"),
            actor=self.user,
            work_order_reference="WO-100",
            from_reserved=True,
        )
        release(
            component_id=self.component.pk,
            quantity=Decimal("4"),
            actor=self.user,
            work_order_reference="WO-100",
        )
        return_stock(
            component_id=self.component.pk,
            quantity=Decimal("1"),
            actor=self.user,
            work_order_reference="WO-100",
            note="Unused material returned",
        )
        balance.refresh_from_db()
        self.assertEqual((balance.on_hand, balance.reserved, balance.available), (
            Decimal("9"), Decimal("0"), Decimal("9")
        ))
        self.assertEqual(StockMovement.objects.count(), 5)

    def test_database_constraints_prevent_negative_and_over_reserved_balances(self):
        balance = StockBalance.objects.create(
            component=self.component,
            on_hand=Decimal("1"),
            reserved=Decimal("0"),
        )
        balance.reserved = Decimal("2")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                balance.save()


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentConsumeTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(
            username="inventory-concurrent",
            email="inventory-concurrent@example.test",
            password="secure-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        self.component = Component.objects.create(
            sku="INV-RACE",
            name="Concurrent component",
            unit="piece",
        )
        receive(
            component_id=self.component.pk,
            quantity=Decimal("5"),
            actor=self.user,
        )

    def test_two_simultaneous_consumes_cannot_make_stock_negative(self):
        barrier = Barrier(2)
        component_id = self.component.pk
        user_id = self.user.pk

        def concurrent_consume():
            close_old_connections()
            from accounts.models import User as ThreadUser

            barrier.wait()
            try:
                consume(
                    component_id=component_id,
                    quantity=Decimal("4"),
                    actor=ThreadUser.objects.get(pk=user_id),
                )
                return "consumed"
            except ValidationError:
                return "insufficient"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: concurrent_consume(), range(2)))

        self.assertCountEqual(outcomes, ["consumed", "insufficient"])
        balance = StockBalance.objects.get(component=self.component)
        self.assertEqual(balance.on_hand, Decimal("1"))
        self.assertGreaterEqual(balance.on_hand, 0)
        self.assertEqual(
            StockMovement.objects.filter(
                component=self.component,
                movement_type=StockMovement.Type.CONSUME,
            ).count(),
            1,
        )
