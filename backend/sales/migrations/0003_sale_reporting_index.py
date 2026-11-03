from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sales", "0002_alter_sale_status_salereturn_salestatushistory"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(
                fields=["sale_date", "status"],
                name="sales_date_status_idx",
            ),
        ),
    ]
