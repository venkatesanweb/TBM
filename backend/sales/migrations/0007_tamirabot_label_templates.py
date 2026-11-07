import sales.models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0006_invoice_credit_note_documents"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LabelTemplate",
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
                ("name", models.CharField(max_length=120)),
                ("width_mm", models.PositiveSmallIntegerField(default=100)),
                ("height_mm", models.PositiveSmallIntegerField(default=150)),
                (
                    "field_toggles",
                    models.JSONField(default=sales.models.default_label_field_toggles),
                ),
                ("is_default", models.BooleanField(default=False)),
                (
                    "platform",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="label_templates",
                        to="sales.salesplatform",
                    ),
                ),
            ],
            options={"ordering": ("platform__name", "name", "pk")},
        ),
        migrations.CreateModel(
            name="LabelPrintLog",
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
                ("printed_at", models.DateTimeField(auto_now_add=True)),
                ("is_reprint", models.BooleanField(default=False)),
                (
                    "printed_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sales_label_prints",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "sale_unit",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="label_print_logs",
                        to="sales.saleunit",
                    ),
                ),
                (
                    "template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="print_logs",
                        to="sales.labeltemplate",
                    ),
                ),
            ],
            options={"ordering": ("-printed_at", "-pk")},
        ),
    ]
