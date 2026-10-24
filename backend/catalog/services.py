from catalog.models import BOMLine, Component, Product, ProductRevision


def product_snapshot(product):
    return {
        "product_number": product.product_number,
        "name": product.name,
        "description": product.description,
        "sku": product.sku,
        "photo": product.photo.name if product.photo else None,
        "active": product.active,
        "current_revision": product.current_revision.revision_number
        if product.current_revision_id
        else None,
    }


def component_snapshot(component):
    return {
        "sku": component.sku,
        "name": component.name,
        "description": component.description,
        "category": component.category,
        "unit": component.unit,
        "revision_number": component.revision_number,
        "photo": component.photo.name if component.photo else None,
        "reorder_level": str(component.reorder_level),
        "active": component.active,
    }


def revision_snapshot(revision):
    return {
        "product": revision.product.product_number,
        "revision_number": revision.revision_number,
        "notes": revision.notes,
        "is_frozen": revision.is_frozen,
        "bom": [
            {
                "component_sku": line.component.sku,
                "quantity_per_unit": str(line.quantity_per_unit),
            }
            for line in revision.bom_lines.select_related("component").all()
        ],
    }


def bom_line_snapshot(line):
    return {
        "product_revision": line.product_revision.revision_number,
        "product_number": line.product_revision.product.product_number,
        "component_sku": line.component.sku,
        "quantity_per_unit": str(line.quantity_per_unit),
    }
