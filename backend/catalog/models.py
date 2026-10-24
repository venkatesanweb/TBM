from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils import timezone

from catalog.images import validate_image


class Component(models.Model):
    sku = models.CharField(max_length=80, unique=True)
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


class Product(models.Model):
    product_number = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
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
