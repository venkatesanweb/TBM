from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from catalog.models import Component, Product, ProductRevision
from inventory.models import StockMovement


class WorkOrderQueueLock(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(id=1), name="workorder_queue_lock_singleton")
        ]


class WorkOrder(models.Model):
    class Priority(models.TextChoices):
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        RESERVED = "RESERVED", "Reserved"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        PACKED = "PACKED", "Packed"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        BLOCKED = "BLOCKED", "Blocked"

    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="work_orders"
    )
    product_revision = models.ForeignKey(
        ProductRevision, on_delete=models.PROTECT, related_name="work_orders"
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    packed_quantity = models.PositiveIntegerField(default=0)
    priority = models.CharField(
        max_length=8, choices=Priority.choices, default=Priority.MEDIUM, db_index=True
    )
    due_date = models.DateField(db_index=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    assigned_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assigned_work_orders",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_work_orders",
    )
    blocked_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-priority", "due_date", "created_at", "pk")
        indexes = [
            models.Index(fields=("status", "-priority", "due_date")),
            models.Index(fields=("assigned_admin", "status")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="workorder_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(packed_quantity__lte=models.F("quantity")),
                name="workorder_packed_not_over_quantity",
            ),
        ]

    def __str__(self):
        return f"WO-{self.pk}: {self.product.product_number} × {self.quantity}"


class WorkOrderReservation(models.Model):
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.CASCADE, related_name="reservations"
    )
    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="work_order_reservations"
    )
    quantity = models.DecimalField(max_digits=15, decimal_places=3)
    released_quantity = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    consumed_quantity = models.DecimalField(max_digits=15, decimal_places=3, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("work_order", "component"),
                name="workorder_unique_component_reservation",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="workorder_reservation_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(released_quantity__gte=0),
                name="workorder_reservation_released_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(consumed_quantity__gte=0),
                name="workorder_reservation_consumed_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    quantity__gte=models.F("released_quantity")
                    + models.F("consumed_quantity")
                ),
                name="workorder_reservation_not_overdrawn",
            ),
        ]

    @property
    def remaining(self):
        return self.quantity - self.released_quantity - self.consumed_quantity


class SerialSequence(models.Model):
    product_code = models.CharField(max_length=80)
    year_month = models.CharField(max_length=4)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("product_code", "year_month"),
                name="workorder_unique_serial_sequence",
            ),
        ]


class PackConfirmation(models.Model):
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.PROTECT, related_name="pack_confirmations"
    )
    idempotency_key = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="pack_confirmations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1),
                name="workorder_pack_quantity_positive",
            ),
            models.UniqueConstraint(
                fields=("work_order", "idempotency_key"),
                name="workorder_unique_pack_idempotency",
            ),
        ]


class FinishedUnit(models.Model):
    class Status(models.TextChoices):
        IN_STOCK = "IN_STOCK", "In Stock"
        RESERVED_FOR_SALE = "RESERVED_FOR_SALE", "Reserved for sale"
        SOLD = "SOLD", "Sold"
        RETURNED = "RETURNED", "Returned"
        UNDER_INSPECTION = "UNDER_INSPECTION", "Under inspection"

    serial_number = models.CharField(max_length=120, unique=True)
    barcode_value = models.CharField(max_length=120, unique=True)
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="finished_units"
    )
    product_revision = models.ForeignKey(
        ProductRevision, on_delete=models.PROTECT, related_name="finished_units"
    )
    work_order = models.ForeignKey(
        WorkOrder, on_delete=models.PROTECT, related_name="finished_units"
    )
    pack_confirmation = models.ForeignKey(
        PackConfirmation, on_delete=models.PROTECT, related_name="finished_units"
    )
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.IN_STOCK, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")


class FinishedUnitHistory(models.Model):
    class EventType(models.TextChoices):
        PACKED = "PACKED", "Packed"
        STATUS_CHANGED = "STATUS_CHANGED", "Status changed"
        SALE = "SALE", "Sale"
        RETURN = "RETURN", "Return"
        INSPECTION = "INSPECTION", "Inspection"

    unit = models.ForeignKey(
        FinishedUnit, on_delete=models.PROTECT, related_name="history"
    )
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    from_status = models.CharField(
        max_length=24, choices=FinishedUnit.Status.choices, blank=True
    )
    to_status = models.CharField(
        max_length=24, choices=FinishedUnit.Status.choices
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="finished_unit_history",
    )
    sale_reference = models.CharField(max_length=120, blank=True)
    note = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("timestamp", "pk")


class PackConfirmationMovement(models.Model):
    confirmation = models.ForeignKey(
        PackConfirmation, on_delete=models.PROTECT, related_name="movements"
    )
    movement = models.ForeignKey(
        StockMovement, on_delete=models.PROTECT, related_name="pack_confirmations"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("confirmation", "movement"),
                name="workorder_unique_confirmation_movement",
            ),
        ]
