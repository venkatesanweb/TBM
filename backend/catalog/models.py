from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from catalog.images import validate_image


class Component(models.Model):
    sku = models.CharField(max_length=80, unique=True)
    barcode = models.CharField(
        max_length=64, null=True, blank=True, unique=True, db_index=True
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True, db_index=True)
    unit = models.CharField(max_length=24)
    revision_number = models.CharField(max_length=40, blank=True)
    photo = models.ImageField(
        upload_to="components/",
        blank=True,
        validators=[validate_image],
    )
    reorder_level = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
        validators=[MinValueValidator(0)],
    )
    cost_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("sku",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(reorder_level__gte=0),
                name="catalog_component_reorder_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.sku} — {self.name}"


class ComponentPriceHistory(models.Model):
    component = models.ForeignKey(
        Component, on_delete=models.CASCADE, related_name="price_history"
    )
    old_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    new_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    changed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="component_price_changes",
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-changed_at", "-pk")


class Product(models.Model):
    product_number = models.CharField(max_length=80, unique=True)
    barcode = models.CharField(
        max_length=64, null=True, blank=True, unique=True, db_index=True
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    generic_name = models.TextField(null=True, blank=True)
    net_quantity_text = models.TextField(null=True, blank=True, default="1 Unit")
    sku = models.CharField(max_length=80, unique=True)
    photo = models.ImageField(
        upload_to="products/",
        blank=True,
        validators=[validate_image],
    )
    active = models.BooleanField(default=True)
    current_revision = models.ForeignKey(
        "ProductRevision",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_for_products",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("product_number",)

    def __str__(self):
        return f"{self.product_number} — {self.name}"


class ProductRevision(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="revisions"
    )
    revision_number = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_frozen = models.BooleanField(default=False, editable=False)
    frozen_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("-revision_number",)
        constraints = [
            models.UniqueConstraint(
                fields=("product", "revision_number"),
                name="catalog_unique_product_revision",
            ),
        ]

    def __str__(self):
        return f"{self.product.product_number} rev {self.revision_number}"

    @property
    def is_current(self):
        return self.product.current_revision_id == self.pk

    @classmethod
    @transaction.atomic
    def create_next(cls, *, product, notes=""):
        product = Product.objects.select_for_update().get(pk=product.pk)
        previous = (
            cls.objects.select_for_update()
            .filter(product=product)
            .order_by("-revision_number")
            .first()
        )
        revision = cls.objects.create(
            product=product,
            revision_number=(previous.revision_number + 1) if previous else 1,
            notes=notes,
        )
        if previous:
            inactive_skus = list(
                previous.bom_lines.filter(component__active=False)
                .values_list("component__sku", flat=True)
            )
            if inactive_skus:
                from django.core.exceptions import ValidationError

                raise ValidationError(
                    "Cannot copy this BOM because these components are inactive: "
                    + ", ".join(inactive_skus)
                )
            BOMLine.objects.bulk_create(
                [
                    BOMLine(
                        product_revision=revision,
                        component_id=line.component_id,
                        quantity_per_unit=line.quantity_per_unit,
                    )
                    for line in previous.bom_lines.all()
                ]
            )
        product.current_revision = revision
        product.save(update_fields=("current_revision", "updated_at"))
        return revision

    @transaction.atomic
    def freeze_for_work_order(self, *, actor=None, work_order_reference=None):
        Product.objects.select_for_update().get(pk=self.product_id)
        revision = type(self).objects.select_for_update().get(pk=self.pk)
        if not revision.is_frozen:
            before = {"is_frozen": False, "frozen_at": None}
            revision.is_frozen = True
            revision.frozen_at = timezone.now()
            revision.save(update_fields=("is_frozen", "frozen_at"))
            from audit.services import record_audit

            record_audit(
                actor=actor,
                action="catalog.revision.frozen",
                instance=revision,
                before=before,
                after={
                    "is_frozen": True,
                    "frozen_at": revision.frozen_at.isoformat(),
                    "work_order_reference": work_order_reference,
                },
            )
        self.is_frozen = revision.is_frozen
        self.frozen_at = revision.frozen_at
        return self


class TaxRate(models.Model):
    rate_percent = models.DecimalField(max_digits=12, decimal_places=2)
    label = models.CharField(max_length=100)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("rate_percent", "label")
        constraints = [
            models.UniqueConstraint(
                fields=("rate_percent",), name="catalog_unique_tax_rate_percent"
            ),
            models.CheckConstraint(
                condition=models.Q(rate_percent__gte=0),
                name="catalog_tax_rate_nonnegative",
            ),
        ]


class IndianState(models.Model):
    state_code = models.CharField(max_length=2, primary_key=True)
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ("state_code",)

    def __str__(self):
        return f"{self.state_code} · {self.name}"


class CompanyProfile(models.Model):
    legal_name = models.CharField(max_length=255, blank=True, default="")
    trade_name = models.CharField(max_length=255, blank=True, default="")
    address_line1 = models.CharField(max_length=255, blank=True, default="")
    address_line2 = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=120, blank=True, default="")
    state_code = models.ForeignKey(
        IndianState,
        db_column="state_code",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="company_profiles",
    )
    pincode = models.CharField(max_length=6, blank=True, default="")
    gstin = models.CharField(max_length=15, blank=True, default="")
    pan = models.CharField(max_length=10, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    packer_manufacturer_name = models.CharField(max_length=255, blank=True, default="")
    packer_address = models.TextField(blank=True, default="")
    country_of_origin = models.CharField(max_length=100, default="India")
    consumer_care_name = models.CharField(max_length=255, blank=True, default="")
    consumer_care_address = models.TextField(blank=True, default="")
    consumer_care_phone = models.CharField(max_length=20, blank=True, default="")
    consumer_care_email = models.EmailField(blank=True, default="")
    invoice_prefix = models.CharField(max_length=40, blank=True, default="")
    credit_note_prefix = models.CharField(max_length=40, blank=True, default="")
    logo = models.ImageField(
        upload_to="company/",
        blank=True,
        validators=[validate_image],
    )
    authorised_signatory_name = models.CharField(max_length=255, blank=True, default="")
    signature_image = models.ImageField(
        upload_to="company/signatures/",
        blank=True,
        validators=[validate_image],
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id=1),
                name="catalog_company_profile_singleton",
            )
        ]

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("The company profile cannot be deleted.")


class ProductPrice(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="price_history"
    )
    effective_from = models.DateTimeField(default=timezone.now, db_index=True)
    cost_snapshot = models.DecimalField(max_digits=12, decimal_places=2)
    margin_percent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    selling_price_incl_tax = models.DecimalField(max_digits=12, decimal_places=2)
    mrp = models.DecimalField(max_digits=12, decimal_places=2)
    hsn_code = models.CharField(max_length=8)
    gst_rate_percent = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="product_prices_created",
    )
    reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-effective_from", "-pk")
        indexes = [
            models.Index(
                fields=("product", "-effective_from", "-id"),
                name="cat_prod_price_latest_idx",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Product prices are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Product prices are append-only.")


class BOMLine(models.Model):
    product_revision = models.ForeignKey(
        ProductRevision, on_delete=models.CASCADE, related_name="bom_lines"
    )
    component = models.ForeignKey(
        Component, on_delete=models.PROTECT, related_name="bom_lines"
    )
    quantity_per_unit = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(0.001)],
    )

    class Meta:
        ordering = ("component__sku",)
        constraints = [
            models.UniqueConstraint(
                fields=("product_revision", "component"),
                name="catalog_unique_component_per_bom",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_per_unit__gt=0),
                name="catalog_bom_quantity_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.component_id and not self.component.active:
            from django.core.exceptions import ValidationError

            raise ValidationError({"component": "Inactive components cannot be added to a BOM."})

    def __str__(self):
        return f"{self.product_revision}: {self.component} × {self.quantity_per_unit}"
