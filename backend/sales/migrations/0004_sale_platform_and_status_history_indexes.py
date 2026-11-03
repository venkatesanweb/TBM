from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0003_sale_reporting_index"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(
                fields=["platform", "sale_date"],
                name="sales_platform_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="salestatushistory",
            index=models.Index(
                fields=["sale", "to_status", "-timestamp"],
                name="sales_history_status_idx",
            ),
        ),
    ]
