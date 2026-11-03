import re

from django.db import connection
from rest_framework.exceptions import ValidationError

from catalog.models import Component, Product


_EDGE_WHITESPACE_AND_CONTROLS = re.compile(r"^[\s\x00-\x1f\x7f]+|[\s\x00-\x1f\x7f]+$")


def normalize_barcode(value):
    if value is None:
        return None
    normalized = _EDGE_WHITESPACE_AND_CONTROLS.sub("", str(value))
    if not normalized:
        return None
    if len(normalized) > 64:
        raise ValidationError("A barcode cannot exceed 64 characters.")
    return normalized


def _owner_label(owner):
    if isinstance(owner, Component):
        identifier = owner.sku
    else:
        identifier = owner.product_number
    return f"{owner.name} ({identifier})"


def find_barcode_owner(barcode, *, excluding=None):
    components = Component.objects.filter(barcode=barcode)
    products = Product.objects.filter(barcode=barcode)
    if isinstance(excluding, Component):
        components = components.exclude(pk=excluding.pk)
    elif isinstance(excluding, Product):
        products = products.exclude(pk=excluding.pk)

    return components.first() or products.first()


def validate_barcode_owner(value, *, instance=None):
    barcode = normalize_barcode(value)
    if barcode is None:
        return None
    owner = find_barcode_owner(barcode, excluding=instance)
    if owner:
        raise ValidationError(f"Barcode already registered to {_owner_label(owner)}")
    return barcode


def lock_and_validate_barcode(value, *, instance=None):
    barcode = normalize_barcode(value)
    if barcode is None:
        return None
    if not connection.in_atomic_block:
        raise RuntimeError("Barcode uniqueness must be checked inside a transaction.")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"catalog-barcode:{barcode}"],
        )
    return validate_barcode_owner(barcode, instance=instance)
