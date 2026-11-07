from django.db import migrations, models
import django.db.models.deletion


def enable_direct_platform_invoices(apps, schema_editor):
    SalesPlatform = apps.get_model("sales", "SalesPlatform")
    SalesPlatform.objects.filter(name__iexact="Tamirabot").update(
        issue_tamirabot_invoice=True
    )


def disable_direct_platform_invoices(apps, schema_editor):
    SalesPlatform = apps.get_model("sales", "SalesPlatform")
    SalesPlatform.objects.filter(name__iexact="Tamirabot").update(
        issue_tamirabot_invoice=False
    )


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0005_sale_billing_address_sale_customer_gstin_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesplatform",
            name="issue_tamirabot_invoice",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="InvoiceSequence",
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
                ("financial_year", models.CharField(max_length=5, unique=True)),
                ("last_number", models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="CreditNoteSequence",
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
                ("financial_year", models.CharField(max_length=5, unique=True)),
                ("last_number", models.PositiveIntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="Invoice",
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
                ("number", models.CharField(max_length=16, unique=True)),
                ("issue_date", models.DateField()),
                ("supplier_snapshot", models.JSONField()),
                ("buyer_snapshot", models.JSONField()),
                ("line_items", models.JSONField(default=list)),
                ("tax_breakup", models.JSONField(default=dict)),
                (
                    "subtotal",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                (
                    "total_tax",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                (
                    "total_amount",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                ("amount_in_words", models.TextField()),
                ("is_cancelled", models.BooleanField(default=False)),
                (
                    "sale",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="invoice",
                        to="sales.sale",
                    ),
                ),
            ],
            options={"ordering": ("-issue_date", "-pk")},
        ),
        migrations.CreateModel(
            name="CreditNote",
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
                ("number", models.CharField(max_length=16, unique=True)),
                ("note_date", models.DateField()),
                ("reason", models.TextField()),
                ("amounts", models.JSONField(default=dict)),
                (
                    "invoice",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="credit_notes",
                        to="sales.invoice",
                    ),
                ),
            ],
            options={"ordering": ("-note_date", "-pk")},
        ),
        migrations.RunPython(
            enable_direct_platform_invoices,
            disable_direct_platform_invoices,
        ),
    ]
