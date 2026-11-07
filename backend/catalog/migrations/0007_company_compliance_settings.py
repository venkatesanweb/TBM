import django.db.models.deletion
from django.db import migrations, models

import catalog.images


STATE_CODES = (
    ("01", "Jammu and Kashmir"),
    ("02", "Himachal Pradesh"),
    ("03", "Punjab"),
    ("04", "Chandigarh"),
    ("05", "Uttarakhand"),
    ("06", "Haryana"),
    ("07", "Delhi"),
    ("08", "Rajasthan"),
    ("09", "Uttar Pradesh"),
    ("10", "Bihar"),
    ("11", "Sikkim"),
    ("12", "Arunachal Pradesh"),
    ("13", "Nagaland"),
    ("14", "Manipur"),
    ("15", "Mizoram"),
    ("16", "Tripura"),
    ("17", "Meghalaya"),
    ("18", "Assam"),
    ("19", "West Bengal"),
    ("20", "Jharkhand"),
    ("21", "Odisha"),
    ("22", "Chhattisgarh"),
    ("23", "Madhya Pradesh"),
    ("24", "Gujarat"),
    ("25", "Daman and Diu"),
    ("26", "Dadra and Nagar Haveli and Daman and Diu"),
    ("27", "Maharashtra"),
    ("28", "Andhra Pradesh (before division)"),
    ("29", "Karnataka"),
    ("30", "Goa"),
    ("31", "Lakshadweep"),
    ("32", "Kerala"),
    ("33", "Tamil Nadu"),
    ("34", "Puducherry"),
    ("35", "Andaman and Nicobar Islands"),
    ("36", "Telangana"),
    ("37", "Andhra Pradesh"),
    ("38", "Ladakh"),
)


def create_reference_data(apps, schema_editor):
    IndianState = apps.get_model("catalog", "IndianState")
    CompanyProfile = apps.get_model("catalog", "CompanyProfile")
    IndianState.objects.bulk_create(
        [IndianState(state_code=code, name=name) for code, name in STATE_CODES]
    )
    CompanyProfile.objects.get_or_create(pk=1)


def remove_reference_data(apps, schema_editor):
    CompanyProfile = apps.get_model("catalog", "CompanyProfile")
    IndianState = apps.get_model("catalog", "IndianState")
    CompanyProfile.objects.filter(pk=1).delete()
    IndianState.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_product_pricing_and_tax_rates"),
    ]

    operations = [
        migrations.CreateModel(
            name="IndianState",
            fields=[
                (
                    "state_code",
                    models.CharField(max_length=2, primary_key=True, serialize=False),
                ),
                ("name", models.CharField(max_length=100, unique=True)),
            ],
            options={"ordering": ("state_code",)},
        ),
        migrations.CreateModel(
            name="CompanyProfile",
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
                ("legal_name", models.CharField(blank=True, default="", max_length=255)),
                ("trade_name", models.CharField(blank=True, default="", max_length=255)),
                ("address_line1", models.CharField(blank=True, default="", max_length=255)),
                ("address_line2", models.CharField(blank=True, default="", max_length=255)),
                ("city", models.CharField(blank=True, default="", max_length=120)),
                ("pincode", models.CharField(blank=True, default="", max_length=6)),
                ("gstin", models.CharField(blank=True, default="", max_length=15)),
                ("pan", models.CharField(blank=True, default="", max_length=10)),
                ("phone", models.CharField(blank=True, default="", max_length=20)),
                ("email", models.EmailField(blank=True, default="", max_length=254)),
                (
                    "packer_manufacturer_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("packer_address", models.TextField(blank=True, default="")),
                ("country_of_origin", models.CharField(default="India", max_length=100)),
                (
                    "consumer_care_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("consumer_care_address", models.TextField(blank=True, default="")),
                (
                    "consumer_care_phone",
                    models.CharField(blank=True, default="", max_length=20),
                ),
                (
                    "consumer_care_email",
                    models.EmailField(blank=True, default="", max_length=254),
                ),
                ("invoice_prefix", models.CharField(blank=True, default="", max_length=40)),
                (
                    "credit_note_prefix",
                    models.CharField(blank=True, default="", max_length=40),
                ),
                (
                    "logo",
                    models.ImageField(
                        blank=True,
                        upload_to="company/",
                        validators=[catalog.images.validate_image],
                    ),
                ),
                (
                    "authorised_signatory_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "signature_image",
                    models.ImageField(
                        blank=True,
                        upload_to="company/signatures/",
                        validators=[catalog.images.validate_image],
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "state_code",
                    models.ForeignKey(
                        blank=True,
                        db_column="state_code",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="company_profiles",
                        to="catalog.indianstate",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(id=1),
                        name="catalog_company_profile_singleton",
                    )
                ]
            },
        ),
        migrations.RunPython(create_reference_data, remove_reference_data),
    ]
