from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.services import record_audit
from catalog.company import company_profile_snapshot
from catalog.models import CompanyProfile, IndianState
from catalog.money import round_money
from sales.dashboard import pdf_bytes
from sales.models import (
    CreditNote,
    CreditNoteSequence,
    Invoice,
    InvoiceSequence,
    Sale,
    SaleReturn,
    SaleUnit,
    SalesPlatform,
)
from workorders.models import FinishedUnit


ZERO = Decimal("0.00")
MAX_MONEY = Decimal("9999999999.99")
ONES = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
TENS = (
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
)


def _under_thousand(number):
    words = []
    if number >= 100:
        words.extend((ONES[number // 100], "hundred"))
        number %= 100
    if number >= 20:
        words.append(TENS[number // 10])
        number %= 10
    if number:
        words.append(ONES[number])
    return " ".join(words)


def _integer_in_indian_words(number):
    if number == 0:
        return "zero"
    parts = []
    for divisor, label in (
        (10_000_000, "crore"),
        (100_000, "lakh"),
        (1_000, "thousand"),
        (1, ""),
    ):
        group, number = divmod(number, divisor)
        if group:
            parts.append(_under_thousand(group))
            if label:
                parts.append(label)
    return " ".join(parts)


def amount_in_words(value):
    amount = round_money(Decimal(value))
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = f"{_integer_in_indian_words(rupees)} {'rupee' if rupees == 1 else 'rupees'}"
    if paise:
        words += f" and {_integer_in_indian_words(paise)} {'paisa' if paise == 1 else 'paise'}"
    return f"{words} only".capitalize()


def financial_year(value):
    start_year = value.year if value.month >= 4 else value.year - 1
    end_year = (start_year + 1) % 100
    return f"{start_year % 100:02d}-{end_year:02d}"


def _next_number(sequence_model, *, prefix, issue_date):
    if not prefix or "/" in prefix.strip():
        raise ValidationError(
            {"number_prefix": "Configure a non-empty prefix without slashes in Company Settings."}
        )
    year = financial_year(issue_date)
    sequence, _ = sequence_model.objects.get_or_create(
        financial_year=year,
        defaults={"last_number": 0},
    )
    sequence = sequence_model.objects.select_for_update().get(pk=sequence.pk)
    if sequence.last_number >= 999_999:
        raise ValidationError({"number": "The financial-year document sequence is exhausted."})
    next_value = sequence.last_number + 1
    number = f"{prefix.strip()}/{year}/{next_value:06d}"
    if len(number) > 16:
        raise ValidationError(
            {"number_prefix": "The configured prefix makes the document number exceed 16 characters."}
        )
    sequence.last_number = next_value
    sequence.save(update_fields=("last_number",))
    return number


def _missing_invoice_fields(sale, units, profile, supplier_state, supply_state):
    missing = []

    def require(value, label):
        if (value is None or not str(value).strip()) and label not in missing:
            missing.append(label)

    require(profile.legal_name or profile.trade_name, "company legal name")
    require(profile.address_line1, "company address")
    require(profile.city, "company city")
    require(supplier_state, "company state")
    require(profile.pincode, "company PIN code")
    require(profile.gstin, "company GSTIN")
    require(profile.invoice_prefix, "invoice prefix")
    require(profile.authorised_signatory_name, "authorised signatory")
    require(sale.customer_name or sale.shipping_name, "customer name")
    require(sale.billing_address or sale.shipping_address, "customer billing address")
    require(sale.delivery_address or sale.shipping_address, "customer delivery address")
    require(supply_state, "customer state / place of supply")

    for item in units:
        unit = item.finished_unit
        product = unit.product
        label = f"{product.product_number} ({unit.serial_number})"
        require(item.hsn_code_snapshot, f"{label}: HSN code")
        require(item.unit_selling_price_incl_tax, f"{label}: selling price snapshot")
        require(item.mrp_snapshot, f"{label}: MRP snapshot")
        require(item.gst_rate_snapshot, f"{label}: GST rate snapshot")
        require(item.taxable_value, f"{label}: taxable value snapshot")
        require(item.total_tax, f"{label}: tax amount snapshot")
        require(product.generic_name or product.name, f"{label}: product description")
        require(product.net_quantity_text, f"{label}: net quantity")
        require(unit.pack_confirmation_id, f"{label}: manufacture date")
        require(profile.packer_manufacturer_name, f"{label}: packer name")
        require(profile.packer_address, f"{label}: packer address")
        require(profile.country_of_origin, f"{label}: country of origin")
        require(profile.consumer_care_name, f"{label}: consumer care name")
        require(profile.consumer_care_address, f"{label}: consumer care address")
        require(profile.consumer_care_phone, f"{label}: consumer care phone")
        require(profile.consumer_care_email, f"{label}: consumer care email")
    return missing


def _decimal_text(value):
    return f"{Decimal(value or 0):.2f}"


def _invoice_snapshots(sale, units, profile, supplier_state, supply_state):
    supplier = company_profile_snapshot(profile)
    supplier.update(
        {
            "name": profile.legal_name or profile.trade_name,
            "state_name": supplier_state.name,
            "address": ", ".join(
                item
                for item in (
                    profile.address_line1,
                    profile.address_line2,
                    profile.city,
                    supplier_state.name,
                    profile.pincode,
                )
                if item
            ),
        }
    )
    buyer = {
        "name": sale.customer_name or sale.shipping_name,
        "gstin": sale.customer_gstin,
        "billing_address": sale.billing_address or sale.shipping_address,
        "delivery_address": sale.delivery_address or sale.shipping_address,
        "phone": sale.customer_phone or sale.shipping_phone,
        "state_code": sale.place_of_supply_state_code,
        "state_name": supply_state.name,
    }

    line_items = []
    tax_breakup = {
        "cgst_amount": ZERO,
        "sgst_amount": ZERO,
        "igst_amount": ZERO,
        "total_tax": ZERO,
    }
    subtotal = ZERO
    total_amount = ZERO
    for item in units:
        unit = item.finished_unit
        product = unit.product
        packed_at = timezone.localtime(unit.pack_confirmation.created_at).date()
        line = {
            "serial_number": unit.serial_number,
            "product_number": product.product_number,
            "description": product.generic_name or product.name,
            "quantity": "1",
            "unit": "unit",
            "net_quantity_text": product.net_quantity_text,
            "manufactured_month_year": packed_at.strftime("%B %Y"),
            "hsn_code": item.hsn_code_snapshot,
            "rate": _decimal_text(item.unit_selling_price_incl_tax),
            "taxable_value": _decimal_text(item.taxable_value),
            "gst_rate_percent": _decimal_text(item.gst_rate_snapshot),
            "cgst_amount": _decimal_text(item.cgst_amount),
            "sgst_amount": _decimal_text(item.sgst_amount),
            "igst_amount": _decimal_text(item.igst_amount),
            "total_tax": _decimal_text(item.total_tax),
            "total_amount": _decimal_text(item.total_amount),
            "mrp_inclusive_of_all_taxes": _decimal_text(item.mrp_snapshot),
            "packer_manufacturer_name": profile.packer_manufacturer_name,
            "packer_address": profile.packer_address,
            "consumer_care_name": profile.consumer_care_name,
            "consumer_care_address": profile.consumer_care_address,
            "consumer_care_phone": profile.consumer_care_phone,
            "consumer_care_email": profile.consumer_care_email,
            "country_of_origin": profile.country_of_origin,
        }
        line_items.append(line)
        subtotal += Decimal(line["taxable_value"])
        total_amount += Decimal(line["total_amount"])
        for field in ("cgst_amount", "sgst_amount", "igst_amount", "total_tax"):
            tax_breakup[field] += Decimal(line[field])
    tax_breakup = {key: _decimal_text(value) for key, value in tax_breakup.items()}
    return (
        supplier,
        buyer,
        line_items,
        tax_breakup,
        subtotal,
        total_amount - subtotal,
        total_amount,
    )


@transaction.atomic
def issue_invoice(*, sale_id, actor):
    sale = (
        Sale.objects.select_for_update()
        .select_related("platform")
        .get(pk=sale_id)
    )
    if not sale.platform.issue_tamirabot_invoice:
        return None
    existing = Invoice.objects.filter(sale=sale).first()
    if existing:
        return existing, False
    if sale.pricing_source != Sale.PricingSource.PRICE_LIST:
        raise ValidationError(
            {"invoice": "Legacy sales have no frozen price snapshots and cannot be invoiced."}
        )

    units = list(
        SaleUnit.objects.select_for_update()
        .select_related(
            "finished_unit__product",
            "finished_unit__pack_confirmation",
        )
        .filter(sale=sale)
        .order_by("finished_unit__serial_number")
    )
    profile = CompanyProfile.objects.select_for_update().filter(pk=1).first()
    if profile is None:
        raise ValidationError({"missing_fields": ["company profile"]})
    supplier_state = (
        IndianState.objects.filter(pk=profile.state_code_id).first()
        if profile.state_code_id
        else None
    )
    supply_state = IndianState.objects.filter(
        pk=sale.place_of_supply_state_code
    ).first()
    missing = _missing_invoice_fields(
        sale, units, profile, supplier_state, supply_state
    )
    if missing:
        raise ValidationError({"missing_fields": missing})

    today = timezone.localdate()
    number = _next_number(
        InvoiceSequence,
        prefix=profile.invoice_prefix,
        issue_date=today,
    )
    (
        supplier,
        buyer,
        line_items,
        tax_breakup,
        subtotal,
        total_tax,
        total_amount,
    ) = _invoice_snapshots(sale, units, profile, supplier_state, supply_state)
    if any(value > MAX_MONEY for value in (subtotal, total_tax, total_amount)):
        raise ValidationError({"total": "Invoice total exceeds the supported amount."})
    invoice = Invoice.objects.create(
        sale=sale,
        number=number,
        issue_date=today,
        supplier_snapshot=supplier,
        buyer_snapshot=buyer,
        line_items=line_items,
        tax_breakup=tax_breakup,
        subtotal=subtotal,
        total_tax=total_tax,
        total_amount=total_amount,
        amount_in_words=amount_in_words(total_amount),
    )
    record_audit(
        actor=actor,
        action="sales.invoice.issued",
        instance=invoice,
        after={"number": invoice.number, "sale_id": sale.pk},
    )
    return invoice, True


def _credited_return_ids(invoice):
    return {
        return_id
        for credit_note in invoice.credit_notes.all()
        for return_id in credit_note.amounts.get("return_ids", [])
    }


def available_credit_note_returns(invoice):
    return list(
        SaleReturn.objects.filter(
            sale_unit__sale_id=invoice.sale_id,
        )
        .exclude(pk__in=_credited_return_ids(invoice))
        .select_related("sale_unit__finished_unit")
        .order_by("sale_unit__finished_unit__serial_number")
    )


@transaction.atomic
def issue_credit_note(*, invoice_id, serial_numbers, reason, actor):
    invoice = (
        Invoice.objects.select_for_update()
        .select_related("sale__platform")
        .get(pk=invoice_id)
    )
    if invoice.is_cancelled:
        raise ValidationError({"invoice": "A cancelled invoice cannot be credited."})
    serial_numbers = list(dict.fromkeys(serial_numbers))
    if not serial_numbers:
        raise ValidationError({"serial_numbers": "Select at least one returned unit."})

    returned = list(
        SaleReturn.objects.select_for_update()
        .select_related("sale_unit__finished_unit")
        .filter(
            sale_unit__sale_id=invoice.sale_id,
            sale_unit__finished_unit__serial_number__in=serial_numbers,
        )
        .order_by("pk")
    )
    if len(returned) != len(serial_numbers):
        raise ValidationError(
            {"serial_numbers": "Select units that have been returned from this sale."}
        )
    already_credited = _credited_return_ids(invoice)
    duplicate = [
        item.sale_unit.finished_unit.serial_number
        for item in returned
        if item.pk in already_credited
    ]
    if duplicate:
        raise ValidationError(
            {"serial_numbers": f"Already credited: {', '.join(duplicate)}."}
        )
    line_by_serial = {
        item["serial_number"]: item for item in invoice.line_items
    }
    selected_lines = []
    for returned_item in returned:
        serial = returned_item.sale_unit.finished_unit.serial_number
        line = line_by_serial.get(serial)
        if line is None:
            raise ValidationError(
                {"serial_numbers": f"{serial} is not present in the issued invoice."}
            )
        selected_lines.append({**line, "return_reason": returned_item.reason})

    sums = {
        key: sum(
            (Decimal(line[key]) for line in selected_lines),
            start=ZERO,
        )
        for key in (
            "taxable_value",
            "cgst_amount",
            "sgst_amount",
            "igst_amount",
            "total_tax",
            "total_amount",
        )
    }
    today = timezone.localdate()
    supplier = invoice.supplier_snapshot
    number = _next_number(
        CreditNoteSequence,
        prefix=supplier.get("credit_note_prefix", ""),
        issue_date=today,
    )
    amounts = {
        **{key: _decimal_text(value) for key, value in sums.items()},
        "return_ids": [item.pk for item in returned],
        "line_items": selected_lines,
        "invoice_number": invoice.number,
    }
    note = CreditNote.objects.create(
        invoice=invoice,
        number=number,
        note_date=today,
        reason=reason.strip(),
        amounts=amounts,
    )
    record_audit(
        actor=actor,
        action="sales.credit_note.issued",
        instance=note,
        after={
            "number": note.number,
            "invoice_number": invoice.number,
            "sale_id": invoice.sale_id,
            "return_ids": amounts["return_ids"],
            "total_amount": amounts["total_amount"],
        },
    )
    return note


def invoice_pdf(invoice):
    rows = [
        ("Invoice number", invoice.number),
        ("Issue date", invoice.issue_date.isoformat()),
        ("Supplier", invoice.supplier_snapshot["name"]),
        ("Supplier address", invoice.supplier_snapshot["address"]),
        ("Supplier GSTIN", invoice.supplier_snapshot["gstin"]),
        ("Buyer", invoice.buyer_snapshot["name"]),
        ("Buyer GSTIN", invoice.buyer_snapshot.get("gstin") or "Not provided"),
        ("Billing address", invoice.buyer_snapshot["billing_address"]),
        ("Delivery address", invoice.buyer_snapshot["delivery_address"]),
        (
            "Place of supply",
            f'{invoice.buyer_snapshot["state_name"]} '
            f'({invoice.buyer_snapshot["state_code"]})',
        ),
    ]
    for index, line in enumerate(invoice.line_items, start=1):
        rows.extend(
            (
                (f"Line {index}", f'{line["description"]} · {line["serial_number"]}'),
                (
                    "HSN / quantity / rate",
                    f'{line["hsn_code"]} · {line["quantity"]} {line["unit"]} · INR {line["rate"]}',
                ),
                (
                    "Taxable / GST",
                    f'INR {line["taxable_value"]} · {line["gst_rate_percent"]}%',
                ),
                (
                    "CGST / SGST / IGST",
                    f'INR {line["cgst_amount"]} / {line["sgst_amount"]} / {line["igst_amount"]}',
                ),
                (
                    "MRP / net quantity",
                    f'INR {line["mrp_inclusive_of_all_taxes"]} inclusive of all taxes · {line["net_quantity_text"]}',
                ),
                (
                    "Manufactured",
                    line["manufactured_month_year"],
                ),
                (
                    "Packer",
                    f'{line["packer_manufacturer_name"]} · {line["packer_address"]}',
                ),
                (
                    "Consumer care",
                    f'{line["consumer_care_name"]} · {line["consumer_care_address"]} · '
                    f'{line["consumer_care_phone"]} · {line["consumer_care_email"]}',
                ),
                ("Country of origin", line["country_of_origin"]),
            )
        )
    rows.extend(
        (
            ("Reverse charge", "No"),
            ("Subtotal / taxable value", f"INR {invoice.subtotal:.2f}"),
            ("CGST", f'INR {invoice.tax_breakup["cgst_amount"]}'),
            ("SGST", f'INR {invoice.tax_breakup["sgst_amount"]}'),
            ("IGST", f'INR {invoice.tax_breakup["igst_amount"]}'),
            ("Total tax", f"INR {invoice.total_tax:.2f}"),
            ("Total", f"INR {invoice.total_amount:.2f}"),
            ("Amount in words", invoice.amount_in_words),
            ("Authorised signatory", invoice.supplier_snapshot["authorised_signatory_name"]),
            ("", "Signature"),
        )
    )
    return pdf_bytes("Tax Invoice", ("Field", "Details"), rows)


def credit_note_pdf(note):
    invoice = note.invoice
    amounts = note.amounts
    rows = [
        ("Credit note number", note.number),
        ("Date", note.note_date.isoformat()),
        ("Reference invoice", invoice.number),
        ("Reason", note.reason),
        ("Supplier", invoice.supplier_snapshot["name"]),
        ("Buyer", invoice.buyer_snapshot["name"]),
    ]
    for line in amounts["line_items"]:
        rows.append(
            (
                f'{line["serial_number"]} · {line["description"]}',
                f'Taxable INR {line["taxable_value"]}; CGST INR {line["cgst_amount"]}; '
                f'SGST INR {line["sgst_amount"]}; IGST INR {line["igst_amount"]}; '
                f'total INR {line["total_amount"]}',
            )
        )
    rows.extend(
        (
            ("Taxable amount", f'INR {amounts["taxable_value"]}'),
            ("CGST", f'INR {amounts["cgst_amount"]}'),
            ("SGST", f'INR {amounts["sgst_amount"]}'),
            ("IGST", f'INR {amounts["igst_amount"]}'),
            ("Total tax", f'INR {amounts["total_tax"]}'),
            ("Credit total", f'INR {amounts["total_amount"]}'),
            ("Amount in words", amount_in_words(amounts["total_amount"])),
            ("Authorised signatory", invoice.supplier_snapshot["authorised_signatory_name"]),
        )
    )
    return pdf_bytes("Credit Note", ("Field", "Details"), rows)
