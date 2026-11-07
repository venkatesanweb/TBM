from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory

from django.db import IntegrityError, transaction
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog
from catalog.images import MAX_IMAGE_EDGE
from catalog.models import (
    BOMLine,
    CompanyProfile,
    Component,
    ComponentPriceHistory,
    IndianState,
    Product,
    ProductPrice,
    ProductRevision,
    TaxRate,
)
from catalog.money import calculate_product_cost, money_string, round_money


class CatalogAPITests(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="catalog-owner",
            email="catalog-owner@example.test",
            password="catalog-owner-password",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="catalog-reader",
            email="catalog-reader@example.test",
            password="catalog-reader-password",
            role=User.Role.ADMIN,
        )
        self.component_a = Component.objects.create(
            sku="CMP-001",
            name="Aluminium plate",
            unit="piece",
            revision_number="A",
            category="Metal",
        )
        self.component_b = Component.objects.create(
            sku="CMP-002",
            name="Bolt",
            unit="piece",
            revision_number="B",
            category="Fastener",
        )
        self.product = Product.objects.create(
            product_number="TB-100",
            name="Test product",
            sku="TB-SKU-100",
        )
        self.revision = ProductRevision.create_next(product=self.product, notes="Initial")
        self.bom_line = BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_b,
            quantity_per_unit=Decimal("1"),
        )
        self.client.force_authenticate(self.super_admin)

    def test_product_creation_creates_first_revision_and_audits(self):
        response = self.client.post(
            "/api/products/",
            {
                "product_number": "TB-200",
                "name": "New product",
                "sku": "TB-SKU-200",
                "bom_lines": [
                    {
                        "component": self.component_a.pk,
                        "quantity_per_unit": "2",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = Product.objects.get(product_number="TB-200")
        self.assertEqual(created.current_revision.revision_number, 1)
        self.assertEqual(created.current_revision.bom_lines.count(), 1)
        self.assertTrue(
            AuditLog.objects.filter(
                action="catalog.product.created", object_id=str(created.pk)
            ).exists()
        )

    def test_admin_is_read_only_for_catalog(self):
        self.client.force_authenticate(self.admin)
        read_response = self.client.get("/api/products/")
        write_response = self.client.patch(
            f"/api/products/{self.product.pk}/",
            {"name": "Changed"},
            format="json",
        )
        component_write_response = self.client.post(
            "/api/components/",
            {"sku": "CMP-NEW", "name": "New", "unit": "piece"},
            format="json",
        )
        bom_write_response = self.client.post(
            f"/api/revisions/{self.revision.pk}/bom/",
            {"component": self.component_a.pk, "quantity_per_unit": "1"},
            format="json",
        )
        revision_write_response = self.client.post(
            f"/api/products/{self.product.pk}/revisions/",
            {"notes": "Admin must not make revisions."},
            format="json",
        )
        self.assertEqual(read_response.status_code, status.HTTP_200_OK)
        line = BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_a,
            quantity_per_unit=1,
        )
        product_create_response = self.client.post(
            "/api/products/",
            {
                "product_number": "TB-ADMIN-CREATE",
                "name": "Admin create attempt",
                "sku": "TB-SKU-ADMIN-CREATE",
                "bom_lines": [
                    {"component": self.component_a.pk, "quantity_per_unit": "1"}
                ],
            },
            format="json",
        )
        component_quick_create_response = self.client.post(
            "/api/components/quick-create/",
            {"sku": "CMP-ADMIN", "name": "Admin", "unit": "piece"},
            format="json",
        )
        product_delete_response = self.client.delete(f"/api/products/{self.product.pk}/")
        component_update_response = self.client.patch(
            f"/api/components/{self.component_a.pk}/",
            {"name": "Admin update"},
            format="json",
        )
        component_delete_response = self.client.delete(
            f"/api/components/{self.component_b.pk}/"
        )
        revision_update_response = self.client.patch(
            f"/api/revisions/{self.revision.pk}/",
            {"notes": "Admin update"},
            format="json",
        )
        revision_delete_response = self.client.delete(
            f"/api/revisions/{self.revision.pk}/"
        )
        bom_update_response = self.client.patch(
            f"/api/bom-lines/{line.pk}/",
            {"quantity_per_unit": "2"},
            format="json",
        )
        bom_delete_response = self.client.delete(f"/api/bom-lines/{line.pk}/")
        denied_writes = (
            write_response,
            component_write_response,
            bom_write_response,
            revision_write_response,
            product_create_response,
            component_quick_create_response,
            product_delete_response,
            component_update_response,
            component_delete_response,
            revision_update_response,
            revision_delete_response,
            bom_update_response,
            bom_delete_response,
        )
        self.assertTrue(
            all(response.status_code == status.HTTP_403_FORBIDDEN for response in denied_writes),
            [response.status_code for response in denied_writes],
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "Test product")


class ComponentPricingTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="pricing-owner",
            email="pricing-owner@example.test",
            password="Secure-test-password-2026",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="pricing-admin",
            email="pricing-admin@example.test",
            password="Secure-test-password-2026",
            role=User.Role.ADMIN,
        )
        self.component = Component.objects.create(
            sku="PRICE-001",
            name="Priced plate",
            unit="piece",
            cost_price=None,
        )
        self.second_component = Component.objects.create(
            sku="PRICE-002",
            name="Priced fastener",
            unit="piece",
            cost_price=None,
        )
        self.product = Product.objects.create(
            product_number="PRICE-PRODUCT",
            name="Price test product",
            sku="PRICE-SKU",
        )
        self.revision = ProductRevision.create_next(product=self.product)
        self.line = BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component,
            quantity_per_unit=Decimal("1.500"),
        )
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.second_component,
            quantity_per_unit=Decimal("1.000"),
        )
        self.client.force_authenticate(self.owner)

    def test_new_component_defaults_to_zero_price(self):
        component = Component.objects.create(
            sku="PRICE-ZERO",
            name="Default-priced component",
            unit="piece",
        )

        self.assertEqual(component.cost_price, Decimal("0.00"))

    def test_super_admin_price_write_creates_history_and_audit(self):
        response = self.client.patch(
            f"/api/components/{self.component.pk}/",
            {"cost_price": "10.25"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["cost_price"], "10.25")
        history = ComponentPriceHistory.objects.get(component=self.component)
        self.assertIsNone(history.old_price)
        self.assertEqual(history.new_price, Decimal("10.25"))
        self.assertEqual(history.changed_by, self.owner)
        self.assertTrue(
            AuditLog.objects.filter(
                action="catalog.component.price_changed",
                object_id=str(self.component.pk),
            ).exists()
        )

    def test_changing_existing_price_requires_a_reason(self):
        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("10.00")
        )
        response = self.client.patch(
            f"/api/components/{self.component.pk}/",
            {"cost_price": "11.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cost_price_reason", response.data)
        self.component.refresh_from_db()
        self.assertEqual(self.component.cost_price, Decimal("10.00"))
        self.assertFalse(ComponentPriceHistory.objects.filter(component=self.component).exists())

    def test_existing_price_can_be_cleared_with_a_reason(self):
        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("10.00")
        )
        response = self.client.patch(
            f"/api/components/{self.component.pk}/",
            {"cost_price": None, "cost_price_reason": "Supplier discontinued item"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["cost_price"])
        history = ComponentPriceHistory.objects.get(component=self.component)
        self.assertEqual(history.old_price, Decimal("10.00"))
        self.assertIsNone(history.new_price)
        self.assertEqual(history.reason, "Supplier discontinued item")

    def test_price_change_with_reason_is_recorded(self):
        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("10.00")
        )
        response = self.client.patch(
            f"/api/components/{self.component.pk}/",
            {"cost_price": "11.00", "cost_price_reason": "Supplier increase"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        history = ComponentPriceHistory.objects.get(component=self.component)
        self.assertEqual(history.old_price, Decimal("10.00"))
        self.assertEqual(history.new_price, Decimal("11.00"))
        self.assertEqual(history.reason, "Supplier increase")

    def test_admin_cannot_read_or_write_any_component_price(self):
        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("12.34")
        )
        self.client.force_authenticate(self.admin)
        responses = [
            self.client.get("/api/components/"),
            self.client.get(f"/api/components/{self.component.pk}/"),
            self.client.get(
                f"/api/revisions/{self.revision.pk}/bom/"
            ),
            self.client.get(f"/api/barcodes/lookup/?code={self.component.sku}"),
            self.client.get("/api/inventory/"),
            self.client.get("/api/work-orders/"),
            self.client.get("/api/finished-units/"),
        ]

        def contains_price_key(value):
            if isinstance(value, dict):
                return "cost_price" in value or any(
                    contains_price_key(item) for item in value.values()
                )
            if isinstance(value, list):
                return any(contains_price_key(item) for item in value)
            return False

        for response in responses:
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertFalse(contains_price_key(response.data))
        self.assertNotIn("product_costs", responses[1].data)

        self.assertEqual(
            self.client.get("/api/components/pricing/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                "/api/components/pricing/",
                {
                    "items": [
                        {"component_id": self.component.pk, "cost_price": "99.00"}
                    ]
                },
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.patch(
                f"/api/components/{self.component.pk}/",
                {"cost_price": "99.00"},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_bulk_price_update_is_all_or_nothing_when_reason_missing(self):
        Component.objects.filter(pk__in=(self.component.pk, self.second_component.pk)).update(
            cost_price=Decimal("10.00")
        )
        response = self.client.post(
            "/api/components/pricing/",
            {
                "items": [
                    {
                        "component_id": self.component.pk,
                        "cost_price": "11.00",
                        "reason": "Supplier increase",
                    },
                    {
                        "component_id": self.second_component.pk,
                        "cost_price": "12.00",
                    },
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.component.refresh_from_db()
        self.second_component.refresh_from_db()
        self.assertEqual(self.component.cost_price, Decimal("10.00"))
        self.assertEqual(self.second_component.cost_price, Decimal("10.00"))
        self.assertFalse(ComponentPriceHistory.objects.exists())

    def test_bulk_price_update_writes_history_and_returns_strings(self):
        response = self.client.post(
            "/api/components/pricing/",
            {
                "items": [
                    {"component_id": self.component.pk, "cost_price": "1.25"},
                    {"component_id": self.second_component.pk, "cost_price": "2.50"},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["updated_count"], 2)
        self.assertEqual(
            [item["cost_price"] for item in response.data["items"]],
            ["1.25", "2.50"],
        )
        self.assertEqual(ComponentPriceHistory.objects.count(), 2)

    def test_prices_reject_negative_excess_precision_and_float_values(self):
        for price in ("-0.01", "1.001", 1.25):
            with self.subTest(price=price):
                response = self.client.patch(
                    f"/api/components/{self.component.pk}/",
                    {"cost_price": price},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsNone(self.component.__class__.objects.get(pk=self.component.pk).cost_price)

    def test_round_half_up_and_incomplete_product_cost(self):
        self.assertEqual(round_money(Decimal("0.015")), Decimal("0.02"))
        self.assertEqual(money_string(Decimal("0.015")), "0.02")
        with self.assertRaises(TypeError):
            round_money(0.015)
        cost, missing = calculate_product_cost(self.revision)
        self.assertIsNone(cost)
        self.assertEqual(missing, 2)

        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("0.01")
        )
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("0.01")
        )
        cost, missing = calculate_product_cost(self.revision)
        self.assertEqual(cost, Decimal("0.03"))
        self.assertEqual(missing, 0)

    def test_preview_calculates_current_product_cost_and_change(self):
        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("0.01")
        )
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("0.01")
        )
        response = self.client.post(
            "/api/components/pricing/preview/",
            {
                "items": [
                    {"component_id": self.second_component.pk, "cost_price": "0.02"}
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        product = response.data["products"][0]
        self.assertEqual(product["computed_cost"], "0.03")
        self.assertEqual(product["proposed_cost"], "0.04")
        self.assertEqual(product["cost_change"], "0.01")

    def test_pricing_list_filter_and_current_revision_costs(self):
        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("0.01")
        )
        response = self.client.get("/api/components/pricing/")
        missing = self.client.get("/api/components/pricing/?price_not_set=true")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(missing.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item["id"] for item in missing.data},
            {self.second_component.pk},
        )
        row = next(item for item in response.data if item["id"] == self.component.pk)
        self.assertEqual(row["cost_price"], "0.01")
        self.assertEqual(row["products"][0]["product_id"], self.product.pk)


class CatalogAPITestsContinuation(APITestCase):
    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="catalog-owner",
            email="catalog-owner@example.test",
            password="Secure-test-password-2026",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="catalog-reader",
            email="catalog-reader@example.test",
            password="Secure-test-password-2026",
            role=User.Role.ADMIN,
        )
        self.component_a = Component.objects.create(
            sku="CMP-001",
            name="Aluminium plate",
            unit="piece",
            revision_number="A",
            category="Metal",
        )
        self.component_b = Component.objects.create(
            sku="CMP-002",
            name="Bolt",
            unit="piece",
            revision_number="B",
            category="Fastener",
        )
        self.product = Product.objects.create(
            product_number="TB-100",
            name="Test product",
            sku="TB-SKU-100",
        )
        self.revision = ProductRevision.create_next(product=self.product, notes="Initial")
        self.bom_line = BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_b,
            quantity_per_unit=Decimal("1"),
        )
        self.client.force_authenticate(self.super_admin)

    def test_bom_rejects_duplicate_component_and_nonpositive_quantity(self):
        url = f"/api/revisions/{self.revision.pk}/bom/"
        payload = {"component": self.component_a.pk, "quantity_per_unit": "2"}
        self.assertEqual(
            self.client.post(url, payload, format="json").status_code,
            status.HTTP_201_CREATED,
        )
        duplicate = self.client.post(url, payload, format="json")
        zero = self.client.post(
            url,
            {"component": self.component_b.pk, "quantity_per_unit": "0"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(zero.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            BOMLine.objects.filter(
                product_revision=self.revision, component=self.component_a
            ).count(),
            1,
        )

    def test_product_creation_rejects_empty_bom(self):
        response = self.client.post(
            "/api/products/",
            {
                "product_number": "TB-EMPTY",
                "name": "Empty BOM",
                "sku": "TB-SKU-EMPTY",
                "bom_lines": [],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Product.objects.filter(product_number="TB-EMPTY").exists())

    def test_product_unknown_component_rejected_then_succeeds_after_creation(self):
        payload = {
            "product_number": "TB-UNKNOWN",
            "name": "Unknown component product",
            "sku": "TB-SKU-UNKNOWN",
            "bom_lines": [{"component": 99999, "quantity_per_unit": "1"}],
        }
        response = self.client.post("/api/products/", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Product.objects.filter(product_number="TB-UNKNOWN").exists())

        component_response = self.client.post(
            "/api/components/quick-create/",
            {
                "sku": "CMP-LATE",
                "name": "Created first",
                "category": "General",
                "unit": "piece",
            },
            format="json",
        )
        self.assertEqual(component_response.status_code, status.HTTP_201_CREATED)
        payload["bom_lines"] = [
            {
                "component": component_response.data["id"],
                "quantity_per_unit": "1",
            }
        ]
        success = self.client.post("/api/products/", payload, format="json")
        self.assertEqual(success.status_code, status.HTTP_201_CREATED)

    def test_product_creation_rejects_duplicates_zero_and_inactive_components(self):
        duplicate_payload = {
            "product_number": "TB-DUP",
            "name": "Duplicate BOM",
            "sku": "TB-SKU-DUP",
            "bom_lines": [
                {"component": self.component_a.pk, "quantity_per_unit": "1"},
                {"component": self.component_a.pk, "quantity_per_unit": "2"},
            ],
        }
        zero_payload = {
            "product_number": "TB-ZERO",
            "name": "Zero BOM",
            "sku": "TB-SKU-ZERO",
            "bom_lines": [{"component": self.component_a.pk, "quantity_per_unit": "0"}],
        }
        inactive = Component.objects.create(
            sku="CMP-INACTIVE", name="Inactive part", unit="piece", active=False
        )
        inactive_payload = {
            "product_number": "TB-INACTIVE",
            "name": "Inactive BOM",
            "sku": "TB-SKU-INACTIVE",
            "bom_lines": [{"component": inactive.pk, "quantity_per_unit": "1"}],
        }
        for payload in (duplicate_payload, zero_payload, inactive_payload):
            with self.subTest(product=payload["product_number"]):
                response = self.client.post("/api/products/", payload, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertFalse(
                    Product.objects.filter(product_number=payload["product_number"]).exists()
                )

    def test_database_rejects_nonpositive_bom_quantity(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BOMLine.objects.create(
                    product_revision=self.revision,
                    component=self.component_a,
                    quantity_per_unit=Decimal("0"),
                )

    def test_bom_totals_for_125_units_with_two_components(self):
        self.bom_line.delete()
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_a,
            quantity_per_unit=Decimal("2"),
        )
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_b,
            quantity_per_unit=Decimal("0.5"),
        )
        response = self.client.get(
            f"/api/revisions/{self.revision.pk}/bom/?units=125"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        totals = {
            item["component_detail"]["sku"]: item["total_required"]
            for item in response.data["results"]
        }
        self.assertEqual(totals, {"CMP-001": "250", "CMP-002": "62.5"})

    def test_new_revision_copies_bom_and_does_not_change_previous_revision(self):
        first_line = BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_a,
            quantity_per_unit=Decimal("3"),
        )
        response = self.client.post(
            f"/api/products/{self.product.pk}/revisions/",
            {"notes": "Second revision"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        second = ProductRevision.objects.get(pk=response.data["id"])
        copied = second.bom_lines.get(component=self.component_a)
        self.assertEqual(second.revision_number, 2)
        self.assertEqual(copied.quantity_per_unit, Decimal("3"))

        updated = self.client.patch(
            f"/api/bom-lines/{copied.pk}/",
            {"quantity_per_unit": "8"},
            format="json",
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        first_line.refresh_from_db()
        self.assertEqual(first_line.quantity_per_unit, Decimal("3"))
        self.assertEqual(
            Product.objects.get(pk=self.product.pk).current_revision_id, second.pk
        )

    def test_product_and_component_creation_has_no_stock_dependency(self):
        self.assertFalse(hasattr(self.component_a, "stock"))
        response = self.client.post(
            "/api/products/",
            {
                "product_number": "TB-ZERO-STOCK",
                "name": "No stock needed",
                "sku": "TB-SKU-ZERO-STOCK",
                "bom_lines": [
                    {"component": self.component_b.pk, "quantity_per_unit": "1"}
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_frozen_revision_and_bom_are_read_only(self):
        line = BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_a,
            quantity_per_unit=Decimal("1"),
        )
        self.revision.freeze_for_work_order(
            actor=self.super_admin, work_order_reference="WO-TEST"
        )
        revision_response = self.client.patch(
            f"/api/revisions/{self.revision.pk}/",
            {"notes": "Changed"},
            format="json",
        )
        bom_response = self.client.patch(
            f"/api/bom-lines/{line.pk}/",
            {"quantity_per_unit": "2"},
            format="json",
        )
        self.assertEqual(revision_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(bom_response.status_code, status.HTTP_400_BAD_REQUEST)
        freeze_log = AuditLog.objects.get(action="catalog.revision.frozen")
        self.assertEqual(
            freeze_log.after["work_order_reference"],
            "WO-TEST",
        )

    def test_product_component_and_bom_changes_are_audited(self):
        self.client.patch(
            f"/api/products/{self.product.pk}/",
            {"description": "Updated description"},
            format="json",
        )
        component_response = self.client.post(
            "/api/components/",
            {"sku": "CMP-AUDIT", "name": "Washer", "unit": "piece"},
            format="json",
        )
        self.client.post(
            f"/api/revisions/{self.revision.pk}/bom/",
            {"component": self.component_a.pk, "quantity_per_unit": "1"},
            format="json",
        )
        self.assertEqual(component_response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(AuditLog.objects.filter(action="catalog.product.updated").exists())
        self.assertTrue(AuditLog.objects.filter(action="catalog.component.created").exists())
        self.assertTrue(AuditLog.objects.filter(action="catalog.bom_line.created").exists())

    def test_catalog_filters_by_sku_and_component_name(self):
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_a,
            quantity_per_unit=Decimal("1"),
        )
        response = self.client.get(
            f"/api/revisions/{self.revision.pk}/bom/?sku=CMP-001&component_name=Aluminium"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        product_response = self.client.get("/api/products/?product_number=TB-100&revision_number=1")
        self.assertEqual(product_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(product_response.data), 1)
        revision_search = self.client.get("/api/products/?search=1")
        self.assertEqual(revision_search.status_code, status.HTTP_200_OK)
        self.assertEqual(len(revision_search.data), 1)

    def test_component_usage_details_and_used_component_cannot_be_deleted(self):
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_a,
            quantity_per_unit=Decimal("1"),
        )
        detail = self.client.get(f"/api/components/{self.component_a.pk}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["used_in_count"], 1)
        self.assertEqual(detail.data["used_in"][0]["product_number"], "TB-100")
        delete = self.client.delete(f"/api/components/{self.component_a.pk}/")
        self.assertEqual(delete.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(Component.objects.filter(pk=self.component_a.pk).exists())

    def test_component_usage_includes_product_and_revision_ids_for_admins(self):
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component_a,
            quantity_per_unit=Decimal("2.500"),
        )

        response = self.client.get(f"/api/components/{self.component_a.pk}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["used_in"][0],
            {
                "product_number": self.product.product_number,
                "product_name": self.product.name,
                "revision_number": self.revision.revision_number,
                "quantity_per_unit": "2.500",
                "product_id": self.product.pk,
                "revision_id": self.revision.pk,
            },
        )
        self.client.force_authenticate(self.admin)
        admin_response = self.client.get(f"/api/components/{self.component_a.pk}/")
        self.assertEqual(admin_response.status_code, status.HTTP_200_OK)
        self.assertEqual(admin_response.data["used_in"][0]["product_id"], self.product.pk)
        self.assertEqual(admin_response.data["used_in"][0]["revision_id"], self.revision.pk)

    def test_cannot_remove_last_bom_line(self):
        response = self.client.delete(f"/api/bom-lines/{self.bom_line.pk}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(BOMLine.objects.filter(pk=self.bom_line.pk).exists())

    def test_inactive_component_cannot_be_added_to_existing_bom(self):
        self.component_a.active = False
        self.component_a.save(update_fields=("active",))
        response = self.client.post(
            f"/api/revisions/{self.revision.pk}/bom/",
            {"component": self.component_a.pk, "quantity_per_unit": "1"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            BOMLine.objects.filter(
                product_revision=self.revision, component=self.component_a
            ).exists()
        )

    def test_image_upload_is_validated_resized_and_saved(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            image_data = BytesIO()
            Image.new("RGB", (MAX_IMAGE_EDGE + 20, 50), "blue").save(
                image_data, format="PNG"
            )
            response = self.client.post(
                "/api/components/",
                {
                    "sku": "CMP-PHOTO",
                    "name": "Photo component",
                    "unit": "piece",
                    "photo": SimpleUploadedFile(
                        "original-name.png",
                        image_data.getvalue(),
                        content_type="image/png",
                    ),
                },
                format="multipart",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            component = Component.objects.get(sku="CMP-PHOTO")
            self.assertNotEqual(component.photo.name.rsplit("/", 1)[-1], "original-name.png")
            with Image.open(component.photo.path) as saved_image:
                self.assertLessEqual(saved_image.width, MAX_IMAGE_EDGE)

    def test_patch_without_photo_keeps_photo_and_new_upload_replaces_it(self):
        def image_bytes(color):
            image_data = BytesIO()
            Image.new("RGB", (32, 32), color).save(image_data, format="PNG")
            return image_data.getvalue()

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.component_a.photo.save(
                "component-original.png", ContentFile(image_bytes("blue")), save=False
            )
            self.component_a.save(update_fields=("photo",))
            self.product.photo.save(
                "product-original.png", ContentFile(image_bytes("blue")), save=False
            )
            self.product.save(update_fields=("photo",))
            component_photo = self.component_a.photo.name
            product_photo = self.product.photo.name

            component_response = self.client.patch(
                f"/api/components/{self.component_a.pk}/",
                {"description": "Updated without a photo"},
                format="multipart",
            )
            product_response = self.client.patch(
                f"/api/products/{self.product.pk}/",
                {"description": "Updated without a photo"},
                format="multipart",
            )

            self.assertEqual(component_response.status_code, status.HTTP_200_OK)
            self.assertEqual(product_response.status_code, status.HTTP_200_OK)
            self.component_a.refresh_from_db()
            self.product.refresh_from_db()
            self.assertEqual(self.component_a.photo.name, component_photo)
            self.assertEqual(self.product.photo.name, product_photo)

            component_response = self.client.patch(
                f"/api/components/{self.component_a.pk}/",
                {
                    "photo": SimpleUploadedFile(
                        "component-new.png", image_bytes("red"), content_type="image/png"
                    )
                },
                format="multipart",
            )
            product_response = self.client.patch(
                f"/api/products/{self.product.pk}/",
                {
                    "photo": SimpleUploadedFile(
                        "product-new.png", image_bytes("red"), content_type="image/png"
                    )
                },
                format="multipart",
            )

            self.assertEqual(component_response.status_code, status.HTTP_200_OK)
            self.assertEqual(product_response.status_code, status.HTTP_200_OK)
            self.component_a.refresh_from_db()
            self.product.refresh_from_db()
            self.assertNotEqual(self.component_a.photo.name, component_photo)
            self.assertNotEqual(self.product.photo.name, product_photo)


class CatalogImageTests(TestCase):
    def test_upload_is_resized_and_stored_with_generated_name(self):
        image_data = BytesIO()
        Image.new("RGB", (MAX_IMAGE_EDGE + 100, 50), "green").save(
            image_data, format="PNG"
        )
        upload = SimpleUploadedFile(
            "unsafe-name.png", image_data.getvalue(), content_type="image/png"
        )
        from catalog.images import resize_image

        resized = resize_image(upload)
        with Image.open(resized) as image:
            self.assertLessEqual(image.width, MAX_IMAGE_EDGE)
            self.assertLessEqual(image.height, MAX_IMAGE_EDGE)
        self.assertNotEqual(resized.name, "unsafe-name.png")

    def test_non_image_upload_is_rejected(self):
        from django.core.exceptions import ValidationError

        from catalog.images import resize_image

        upload = SimpleUploadedFile(
            "fake.png", b"this is not an image", content_type="image/png"
        )
        with self.assertRaises(ValidationError):
            resize_image(upload)

    def test_oversized_image_is_rejected_before_decoding(self):
        from django.core.exceptions import ValidationError

        from catalog.images import MAX_IMAGE_BYTES, resize_image

        upload = SimpleUploadedFile(
            "large.png",
            b"x" * (MAX_IMAGE_BYTES + 1),
            content_type="image/png",
        )
        with self.assertRaises(ValidationError):
            resize_image(upload)


class DummyDataSeedTests(TestCase):
    def test_seed_loads_components_before_products_and_ignores_opening_stock(self):
        call_command("seed_tamirabot_dummy_data", verbosity=0)
        product = Product.objects.get(product_number="TB-PROD-001")
        revision = product.current_revision
        self.assertEqual(revision.revision_number, 1)
        self.assertEqual(revision.bom_lines.count(), 2)
        self.assertTrue(
            Component.objects.filter(sku="TB-CMP-001").exists()
        )

        call_command("seed_tamirabot_dummy_data", verbosity=0)
        self.assertEqual(Product.objects.filter(product_number="TB-PROD-001").count(), 1)
        self.assertEqual(ProductRevision.objects.filter(product=product).count(), 1)
        self.assertEqual(revision.bom_lines.count(), 2)


class ProductPricingTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="product-price-owner",
            email="product-price-owner@example.test",
            password="pricing-test-password",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="product-price-admin",
            email="product-price-admin@example.test",
            password="pricing-test-password",
            role=User.Role.ADMIN,
        )
        self.component = Component.objects.create(
            sku="PP-CMP-001",
            name="Priced housing",
            unit="piece",
            cost_price=Decimal("1.01"),
        )
        self.second_component = Component.objects.create(
            sku="PP-CMP-002",
            name="Unpriced screw",
            unit="piece",
            cost_price=None,
        )
        self.product = Product.objects.create(
            product_number="PP-001",
            name="Pricing test product",
            sku="PP-SKU-001",
        )
        self.revision = ProductRevision.create_next(product=self.product)
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.component,
            quantity_per_unit=Decimal("1.500"),
        )
        BOMLine.objects.create(
            product_revision=self.revision,
            component=self.second_component,
            quantity_per_unit=Decimal("1.000"),
        )
        self.tax_rate = TaxRate.objects.get(rate_percent=Decimal("18.00"))
        self.client.force_authenticate(self.owner)

    def price_payload(self, **overrides):
        payload = {
            "margin_percent": "10.00",
            "selling_price_incl_tax": "5.22",
            "mrp": "6.00",
            "hsn_code": "123456",
            "tax_rate_id": self.tax_rate.pk,
            "reason": "",
        }
        payload.update(overrides)
        return payload

    def test_cost_is_incomplete_and_names_missing_components(self):
        response = self.client.get("/api/products/pricing/")
        product = response.data["products"][0]
        self.assertEqual(product["status"], "Incomplete")
        self.assertIsNone(product["computed_cost"])
        self.assertEqual(product["missing_components"], ["Unpriced screw"])

        preview = self.client.get(
            f"/api/products/{self.product.pk}/pricing/preview/",
            {"margin_percent": "0.00", "tax_rate_id": self.tax_rate.pk},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertFalse(preview.data["complete"])
        self.assertEqual(preview.data["missing_components"], ["Unpriced screw"])

    def test_cost_and_suggested_price_use_decimal_half_up_rounding(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        preview = self.client.get(
            f"/api/products/{self.product.pk}/pricing/preview/",
            {"margin_percent": "10.00", "tax_rate_id": self.tax_rate.pk},
        )
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertEqual(preview.data["cost"], "4.02")
        self.assertEqual(preview.data["margin_amount"], "0.40")
        self.assertEqual(preview.data["taxable_value"], "4.42")
        self.assertEqual(preview.data["gst_amount"], "0.80")
        self.assertEqual(preview.data["suggested_selling_price"], "5.22")

    def test_preview_uses_the_current_product_revision_bom(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        new_revision = ProductRevision.create_next(product=self.product)
        BOMLine.objects.filter(product_revision=new_revision).delete()
        BOMLine.objects.create(
            product_revision=new_revision,
            component=self.component,
            quantity_per_unit=Decimal("2.000"),
        )
        response = self.client.get(
            f"/api/products/{self.product.pk}/pricing/preview/",
            {"margin_percent": "0.00", "tax_rate_id": self.tax_rate.pk},
        )
        self.assertEqual(response.data["cost"], "2.02")

    def test_margin_zero_suggests_cost_plus_gst(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        preview = self.client.get(
            f"/api/products/{self.product.pk}/pricing/preview/",
            {"margin_percent": "0.00", "tax_rate_id": self.tax_rate.pk},
        )
        self.assertEqual(preview.data["suggested_selling_price"], "4.74")

    def test_active_tax_rate_table_controls_preview_rate(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        rate_response = self.client.patch(
            f"/api/tax-rates/{self.tax_rate.pk}/",
            {"rate_percent": "12.00"},
            format="json",
        )
        self.assertEqual(rate_response.status_code, status.HTTP_200_OK)
        preview = self.client.get(
            f"/api/products/{self.product.pk}/pricing/preview/",
            {"margin_percent": "0.00", "tax_rate_id": self.tax_rate.pk},
        )
        self.assertEqual(preview.data["gst_rate_percent"], "12.00")
        self.assertEqual(preview.data["suggested_selling_price"], "4.50")

    def test_price_save_appends_history_and_requires_reason_on_later_prices(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        first = self.client.post(
            f"/api/products/{self.product.pk}/prices/",
            self.price_payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProductPrice.objects.filter(product=self.product).count(), 1)
        price = ProductPrice.objects.get(product=self.product)
        self.assertEqual(price.cost_snapshot, Decimal("4.02"))
        self.assertEqual(price.created_by, self.owner)
        self.assertTrue(
            AuditLog.objects.filter(
                action="catalog.product.price_created",
                object_id=str(price.pk),
            ).exists()
        )

        missing_reason = self.client.post(
            f"/api/products/{self.product.pk}/prices/",
            self.price_payload(selling_price_incl_tax="5.50", mrp="6.00"),
            format="json",
        )
        self.assertEqual(missing_reason.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ProductPrice.objects.filter(product=self.product).count(), 1)

        second = self.client.post(
            f"/api/products/{self.product.pk}/prices/",
            self.price_payload(
                selling_price_incl_tax="5.50",
                mrp="6.00",
                reason="Quarterly price review",
            ),
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProductPrice.objects.filter(product=self.product).count(), 2)

    def test_selling_price_cannot_exceed_mrp_and_both_must_be_positive(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        response = self.client.post(
            f"/api/products/{self.product.pk}/prices/",
            self.price_payload(selling_price_incl_tax="6.01", mrp="6.00"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        zero_price = self.client.post(
            f"/api/products/{self.product.pk}/prices/",
            self.price_payload(selling_price_incl_tax="0.00", mrp="0.00"),
            format="json",
        )
        self.assertEqual(zero_price.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ProductPrice.objects.exists())

    def test_hsn_must_be_four_six_or_eight_digits(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        response = self.client.post(
            f"/api/products/{self.product.pk}/prices/",
            self.price_payload(hsn_code="12A456"),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ProductPrice.objects.exists())

    def test_money_api_fields_reject_json_floats(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        response = self.client.post(
            f"/api/products/{self.product.pk}/prices/",
            self.price_payload(selling_price_incl_tax=5.22),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(ProductPrice.objects.exists())

    def test_admin_only_sees_price_summary_with_money_section(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        ProductPrice.objects.create(
            product=self.product,
            cost_snapshot=Decimal("4.02"),
            margin_percent=Decimal("10.00"),
            selling_price_incl_tax=Decimal("5.22"),
            mrp=Decimal("6.00"),
            hsn_code="123456",
            gst_rate_percent=Decimal("18.00"),
            created_by=self.owner,
        )
        self.client.force_authenticate(self.admin)
        denied_detail = self.client.get(f"/api/products/{self.product.pk}/")
        self.assertEqual(denied_detail.status_code, status.HTTP_200_OK)
        self.assertNotIn("product_price_summary", denied_detail.data)
        self.assertEqual(
            self.client.get("/api/products/pricing/").status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                f"/api/products/{self.product.pk}/prices/",
                self.price_payload(),
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        allowed_detail = self.client.get(f"/api/products/{self.product.pk}/")
        self.assertEqual(allowed_detail.status_code, status.HTTP_200_OK)
        summary = allowed_detail.data["product_price_summary"]
        self.assertEqual(summary["selling_price_incl_tax"], "5.22")
        self.assertEqual(summary["computed_cost"] if "computed_cost" in summary else summary["cost"], "4.02")

    def test_component_cost_change_flags_existing_price_for_review_only(self):
        Component.objects.filter(pk=self.second_component.pk).update(
            cost_price=Decimal("2.50")
        )
        ProductPrice.objects.create(
            product=self.product,
            cost_snapshot=Decimal("4.02"),
            margin_percent=Decimal("0.00"),
            selling_price_incl_tax=Decimal("4.74"),
            mrp=Decimal("5.00"),
            hsn_code="1234",
            gst_rate_percent=Decimal("18.00"),
            created_by=self.owner,
        )
        Component.objects.filter(pk=self.component.pk).update(
            cost_price=Decimal("2.00")
        )
        response = self.client.get("/api/products/pricing/")
        row = response.data["products"][0]
        self.assertTrue(row["cost_changed_review"])
        self.assertEqual(ProductPrice.objects.get(product=self.product).selling_price_incl_tax, Decimal("4.74"))

    def test_tax_rate_examples_are_editable_and_admin_cannot_write_them(self):
        self.assertEqual(
            set(TaxRate.objects.values_list("rate_percent", flat=True)),
            {Decimal("0.00"), Decimal("5.00"), Decimal("18.00")},
        )
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/tax-rates/{self.tax_rate.pk}/",
            {"label": "No write"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(self.owner)
        response = self.client.patch(
            f"/api/tax-rates/{self.tax_rate.pk}/",
            {"label": "Accountant-confirmed GST"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["label"], "Accountant-confirmed GST")

    def test_product_price_model_rejects_mutation(self):
        price = ProductPrice.objects.create(
            product=self.product,
            cost_snapshot=Decimal("1.52"),
            margin_percent=Decimal("0.00"),
            selling_price_incl_tax=Decimal("1.79"),
            mrp=Decimal("2.00"),
            hsn_code="1234",
            gst_rate_percent=Decimal("18.00"),
            created_by=self.owner,
        )
        price.reason = "Should not be overwritten"
        with self.assertRaises(ValueError):
            price.save()
        with self.assertRaises(ValueError):
            price.delete()

    def test_product_form_accepts_generic_name_and_net_quantity(self):
        response = self.client.patch(
            f"/api/products/{self.product.pk}/",
            {"generic_name": "Industrial fitting", "net_quantity_text": "2 Pieces"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.product.refresh_from_db()
        self.assertEqual(self.product.generic_name, "Industrial fitting")
        self.assertEqual(self.product.net_quantity_text, "2 Pieces")


class CompanyProfileTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="company-owner",
            email="company-owner@example.test",
            password="company-test-password",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="company-admin",
            email="company-admin@example.test",
            password="company-test-password",
            role=User.Role.ADMIN,
        )
        self.profile, _ = CompanyProfile.objects.get_or_create(pk=1)
        self.client.force_authenticate(self.owner)

    def test_company_profile_is_a_single_fixed_row(self):
        self.assertEqual(CompanyProfile.objects.count(), 1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CompanyProfile.objects.create(pk=2, legal_name="Second company")
        self.assertEqual(CompanyProfile.objects.count(), 1)

    def test_official_indian_state_code_reference_is_loaded(self):
        self.assertEqual(IndianState.objects.count(), 38)
        self.assertEqual(IndianState.objects.get(state_code="33").name, "Tamil Nadu")
        self.assertEqual(IndianState.objects.get(state_code="38").name, "Ladakh")

    def test_gstin_format_and_state_prefix_are_validated(self):
        invalid_format = self.client.patch(
            "/api/company-profile/",
            {"state_code": "33", "gstin": "33ABC1234F1Z5"},
            format="json",
        )
        self.assertEqual(invalid_format.status_code, status.HTTP_400_BAD_REQUEST)
        mismatched_state = self.client.patch(
            "/api/company-profile/",
            {"state_code": "33", "gstin": "29ABCDE1234F1Z5"},
            format="json",
        )
        self.assertEqual(mismatched_state.status_code, status.HTTP_400_BAD_REQUEST)
        valid = self.client.patch(
            "/api/company-profile/",
            {"state_code": "33", "gstin": "33ABCDE1234F1Z5"},
            format="json",
        )
        self.assertEqual(valid.status_code, status.HTTP_200_OK)
        self.assertEqual(valid.data["profile"]["state_code"], "33")
        self.assertEqual(valid.data["profile"]["gstin"], "33ABCDE1234F1Z5")
        changed_state = self.client.patch(
            "/api/company-profile/",
            {"state_code": "29"},
            format="json",
        )
        self.assertEqual(changed_state.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pin_phone_and_email_formats_are_validated(self):
        invalid = self.client.patch(
            "/api/company-profile/",
            {
                "pincode": "12345x",
                "phone": "not-a-phone",
                "email": "invalid-email",
            },
            format="json",
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        valid = self.client.patch(
            "/api/company-profile/",
            {
                "pincode": "600001",
                "phone": "+91 98765-43210",
                "email": "office@example.test",
            },
            format="json",
        )
        self.assertEqual(valid.status_code, status.HTTP_200_OK)

    def test_company_profile_change_is_audited(self):
        response = self.client.patch(
            "/api/company-profile/",
            {"legal_name": "Tamirabot Industries"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            AuditLog.objects.filter(
                action="company.profile.updated",
                object_id="1",
                actor=self.owner,
            ).exists()
        )

    def test_admin_cannot_read_or_write_company_settings(self):
        self.client.force_authenticate(self.admin)
        read_response = self.client.get("/api/company-profile/")
        write_response = self.client.patch(
            "/api/company-profile/",
            {"legal_name": "Admin attempt"},
            format="json",
        )
        self.assertEqual(read_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(write_response.status_code, status.HTTP_403_FORBIDDEN)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.legal_name, "")
