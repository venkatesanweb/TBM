from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from threading import Barrier
from unittest import skipUnless

from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import TestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from catalog.models import BOMLine, Component, Product, ProductRevision
from inventory.services import receive
from sales.models import Sale, SaleReturn, SaleStatusHistory, SaleUnit, SalesPlatform
from sales.services import create_sale
from workorders.models import FinishedUnit, FinishedUnitHistory, WorkOrder
from workorders.services import confirm_packing, create_work_order, start_work_order


class SalesFlowTests(TestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="sales-super",
            email="sales-super@example.test",
            password="safe-test-password-2026",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="sales-admin",
            email="sales-admin@example.test",
            password="safe-test-password-2026",
            role=User.Role.ADMIN,
        )
        self.component = Component.objects.create(
            sku="SALES-PART",
            name="Sales test component",
            unit="pcs",
        )
        self.product = Product.objects.create(
            product_number="SALES-PRODUCT",
            name="Sales test product",
            sku="SALES-SKU",
        )
        self.revision = ProductRevision.create_next(product=self.product)
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component,
            quantity_per_unit=Decimal("1"),
        )
        receive(component_id=self.component.pk, quantity=2, actor=self.super_admin)
        order = create_work_order(
            product_id=self.product.pk,
            quantity=2,
            priority=WorkOrder.Priority.HIGH,
            due_date=date.today() + timedelta(days=1),
            assigned_admin=self.admin,
            actor=self.super_admin,
        )
        start_work_order(work_order_id=order.pk, actor=self.admin)
        confirmation = confirm_packing(
            work_order_id=order.pk,
            quantity=2,
            idempotency_key="sales-flow-pack",
            actor=self.admin,
        )
        self.units = list(
            FinishedUnit.objects.filter(pack_confirmation=confirmation).order_by("pk")
        )
        self.tamirabot = SalesPlatform.objects.create(
            name="Tamirabot",
            label_template_type=SalesPlatform.LabelTemplateType.TAMIRABOT,
        )
        self.amazon = SalesPlatform.objects.create(
            name="Amazon",
            label_template_type=SalesPlatform.LabelTemplateType.SELLER_PROVIDED,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def sale_payload(self, **overrides):
        payload = {
            "platform": self.tamirabot.pk,
            "unit_ids": [self.units[0].pk],
            "order_reference": "ORDER-123",
            "price": "2500.00",
            "sale_date": date.today().isoformat(),
            "shipping_name": "A Tamirabot customer",
            "shipping_address": "12 Workshop Road",
            "shipping_city": "Chennai",
            "shipping_state": "Tamil Nadu",
            "shipping_postal_code": "600001",
            "shipping_phone": "9000000000",
            "notes": "Handle with care",
        }
        payload.update(overrides)
        return payload

    def test_tamirabot_sale_generates_packing_label_and_records_transitions(self):
        response = self.client.post("/api/sales/", self.sale_payload(), format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Sale.Status.READY_TO_SHIP)
        self.assertEqual(response.data["label"]["mode"], "generated")
        self.assertEqual(response.data["label"]["order_reference"], "ORDER-123")
        self.assertEqual(
            response.data["label"]["recipient"]["name"],
            "A Tamirabot customer",
        )
        self.assertEqual(
            response.data["label"]["units"][0]["barcode_value"],
            self.units[0].barcode_value,
        )
        self.units[0].refresh_from_db()
        self.assertEqual(self.units[0].status, FinishedUnit.Status.SOLD)
        history = list(self.units[0].history.order_by("pk"))
        self.assertEqual(
            [row.to_status for row in history[-2:]],
            [FinishedUnit.Status.RESERVED_FOR_SALE, FinishedUnit.Status.SOLD],
        )
        self.assertTrue(AuditLog.objects.filter(action="sale.created").exists())

    def test_seller_platform_stores_reference_without_generating_label(self):
        response = self.client.post(
            "/api/sales/",
            self.sale_payload(
                platform=self.amazon.pk,
                unit_ids=[],
                product_quantities=[{"product": self.product.pk, "quantity": 1}],
                shipping_name="",
                shipping_address="",
                seller_label_reference="https://seller.example/labels/123",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["label"], {
            "mode": "seller_provided",
            "reference": "https://seller.example/labels/123",
        })

    def test_unit_cannot_be_sold_twice(self):
        first = self.client.post("/api/sales/", self.sale_payload(), format="json")
        self.assertEqual(first.status_code, 201, first.data)

        second = self.client.post(
            "/api/sales/",
            self.sale_payload(
                platform=self.amazon.pk,
                seller_label_reference="seller-label-2",
                shipping_name="",
                shipping_address="",
            ),
            format="json",
        )

        self.assertEqual(second.status_code, 400)
        self.assertEqual(SaleUnit.objects.filter(finished_unit=self.units[0]).count(), 1)

    def test_database_unique_constraint_blocks_duplicate_sale_link(self):
        first = self.client.post("/api/sales/", self.sale_payload(), format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.units[0].status = FinishedUnit.Status.IN_STOCK
        self.units[0].save(update_fields=("status",))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.client.post("/api/sales/", self.sale_payload(), format="json")

    def test_sale_edit_is_audited(self):
        response = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = response.data["id"]

        updated = self.client.patch(
            f"/api/sales/{sale_id}/",
            {"notes": "Updated packing note"},
            format="json",
        )

        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["notes"], "Updated packing note")
        event = AuditLog.objects.get(action="sale.updated", object_id=str(sale_id))
        self.assertEqual(event.before["notes"], "Handle with care")
        self.assertEqual(event.after["notes"], "Updated packing note")

    def test_only_next_status_transition_is_allowed_and_history_is_recorded(self):
        created = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = created.data["id"]

        delivered = self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.DELIVERED, "note": "Courier delivered"},
            format="json",
        )
        self.assertEqual(delivered.status_code, 200, delivered.data)
        skipped = self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.PAYMENT_RECEIVED},
            format="json",
        )
        self.assertEqual(skipped.status_code, 400)
        pending = self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.PAYMENT_PENDING},
            format="json",
        )
        self.assertEqual(pending.status_code, 200, pending.data)
        paid = self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.PAYMENT_RECEIVED},
            format="json",
        )
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(
            list(
                SaleStatusHistory.objects.filter(sale_id=sale_id).values_list(
                    "to_status", flat=True
                )
            ),
            [
                Sale.Status.READY_TO_SHIP,
                Sale.Status.DELIVERED,
                Sale.Status.PAYMENT_PENDING,
                Sale.Status.PAYMENT_RECEIVED,
            ],
        )

    def test_return_requires_a_payment_or_delivered_stage_and_super_admin_decision(self):
        created = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = created.data["id"]
        early_return = self.client.post(
            f"/api/sales/{sale_id}/return/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "reason": "Damaged in transit",
            },
            format="json",
        )
        self.assertEqual(early_return.status_code, 400)

        self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.DELIVERED},
            format="json",
        )
        returned = self.client.post(
            f"/api/sales/{sale_id}/return/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "reason": "Customer returned damaged unit",
            },
            format="json",
        )
        self.assertEqual(returned.status_code, 200, returned.data)
        self.units[0].refresh_from_db()
        self.assertEqual(self.units[0].status, FinishedUnit.Status.UNDER_INSPECTION)
        return_record = SaleReturn.objects.get(sale_unit__finished_unit=self.units[0])
        self.assertEqual(return_record.decision, SaleReturn.Decision.PENDING)

        denied = self.client.post(
            f"/api/sales/{sale_id}/return-decision/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "decision": SaleReturn.Decision.RESTOCK,
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.super_admin)
        restocked = self.client.post(
            f"/api/sales/{sale_id}/return-decision/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "decision": SaleReturn.Decision.RESTOCK,
                "note": "Passed inspection",
            },
            format="json",
        )
        self.assertEqual(restocked.status_code, 200, restocked.data)
        self.units[0].refresh_from_db()
        return_record.refresh_from_db()
        self.assertEqual(self.units[0].status, FinishedUnit.Status.IN_STOCK)
        self.assertEqual(return_record.decision, SaleReturn.Decision.RESTOCK)
        self.assertEqual(
            AuditLog.objects.filter(action="sale.return.disposition").count(),
            1,
        )

    def test_return_is_allowed_after_payment_received(self):
        created = self.client.post(
            "/api/sales/",
            self.sale_payload(unit_ids=[self.units[1].pk]),
            format="json",
        )
        sale_id = created.data["id"]
        for target_status in (
            Sale.Status.DELIVERED,
            Sale.Status.PAYMENT_PENDING,
            Sale.Status.PAYMENT_RECEIVED,
        ):
            response = self.client.post(
                f"/api/sales/{sale_id}/status/",
                {"status": target_status},
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.data)

        returned = self.client.post(
            f"/api/sales/{sale_id}/return/",
            {
                "serial_numbers": [self.units[1].serial_number],
                "reason": "Payment received but customer returned the item",
            },
            format="json",
        )

        self.assertEqual(returned.status_code, 200, returned.data)
        self.units[1].refresh_from_db()
        self.assertEqual(self.units[1].status, FinishedUnit.Status.UNDER_INSPECTION)

    def test_return_rework_creates_assigned_work_order(self):
        created = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = created.data["id"]
        self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.DELIVERED},
            format="json",
        )
        self.client.post(
            f"/api/sales/{sale_id}/return/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "reason": "Requires repair",
            },
            format="json",
        )
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(
            f"/api/sales/{sale_id}/return-decision/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "decision": SaleReturn.Decision.REWORK,
                "assigned_admin": self.admin.pk,
                "note": "Replace damaged assembly",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        return_record = SaleReturn.objects.get(sale_unit__finished_unit=self.units[0])
        self.assertEqual(return_record.decision, SaleReturn.Decision.REWORK)
        self.assertIsNotNone(return_record.rework_order_id)
        self.assertEqual(return_record.rework_order.assigned_admin_id, self.admin.pk)
        self.assertEqual(return_record.rework_order.quantity, 1)

    def test_scrap_marks_unit_out_of_stock(self):
        created = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = created.data["id"]
        self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.DELIVERED},
            format="json",
        )
        self.client.post(
            f"/api/sales/{sale_id}/return/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "reason": "Unrepairable",
            },
            format="json",
        )
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(
            f"/api/sales/{sale_id}/return-decision/",
            {
                "serial_numbers": [self.units[0].serial_number],
                "decision": SaleReturn.Decision.SCRAP,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.units[0].refresh_from_db()
        self.assertEqual(self.units[0].status, FinishedUnit.Status.SCRAPPED)

    def test_scan_filter_and_bulk_status_update(self):
        created = self.client.post(
            "/api/sales/",
            self.sale_payload(unit_ids=[unit.pk for unit in self.units]),
            format="json",
        )
        sale_id = created.data["id"]
        scanned = self.client.get(
            "/api/sales/scan/",
            {"serial_number": self.units[0].serial_number},
        )
        self.assertEqual(scanned.status_code, 200)
        self.assertEqual(scanned.data["id"], sale_id)
        filtered = self.client.get(
            "/api/sales/",
            {
                "status": Sale.Status.READY_TO_SHIP,
                "platform": self.tamirabot.pk,
                "product": self.product.pk,
                "date_from": date.today().isoformat(),
                "date_to": date.today().isoformat(),
            },
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(len(filtered.data), 1)
        bulk = self.client.post(
            "/api/sales/bulk-status/",
            {
                "serial_numbers": [unit.serial_number for unit in self.units],
                "status": Sale.Status.DELIVERED,
                "note": "Carrier scan batch",
            },
            format="json",
        )
        self.assertEqual(bulk.status_code, 200, bulk.data)
        self.assertEqual(bulk.data[0]["status"], Sale.Status.DELIVERED)

    def test_platform_management_is_super_admin_only_and_audited(self):
        denied = self.client.post(
            "/api/platforms/",
            {
                "name": "Custom seller",
                "label_template_type": SalesPlatform.LabelTemplateType.SELLER_PROVIDED,
            },
            format="json",
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_authenticate(self.super_admin)
        created = self.client.post(
            "/api/platforms/",
            {
                "name": "Custom seller",
                "label_template_type": SalesPlatform.LabelTemplateType.SELLER_PROVIDED,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201, created.data)
        platform_id = created.data["id"]
        deactivated = self.client.patch(
            f"/api/platforms/{platform_id}/",
            {"active": False},
            format="json",
        )
        self.assertEqual(deactivated.status_code, 200)
        self.assertFalse(deactivated.data["active"])
        self.assertEqual(
            AuditLog.objects.filter(action="sales.platform.created").count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(action="sales.platform.updated").count(),
            1,
        )


@skipUnless(
    connection.features.has_select_for_update,
    "Concurrent sale tests require PostgreSQL or another SELECT FOR UPDATE database.",
)
class SalesConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_sales_cannot_sell_the_same_finished_unit(self):
        supervisor = User.objects.create_user(
            username="sale-race-super",
            email="sale-race-super@example.test",
            password="safe-test-password-2026",
            role=User.Role.SUPER_ADMIN,
        )
        admin = User.objects.create_user(
            username="sale-race-admin",
            email="sale-race-admin@example.test",
            password="safe-test-password-2026",
            role=User.Role.ADMIN,
        )
        component = Component.objects.create(
            sku="SALE-RACE-PART",
            name="Sale race part",
            unit="pcs",
        )
        with transaction.atomic():
            product = Product.objects.create(
                product_number="SALE-RACE-PRODUCT",
                name="Sale race product",
                sku="SALE-RACE-SKU",
            )
            revision = ProductRevision.create_next(product=product)
            BOMLine.objects.create(
                product_revision=revision,
                component=component,
                quantity_per_unit=Decimal("1"),
            )
        receive(component_id=component.pk, quantity=1, actor=supervisor)
        order = create_work_order(
            product_id=product.pk,
            quantity=1,
            priority=WorkOrder.Priority.HIGH,
            due_date=date.today() + timedelta(days=1),
            assigned_admin=admin,
            actor=supervisor,
        )
        start_work_order(work_order_id=order.pk, actor=admin)
        confirmation = confirm_packing(
            work_order_id=order.pk,
            quantity=1,
            idempotency_key="sale-race-pack",
            actor=admin,
        )
        unit = FinishedUnit.objects.get(pack_confirmation=confirmation)
        platform = SalesPlatform.objects.create(
            name="Sale race platform",
            label_template_type=SalesPlatform.LabelTemplateType.TAMIRABOT,
        )
        barrier = Barrier(2)

        def create_racing_sale():
            close_old_connections()
            try:
                actor = User.objects.get(pk=admin.pk)
                barrier.wait(timeout=10)
                create_sale(
                    values={
                        "platform": platform,
                        "unit_ids": [unit.pk],
                        "price": Decimal("12.00"),
                        "sale_date": date.today(),
                        "shipping_name": "Test recipient",
                        "shipping_address": "Test address",
                    },
                    actor=actor,
                )
                return "created"
            except APIValidationError:
                return "rejected"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create_racing_sale(), range(2)))

        self.assertCountEqual(results, ["created", "rejected"])
        self.assertEqual(SaleUnit.objects.filter(finished_unit=unit).count(), 1)
        unit.refresh_from_db()
        self.assertEqual(unit.status, FinishedUnit.Status.SOLD)
