from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.db import close_old_connections, connections, transaction
from django.test import TransactionTestCase
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog
from catalog.barcodes import lock_and_validate_barcode, normalize_barcode
from catalog.models import BOMLine, Component, Product, ProductRevision
from inventory.models import StockBalance, StockMovement
from inventory.services import receive
from workorders.models import FinishedUnit, PackConfirmation, WorkOrder


class BarcodeNormalizationTests(APITestCase):
    def test_normalization_trims_edges_keeps_zeros_and_accepts_empty_as_null(self):
        self.assertEqual(normalize_barcode("\x00 \t0000123\r\n\x7f"), "0000123")
        self.assertEqual(normalize_barcode("\x00 \t\x7f"), None)
        self.assertEqual(normalize_barcode("0" * 64), "0" * 64)
        with self.assertRaises(ValidationError):
            normalize_barcode("0" * 65)


class BarcodeAPITests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="barcode-owner",
            email="barcode-owner@example.test",
            password="test-password",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="barcode-reader",
            email="barcode-reader@example.test",
            password="test-password",
            role=User.Role.ADMIN,
        )
        self.component = Component.objects.create(
            sku="TB-CMP-002",
            name="Controller PCB",
            unit="piece",
            category="Electronics",
            revision_number="A",
            barcode="000123",
        )
        with transaction.atomic():
            self.product = Product.objects.create(
                product_number="TB-STARTER",
                name="Tamirabot Starter",
                sku="TB-SKU-STARTER",
            )
            self.revision = ProductRevision.create_next(product=self.product)
            BOMLine.objects.create(
                product_revision=self.revision,
                component=self.component,
                quantity_per_unit=Decimal("2"),
            )
        self.client.force_authenticate(self.super_admin)

    def _create_product(self, *, number, barcode):
        return self.client.post(
            "/api/products/",
            {
                "product_number": number,
                "name": f"Product {number}",
                "sku": f"SKU-{number}",
                "barcode": barcode,
                "bom_lines": [
                    {"component": self.component.pk, "quantity_per_unit": "1"}
                ],
            },
            format="json",
        )

    def test_component_create_normalizes_and_lookup_returns_stock_display_fields(self):
        response = self.client.post(
            "/api/components/",
            {
                "sku": "TB-CMP-SCAN",
                "name": "Scannable component",
                "unit": "piece",
                "barcode": " \t0000456\r\n",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["barcode"], "0000456")

        lookup = self.client.get(
            "/api/barcodes/lookup/", {"code": " 0000456 "}
        )
        self.assertEqual(lookup.status_code, status.HTTP_200_OK)
        self.assertEqual(lookup.data["type"], "component")
        self.assertEqual(lookup.data["matched_by"], "barcode")
        self.assertEqual(lookup.data["id"], response.data["id"])
        self.assertEqual(lookup.data["stock"]["available"], "0")
        self.assertEqual(lookup.data["stock"]["unit"], "piece")

    def test_duplicate_barcode_is_rejected_with_existing_owner_for_both_models(self):
        expected = "Barcode already registered to Controller PCB (TB-CMP-002)"
        component_response = self.client.post(
            "/api/components/",
            {
                "sku": "TB-CMP-DUP",
                "name": "Duplicate component",
                "unit": "piece",
                "barcode": "000123",
            },
            format="json",
        )
        product_response = self._create_product(number="TB-DUP", barcode="000123")
        self.assertEqual(component_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(component_response.data["barcode"][0]), expected)
        self.assertEqual(product_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(str(product_response.data["barcode"][0]), expected)

        first_product = self._create_product(
            number="TB-OWNED-BARCODE", barcode="product-only-code"
        )
        duplicate_product = self._create_product(
            number="TB-DUPLICATE-PRODUCT", barcode="product-only-code"
        )
        self.assertEqual(first_product.status_code, status.HTTP_201_CREATED)
        self.assertEqual(duplicate_product.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Product TB-OWNED-BARCODE", str(duplicate_product.data["barcode"]))

    def test_empty_barcode_is_stored_as_null(self):
        response = self.client.post(
            "/api/components/",
            {
                "sku": "TB-CMP-EMPTY-BARCODE",
                "name": "No barcode component",
                "unit": "piece",
                "barcode": " \x00\t ",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(Component.objects.get(pk=response.data["id"]).barcode)

    def test_product_barcode_updates_are_audited_and_same_owner_can_keep_code(self):
        created = self._create_product(number="TB-BARCODE", barcode="  000987 ")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        product = Product.objects.get(pk=created.data["id"])
        self.assertEqual(product.barcode, "000987")

        same_owner = self.client.patch(
            f"/api/products/{product.pk}/",
            {"barcode": "000987"},
            format="json",
        )
        self.assertEqual(same_owner.status_code, status.HTTP_200_OK)

        changed = self.client.patch(
            f"/api/products/{product.pk}/",
            {"barcode": "000988"},
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_200_OK)
        audit = AuditLog.objects.filter(
            action="catalog.product.updated", object_id=str(product.pk)
        ).latest("pk")
        self.assertEqual(audit.before["barcode"], "000987")
        self.assertEqual(audit.after["barcode"], "000988")

    def test_component_update_barcode_change_is_audited_and_cross_model_conflict_blocked(self):
        product_response = self._create_product(number="TB-OTHER", barcode="000777")
        self.assertEqual(product_response.status_code, status.HTTP_201_CREATED)
        conflict = self.client.patch(
            f"/api/components/{self.component.pk}/",
            {"barcode": "000777"},
            format="json",
        )
        self.assertEqual(conflict.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Product TB-OTHER", str(conflict.data["barcode"]))

        changed = self.client.patch(
            f"/api/components/{self.component.pk}/",
            {"barcode": "000124"},
            format="json",
        )
        self.assertEqual(changed.status_code, status.HTTP_200_OK)
        audit = AuditLog.objects.filter(
            action="catalog.component.updated", object_id=str(self.component.pk)
        ).latest("pk")
        self.assertEqual(audit.before["barcode"], "000123")
        self.assertEqual(audit.after["barcode"], "000124")

    def test_lookup_order_sku_product_number_and_unknown_code(self):
        component_sku = self.client.get("/api/barcodes/lookup/?code=TB-CMP-002")
        product_number = self.client.get("/api/barcodes/lookup/?code=TB-STARTER")
        unknown = self.client.get("/api/barcodes/lookup/?code=not-registered")
        self.assertEqual(component_sku.data["matched_by"], "sku")
        self.assertEqual(component_sku.data["type"], "component")
        self.assertEqual(product_number.data["matched_by"], "sku")
        self.assertEqual(product_number.data["type"], "product")
        self.assertEqual(
            unknown.data,
            {"found": False, "type": "none", "matched_by": None},
        )

    def test_product_barcode_lookup_includes_bom_and_finished_goods_count(self):
        self.product.barcode = "000999"
        self.product.save(update_fields=("barcode",))
        lookup = self.client.get("/api/barcodes/lookup/?code=000999")
        self.assertEqual(lookup.data["type"], "product")
        self.assertEqual(lookup.data["matched_by"], "barcode")
        self.assertEqual(lookup.data["finished_goods"]["in_stock"], 0)
        self.assertEqual(lookup.data["bom"][0]["sku"], self.component.sku)

    def test_inactive_component_is_reported_as_inactive(self):
        inactive_component = Component.objects.create(
            sku="TB-CMP-INACTIVE-LOOKUP",
            name="Inactive component",
            unit="piece",
            active=False,
            barcode="inactive-code",
        )
        lookup = self.client.get(
            f"/api/barcodes/lookup/?code={inactive_component.barcode}"
        )
        self.assertEqual(lookup.data["active"], False)

    def test_finished_unit_serial_is_identified_without_becoming_a_product(self):
        order = WorkOrder.objects.create(
            product=self.product,
            product_revision=self.revision,
            quantity=1,
            due_date=date.today(),
            assigned_admin=self.admin,
            created_by=self.super_admin,
        )
        confirmation = PackConfirmation.objects.create(
            work_order=order,
            idempotency_key="barcode-lookup-test",
            quantity=1,
            confirmed_by=self.admin,
        )
        unit = FinishedUnit.objects.create(
            serial_number="TB-STARTER-2610-000001",
            barcode_value="TB-STARTER-2610-000001",
            product=self.product,
            product_revision=self.revision,
            work_order=order,
            pack_confirmation=confirmation,
        )
        lookup = self.client.get(
            f"/api/barcodes/lookup/?code={unit.serial_number}"
        )
        self.assertEqual(lookup.data["type"], "finished_unit")
        self.assertEqual(lookup.data["matched_by"], "serial")
        self.assertEqual(lookup.data["id"], unit.pk)

    def test_admin_can_lookup_and_unauthenticated_user_is_denied(self):
        self.client.force_authenticate(self.admin)
        admin_response = self.client.get("/api/barcodes/lookup/?code=000123")
        self.assertEqual(admin_response.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(None)
        anonymous_response = self.client.get("/api/barcodes/lookup/?code=000123")
        self.assertEqual(anonymous_response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_scanned_receipt_is_atomic_audited_and_idempotent(self):
        payload = {
            "idempotency_key": "receipt-once",
            "note": "Initial barcode receipt",
            "items": [
                {"component_id": self.component.pk, "quantity": "3"},
            ],
        }
        first = self.client.post(
            "/api/components/receive-scanned/", payload, format="json"
        )
        second = self.client.post(
            "/api/components/receive-scanned/", payload, format="json"
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data, second.data)
        self.assertEqual(StockBalance.objects.get(component=self.component).on_hand, 3)
        movements = StockMovement.objects.filter(
            component=self.component, movement_type=StockMovement.Type.RECEIVE
        )
        self.assertEqual(movements.count(), 1)
        movement_audit = AuditLog.objects.get(
            action="inventory.stock.receive", object_id=str(movements.get().pk)
        )
        self.assertEqual(movement_audit.after["source"], "barcode scan session")
        operation = AuditLog.objects.get(
            action="inventory.scanned_receipt.confirmed"
        )
        self.assertEqual(operation.after["source"], "barcode scan session")

    def test_scanned_receipt_rejects_admin_inactive_duplicate_invalid_and_product_lines(self):
        self.client.force_authenticate(self.admin)
        admin_response = self.client.post(
            "/api/components/receive-scanned/",
            {
                "idempotency_key": "admin-denied",
                "items": [{"component_id": self.component.pk, "quantity": "1"}],
            },
            format="json",
        )
        self.assertEqual(admin_response.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.super_admin)

        inactive = Component.objects.create(
            sku="TB-CMP-INACTIVE",
            name="Inactive component",
            unit="piece",
            active=False,
        )
        inactive_response = self.client.post(
            "/api/components/receive-scanned/",
            {
                "idempotency_key": "inactive",
                "items": [{"component_id": inactive.pk, "quantity": "1"}],
            },
            format="json",
        )
        duplicate_response = self.client.post(
            "/api/components/receive-scanned/",
            {
                "idempotency_key": "duplicate-lines",
                "items": [
                    {"component_id": self.component.pk, "quantity": "1"},
                    {"component_id": self.component.pk, "quantity": "2"},
                ],
            },
            format="json",
        )
        zero_response = self.client.post(
            "/api/components/receive-scanned/",
            {
                "idempotency_key": "zero-quantity",
                "items": [{"component_id": self.component.pk, "quantity": "0"}],
            },
            format="json",
        )
        product_id_response = self.client.post(
            "/api/components/receive-scanned/",
            {
                "idempotency_key": "product-id",
                "items": [{"product_id": self.product.pk, "quantity": "1"}],
            },
            format="json",
        )
        self.assertEqual(inactive_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(duplicate_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(zero_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(product_id_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StockMovement.objects.count(), 0)

    def test_scanned_receipt_failure_on_later_line_rolls_back_everything(self):
        second_component = Component.objects.create(
            sku="TB-CMP-SECOND",
            name="Second component",
            unit="piece",
        )
        real_receive = receive
        calls = 0

        def fail_on_second_receive(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValidationError("Simulated receive failure.")
            return real_receive(**kwargs)

        with patch("inventory.services.receive", side_effect=fail_on_second_receive):
            response = self.client.post(
                "/api/components/receive-scanned/",
                {
                    "idempotency_key": "atomic-failure",
                    "items": [
                        {"component_id": self.component.pk, "quantity": "1"},
                        {"component_id": second_component.pk, "quantity": "1"},
                    ],
                },
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(action="inventory.stock.receive").count(), 0)
        self.assertEqual(
            AuditLog.objects.filter(action="inventory.scanned_receipt.confirmed").count(),
            0,
        )

    def test_reusing_idempotency_key_with_different_payload_is_rejected(self):
        url = "/api/components/receive-scanned/"
        first = self.client.post(
            url,
            {
                "idempotency_key": "receipt-payload",
                "items": [{"component_id": self.component.pk, "quantity": "1"}],
            },
            format="json",
        )
        changed = self.client.post(
            url,
            {
                "idempotency_key": "receipt-payload",
                "items": [{"component_id": self.component.pk, "quantity": "2"}],
            },
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(changed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(StockMovement.objects.count(), 1)


class BarcodeConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_component_and_product_assignment_cannot_share_barcode(self):
        component = Component.objects.create(
            sku="RACE-COMPONENT",
            name="Race component",
            unit="piece",
        )
        with transaction.atomic():
            product = Product.objects.create(
                product_number="RACE-PRODUCT",
                name="Race product",
                sku="RACE-SKU",
            )
            revision = ProductRevision.create_next(product=product)
            BOMLine.objects.create(
                product_revision=revision,
                component=component,
                quantity_per_unit=Decimal("1"),
            )
        barrier = Barrier(2)

        def assign(model, record_id):
            close_old_connections()
            try:
                record = model.objects.get(pk=record_id)
                barrier.wait(timeout=10)
                try:
                    with transaction.atomic():
                        record.barcode = lock_and_validate_barcode(
                            "RACE-0001", instance=record
                        )
                        record.save(update_fields=("barcode",))
                    return "saved"
                except ValidationError:
                    return "conflict"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda pair: assign(*pair),
                    ((Component, component.pk), (Product, product.pk)),
                )
            )

        self.assertCountEqual(results, ["saved", "conflict"])
        self.assertEqual(
            int(Component.objects.filter(barcode="RACE-0001").count())
            + int(Product.objects.filter(barcode="RACE-0001").count()),
            1,
        )
