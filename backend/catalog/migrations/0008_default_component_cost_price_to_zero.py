from decimal import Decimal

from django.db import migrations, models

import django.core.validators


DEFAULT_REASON = "Initialized to INR 0.00 by catalog pricing migration"
ZERO = Decimal("0.00")


def set_missing_prices_to_zero(apps, schema_editor):
    Component = apps.get_model("catalog", "Component")
    ComponentPriceHistory = apps.get_model("catalog", "ComponentPriceHistory")

    for component in Component.objects.filter(cost_price__isnull=True).iterator():
        Component.objects.filter(pk=component.pk, cost_price__isnull=True).update(
            cost_price=ZERO
        )
        ComponentPriceHistory.objects.create(
            component_id=component.pk,
            old_price=None,
            new_price=ZERO,
            changed_by=None,
            reason=DEFAULT_REASON,
        )


def restore_missing_prices(apps, schema_editor):
    Component = apps.get_model("catalog", "Component")
    ComponentPriceHistory = apps.get_model("catalog", "ComponentPriceHistory")

    seed_rows = ComponentPriceHistory.objects.filter(
        reason=DEFAULT_REASON,
        old_price__isnull=True,
        new_price=ZERO,
    )
    for history in seed_rows.iterator():
        latest = (
            ComponentPriceHistory.objects.filter(component_id=history.component_id)
            .order_by("-changed_at", "-pk")
            .first()
        )
        if latest and latest.pk == history.pk:
            Component.objects.filter(
                pk=history.component_id,
                cost_price=ZERO,
            ).update(cost_price=None)
        history.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0007_company_compliance_settings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="component",
            name="cost_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                default=ZERO,
                max_digits=12,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.RunPython(set_missing_prices_to_zero, restore_missing_prices),
    ]
