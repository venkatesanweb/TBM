import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from PIL import Image, ImageDraw
from django.db import (
    IntegrityError,
    close_old_connections,
    connection,
    connections,
    transaction,
)
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError as APIValidationError
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from catalog.models import (
    BOMLine,
    CompanyProfile,
    Component,
    IndianState,
    Product,
    ProductPrice,
    ProductRevision,
)
from inventory.services import receive
from sales.invoices import amount_in_words, financial_year, issue_invoice
from sales.models import (
    CreditNote,
    CreditNoteSequence,
    Invoice,
    InvoiceSequence,
    LabelPrintLog,
    LabelTemplate,
    Sale,
    SaleReturn,
    SaleStatusHistory,
    SaleUnit,
    SalesPlatform,
)
from sales.services import create_sale
from sales.tax import calculate_sale_tax
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
        self.admin.dashboard_sections = []
        self.admin.save(update_fields=("dashboard_sections",))
        self.state, _ = IndianState.objects.get_or_create(
            state_code="33",
            defaults={"name": "Tamil Nadu"},
        )
        CompanyProfile.objects.update_or_create(
            pk=1, defaults={"state_code": self.state}
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
        self.product_price = ProductPrice.objects.create(
            product=self.product,
            cost_snapshot=Decimal("1000.00"),
            margin_percent=Decimal("0.00"),
            selling_price_incl_tax=Decimal("2500.00"),
            mrp=Decimal("3000.00"),
            hsn_code="123456",
            gst_rate_percent=Decimal("18.00"),
            created_by=self.super_admin,
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
            "sale_date": date.today().isoformat(),
            "customer_state_code": self.state.state_code,
            "customer_name": "A Tamirabot customer",
            "customer_phone": "9000000000",
            "billing_address": "12 Workshop Road",
            "delivery_address": "12 Workshop Road",
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

    def prepare_invoice_settings(self):
        profile = CompanyProfile.objects.get(pk=1)
        profile.legal_name = "Tamirabot Technologies"
        profile.trade_name = "Tamirabot"
        profile.address_line1 = "12 Workshop Road"
        profile.city = "Chennai"
        profile.state_code = self.state
        profile.pincode = "600001"
        profile.gstin = "33ABCDE1234F1Z5"
        profile.packer_manufacturer_name = "Tamirabot Works"
        profile.packer_address = "12 Workshop Road, Chennai"
        profile.country_of_origin = "India"
        profile.consumer_care_name = "Tamirabot Care"
        profile.consumer_care_address = "12 Workshop Road, Chennai"
        profile.consumer_care_phone = "9000000000"
        profile.consumer_care_email = "care@example.test"
        profile.invoice_prefix = "TB"
        profile.credit_note_prefix = "CN"
        profile.authorised_signatory_name = "Authorised Person"
        profile.save()
        self.product.generic_name = "Electronic controller"
        self.product.net_quantity_text = "1 Unit"
        self.product.save(update_fields=("generic_name", "net_quantity_text"))
        self.tamirabot.issue_tamirabot_invoice = True
        self.tamirabot.save(update_fields=("issue_tamirabot_invoice",))

    def create_invoice_sale(self, *, platform=None, unit=None, state=None):
        platform = platform or self.tamirabot
        unit = unit or self.units[0]
        payload = self.sale_payload(
            platform=platform.pk,
            unit_ids=[unit.pk],
            customer_state_code=(state or self.state).state_code,
            product_quantities=[],
            seller_label_reference=(
                "seller reference"
                if platform.label_template_type == SalesPlatform.LabelTemplateType.SELLER_PROVIDED
                else ""
            ),
        )
        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        response = self.client.post("/api/sales/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def issue_test_invoice(self, *, sale_id=None):
        self.prepare_invoice_settings()
        sale_id = sale_id or self.create_invoice_sale()
        self.client.force_authenticate(self.super_admin)
        response = self.client.post(f"/api/sales/{sale_id}/invoice/", {}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return sale_id, response.data["invoice"]

    def test_tamirabot_sale_generates_packing_label_and_records_transitions(self):
        response = self.client.post("/api/sales/", self.sale_payload(), format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("price", response.data)
        self.assertNotIn("unit_selling_price_incl_tax", response.data["units"][0])
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

    def test_price_snapshot_comes_from_price_list_and_money_is_section_gated(self):
        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        response = self.client.post("/api/sales/", self.sale_payload(), format="json")

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["price"], "2500.00")
        self.assertEqual(response.data["pricing_source"], Sale.PricingSource.PRICE_LIST)
        sale_unit = SaleUnit.objects.get(sale_id=response.data["id"])
        self.assertEqual(sale_unit.unit_selling_price_incl_tax, Decimal("2500.00"))
        self.assertEqual(sale_unit.mrp_snapshot, Decimal("3000.00"))
        self.assertEqual(sale_unit.hsn_code_snapshot, "123456")
        self.assertEqual(sale_unit.gst_rate_snapshot, Decimal("18.00"))
        self.assertEqual(sale_unit.taxable_value, Decimal("2118.64"))
        self.assertEqual(sale_unit.cgst_amount, Decimal("190.68"))
        self.assertEqual(sale_unit.sgst_amount, Decimal("190.68"))
        self.assertEqual(sale_unit.igst_amount, Decimal("0.00"))
        self.assertEqual(sale_unit.total_tax, Decimal("381.36"))
        self.assertEqual(sale_unit.total_amount, Decimal("2500.00"))

    def test_sales_endpoints_never_return_money_without_a_money_section(self):
        created = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = created.data["id"]
        responses = [
            self.client.get(f"/api/sales/{sale_id}/"),
            self.client.get("/api/sales/"),
            self.client.get(
                f"/api/sales/scan/?serial_number={self.units[0].serial_number}"
            ),
        ]
        hidden_fields = {
            "price",
            "unit_selling_price_incl_tax",
            "mrp_snapshot",
            "hsn_code_snapshot",
            "gst_rate_snapshot",
            "taxable_value",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "total_tax",
            "total_amount",
            "payment_received_at",
        }
        for response in responses:
            self.assertEqual(response.status_code, 200, response.data)
            rows = response.data if isinstance(response.data, list) else [response.data]
            for sale in rows:
                self.assertTrue(hidden_fields.isdisjoint(sale))
                self.assertTrue(hidden_fields.isdisjoint(sale["units"][0]))

    def test_client_supplied_price_is_rejected(self):
        response = self.client.post(
            "/api/sales/",
            self.sale_payload(price="1.00"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("price", response.data)
        self.assertFalse(Sale.objects.exists())

    def test_sale_edit_cannot_change_price(self):
        created = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = created.data["id"]

        updated = self.client.patch(
            f"/api/sales/{sale_id}/",
            {"price": "1.00"},
            format="json",
        )

        self.assertEqual(updated.status_code, 400)
        self.assertIn("price", updated.data)
        self.assertEqual(Sale.objects.get(pk=sale_id).price, Decimal("2500.00"))

    def test_missing_active_price_rejects_sale_and_leaves_unit_available(self):
        with patch("sales.services.ProductPrice.objects.filter", return_value=ProductPrice.objects.none()):
            response = self.client.post("/api/sales/", self.sale_payload(), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Price not set for Sales test product", str(response.data))
        self.units[0].refresh_from_db()
        self.assertEqual(self.units[0].status, FinishedUnit.Status.IN_STOCK)
        self.assertFalse(Sale.objects.exists())

    def test_sale_time_mrp_check_rejects_invalid_active_price(self):
        ProductPrice.objects.create(
            product=self.product,
            effective_from=timezone.now() + timedelta(seconds=1),
            cost_snapshot=Decimal("1000.00"),
            margin_percent=Decimal("0.00"),
            selling_price_incl_tax=Decimal("3500.00"),
            mrp=Decimal("3000.00"),
            hsn_code="123456",
            gst_rate_percent=Decimal("18.00"),
            created_by=self.super_admin,
        )

        response = self.client.post("/api/sales/", self.sale_payload(), format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("no higher than its MRP", str(response.data))

    def test_legacy_sale_is_not_repriced_when_catalog_price_changes(self):
        legacy = Sale.objects.create(
            platform=self.tamirabot,
            created_by=self.super_admin,
            order_reference="LEGACY-ORDER",
            price=Decimal("99.00"),
            sale_date=date.today(),
        )
        sale_unit = SaleUnit.objects.create(
            sale=legacy,
            finished_unit=self.units[0],
        )
        ProductPrice.objects.create(
            product=self.product,
            effective_from=timezone.now() + timedelta(seconds=1),
            cost_snapshot=Decimal("1500.00"),
            margin_percent=Decimal("0.00"),
            selling_price_incl_tax=Decimal("3500.00"),
            mrp=Decimal("4000.00"),
            hsn_code="123456",
            gst_rate_percent=Decimal("18.00"),
            created_by=self.super_admin,
        )

        legacy.refresh_from_db()
        sale_unit.refresh_from_db()

        self.assertEqual(legacy.pricing_source, Sale.PricingSource.LEGACY)
        self.assertEqual(legacy.price, Decimal("99.00"))
        self.assertIsNone(sale_unit.unit_selling_price_incl_tax)
        self.assertIsNone(sale_unit.total_amount)

    def test_direct_sale_gstin_must_match_delivery_state(self):
        response = self.client.post(
            "/api/sales/",
            self.sale_payload(customer_gstin="29ABCDE1234F1Z5"),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("match the delivery state", str(response.data))

    def test_valid_customer_gstin_is_stored(self):
        response = self.client.post(
            "/api/sales/",
            self.sale_payload(
                unit_ids=[self.units[1].pk],
                customer_gstin="33ABCDE1234F1Z5",
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(
            Sale.objects.get(pk=response.data["id"]).customer_gstin,
            "33ABCDE1234F1Z5",
        )

    def test_delivery_state_is_required_for_every_sale(self):
        payload = self.sale_payload()
        del payload["customer_state_code"]

        response = self.client.post("/api/sales/", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("customer_state_code", response.data)

    def test_state_list_is_separate_from_super_admin_company_settings(self):
        states = self.client.get("/api/states/")
        settings = self.client.get("/api/company-profile/")

        self.assertEqual(states.status_code, 200)
        self.assertIn(
            {"state_code": "33", "name": "Tamil Nadu"},
            states.data,
        )
        self.assertEqual(settings.status_code, 403)

    def test_payment_received_date_comes_from_status_history(self):
        self.admin.dashboard_sections = ["payments"]
        self.admin.save(update_fields=("dashboard_sections",))
        created = self.client.post("/api/sales/", self.sale_payload(), format="json")
        sale_id = created.data["id"]
        for target in (
            Sale.Status.DELIVERED,
            Sale.Status.PAYMENT_PENDING,
            Sale.Status.PAYMENT_RECEIVED,
        ):
            response = self.client.post(
                f"/api/sales/{sale_id}/status/",
                {"status": target},
                format="json",
            )
            self.assertEqual(response.status_code, 200, response.data)

        received = SaleStatusHistory.objects.get(
            sale_id=sale_id, to_status=Sale.Status.PAYMENT_RECEIVED
        )
        self.assertEqual(
            parse_datetime(response.json()["payment_received_at"]),
            received.timestamp,
        )
        self.assertEqual(response.data["price"], "2500.00")

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

    def test_invoice_reports_missing_fields_without_consuming_a_number(self):
        self.tamirabot.issue_tamirabot_invoice = True
        self.tamirabot.save(update_fields=("issue_tamirabot_invoice",))
        sale_id = self.create_invoice_sale()

        self.client.force_authenticate(self.super_admin)
        response = self.client.post(f"/api/sales/{sale_id}/invoice/", {}, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("company GSTIN", response.data["missing_fields"])
        self.assertIn(": consumer care email", " ".join(response.data["missing_fields"]))
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(InvoiceSequence.objects.exists())

    def test_invoice_snapshots_supplier_buyer_product_tax_and_number(self):
        sale_id, invoice = self.issue_test_invoice()

        self.assertEqual(invoice["number"], "TB/26-27/000001")
        self.assertEqual(len(invoice["number"]), 15)
        self.assertEqual(invoice["supplier_snapshot"]["gstin"], "33ABCDE1234F1Z5")
        self.assertEqual(invoice["buyer_snapshot"]["state_name"], "Tamil Nadu")
        self.assertEqual(invoice["line_items"][0]["hsn_code"], "123456")
        self.assertEqual(invoice["line_items"][0]["manufactured_month_year"], timezone.localtime(
            self.units[0].pack_confirmation.created_at
        ).date().strftime("%B %Y"))
        self.assertEqual(invoice["tax_breakup"]["cgst_amount"], "190.68")
        self.assertEqual(invoice["tax_breakup"]["sgst_amount"], "190.68")
        self.assertEqual(invoice["tax_breakup"]["igst_amount"], "0.00")
        self.assertEqual(invoice["amount_in_words"], "Two thousand five hundred rupees only")

        profile = CompanyProfile.objects.get(pk=1)
        profile.legal_name = "Changed after issue"
        profile.save(update_fields=("legal_name",))
        self.product.generic_name = "Changed product description"
        self.product.save(update_fields=("generic_name",))
        detail = self.client.get(f"/api/sales/{sale_id}/invoice/")
        self.assertEqual(
            detail.data["invoice"]["supplier_snapshot"]["name"],
            "Tamirabot Technologies",
        )
        self.assertEqual(
            detail.data["invoice"]["line_items"][0]["description"],
            "Electronic controller",
        )

    def test_interstate_invoice_snapshots_igst_only(self):
        self.prepare_invoice_settings()
        other_state, _ = IndianState.objects.get_or_create(
            state_code="29",
            defaults={"name": "Karnataka"},
        )
        sale_id = self.create_invoice_sale(state=other_state)
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(f"/api/sales/{sale_id}/invoice/", {}, format="json")

        self.assertEqual(response.status_code, 201, response.data)
        line = response.data["invoice"]["line_items"][0]
        self.assertEqual(line["cgst_amount"], "0.00")
        self.assertEqual(line["sgst_amount"], "0.00")
        self.assertEqual(line["igst_amount"], "381.36")

    def test_invoice_numbering_is_sequential_and_resets_each_financial_year(self):
        self.prepare_invoice_settings()
        first_sale = self.create_invoice_sale(unit=self.units[0])
        second_sale = self.create_invoice_sale(unit=self.units[1])
        self.client.force_authenticate(self.super_admin)

        first = self.client.post(f"/api/sales/{first_sale}/invoice/", {}, format="json")
        second = self.client.post(f"/api/sales/{second_sale}/invoice/", {}, format="json")

        self.assertEqual(first.data["invoice"]["number"], "TB/26-27/000001")
        self.assertEqual(second.data["invoice"]["number"], "TB/26-27/000002")
        self.assertEqual(financial_year(date(2027, 3, 31)), "26-27")
        self.assertEqual(financial_year(date(2027, 4, 1)), "27-28")

    def test_invoice_prefix_that_exceeds_sixteen_characters_is_rejected(self):
        self.prepare_invoice_settings()
        profile = CompanyProfile.objects.get(pk=1)
        profile.invoice_prefix = "ABC"
        profile.save(update_fields=("invoice_prefix",))
        sale_id = self.create_invoice_sale(unit=self.units[0])
        self.client.force_authenticate(self.super_admin)
        accepted = self.client.post(f"/api/sales/{sale_id}/invoice/", {}, format="json")
        self.assertEqual(accepted.status_code, 201, accepted.data)
        self.assertEqual(len(accepted.data["invoice"]["number"]), 16)

        profile.invoice_prefix = "ABCD"
        profile.save(update_fields=("invoice_prefix",))
        second_sale_id = self.create_invoice_sale(unit=self.units[1])

        response = self.client.post(
            f"/api/sales/{second_sale_id}/invoice/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("number_prefix", response.data)
        self.assertEqual(Invoice.objects.count(), 1)
        self.assertEqual(InvoiceSequence.objects.get().last_number, 1)

    def test_invoice_sequence_restarts_for_new_financial_year(self):
        self.prepare_invoice_settings()
        first_sale = self.create_invoice_sale(unit=self.units[0])
        second_sale = self.create_invoice_sale(unit=self.units[1])
        self.client.force_authenticate(self.super_admin)

        with patch("sales.invoices.timezone.localdate", return_value=date(2027, 3, 31)):
            first = self.client.post(
                f"/api/sales/{first_sale}/invoice/",
                {},
                format="json",
            )
        with patch("sales.invoices.timezone.localdate", return_value=date(2027, 4, 1)):
            second = self.client.post(
                f"/api/sales/{second_sale}/invoice/",
                {},
                format="json",
            )

        self.assertEqual(first.data["invoice"]["number"], "TB/26-27/000001")
        self.assertEqual(second.data["invoice"]["number"], "TB/27-28/000001")
        self.assertEqual(
            set(InvoiceSequence.objects.values_list("financial_year", flat=True)),
            {"26-27", "27-28"},
        )

    def test_marketplace_never_generates_a_tamirabot_invoice(self):
        sale_id = self.create_invoice_sale(platform=self.amazon)
        self.client.force_authenticate(self.super_admin)

        response = self.client.post(f"/api/sales/{sale_id}/invoice/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["marketplace_invoice"])
        self.assertEqual(response.data["message"], "The marketplace issues the invoice.")
        self.assertFalse(Invoice.objects.exists())

    def test_invoice_is_immutable_and_pdf_download_is_audited(self):
        _, invoice_data = self.issue_test_invoice()
        invoice = Invoice.objects.get(pk=invoice_data["id"])
        original_number = invoice.number
        invoice.number = "CHANGED"
        with self.assertRaisesMessage(ValueError, "Issued invoices are immutable."):
            invoice.save()
        with self.assertRaisesMessage(ValueError, "Issued invoices cannot be deleted."):
            invoice.delete()
        invoice.refresh_from_db()
        self.assertEqual(invoice.number, original_number)

        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        pdf = self.client.get(f"/api/invoices/{invoice.pk}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF-1.4"))
        self.assertTrue(
            AuditLog.objects.filter(
                action="sales.invoice.downloaded",
                object_id=str(invoice.pk),
            ).exists()
        )

    def test_invoice_access_requires_super_admin_or_responsible_sales_admin(self):
        self.prepare_invoice_settings()
        sale_id = self.create_invoice_sale()
        self.client.force_authenticate(self.super_admin)
        issue = self.client.post(f"/api/sales/{sale_id}/invoice/", {}, format="json")
        invoice_id = issue.data["invoice"]["id"]

        self.admin.dashboard_sections = []
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        denied_detail = self.client.get(f"/api/sales/{sale_id}/invoice/")
        denied_pdf = self.client.get(f"/api/invoices/{invoice_id}/pdf/")
        self.assertEqual(denied_detail.status_code, 403)
        self.assertEqual(denied_pdf.status_code, 403)

        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        allowed = self.client.get(f"/api/invoices/{invoice_id}/pdf/")
        self.assertEqual(allowed.status_code, 200)

    def test_returned_invoiced_unit_gets_its_own_credit_note_series(self):
        sale_id, invoice_data = self.issue_test_invoice()
        serial_number = self.units[0].serial_number
        self.client.force_authenticate(self.admin)
        delivered = self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.DELIVERED},
            format="json",
        )
        self.assertEqual(delivered.status_code, 200)
        returned = self.client.post(
            f"/api/sales/{sale_id}/return/",
            {"serial_numbers": [serial_number], "reason": "Damaged"},
            format="json",
        )
        self.assertEqual(returned.status_code, 200)

        self.client.force_authenticate(self.super_admin)
        response = self.client.post(
            f"/api/sales/{sale_id}/credit-note/",
            {"serial_numbers": [serial_number], "reason": "Customer refund"},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["number"], "CN/26-27/000001")
        note = CreditNote.objects.get(pk=response.data["id"])
        self.assertEqual(note.invoice_id, invoice_data["id"])
        self.assertEqual(note.amounts["invoice_number"], invoice_data["number"])
        self.assertEqual(note.amounts["total_amount"], "2500.00")
        duplicate = self.client.post(
            f"/api/sales/{sale_id}/credit-note/",
            {"serial_numbers": [serial_number], "reason": "Duplicate"},
            format="json",
        )
        self.assertEqual(duplicate.status_code, 400)
        pdf = self.client.get(f"/api/credit-notes/{note.pk}/pdf/")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF-1.4"))
        self.assertTrue(
            AuditLog.objects.filter(
                action="sales.credit_note.downloaded",
                object_id=str(note.pk),
            ).exists()
        )

    def test_only_super_admin_can_issue_credit_notes(self):
        sale_id, _ = self.issue_test_invoice()
        serial_number = self.units[0].serial_number
        self.client.force_authenticate(self.admin)
        self.client.post(
            f"/api/sales/{sale_id}/status/",
            {"status": Sale.Status.DELIVERED},
            format="json",
        )
        self.client.post(
            f"/api/sales/{sale_id}/return/",
            {"serial_numbers": [serial_number], "reason": "Damaged"},
            format="json",
        )

        denied = self.client.post(
            f"/api/sales/{sale_id}/credit-note/",
            {"serial_numbers": [serial_number], "reason": "Refund"},
            format="json",
        )

        self.assertEqual(denied.status_code, 403)
        self.assertFalse(CreditNote.objects.exists())

    def create_label_template(self, **overrides):
        values = {
            "platform": self.tamirabot,
            "name": "Tamirabot 100 x 150 mm",
            "width_mm": 100,
            "height_mm": 150,
            "is_default": True,
        }
        values.update(overrides)
        return LabelTemplate.objects.create(**values)

    def label_barcode_png(self, barcode_value):
        image = Image.new("RGB", (max(150, len(barcode_value) * 12), 40), "white")
        draw = ImageDraw.Draw(image)
        for x in range(0, image.width, 6):
            draw.rectangle((x, 0, x + 2, 39), fill="black")
        output = BytesIO()
        image.save(output, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(output.getvalue()).decode('ascii')}"

    def test_sale_label_uses_sale_and_company_snapshots_and_pack_date(self):
        self.prepare_invoice_settings()
        template = self.create_label_template()
        sale_id = self.create_invoice_sale()

        response = self.client.get(f"/api/sales/{sale_id}/labels/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["generated"])
        self.assertTrue(response.data["can_print"])
        self.assertEqual(response.data["template"]["id"], template.pk)
        label = response.data["labels"][0]
        self.assertEqual(label["customer_name"], "A Tamirabot customer")
        self.assertEqual(
            label["delivery_address"],
            "12 Workshop Road, Chennai, Tamil Nadu, 600001",
        )
        self.assertEqual(label["customer_phone"], "9000000000")
        self.assertEqual(label["order_reference"], "ORDER-123")
        self.assertEqual(label["sale_date"], date.today().isoformat())
        self.assertEqual(
            label["company_return_address"],
            "Tamirabot, 12 Workshop Road, Chennai, Tamil Nadu, 600001",
        )
        self.assertEqual(label["product_name"], "Sales test product")
        self.assertEqual(label["product_generic_name"], "Electronic controller")
        self.assertEqual(label["product_sku"], "SALES-SKU")
        self.assertEqual(label["revision_number"], self.revision.revision_number)
        self.assertEqual(label["serial_number"], self.units[0].serial_number)
        self.assertEqual(label["barcode_value"], self.units[0].barcode_value)
        self.assertEqual(label["mrp"], "3000.00")
        self.assertEqual(label["net_quantity_text"], "1 Unit")
        self.assertEqual(
            label["manufactured_month_year"],
            timezone.localtime(self.units[0].pack_confirmation.created_at).strftime(
                "%B %Y"
            ),
        )
        self.assertEqual(label["packer_manufacturer_name"], "Tamirabot Works")
        self.assertEqual(label["packer_address"], "12 Workshop Road, Chennai")
        self.assertEqual(label["country_of_origin"], "India")
        self.assertEqual(label["consumer_care_name"], "Tamirabot Care")
        self.assertEqual(label["consumer_care_address"], "12 Workshop Road, Chennai")
        self.assertEqual(label["consumer_care_phone"], "9000000000")
        self.assertEqual(label["consumer_care_email"], "care@example.test")

    def test_label_print_is_blocked_with_each_missing_legal_declaration(self):
        self.prepare_invoice_settings()
        self.create_label_template()
        sale_id = self.create_invoice_sale()
        sale_unit = SaleUnit.objects.get(sale_id=sale_id)
        profile = CompanyProfile.objects.get(pk=1)
        required_fields = (
            ("mrp_snapshot", "MRP"),
            ("product.net_quantity_text", "Net quantity"),
            ("profile.packer_manufacturer_name", "Packer / manufacturer name"),
            ("profile.packer_address", "Packer / manufacturer address"),
            ("profile.country_of_origin", "Country of origin"),
            ("profile.consumer_care_name", "Consumer care name"),
            ("profile.consumer_care_address", "Consumer care address"),
            ("profile.consumer_care_phone", "Consumer care phone"),
            ("profile.consumer_care_email", "Consumer care email"),
            ("profile.address_line1", "Company return address"),
        )
        self.client.force_authenticate(self.super_admin)
        for field_path, message in required_fields:
            with self.subTest(field=field_path):
                if field_path == "mrp_snapshot":
                    SaleUnit.objects.filter(pk=sale_unit.pk).update(mrp_snapshot=None)
                elif field_path == "product.net_quantity_text":
                    Product.objects.filter(pk=self.product.pk).update(net_quantity_text="")
                else:
                    field_name = field_path.removeprefix("profile.")
                    setattr(profile, field_name, "")
                    profile.save(update_fields=(field_name,))

                preview = self.client.get(f"/api/sales/{sale_id}/labels/")
                self.assertEqual(preview.status_code, 200, preview.data)
                self.assertFalse(preview.data["can_print"])
                self.assertIn(message, preview.data["labels"][0]["missing_fields"])
                blocked = self.client.post(
                    f"/api/sales/{sale_id}/labels/{sale_unit.pk}/print/",
                    {},
                    format="json",
                )
                self.assertEqual(blocked.status_code, 400, blocked.data)
                self.assertIn(message, str(blocked.data))
                self.assertFalse(LabelPrintLog.objects.exists())

                if field_path == "mrp_snapshot":
                    SaleUnit.objects.filter(pk=sale_unit.pk).update(
                        mrp_snapshot=Decimal("3000.00")
                    )
                elif field_path == "product.net_quantity_text":
                    Product.objects.filter(pk=self.product.pk).update(
                        net_quantity_text="1 Unit"
                    )
                else:
                    field_name = field_path.removeprefix("profile.")
                    restored = {
                        "packer_manufacturer_name": "Tamirabot Works",
                        "packer_address": "12 Workshop Road, Chennai",
                        "country_of_origin": "India",
                        "consumer_care_name": "Tamirabot Care",
                        "consumer_care_address": "12 Workshop Road, Chennai",
                        "consumer_care_phone": "9000000000",
                        "consumer_care_email": "care@example.test",
                        "address_line1": "12 Workshop Road",
                    }[field_name]
                    setattr(profile, field_name, restored)
                    profile.save(update_fields=(field_name,))

    def test_marketplace_sale_does_not_get_a_generated_label(self):
        sale_id = self.create_invoice_sale(platform=self.amazon)

        response = self.client.get(f"/api/sales/{sale_id}/labels/")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["generated"])
        self.assertIn("supplies its own label", response.data["message"])
        self.assertEqual(response.data["labels"], [])
        self.assertFalse(LabelPrintLog.objects.exists())

    def test_label_print_logs_first_print_and_reprints_with_audit(self):
        self.prepare_invoice_settings()
        template = self.create_label_template()
        sale_id = self.create_invoice_sale()
        sale_unit = SaleUnit.objects.get(sale_id=sale_id)
        path = f"/api/sales/{sale_id}/labels/{sale_unit.pk}/print/"

        first = self.client.post(path, {}, format="json")
        second = self.client.post(path, {}, format="json")

        self.assertEqual(first.status_code, 201, first.data)
        self.assertFalse(first.data["is_reprint"])
        self.assertEqual(second.status_code, 201, second.data)
        self.assertTrue(second.data["is_reprint"])
        self.assertEqual(
            list(
                LabelPrintLog.objects.filter(sale_unit=sale_unit)
                .order_by("pk")
                .values_list("template_id", "is_reprint")
            ),
            [(template.pk, False), (template.pk, True)],
        )
        self.assertTrue(AuditLog.objects.filter(action="sales.label.printed").exists())
        self.assertTrue(AuditLog.objects.filter(action="sales.label.reprinted").exists())

    def test_label_pdf_download_is_gated_audited_and_embeds_generated_barcode(self):
        self.prepare_invoice_settings()
        self.create_label_template()
        sale_id = self.create_invoice_sale()
        sale_unit = SaleUnit.objects.select_related("finished_unit").get(sale_id=sale_id)
        payload = {
            "barcodes": [{
                "sale_unit_id": sale_unit.pk,
                "barcode_value": sale_unit.finished_unit.barcode_value,
                "image": self.label_barcode_png(sale_unit.finished_unit.barcode_value),
            }]
        }

        missing_barcode = self.client.post(
            f"/api/sales/{sale_id}/labels/pdf/",
            {"barcodes": []},
            format="json",
        )
        self.assertEqual(missing_barcode.status_code, 400, missing_barcode.data)
        self.assertFalse(LabelPrintLog.objects.exists())

        first = self.client.post(
            f"/api/sales/{sale_id}/labels/pdf/",
            payload,
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.content[:200])
        self.assertEqual(first["Content-Type"], "application/pdf")
        self.assertTrue(first.content.startswith(b"%PDF-"))
        self.assertEqual(
            first["Content-Disposition"],
            f'attachment; filename="SALE-{sale_id}-labels.pdf"',
        )
        first_result = json.loads(first["X-Label-Print-Results"])
        self.assertEqual(first_result[0]["serial_number"], sale_unit.finished_unit.serial_number)
        self.assertFalse(first_result[0]["is_reprint"])

        second = self.client.post(
            f"/api/sales/{sale_id}/labels/pdf/",
            payload,
            format="json",
        )
        self.assertEqual(second.status_code, 200)
        self.assertTrue(json.loads(second["X-Label-Print-Results"])[0]["is_reprint"])
        self.assertEqual(
            LabelPrintLog.objects.filter(sale_unit=sale_unit).count(),
            2,
        )
        self.assertTrue(AuditLog.objects.filter(action="sales.label.printed").exists())
        self.assertTrue(AuditLog.objects.filter(action="sales.label.reprinted").exists())

    def test_label_template_management_is_super_admin_only_and_audited(self):
        payload = {
            "platform": self.tamirabot.pk,
            "name": "Primary label",
            "width_mm": 100,
            "height_mm": 150,
            "field_toggles": {
                "show_company_trade_name": True,
                "show_company_logo": False,
                "show_product_generic_name": True,
                "show_order_reference": True,
                "show_invoice_number": True,
            },
            "is_default": True,
        }
        self.client.force_authenticate(self.admin)
        denied_list = self.client.get("/api/label-templates/")
        denied_create = self.client.post(
            "/api/label-templates/",
            payload,
            format="json",
        )
        self.assertEqual(denied_list.status_code, 403)
        self.assertEqual(denied_create.status_code, 403)

        self.client.force_authenticate(self.super_admin)
        created = self.client.post("/api/label-templates/", payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["width_mm"], 100)
        self.assertEqual(created.data["height_mm"], 150)
        template_id = created.data["id"]
        second = self.client.post(
            "/api/label-templates/",
            {**payload, "name": "A6 label", "width_mm": 105, "height_mm": 148},
            format="json",
        )
        self.assertEqual(second.status_code, 201, second.data)
        self.assertFalse(
            LabelTemplate.objects.get(pk=template_id).is_default
        )
        self.assertTrue(
            LabelTemplate.objects.get(pk=second.data["id"]).is_default
        )
        invalid = self.client.patch(
            f"/api/label-templates/{second.data['id']}/",
            {"width_mm": 99, "height_mm": 150},
            format="json",
        )
        self.assertEqual(invalid.status_code, 400)
        legal_toggle = self.client.patch(
            f"/api/label-templates/{second.data['id']}/",
            {"field_toggles": {"show_legal_block": False}},
            format="json",
        )
        self.assertEqual(legal_toggle.status_code, 400)
        self.assertEqual(
            AuditLog.objects.filter(action="sales.label_template.created").count(),
            2,
        )

    def test_label_endpoints_require_sale_money_access(self):
        self.prepare_invoice_settings()
        self.create_label_template()
        sale_id = self.create_invoice_sale()
        self.admin.dashboard_sections = []
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)

        preview = self.client.get(f"/api/sales/{sale_id}/labels/")
        self.assertEqual(preview.status_code, 403)
        pdf_denied = self.client.post(
            f"/api/sales/{sale_id}/labels/pdf/",
            {},
            format="json",
        )
        self.assertEqual(pdf_denied.status_code, 403)

        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.super_admin)
        foreign_sale = self.create_invoice_sale(unit=self.units[1])
        Sale.objects.filter(pk=foreign_sale).update(created_by=self.super_admin)
        self.client.force_authenticate(self.admin)
        denied = self.client.get(f"/api/sales/{foreign_sale}/labels/")
        self.assertEqual(denied.status_code, 403)
        foreign_pdf_denied = self.client.post(
            f"/api/sales/{foreign_sale}/labels/pdf/",
            {},
            format="json",
        )
        self.assertEqual(foreign_pdf_denied.status_code, 403)


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
        state, _ = IndianState.objects.get_or_create(
            state_code="33",
            defaults={"name": "Tamil Nadu"},
        )
        CompanyProfile.objects.update_or_create(pk=1, defaults={"state_code": state})
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
            ProductPrice.objects.create(
                product=product,
                cost_snapshot=Decimal("12.00"),
                margin_percent=Decimal("0.00"),
                selling_price_incl_tax=Decimal("12.00"),
                mrp=Decimal("15.00"),
                hsn_code="1234",
                gst_rate_percent=Decimal("0.00"),
                created_by=supervisor,
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
                        "customer_state_code": state,
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


@skipUnless(
    connection.features.has_select_for_update,
    "Concurrent invoice tests require PostgreSQL or another SELECT FOR UPDATE database.",
)
class InvoiceNumberingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_invoices_receive_gapless_numbers(self):
        supervisor = User.objects.create_user(
            username="invoice-race-super",
            email="invoice-race-super@example.test",
            password="test-password",
            role=User.Role.SUPER_ADMIN,
        )
        admin = User.objects.create_user(
            username="invoice-race-admin",
            email="invoice-race-admin@example.test",
            password="test-password",
            role=User.Role.ADMIN,
        )
        state, _ = IndianState.objects.get_or_create(
            state_code="33",
            defaults={"name": "Tamil Nadu"},
        )
        profile, _ = CompanyProfile.objects.update_or_create(
            pk=1,
            defaults={
                "legal_name": "Tamirabot Technologies",
                "address_line1": "12 Workshop Road",
                "city": "Chennai",
                "state_code": state,
                "pincode": "600001",
                "gstin": "33ABCDE1234F1Z5",
                "packer_manufacturer_name": "Tamirabot Works",
                "packer_address": "12 Workshop Road, Chennai",
                "country_of_origin": "India",
                "consumer_care_name": "Tamirabot Care",
                "consumer_care_address": "12 Workshop Road, Chennai",
                "consumer_care_phone": "9000000000",
                "consumer_care_email": "care@example.test",
                "invoice_prefix": "TB",
                "credit_note_prefix": "CN",
                "authorised_signatory_name": "Authorised Person",
            },
        )
        component = Component.objects.create(
            sku="INVOICE-RACE-PART",
            name="Invoice race part",
            unit="pcs",
        )
        with transaction.atomic():
            product = Product.objects.create(
                product_number="INVOICE-RACE-PRODUCT",
                name="Invoice race product",
                generic_name="Electronic controller",
                net_quantity_text="1 Unit",
                sku="INVOICE-RACE-SKU",
            )
            ProductPrice.objects.create(
                product=product,
                cost_snapshot=Decimal("12.00"),
                margin_percent=Decimal("0.00"),
                selling_price_incl_tax=Decimal("12.00"),
                mrp=Decimal("15.00"),
                hsn_code="1234",
                gst_rate_percent=Decimal("0.00"),
                created_by=supervisor,
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
        start_work_order(work_order_id=order.pk, actor=admin)
        confirmation = confirm_packing(
            work_order_id=order.pk,
            quantity=2,
            idempotency_key="invoice-race-pack",
            actor=admin,
        )
        units = list(
            FinishedUnit.objects.filter(pack_confirmation=confirmation).order_by("pk")
        )
        platform = SalesPlatform.objects.create(
            name="Invoice race platform",
            label_template_type=SalesPlatform.LabelTemplateType.TAMIRABOT,
            issue_tamirabot_invoice=True,
        )
        sale_ids = []
        for unit in units:
            sale = create_sale(
                values={
                    "platform": platform,
                    "unit_ids": [unit.pk],
                    "customer_state_code": state,
                    "sale_date": date.today(),
                    "customer_name": "Test customer",
                    "customer_phone": "9000000000",
                    "billing_address": "12 Workshop Road",
                    "delivery_address": "12 Workshop Road",
                    "shipping_name": "Test customer",
                    "shipping_address": "12 Workshop Road",
                },
                actor=admin,
            )
            sale_ids.append(sale.pk)

        barrier = Barrier(2)

        def issue_racing_invoice(sale_id):
            close_old_connections()
            try:
                actor = User.objects.get(pk=supervisor.pk)
                barrier.wait(timeout=10)
                invoice, created = issue_invoice(sale_id=sale_id, actor=actor)
                return invoice.number, created
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(issue_racing_invoice, sale_ids))

        self.assertEqual(sorted(number for number, _ in results), [
            "TB/26-27/000001",
            "TB/26-27/000002",
        ])
        self.assertTrue(all(created for _, created in results))
        sequence = InvoiceSequence.objects.get(financial_year="26-27")
        self.assertEqual(sequence.last_number, 2)
        self.assertEqual(Invoice.objects.count(), 2)


class SaleTaxTests(SimpleTestCase):
    def test_invoice_amount_words_use_shared_half_up_rounding(self):
        self.assertEqual(
            amount_in_words(Decimal("1.005")),
            "One rupee and one paisa only",
        )

    def test_taxable_value_uses_half_up_rounding(self):
        amounts = calculate_sale_tax(
            price_incl_tax=Decimal("100.00"),
            gst_rate_percent=Decimal("18.00"),
            place_of_supply_state_code="33",
            company_state_code="29",
        )

        self.assertEqual(amounts["taxable_value"], Decimal("84.75"))
        self.assertEqual(amounts["total_tax"], Decimal("15.25"))

    def test_intrastate_odd_paise_goes_to_cgst_and_sgst_gets_remainder(self):
        amounts = calculate_sale_tax(
            price_incl_tax=Decimal("101.00"),
            gst_rate_percent=Decimal("5.00"),
            place_of_supply_state_code="33",
            company_state_code="33",
        )

        self.assertEqual(amounts["total_tax"], Decimal("4.81"))
        self.assertEqual(amounts["cgst_amount"], Decimal("2.41"))
        self.assertEqual(amounts["sgst_amount"], Decimal("2.40"))
        self.assertEqual(amounts["igst_amount"], Decimal("0.00"))

    def test_interstate_sale_uses_igst_only(self):
        amounts = calculate_sale_tax(
            price_incl_tax=Decimal("101.00"),
            gst_rate_percent=Decimal("5.00"),
            place_of_supply_state_code="29",
            company_state_code="33",
        )

        self.assertEqual(amounts["cgst_amount"], Decimal("0.00"))
        self.assertEqual(amounts["sgst_amount"], Decimal("0.00"))
        self.assertEqual(amounts["igst_amount"], Decimal("4.81"))
