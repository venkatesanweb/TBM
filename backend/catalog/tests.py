from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory

from django.db import IntegrityError, transaction
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from audit.models import AuditLog
from catalog.images import MAX_IMAGE_EDGE
from catalog.models import BOMLine, Component, Product, ProductRevision


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
