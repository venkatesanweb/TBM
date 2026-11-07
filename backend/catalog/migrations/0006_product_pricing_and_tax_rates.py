import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def seed_example_tax_rates(apps, schema_editor):
    TaxRate = apps.get_model("catalog", "TaxRate")
    for rate, label in (
        ("0.00", "0% example"),
        ("5.00", "5% example"),
        ("18.00", "18% example"),
    ):
        TaxRate.objects.get_or_create(
            rate_percent=rate,
            defaults={"label": label, "active": True},
        )


def remove_example_tax_rates(apps, schema_editor):
    TaxRate = apps.get_model("catalog", "TaxRate")
    TaxRate.objects.filter(rate_percent__in=("0.00", "5.00", "18.00")).delete()


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0005_component_cost_price_and_history"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="generic_name",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="product",
            name="net_quantity_text",
            field=models.TextField(blank=True, default="1 Unit", null=True),
        ),
        migrations.CreateModel(
            name="TaxRate",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("rate_percent", models.DecimalField(decimal_places=2, max_digits=12)),
                ("label", models.CharField(max_length=100)),
                ("active", models.BooleanField(default=True)),
            ],
            options={
                "ordering": ("rate_percent", "label"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("rate_percent",),
                        name="catalog_unique_tax_rate_percent",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(rate_percent__gte=0),
                        name="catalog_tax_rate_nonnegative",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ProductPrice",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "effective_from",
                    models.DateTimeField(db_index=True, default=timezone.now),
                ),
                ("cost_snapshot", models.DecimalField(decimal_places=2, max_digits=12)),
                (
                    "margin_percent",
                    models.DecimalField(decimal_places=2, default=0, max_digits=12),
                ),
                (
                    "selling_price_incl_tax",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                ("mrp", models.DecimalField(decimal_places=2, max_digits=12)),
                ("hsn_code", models.CharField(max_length=8)),
                (
                    "gst_rate_percent",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                ("reason", models.TextField(blank=True, default="")),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="product_prices_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="price_history",
                        to="catalog.product",
                    ),
                ),
            ],
            options={
                "ordering": ("-effective_from", "-pk"),
                "indexes": [
                    models.Index(
                        fields=["product", "-effective_from", "-id"],
                        name="cat_prod_price_latest_idx",
                    )
                ],
            },
        ),
        migrations.RunPython(seed_example_tax_rates, remove_example_tax_rates),
    ]
