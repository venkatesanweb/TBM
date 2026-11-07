from datetime import timedelta
from decimal import Decimal
from time import perf_counter

from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from catalog.models import BOMLine, Component, Product, ProductRevision
from sales.models import Sale, SaleReturn, SaleStatusHistory, SaleUnit, SalesPlatform
from workorders.models import FinishedUnit, PackConfirmation, WorkOrder


class DashboardSectionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="dashboard-owner-test",
            email="dashboard-owner-test@example.test",
            password="dashboard-test-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        self.admin = User.objects.create_user(
            username="dashboard-admin-test",
            email="dashboard-admin-test@example.test",
            password="dashboard-test-password-123",
            role=User.Role.ADMIN,
        )
        self.other_admin = User.objects.create_user(
            username="dashboard-other-test",
            email="dashboard-other-test@example.test",
            password="dashboard-test-password-123",
            role=User.Role.ADMIN,
        )
        self.product = Product.objects.create(
            product_number="DASH-P1",
            name="Dashboard Product",
            sku="DASH-SKU1",
        )
        self.revision = ProductRevision.create_next(product=self.product)
        component = Component.objects.create(
            sku="DASH-COMPONENT",
            name="Dashboard component",
            unit="unit",
        )
        BOMLine.objects.create(
            product_revision=self.revision,
            component=component,
            quantity_per_unit=1,
        )
        self.platform = SalesPlatform.objects.create(name="Dashboard Test Platform")
        self.work_order = WorkOrder.objects.create(
            product=self.product,
            product_revision=self.revision,
            quantity=3,
            priority=WorkOrder.Priority.HIGH,
            due_date=timezone.localdate() + timedelta(days=2),
            status=WorkOrder.Status.IN_PROGRESS,
            assigned_admin=self.admin,
            created_by=self.owner,
        )
        self.other_work_order = WorkOrder.objects.create(
            product=self.product,
            product_revision=self.revision,
            quantity=2,
            priority=WorkOrder.Priority.LOW,
            due_date=timezone.localdate() + timedelta(days=4),
            status=WorkOrder.Status.RESERVED,
            assigned_admin=self.other_admin,
            created_by=self.owner,
        )
        self.confirmation = PackConfirmation.objects.create(
            work_order=self.work_order,
            idempotency_key="dashboard-test-pack",
            quantity=3,
            confirmed_by=self.admin,
        )
        self.units = [
            FinishedUnit.objects.create(
                serial_number=f"DASH-SERIAL-{index}",
                barcode_value=f"DASH-CODE-{index}",
                product=self.product,
                product_revision=self.revision,
                work_order=self.work_order,
                pack_confirmation=self.confirmation,
                status=FinishedUnit.Status.SOLD,
            )
            for index in range(1, 3)
        ]
        self.sale = Sale.objects.create(
            platform=self.platform,
            created_by=self.admin,
            status=Sale.Status.PAYMENT_PENDING,
            order_reference="DASH-ORDER-1",
            price=Decimal("1000.00"),
            sale_date=timezone.localdate(),
        )
        self.sale_units = [SaleUnit.objects.create(sale=self.sale, finished_unit=unit) for unit in self.units]
        self.return_record = SaleReturn.objects.create(
            sale_unit=self.sale_units[0],
            reason="Damaged",
        )
        SaleStatusHistory.objects.create(
            sale=self.sale,
            from_status=Sale.Status.DELIVERED,
            to_status=Sale.Status.PAYMENT_PENDING,
            actor=self.admin,
            timestamp=timezone.now() - timedelta(days=10),
        )
        self.client = APIClient()

    def test_unassigned_admin_gets_forbidden_for_section_and_report(self):
        self.client.force_authenticate(self.admin)
        dashboard = self.client.get("/api/dashboard/sales-overview/")
        report = self.client.get("/api/dashboard/reports/sales/")
        self.assertEqual(dashboard.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(report.status_code, status.HTTP_403_FORBIDDEN)

    def test_production_admin_sees_only_assigned_work_orders_without_money(self):
        self.admin.dashboard_sections = ["production"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/dashboard/production/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row["id"] for row in response.data["open_work_orders"]]
        self.assertEqual(ids, [self.work_order.pk])
        self.assertNotIn("price", response.data)
        self.assertNotIn("revenue", response.data)
        self.assertNotIn("amount", response.data)

    def test_sales_admin_cannot_read_raw_material_quantities(self):
        self.admin.dashboard_sections = ["sales_overview", "payments", "returns", "finished_goods"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/dashboard/inventory/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_revoking_a_section_takes_effect_on_the_next_request(self):
        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        allowed = self.client.get("/api/dashboard/sales-overview/")
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.admin.dashboard_sections = []
        self.admin.save(update_fields=("dashboard_sections",))
        revoked = self.client.get("/api/dashboard/sales-overview/")
        self.assertEqual(revoked.status_code, status.HTTP_403_FORBIDDEN)

    def test_export_requires_permission_and_successful_export_is_audited(self):
        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        denied = self.client.get("/api/dashboard/reports/sales/?export_format=xlsx")
        self.assertEqual(
            denied.status_code,
            status.HTTP_403_FORBIDDEN,
            denied.data,
        )
        self.admin.can_export_dashboard_reports = True
        self.admin.save(update_fields=("can_export_dashboard_reports",))
        exported = self.client.get("/api/dashboard/reports/sales/?export_format=xlsx")
        self.assertEqual(exported.status_code, status.HTTP_200_OK)
        self.assertEqual(
            AuditLog.objects.filter(
                actor=self.admin,
                action="dashboard.report.exported",
            ).count(),
            1,
        )
        self.assertTrue(exported["Content-Type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ))
        pdf_export = self.client.get("/api/dashboard/reports/sales/?export_format=pdf")
        self.assertEqual(pdf_export.status_code, status.HTTP_200_OK)
        self.assertEqual(pdf_export["Content-Type"], "application/pdf")
        self.assertTrue(pdf_export.content.startswith(b"%PDF-1.4"))

    def test_sales_metrics_exclude_returned_unit_revenue_and_match_report(self):
        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        dashboard = self.client.get("/api/dashboard/sales-overview/")
        report = self.client.get("/api/dashboard/reports/sales/?limit=10")
        self.assertEqual(dashboard.status_code, status.HTTP_200_OK, dashboard.data)
        self.assertEqual(report.status_code, status.HTTP_200_OK, report.data)
        self.assertEqual(dashboard.data["units_sold"], 2)
        self.assertEqual(Decimal(dashboard.data["revenue"]), Decimal("500"))
        self.assertEqual(report.data["total"], 2)
        self.assertEqual(
            sum(Decimal(row[8]) for row in report.data["rows"]),
            Decimal(dashboard.data["revenue"]),
        )
        self.assertEqual(report.data["limit"], 10)

    def test_price_list_dashboard_uses_unit_snapshots_for_returned_sales(self):
        self.sale.pricing_source = Sale.PricingSource.PRICE_LIST
        self.sale.save(update_fields=("pricing_source",))
        self.sale_units[0].total_amount = Decimal("300.00")
        self.sale_units[0].save(update_fields=("total_amount",))
        self.sale_units[1].total_amount = Decimal("700.00")
        self.sale_units[1].save(update_fields=("total_amount",))
        self.admin.dashboard_sections = ["sales_overview", "payments"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)

        dashboard = self.client.get("/api/dashboard/sales-overview/")
        report = self.client.get("/api/dashboard/reports/sales/?limit=10")
        payments = self.client.get("/api/dashboard/payments/")

        self.assertEqual(dashboard.status_code, status.HTTP_200_OK, dashboard.data)
        self.assertEqual(Decimal(dashboard.data["revenue"]), Decimal("700.00"))
        self.assertEqual(report.status_code, status.HTTP_200_OK, report.data)
        self.assertEqual(
            sum(Decimal(row[8]) for row in report.data["rows"]),
            Decimal("700.00"),
        )
        self.assertEqual(payments.status_code, status.HTTP_200_OK, payments.data)
        self.assertEqual(
            Decimal(payments.data["outstanding_amount"]),
            Decimal("700.00"),
        )

    def test_invalid_custom_range_is_rejected(self):
        self.admin.dashboard_sections = ["sales_overview"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/dashboard/sales-overview/?range=custom&date_from=2026-10-10&date_to=2026-10-01")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_rate_uses_returned_units_divided_by_units_sold(self):
        self.admin.dashboard_sections = ["returns"]
        self.admin.save(update_fields=("dashboard_sections",))
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/dashboard/returns/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["units_sold"], 2)
        self.assertEqual(response.data["returned_units"], 1)
        self.assertEqual(Decimal(response.data["return_rate"]), Decimal("0.5"))

    def test_pending_payment_total_counts_only_payment_pending_status(self):
        self.admin.dashboard_sections = ["payments"]
        self.admin.save(update_fields=("dashboard_sections",))
        delivered_unit = FinishedUnit.objects.create(
            serial_number="DASH-SERIAL-DELIVERED",
            barcode_value="DASH-CODE-DELIVERED",
            product=self.product,
            product_revision=self.revision,
            work_order=self.work_order,
            pack_confirmation=self.confirmation,
            status=FinishedUnit.Status.SOLD,
        )
        delivered_sale = Sale.objects.create(
            platform=self.platform,
            created_by=self.admin,
            status=Sale.Status.DELIVERED,
            price=Decimal("700.00"),
            sale_date=timezone.localdate(),
        )
        SaleUnit.objects.create(sale=delivered_sale, finished_unit=delivered_unit)
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/dashboard/payments/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.data["outstanding_amount"]), Decimal("500"))

    def test_super_admin_can_read_every_section(self):
        self.client.force_authenticate(self.owner)
        for path in (
            "sales-overview",
            "payments",
            "returns",
            "inventory",
            "finished-goods",
            "production",
        ):
            with self.subTest(path=path):
                response = self.client.get(f"/api/dashboard/{path}/")
                self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)


class DashboardPerformanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="dashboard-performance-owner",
            email="dashboard-performance-owner@example.test",
            password="dashboard-test-password-123",
            role=User.Role.SUPER_ADMIN,
        )
        cls.admin = User.objects.create_user(
            username="dashboard-performance-admin",
            email="dashboard-performance-admin@example.test",
            password="dashboard-test-password-123",
            role=User.Role.ADMIN,
        )
        product = Product.objects.create(
            product_number="PERF-PRODUCT",
            name="Performance Product",
            sku="PERF-SKU",
        )
        revision = ProductRevision.create_next(product=product)
        component = Component.objects.create(
            sku="PERF-COMPONENT",
            name="Performance component",
            unit="unit",
        )
        BOMLine.objects.create(
            product_revision=revision,
            component=component,
            quantity_per_unit=1,
        )
        platform = SalesPlatform.objects.create(name="Performance Platform")
        order = WorkOrder.objects.create(
            product=product,
            product_revision=revision,
            quantity=100000,
            priority=WorkOrder.Priority.MEDIUM,
            due_date=timezone.localdate() + timedelta(days=3),
            status=WorkOrder.Status.IN_PROGRESS,
            assigned_admin=cls.admin,
            created_by=cls.owner,
        )
        confirmation = PackConfirmation.objects.create(
            work_order=order,
            idempotency_key="dashboard-100k-pack",
            quantity=100000,
            confirmed_by=cls.admin,
        )
        for start in range(0, 100000, 2000):
            FinishedUnit.objects.bulk_create([
                FinishedUnit(
                    serial_number=f"PERF-{index:06d}",
                    barcode_value=f"PERFCODE-{index:06d}",
                    product=product,
                    product_revision=revision,
                    work_order=order,
                    pack_confirmation=confirmation,
                    status=FinishedUnit.Status.SOLD,
                )
                for index in range(start, start + 2000)
            ], batch_size=2000)
        unit_ids = list(FinishedUnit.objects.order_by("pk").values_list("pk", flat=True))
        today = timezone.localdate()
        for start in range(0, 100000, 2000):
            created = Sale.objects.bulk_create([
                Sale(
                platform=platform,
                created_by=cls.admin,
                status=Sale.Status.READY_TO_SHIP,
                price=Decimal("25.00"),
                sale_date=today,
                )
                for _ in range(2000)
            ], batch_size=2000)
            SaleUnit.objects.bulk_create([
                SaleUnit(sale_id=sale.pk, finished_unit_id=unit_id)
                for sale, unit_id in zip(created, unit_ids[start:start + 2000])
            ], batch_size=2000)

    def test_dashboard_endpoints_are_under_two_seconds_with_100000_sales(self):
        client = APIClient()
        client.force_authenticate(self.owner)
        paths = (
            "sales-overview",
            "payments",
            "returns",
            "inventory",
            "finished-goods",
            "production",
        )
        timings = {}
        for path in paths:
            start = perf_counter()
            response = client.get(f"/api/dashboard/{path}/?range=today")
            timings[path] = perf_counter() - start
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertLess(timings[path], 2.0, f"{path} took {timings[path]:.3f}s")
        self.assertEqual(Sale.objects.count(), 100000)
        print("Dashboard timings with 100,000 sales: " + ", ".join(
            f"{path}={duration:.3f}s" for path, duration in timings.items()
        ))
