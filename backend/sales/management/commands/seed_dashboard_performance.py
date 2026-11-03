from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from accounts.models import User
from catalog.models import BOMLine, Component, Product, ProductRevision
from sales.models import Sale, SaleUnit, SalesPlatform
from workorders.models import FinishedUnit, PackConfirmation, WorkOrder


class Command(BaseCommand):
    help = "Seed a development PostgreSQL database with data for dashboard performance testing."

    def add_arguments(self, parser):
        parser.add_argument("--super-admin-username", required=True)
        parser.add_argument("--admin-username", required=True)
        parser.add_argument("--count", type=int, default=100000)

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Performance seed data can only be created when DEBUG is enabled.")
        if connection.vendor != "postgresql":
            raise CommandError("Dashboard performance data requires PostgreSQL.")
        count = options["count"]
        if count < 1 or count > 100000:
            raise CommandError("--count must be between 1 and 100000.")

        try:
            owner = User.objects.get(
                username=options["super_admin_username"],
                role=User.Role.SUPER_ADMIN,
                is_active=True,
            )
            admin = User.objects.get(
                username=options["admin_username"],
                role=User.Role.ADMIN,
                is_active=True,
            )
        except User.DoesNotExist as exc:
            raise CommandError("Provide existing active Super Admin and Admin usernames.") from exc

        run_id = uuid4().hex[:8].upper()
        today = timezone.localdate()
        with transaction.atomic():
            product = Product.objects.create(
                product_number=f"DASH-PERF-{run_id}",
                name=f"Dashboard performance fixture {run_id}",
                sku=f"DASH-PERF-SKU-{run_id}",
            )
            revision = ProductRevision.create_next(product=product)
            component = Component.objects.create(
                sku=f"DASH-PERF-COMP-{run_id}",
                name=f"Dashboard performance component {run_id}",
                unit="unit",
            )
            BOMLine.objects.create(
                product_revision=revision,
                component=component,
                quantity_per_unit=1,
            )
            platform = SalesPlatform.objects.create(name=f"Dashboard performance {run_id}")
            work_order = WorkOrder.objects.create(
                product=product,
                product_revision=revision,
                quantity=count,
                priority=WorkOrder.Priority.MEDIUM,
                due_date=today,
                status=WorkOrder.Status.IN_PROGRESS,
                assigned_admin=admin,
                created_by=owner,
            )
            confirmation = PackConfirmation.objects.create(
                work_order=work_order,
                idempotency_key=f"dashboard-perf-{run_id}",
                quantity=count,
                confirmed_by=admin,
            )
            for start in range(0, count, 2000):
                end = min(start + 2000, count)
                units = FinishedUnit.objects.bulk_create([
                    FinishedUnit(
                        serial_number=f"DASH-{run_id}-{index:06d}",
                        barcode_value=f"DBP-{run_id}-{index:06d}",
                        product=product,
                        product_revision=revision,
                        work_order=work_order,
                        pack_confirmation=confirmation,
                        status=FinishedUnit.Status.SOLD,
                    )
                    for index in range(start, end)
                ], batch_size=2000)
                sales = Sale.objects.bulk_create([
                    Sale(
                        platform=platform,
                        created_by=admin,
                        status=Sale.Status.READY_TO_SHIP,
                        price=Decimal("25.00"),
                        sale_date=today,
                    )
                    for _ in units
                ], batch_size=2000)
                SaleUnit.objects.bulk_create([
                    SaleUnit(sale=sale, finished_unit=unit)
                    for sale, unit in zip(sales, units)
                ], batch_size=2000)

        self.stdout.write(self.style.SUCCESS(
            f"Created {count:,} sales for product {product.product_number} in run {run_id}."
        ))
        self.stdout.write("Record this fixture identifier to coordinate a targeted cleanup later.")
