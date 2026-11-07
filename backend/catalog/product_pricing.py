from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch

from accounts.dashboard_access import PAYMENTS, RETURNS, SALES_OVERVIEW, has_dashboard_section
from accounts.models import User
from audit.services import record_audit
from catalog.models import BOMLine, Product, ProductPrice, TaxRate
from catalog.money import (
    calculate_product_cost_details,
    money_string,
    round_money,
)


def can_view_product_prices(user):
    return bool(
        user
        and user.is_authenticated
        and (
            user.role == User.Role.SUPER_ADMIN
            or any(
                has_dashboard_section(user, section)
                for section in (SALES_OVERVIEW, PAYMENTS, RETURNS)
            )
        )
    )


def product_cost_breakdown(product, *, margin_percent, gst_rate_percent):
    revision = product.current_revision
    cost, missing_components = calculate_product_cost_details(revision)
    if revision is None or not revision.bom_lines.exists():
        missing_components = ["Current BOM"]
    if missing_components or cost is None:
        return {
            "complete": False,
            "cost": None,
            "missing_components": missing_components,
            "margin_percent": money_string(margin_percent),
            "margin_amount": None,
            "taxable_value": None,
            "gst_rate_percent": money_string(gst_rate_percent),
            "gst_amount": None,
            "suggested_selling_price": None,
        }

    hundred = Decimal("100")
    margin_amount = round_money(cost * margin_percent / hundred)
    taxable_value = round_money(cost + margin_amount)
    suggested_selling_price = round_money(
        cost
        * (Decimal("1") + margin_percent / hundred)
        * (Decimal("1") + gst_rate_percent / hundred)
    )
    gst_amount = round_money(suggested_selling_price - taxable_value)
    return {
        "complete": True,
        "cost": money_string(cost),
        "missing_components": [],
        "margin_percent": money_string(margin_percent),
        "margin_amount": money_string(margin_amount),
        "taxable_value": money_string(taxable_value),
        "gst_rate_percent": money_string(gst_rate_percent),
        "gst_amount": money_string(gst_amount),
        "suggested_selling_price": money_string(suggested_selling_price),
    }


def product_price_summary(product, *, include_history=False):
    latest = product.price_history.first()
    breakdown = product_cost_breakdown(
        product,
        margin_percent=latest.margin_percent if latest else Decimal("0"),
        gst_rate_percent=(
            latest.gst_rate_percent if latest else Decimal("0")
        ),
    )
    if not breakdown["complete"]:
        status = "Incomplete"
    elif latest is None:
        status = "Not set"
    else:
        status = "Complete"

    result = {
        **breakdown,
        "status": status,
        "selling_price_incl_tax": (
            money_string(latest.selling_price_incl_tax) if latest else None
        ),
        "mrp": money_string(latest.mrp) if latest else None,
        "margin_percent": money_string(latest.margin_percent) if latest else None,
        "gst_rate_percent": (
            money_string(latest.gst_rate_percent) if latest else None
        ),
        "hsn_code": latest.hsn_code if latest else None,
        "effective_from": latest.effective_from if latest else None,
        "cost_changed_review": bool(
            latest
            and (
                not breakdown["complete"]
                or Decimal(breakdown["cost"]) != latest.cost_snapshot
            )
        ),
        "latest_price_id": latest.pk if latest else None,
    }
    if include_history:
        result["history"] = [
            {
                "id": price.pk,
                "effective_from": price.effective_from,
                "cost_snapshot": money_string(price.cost_snapshot),
                "margin_percent": money_string(price.margin_percent),
                "selling_price_incl_tax": money_string(
                    price.selling_price_incl_tax
                ),
                "mrp": money_string(price.mrp),
                "hsn_code": price.hsn_code,
                "gst_rate_percent": money_string(price.gst_rate_percent),
                "reason": price.reason,
                "created_by": (
                    price.created_by.get_username() if price.created_by else None
                ),
            }
            for price in product.price_history.select_related("created_by").all()
        ]
    return result


def product_pricing_rows():
    products = (
        Product.objects.select_related("current_revision")
        .prefetch_related(
            Prefetch(
                "current_revision__bom_lines",
                queryset=BOMLine.objects.select_related("component"),
            ),
            "price_history",
        )
        .order_by("product_number")
    )
    rows = []
    for product in products:
        summary = product_price_summary(product)
        rows.append(
            {
                "id": product.pk,
                "product_number": product.product_number,
                "name": product.name,
                "sku": product.sku,
                "computed_cost": summary["cost"],
                "selling_price_incl_tax": summary["selling_price_incl_tax"],
                "mrp": summary["mrp"],
                "gst_rate_percent": summary["gst_rate_percent"],
                "hsn_code": summary["hsn_code"],
                "status": summary["status"],
                "last_changed": summary["effective_from"],
                "cost_changed_review": summary["cost_changed_review"],
                "missing_components": summary["missing_components"],
            }
        )
    return rows


@transaction.atomic
def append_product_price(
    *,
    product_id,
    margin_percent,
    selling_price_incl_tax,
    mrp,
    hsn_code,
    tax_rate_id,
    reason,
    actor,
):
    product = Product.objects.select_for_update().get(pk=product_id)
    cost, missing_components = calculate_product_cost_details(product.current_revision)
    if product.current_revision_id is None or not product.current_revision.bom_lines.exists():
        missing_components = ["Current BOM"]
    if missing_components or cost is None:
        names = ", ".join(missing_components)
        raise ValueError(f"Cannot save price; missing component prices: {names}.")

    tax_rate = TaxRate.objects.select_for_update().get(
        pk=tax_rate_id, active=True
    )
    if product.price_history.exists() and not reason.strip():
        raise ValueError("Provide a reason when changing an existing product price.")
    if selling_price_incl_tax > mrp:
        raise ValueError("Selling price inclusive of tax cannot exceed MRP.")
    if selling_price_incl_tax <= 0 or mrp <= 0:
        raise ValueError("Selling price and MRP must both be greater than zero.")

    price = ProductPrice.objects.create(
        product=product,
        cost_snapshot=cost,
        margin_percent=margin_percent,
        selling_price_incl_tax=selling_price_incl_tax,
        mrp=mrp,
        hsn_code=hsn_code,
        gst_rate_percent=tax_rate.rate_percent,
        created_by=actor,
        reason=reason.strip(),
    )
    record_audit(
        actor=actor,
        action="catalog.product.price_created",
        instance=price,
        after={
            "product_id": product.pk,
            "cost_snapshot": money_string(cost),
            "margin_percent": money_string(margin_percent),
            "selling_price_incl_tax": money_string(selling_price_incl_tax),
            "mrp": money_string(mrp),
            "hsn_code": hsn_code,
            "gst_rate_percent": money_string(tax_rate.rate_percent),
            "reason": reason.strip(),
        },
    )
    return price
