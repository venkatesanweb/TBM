import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError

from accounts.models import User
from audit.models import AuditLog
from catalog.models import BOMLine, Component, Product, ProductRevision
from inventory.models import StockBalance, StockMovement
from inventory.services import receive
from workorders.models import (
    FinishedUnit,
    FinishedUnitHistory,
    PackConfirmation,
    WorkOrder,
    WorkOrderReservation,
)
from workorders.services import (
    audit_label_reprint,
    cancel_work_order,
    confirm_packing,
    create_work_order,
    reassign_work_order,
    start_work_order,
    update_finished_unit_status,
)


class WorkOrderServiceTests(TestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="supervisor",
            email="supervisor@example.test",
            password="test-password-12345",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="builder",
            email="builder@example.test",
            password="test-password-12345",
            role=User.Role.ADMIN,
        )
        self.other_admin = User.objects.create_user(
            username="other-builder",
            email="other@example.test",
            password="test-password-12345",
            role=User.Role.ADMIN,
        )
        self.component = self.make_component("PART-1")

    def make_component(self, sku):
        return Component.objects.create(
            sku=sku,
            name=f"Component {sku}",
            unit="pcs",
        )

    def make_product(self, code, components=None):
        product = Product.objects.create(
            product_number=code,
            name=f"Product {code}",
            sku=f"SKU-{code}",
        )
        revision = ProductRevision.create_next(product=product)
        for component, amount in components or [(self.component, Decimal("1"))]:
            BOMLine.objects.create(
                product_revision=revision,
                component=component,
                quantity_per_unit=amount,
            )
        return product

    def make_order(self, product, *, quantity=1, priority=WorkOrder.Priority.MEDIUM):
        return create_work_order(
            product_id=product.pk,
            quantity=quantity,
            priority=priority,
            due_date=date.today() + timedelta(days=2),
            assigned_admin=self.admin,
            actor=self.super_admin,
        )

    def test_reservation_is_all_or_nothing_and_reports_component_shortage(self):
        second = self.make_component("PART-2")
        receive(
            component_id=self.component.pk,
            quantity=5,
            actor=self.super_admin,
        )
        product = self.make_product(
            "PROD-1",
            [(self.component, Decimal("1")), (second, Decimal("2"))],
        )

        order = self.make_order(product, quantity=2)

        self.assertEqual(order.status, WorkOrder.Status.BLOCKED)
        self.assertIn("short by 4 of component PART-2", order.blocked_reason)
        self.assertFalse(order.reservations.exists())
        self.assertFalse(
            StockMovement.objects.filter(movement_type=StockMovement.Type.RESERVE).exists()
        )
        self.assertEqual(StockBalance.objects.get(component=self.component).reserved, 0)

    def test_priority_queue_retries_blocked_orders_after_stock_receipt(self):
        product = self.make_product("PROD-QUEUE")
        low = self.make_order(product, priority=WorkOrder.Priority.LOW)
        high = self.make_order(product, priority=WorkOrder.Priority.HIGH)
        self.assertEqual(low.status, WorkOrder.Status.BLOCKED)
        self.assertEqual(high.status, WorkOrder.Status.BLOCKED)

        with self.captureOnCommitCallbacks(execute=True):
            receive(
                component_id=self.component.pk,
                quantity=1,
                actor=self.super_admin,
            )

        low.refresh_from_db()
        high.refresh_from_db()
        self.assertEqual(high.status, WorkOrder.Status.RESERVED)
        self.assertEqual(low.status, WorkOrder.Status.BLOCKED)
        self.assertEqual(StockBalance.objects.get(component=self.component).reserved, 1)

    def test_new_higher_priority_order_preempts_unstarted_lower_priority_reservation(self):
        receive(
            component_id=self.component.pk,
            quantity=1,
            actor=self.super_admin,
        )
        product = self.make_product("PROD-PREEMPT")
        low = self.make_order(product, priority=WorkOrder.Priority.LOW)
        self.assertEqual(low.status, WorkOrder.Status.RESERVED)

        high = self.make_order(product, priority=WorkOrder.Priority.HIGH)

        low.refresh_from_db()
        self.assertEqual(high.status, WorkOrder.Status.RESERVED)
        self.assertEqual(low.status, WorkOrder.Status.BLOCKED)
        self.assertEqual(StockBalance.objects.get(component=self.component).reserved, 1)
        with self.captureOnCommitCallbacks(execute=True):
            cancel_work_order(work_order_id=high.pk, actor=self.super_admin)
        low.refresh_from_db()
        self.assertEqual(low.status, WorkOrder.Status.RESERVED)

    def test_reassignment_and_label_reprint_are_audited(self):
        receive(
            component_id=self.component.pk,
            quantity=1,
            actor=self.super_admin,
        )
        product = self.make_product("PROD-AUDIT")
        order = self.make_order(product)
        reassign_work_order(
            work_order_id=order.pk,
            assigned_admin=self.other_admin,
            actor=self.super_admin,
        )
        confirmation = PackConfirmation.objects.create(
            work_order=order,
            idempotency_key="audit-label-test",
            quantity=1,
            confirmed_by=self.other_admin,
        )
        unit = FinishedUnit.objects.create(
            serial_number="PROD-AUDIT-2610-000001",
            barcode_value="PROD-AUDIT-2610-000001",
            product=product,
            product_revision=order.product_revision,
            work_order=order,
            pack_confirmation=confirmation,
        )
        audit_label_reprint(unit_id=unit.pk, actor=self.super_admin)
        self.assertTrue(
            AuditLog.objects.filter(
                action="workorder.reassigned",
                object_id=str(order.pk),
            ).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(
                action="finished_unit.label.reprinted",
                object_id=str(unit.pk),
            ).exists()
        )

    def test_only_assigned_admin_can_confirm_and_retries_are_idempotent(self):
        receive(
            component_id=self.component.pk,
            quantity=2,
            actor=self.super_admin,
        )
        product = self.make_product("PROD-PACK")
        order = self.make_order(product, quantity=2)
        with self.assertRaises(ValidationError):
            confirm_packing(
                work_order_id=order.pk,
                quantity=1,
                idempotency_key="wrong-owner",
                actor=self.other_admin,
            )

        start_work_order(work_order_id=order.pk, actor=self.admin)
        first = confirm_packing(
            work_order_id=order.pk,
            quantity=1,
            idempotency_key="pack-one",
            actor=self.admin,
        )
        retry = confirm_packing(
            work_order_id=order.pk,
            quantity=1,
            idempotency_key="pack-one",
            actor=self.admin,
        )
        self.assertEqual(first.pk, retry.pk)
        self.assertEqual(first.finished_units.count(), 1)
        self.assertRegex(
            first.finished_units.get().serial_number,
            re.compile(r"^PROD-PACK-\d{4}-\d{6}$"),
        )
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrder.Status.PACKED)
        self.assertEqual(order.packed_quantity, 1)
        self.assertEqual(StockBalance.objects.get(component=self.component).on_hand, 1)

        confirm_packing(
            work_order_id=order.pk,
            quantity=1,
            idempotency_key="pack-two",
            actor=self.admin,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, WorkOrder.Status.COMPLETED)
        self.assertEqual(FinishedUnit.objects.filter(work_order=order).count(), 2)
        self.assertEqual(
            FinishedUnit.objects.filter(work_order=order)
            .values("serial_number")
            .distinct()
            .count(),
            2,
        )
        self.assertEqual(StockBalance.objects.get(component=self.component).on_hand, 0)

    def test_order_reduction_and_cancellation_release_reserved_stock(self):
        receive(
            component_id=self.component.pk,
            quantity=5,
            actor=self.super_admin,
        )
        product = self.make_product("PROD-RELEASE")
        order = self.make_order(product, quantity=4)
        reservation = order.reservations.get()
        self.assertEqual(reservation.remaining, 4)

        from workorders.services import reduce_work_order, cancel_work_order

        reduce_work_order(work_order_id=order.pk, quantity=2, actor=self.super_admin)
        reservation.refresh_from_db()
        self.assertEqual(reservation.remaining, 2)
        self.assertEqual(StockBalance.objects.get(component=self.component).reserved, 2)
        cancel_work_order(work_order_id=order.pk, actor=self.super_admin)
        self.assertEqual(StockBalance.objects.get(component=self.component).reserved, 0)


class WorkOrderAPITests(TestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="api-supervisor",
            email="api-supervisor@example.test",
            password="test-password-12345",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="api-builder",
            email="api-builder@example.test",
            password="test-password-12345",
            role=User.Role.ADMIN,
        )
        self.other_admin = User.objects.create_user(
            username="api-other",
            email="api-other@example.test",
            password="test-password-12345",
            role=User.Role.ADMIN,
        )
        component = Component.objects.create(sku="API-PART", name="API component", unit="pcs")
        product = Product.objects.create(
            product_number="API-PRODUCT",
            name="API product",
            sku="API-SKU",
        )
        revision = ProductRevision.create_next(product=product)
        BOMLine.objects.create(
            product_revision=revision,
            component=component,
            quantity_per_unit=Decimal("1"),
        )
        receive(component_id=component.pk, quantity=1, actor=self.super_admin)
        self.order = create_work_order(
            product_id=product.pk,
            quantity=1,
            priority=WorkOrder.Priority.HIGH,
            due_date=date.today() + timedelta(days=1),
            assigned_admin=self.admin,
            actor=self.super_admin,
        )
        self.client = APIClient()

    def test_admin_sees_only_assigned_orders_and_only_assignee_can_pack(self):
        self.client.force_authenticate(self.other_admin)
        self.assertEqual(self.client.get("/api/work-orders/").data, [])
        denied = self.client.post(
            f"/api/work-orders/{self.order.pk}/start/",
            {},
            format="json",
        )
        self.assertEqual(denied.status_code, 400)

        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/work-orders/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], self.order.pk)
        started = self.client.post(
            f"/api/work-orders/{self.order.pk}/start/",
            {},
            format="json",
        )
        self.assertEqual(started.status_code, 200)
        packed = self.client.post(
            f"/api/work-orders/{self.order.pk}/pack/",
            {"quantity": 1, "idempotency_key": "api-pack"},
            format="json",
        )
        self.assertEqual(packed.status_code, 200)
        self.assertEqual(len(packed.data["finished_units"]), 1)


class FinishedGoodsTests(TestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="goods-super",
            email="goods-super@example.test",
            password="test-password-12345",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="goods-admin",
            email="goods-admin@example.test",
            password="test-password-12345",
            role=User.Role.ADMIN,
        )
        self.other_admin = User.objects.create_user(
            username="goods-other",
            email="goods-other@example.test",
            password="test-password-12345",
            role=User.Role.ADMIN,
        )
        self.component = Component.objects.create(
            sku="GOODS-PART",
            name="Finished goods test part",
            unit="pcs",
        )
        self.product = Product.objects.create(
            product_number="GOODS-PROD",
            name="Finished goods test product",
            sku="GOODS-SKU",
        )
        self.revision = ProductRevision.create_next(product=self.product)
        self.bom_line = BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component,
            quantity_per_unit=Decimal("2"),
        )
        receive(
            component_id=self.component.pk,
            quantity=4,
            actor=self.super_admin,
        )
        self.order = create_work_order(
            product_id=self.product.pk,
            quantity=2,
            priority=WorkOrder.Priority.HIGH,
            due_date=date.today() + timedelta(days=1),
            assigned_admin=self.admin,
            actor=self.super_admin,
        )
        start_work_order(work_order_id=self.order.pk, actor=self.admin)
        self.confirmation = confirm_packing(
            work_order_id=self.order.pk,
            quantity=2,
            idempotency_key="finished-goods-test",
            actor=self.admin,
        )
        self.units = list(FinishedUnit.objects.filter(work_order=self.order).order_by("pk"))
        self.client = APIClient()

    def test_finished_unit_filters_summary_detail_and_serial_lookup(self):
        self.client.force_authenticate(self.other_admin)
        response = self.client.get(
            "/api/finished-units/",
            {
                "product": self.product.pk,
                "revision": self.revision.pk,
                "status": FinishedUnit.Status.IN_STOCK,
                "work_order": self.order.pk,
                "date_from": date.today().isoformat(),
                "date_to": date.today().isoformat(),
                "search": self.units[0].serial_number[-6:],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["packed_by"], self.admin.username)
        self.assertEqual(len(response.data[0]["history"]), 1)
        self.assertEqual(response.data[0]["history"][0]["event_type"], "PACKED")

        lookup = self.client.get(
            "/api/finished-units/lookup/",
            {"serial_number": self.units[1].serial_number},
        )
        self.assertEqual(lookup.status_code, 200)
        detail = self.client.get(f"/api/finished-units/{self.units[0].pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["work_order"], self.order.pk)
        self.assertEqual(
            self.client.get("/api/finished-units/summary/").data[0]["in_stock"],
            2,
        )

    def test_status_history_return_events_and_reconciliation(self):
        unit = self.units[0]
        update_finished_unit_status(
            unit_id=unit.pk,
            status=FinishedUnit.Status.RESERVED_FOR_SALE,
            actor=self.super_admin,
        )
        update_finished_unit_status(
            unit_id=unit.pk,
            status=FinishedUnit.Status.SOLD,
            event_type=FinishedUnitHistory.EventType.SALE,
            sale_reference="SALE-2026-001",
            actor=self.super_admin,
        )
        update_finished_unit_status(
            unit_id=unit.pk,
            status=FinishedUnit.Status.RETURNED,
            event_type=FinishedUnitHistory.EventType.RETURN,
            note="Customer return",
            actor=self.super_admin,
        )
        self.client.force_authenticate(self.super_admin)
        summary = self.client.get("/api/finished-units/summary/")
        self.assertEqual(summary.status_code, 200)
        product_summary = next(
            row for row in summary.data if row["product_id"] == self.product.pk
        )
        self.assertEqual(product_summary["in_stock"], 1)
        self.assertEqual(product_summary["sold"], 0)
        self.assertEqual(product_summary["returned"], 1)

        unit_detail = self.client.get(f"/api/finished-units/{unit.pk}/")
        self.assertEqual(
            [row["event_type"] for row in unit_detail.data["history"]],
            ["PACKED", "STATUS_CHANGED", "SALE", "RETURN"],
        )
        self.assertEqual(unit_detail.data["history"][2]["sale_reference"], "SALE-2026-001")

        report = self.client.get("/api/finished-units/reconciliation/")
        self.assertEqual(report.status_code, 200)
        row = next(item for item in report.data if item["work_order_id"] == self.order.pk)
        self.assertTrue(row["reconciled"])
        self.assertEqual(row["discrepancies"], [])

        confirmation_movement = self.confirmation.movements.first()
        confirmation_movement.delete()
        report = self.client.get("/api/finished-units/reconciliation/")
        row = next(item for item in report.data if item["work_order_id"] == self.order.pk)
        self.assertFalse(row["reconciled"])
        self.assertEqual(row["discrepancies"][0]["expected"], "4.000")
        self.assertEqual(row["discrepancies"][0]["actual"], "0")
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.get("/api/finished-units/reconciliation/").status_code,
            403,
        )


@skipUnless(
    connection.features.has_select_for_update,
    "Concurrent row-lock tests require PostgreSQL or another SELECT FOR UPDATE database.",
)
class WorkOrderConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_packing_creates_unique_serials_without_overconsuming(self):
        supervisor = User.objects.create_user(
            username="concurrent-super",
            email="concurrent-super@example.test",
            password="test-password-12345",
            role=User.Role.SUPER_ADMIN,
        )
        admin = User.objects.create_user(
            username="concurrent-admin",
            email="concurrent-admin@example.test",
            password="test-password-12345",
            role=User.Role.ADMIN,
        )
        component = Component.objects.create(sku="LOCKED-PART", name="Locked part", unit="pcs")
        product = Product.objects.create(
            product_number="LOCKED-PRODUCT",
            name="Locked product",
            sku="LOCKED-SKU",
        )
        revision = ProductRevision.create_next(product=product)
        BOMLine.objects.create(
            product_revision=revision,
            component=component,
            quantity_per_unit=Decimal("1"),
        )
        receive(component_id=component.pk, quantity=2, actor=supervisor)
        order = create_work_order(
            product_id=product.pk,
            quantity=2,
            priority=WorkOrder.Priority.HIGH,
            due_date=date.today() + timedelta(days=1),
            assigned_admin=admin,
            actor=supervisor,
        )
        barrier = Barrier(2)

        def pack(key):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return confirm_packing(
                    work_order_id=order.pk,
                    quantity=1,
                    idempotency_key=key,
                    actor=User.objects.get(pk=admin.pk),
                ).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            confirmation_ids = list(pool.map(pack, ("parallel-a", "parallel-b")))

        self.assertEqual(len(set(confirmation_ids)), 2)
        units = list(FinishedUnit.objects.filter(work_order=order))
        self.assertEqual(len(units), 2)
        self.assertEqual(len({unit.serial_number for unit in units}), 2)
        self.assertEqual(StockBalance.objects.get(component=component).on_hand, 0)
