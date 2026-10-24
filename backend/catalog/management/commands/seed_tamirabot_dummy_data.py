import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.services import record_audit
from catalog.models import BOMLine, Component, Product, ProductRevision
from catalog.services import bom_line_snapshot, revision_snapshot


class Command(BaseCommand):
    help = "Load Tamirabot dummy components, products, revisions, and BOMs."

    def add_arguments(self, parser):
        default_path = Path(__file__).resolve().parents[2] / "fixtures" / "tamirabot_dummy_data.json"
        parser.add_argument(
            "json_file",
            nargs="?",
            default=str(default_path),
            help="Path to tamirabot_dummy_data.json",
        )

    def handle(self, *args, **options):
        path = Path(options["json_file"])
        try:
            with path.open(encoding="utf-8") as data_file:
                data = json.load(data_file)
        except OSError as exc:
            raise CommandError(f"Could not read dummy data file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in {path}: {exc}") from exc

        components, products = self.validate_data(data)
        created_components = created_products = created_revisions = created_lines = 0

        with transaction.atomic():
            for item in products:
                existing_product = Product.objects.filter(
                    product_number=item["product_number"]
                ).first()
                if existing_product and existing_product.sku != item["sku"]:
                    raise CommandError(
                        f"Product number {item['product_number']} already exists with a different SKU."
                    )
                sku_owner = Product.objects.filter(sku=item["sku"]).first()
                if sku_owner and sku_owner.product_number != item["product_number"]:
                    raise CommandError(
                        f"Product SKU {item['sku']} already belongs to "
                        f"{sku_owner.product_number}."
                    )

            for item in components:
                component, created = Component.objects.get_or_create(
                    sku=item["sku"],
                    defaults={key: value for key, value in item.items() if key != "sku"},
                )
                if created:
                    created_components += 1
                    record_audit(
                        actor=None,
                        action="catalog.component.seeded",
                        instance=component,
                        after={"sku": component.sku, "name": component.name},
                    )

            for item in products:
                product, product_created = Product.objects.get_or_create(
                    product_number=item["product_number"],
                    defaults={
                        key: value
                        for key, value in item.items()
                        if key not in {"product_number", "revisions", "current_revision"}
                    },
                )
                if product_created:
                    created_products += 1
                    record_audit(
                        actor=None,
                        action="catalog.product.seeded",
                        instance=product,
                        after={
                            "product_number": product.product_number,
                            "sku": product.sku,
                        },
                    )
                revisions_by_number = {}
                for revision_data in item["revisions"]:
                    revision, revision_created = ProductRevision.objects.get_or_create(
                        product=product,
                        revision_number=revision_data["revision_number"],
                        defaults={"notes": revision_data.get("notes", "")},
                    )
                    revisions_by_number[revision.revision_number] = revision
                    if revision_created:
                        created_revisions += 1
                    if revision.bom_lines.exists():
                        continue
                    lines = []
                    for line in revision_data["bom"]:
                        component = Component.objects.get(sku=line["component_sku"])
                        if not component.active:
                            raise CommandError(
                                f"Cannot seed BOM for {product.product_number}: "
                                f"component {component.sku} is inactive."
                            )
                        lines.append(
                            BOMLine(
                                product_revision=revision,
                                component=component,
                                quantity_per_unit=line["quantity_per_unit"],
                            )
                        )
                    BOMLine.objects.bulk_create(lines)
                    created_lines += len(lines)
                    if revision_created or lines:
                        record_audit(
                            actor=None,
                            action="catalog.revision.seeded",
                            instance=revision,
                            after=revision_snapshot(revision),
                        )
                    for line in lines:
                        line.refresh_from_db()
                        record_audit(
                            actor=None,
                            action="catalog.bom_line.seeded",
                            instance=line,
                            after=bom_line_snapshot(line),
                        )

                current_revision = revisions_by_number[item["current_revision"]]
                if product.current_revision_id != current_revision.pk:
                    previous_revision = (
                        product.current_revision.revision_number
                        if product.current_revision_id
                        else None
                    )
                    product.current_revision = current_revision
                    product.save(update_fields=("current_revision", "updated_at"))
                    record_audit(
                        actor=None,
                        action="catalog.product.current_revision.seeded",
                        instance=product,
                        before={"current_revision": previous_revision},
                        after={"current_revision": current_revision.revision_number},
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "Dummy data loaded: "
                f"{created_components} components, {created_products} products, "
                f"{created_revisions} revisions, {created_lines} BOM lines. "
                "Opening stock was not loaded."
            )
        )

    @staticmethod
    def validate_data(data):
        if not isinstance(data, dict):
            raise CommandError("The JSON root must be an object.")
        components = data.get("components")
        products = data.get("products")
        if not isinstance(components, list) or not isinstance(products, list):
            raise CommandError("The JSON file must contain components and products arrays.")

        component_skus = set()
        for index, item in enumerate(components):
            required = ("sku", "name", "unit")
            if not isinstance(item, dict) or any(not item.get(key) for key in required):
                raise CommandError(f"Component at index {index} needs sku, name, and unit.")
            if item["sku"] in component_skus:
                raise CommandError(f"Duplicate component SKU in JSON: {item['sku']}.")
            component_skus.add(item["sku"])
            item.setdefault("description", "")
            item.setdefault("category", "")
            item.setdefault("revision_number", "")
            item.setdefault("reorder_level", "0")
            item.setdefault("active", True)
            try:
                item["reorder_level"] = Decimal(str(item["reorder_level"]))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise CommandError(f"Invalid reorder level for component {item['sku']}.") from exc
            if not item["reorder_level"].is_finite() or item["reorder_level"] < 0:
                raise CommandError(f"Reorder level for {item['sku']} cannot be negative.")

        product_numbers = set()
        product_skus = set()
        for index, item in enumerate(products):
            required = ("product_number", "name", "sku", "current_revision", "revisions")
            if not isinstance(item, dict) or any(key not in item for key in required):
                raise CommandError(
                    f"Product at index {index} needs product_number, name, sku, "
                    "current_revision, and revisions."
                )
            if item["product_number"] in product_numbers or item["sku"] in product_skus:
                raise CommandError("Product numbers and SKUs must be unique in the JSON file.")
            product_numbers.add(item["product_number"])
            product_skus.add(item["sku"])
            item.setdefault("description", "")
            item.setdefault("active", True)
            revisions = item["revisions"]
            if not isinstance(revisions, list) or not revisions:
                raise CommandError(f"Product {item['product_number']} needs at least one revision.")
            revision_numbers = set()
            for revision in revisions:
                if (
                    not isinstance(revision, dict)
                    or not isinstance(revision.get("revision_number"), int)
                    or revision["revision_number"] < 1
                    or not isinstance(revision.get("bom"), list)
                    or not revision["bom"]
                ):
                    raise CommandError(
                        f"Each revision for {item['product_number']} needs a positive "
                        "revision_number and a non-empty bom array."
                    )
                if revision["revision_number"] in revision_numbers:
                    raise CommandError(
                        f"Duplicate revision number for product {item['product_number']}."
                    )
                revision_numbers.add(revision["revision_number"])
                seen_skus = set()
                for line in revision["bom"]:
                    if not isinstance(line, dict):
                        raise CommandError("Each BOM line must be an object.")
                    component_sku = line.get("component_sku")
                    if component_sku not in component_skus:
                        raise CommandError(
                            f"BOM references unknown component SKU: {component_sku}."
                        )
                    if component_sku in seen_skus:
                        raise CommandError(
                            f"Duplicate component {component_sku} in {item['product_number']} BOM."
                        )
                    seen_skus.add(component_sku)
                    try:
                        quantity = Decimal(str(line["quantity_per_unit"]))
                    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
                        raise CommandError(
                            f"Invalid BOM quantity for component {component_sku}."
                        ) from exc
                    if (
                        not quantity.is_finite()
                        or quantity <= 0
                        or quantity.as_tuple().exponent < -3
                        or quantity > Decimal("999999999.999")
                    ):
                        raise CommandError(
                            f"BOM quantity for {component_sku} must be positive "
                            "and fit 9 digits plus 3 decimals."
                        )
                    line["quantity_per_unit"] = quantity
            if item["current_revision"] not in revision_numbers:
                raise CommandError(
                    f"Current revision {item['current_revision']} is missing for "
                    f"{item['product_number']}."
                )
        return components, products
