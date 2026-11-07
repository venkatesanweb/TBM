from decimal import Decimal

from django.db.models import OuterRef, Prefetch, Subquery

from audit.services import record_audit
from catalog.models import (
    BOMLine,
    Component,
    ComponentPriceHistory,
    Product,
)
from catalog.money import calculate_product_cost, money_string, round_money


def _current_products(component_ids):
    return list(
        Product.objects.filter(
            current_revision__bom_lines__component_id__in=component_ids
        )
        .select_related("current_revision")
        .prefetch_related(
            Prefetch(
                "current_revision__bom_lines",
                queryset=BOMLine.objects.select_related("component"),
            )
        )
        .distinct()
        .order_by("product_number")
    )


def product_cost_rows(products, *, price_overrides=None):
    rows = {}
    for product in products:
        cost, missing_count = calculate_product_cost(
            product.current_revision, price_overrides=price_overrides
        )
        rows[product.pk] = {
            "product_id": product.pk,
            "product_number": product.product_number,
            "product_name": product.name,
            "revision_number": (
                product.current_revision.revision_number
                if product.current_revision_id
                else None
            ),
            "computed_cost": money_string(cost),
            "missing_component_count": missing_count,
        }
    return rows


def component_price_preview(price_changes):
    component_ids = tuple(price_changes)
    products = _current_products(component_ids)
    current = product_cost_rows(products)
    proposed = product_cost_rows(products, price_overrides=price_changes)
    impacts = []
    for product in products:
        before = current[product.pk]
        after = proposed[product.pk]
        current_cost = before["computed_cost"]
        proposed_cost = after["computed_cost"]
        delta = None
        if current_cost is not None and proposed_cost is not None:
            delta = money_string(
                round_money(Decimal(proposed_cost) - Decimal(current_cost))
            )
        impacts.append(
            {
                **before,
                "proposed_cost": proposed_cost,
                "cost_change": delta,
                "proposed_missing_component_count": after[
                    "missing_component_count"
                ],
            }
        )
    return impacts


def component_pricing_rows(*, price_not_set=False):
    last_changed = ComponentPriceHistory.objects.filter(
        component_id=OuterRef("pk")
    ).order_by("-changed_at", "-pk")
    queryset = Component.objects.annotate(
        last_price_changed=Subquery(last_changed.values("changed_at")[:1])
    ).order_by("sku")
    if price_not_set:
        queryset = queryset.filter(cost_price__isnull=True)
    components = list(queryset)
    component_ids = tuple(component.pk for component in components)
    products = _current_products(component_ids) if component_ids else []
    product_rows = product_cost_rows(products)
    products_by_component = {component_id: [] for component_id in component_ids}
    for product in products:
        summary = product_rows[product.pk]
        for line in product.current_revision.bom_lines.all():
            if line.component_id in products_by_component:
                products_by_component[line.component_id].append(summary)

    return [
        {
            "id": component.pk,
            "sku": component.sku,
            "name": component.name,
            "unit": component.unit,
            "cost_price": money_string(component.cost_price),
            "last_changed": component.last_price_changed,
            "products": products_by_component[component.pk],
        }
        for component in components
    ]


def product_costs_for_component(component):
    products = _current_products((component.pk,))
    return list(product_cost_rows(products).values())


def set_component_price(*, component, new_price, actor, reason=""):
    old_price = component.cost_price
    if old_price == new_price:
        return None

    component.cost_price = new_price
    component.save(update_fields=("cost_price", "updated_at"))
    history = ComponentPriceHistory.objects.create(
        component=component,
        old_price=old_price,
        new_price=new_price,
        changed_by=actor,
        reason=reason,
    )
    record_audit(
        actor=actor,
        action="catalog.component.price_changed",
        instance=component,
        before={"cost_price": money_string(old_price)},
        after={
            "cost_price": money_string(new_price),
            "reason": reason,
        },
    )
    return history
