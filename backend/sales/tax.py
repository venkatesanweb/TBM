from decimal import Decimal

from catalog.money import round_money


def calculate_sale_tax(
    *,
    price_incl_tax,
    gst_rate_percent,
    place_of_supply_state_code,
    company_state_code,
):
    if not isinstance(price_incl_tax, Decimal) or not isinstance(
        gst_rate_percent, Decimal
    ):
        raise TypeError("Sale tax calculations must use Decimal values.")
    if price_incl_tax <= 0 or gst_rate_percent < 0:
        raise ValueError("Sale price must be positive and GST rate cannot be negative.")
    if not place_of_supply_state_code or not company_state_code:
        raise ValueError("A delivery state and company state are required for tax.")

    taxable_value = round_money(
        price_incl_tax / (Decimal("1") + gst_rate_percent / Decimal("100"))
    )
    total_tax = price_incl_tax - taxable_value
    if place_of_supply_state_code == company_state_code:
        cgst_amount = round_money(total_tax / Decimal("2"))
        sgst_amount = total_tax - cgst_amount
        igst_amount = Decimal("0.00")
    else:
        cgst_amount = Decimal("0.00")
        sgst_amount = Decimal("0.00")
        igst_amount = total_tax

    return {
        "taxable_value": taxable_value,
        "cgst_amount": cgst_amount,
        "sgst_amount": sgst_amount,
        "igst_amount": igst_amount,
        "total_tax": total_tax,
        "total_amount": price_incl_tax,
    }
