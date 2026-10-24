from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from catalog.models import Component


class StockBalance(models.Model):
    component = models.OneToOneField(
        Component, on_delete=models.PROTECT, related_name="stock_balance"
    )
    on_hand = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    reserved = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(on_hand__gte=0),
                name="inventory_balance_on_hand_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved__gte=0),
                name="inventory_balance_reserved_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(on_hand__gte=models.F("reserved")),
                name="inventory_balance_available_nonnegative",
            ),
        ]

    @property
    def available(self):
        return self.on_hand - self.reserved


class StockMovement(models.Model):
    class Type(models.TextChoices):
        RECEIVE = "RECEIVE", "Receive"
        RESERVE = "RESERVE", "Reserve"
        RELEASE = "RELEASE", "Release"
        CONSUME = "CONSUME", "Consume"
        RETURN = "RETURN", "Return"
        ADJUST = "ADJUST", "Adjustment"

    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="stock_movements"
    )
    movement_type = models.CharField(max_length=12, choices=Type.choices)
    quantity = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(0.001)],
    )
    delta_on_hand = models.DecimalField(max_digits=15, decimal_places=3)
    delta_reserved = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    work_order_reference = models.CharField(max_length=100, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stock_movements",
    )
    note = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-timestamp", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="inventory_movement_quantity_positive",
            ),
        ]
        indexes = [
            models.Index(fields=("component", "timestamp")),
            models.Index(fields=("movement_type", "timestamp")),
        ]


class StockAdjustment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="stock_adjustments"
    )
    delta = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.TextField()
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="stock_adjustments_requested",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_adjustments_reviewed",
    )
    movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="adjustment",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(delta=0),
                name="inventory_adjustment_delta_nonzero",
            ),
        ]
