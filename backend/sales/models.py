from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from workorders.models import FinishedUnit


class SalesPlatform(models.Model):
    class LabelTemplateType(models.TextChoices):
        TAMIRABOT = "TAMIRABOT", "Tamirabot packing label"
        SELLER_PROVIDED = "SELLER_PROVIDED", "Seller-provided label/reference"

    name = models.CharField(max_length=120, unique=True)
    label_template_type = models.CharField(
        max_length=24,
        choices=LabelTemplateType.choices,
        default=LabelTemplateType.SELLER_PROVIDED,
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class Sale(models.Model):
    class Status(models.TextChoices):
        READY_TO_SHIP = "READY_TO_SHIP", "Ready to Ship"
        DELIVERED = "DELIVERED", "Delivered"
        PAYMENT_PENDING = "PAYMENT_PENDING", "Payment Pending"
        PAYMENT_RECEIVED = "PAYMENT_RECEIVED", "Payment Received"
        RETURNED = "RETURNED", "Return"

    platform = models.ForeignKey(
        SalesPlatform, on_delete=models.PROTECT, related_name="sales"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sales_created"
    )
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.READY_TO_SHIP, db_index=True
    )
    order_reference = models.CharField(max_length=120, blank=True)
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    sale_date = models.DateField()
    notes = models.TextField(blank=True)
    shipping_name = models.CharField(max_length=160, blank=True)
    shipping_address = models.TextField(blank=True)
    shipping_city = models.CharField(max_length=120, blank=True)
    shipping_state = models.CharField(max_length=120, blank=True)
    shipping_postal_code = models.CharField(max_length=24, blank=True)
    shipping_phone = models.CharField(max_length=40, blank=True)
    seller_label_reference = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-sale_date", "-created_at", "-pk")
        indexes = [
            models.Index(fields=("sale_date", "status"), name="sales_date_status_idx"),
            models.Index(fields=("platform", "sale_date"), name="sales_platform_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price__gte=0),
                name="sales_sale_price_nonnegative",
            ),
        ]

    def __str__(self):
        return f"Sale {self.pk} · {self.platform.name}"


class SaleUnit(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="units")
    finished_unit = models.OneToOneField(
        FinishedUnit,
        on_delete=models.PROTECT,
        related_name="sale_link",
    )

    class Meta:
        ordering = ("pk",)


class SaleStatusHistory(models.Model):
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="status_history")
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, choices=Sale.Status.choices)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sale_status_changes",
    )
    note = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("timestamp", "pk")
        indexes = [
            models.Index(
                fields=("sale", "to_status", "-timestamp"),
                name="sales_history_status_idx",
            ),
        ]


class SaleReturn(models.Model):
    class Decision(models.TextChoices):
        PENDING = "PENDING", "Pending inspection"
        RESTOCK = "RESTOCK", "Restock as finished good"
        REWORK = "REWORK", "Rework"
        SCRAP = "SCRAP", "Scrap"

    sale_unit = models.OneToOneField(
        SaleUnit, on_delete=models.PROTECT, related_name="return_record"
    )
    reason = models.TextField()
    decision = models.CharField(
        max_length=12, choices=Decision.choices, default=Decision.PENDING, db_index=True
    )
    decision_note = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sale_returns_decided",
    )
    rework_order = models.ForeignKey(
        "workorders.WorkOrder",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sale_returns",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
