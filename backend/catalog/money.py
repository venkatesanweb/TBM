from decimal import Decimal, ROUND_HALF_UP


MONEY_QUANTUM = Decimal("0.01")


def round_money(amount):
    if not isinstance(amount, Decimal):
        raise TypeError("Money calculations must use Decimal values.")
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def money_string(amount):
    if amount is None:
        return None
    return format(round_money(amount), ".2f")


def calculate_product_cost_details(revision, *, price_overrides=None):
    if revision is None:
        return None, []

    overrides = price_overrides or {}
    lines = list(revision.bom_lines.select_related("component"))
    if not lines:
        return None, []

    total = Decimal("0")
    missing_components = []
    for line in lines:
        price = overrides.get(line.component_id, line.component.cost_price)
        if price is None:
            missing_components.append(line.component.name)
            continue
        if not isinstance(price, Decimal):
            raise TypeError("Component prices must use Decimal values.")
        total += price * line.quantity_per_unit

    if missing_components:
        return None, missing_components
    return round_money(total), []


def calculate_product_cost(revision, *, price_overrides=None):
    cost, missing_components = calculate_product_cost_details(
        revision, price_overrides=price_overrides
    )
    return cost, len(missing_components)
