import base64
import binascii
import struct
from datetime import date
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.services import record_audit
from catalog.models import CompanyProfile
from sales.models import LabelPrintLog, LabelTemplate, SaleUnit, SalesPlatform


LABEL_PDF_DPI = 300
LABEL_PDF_MIN_FONT_PT = 6
LABEL_PDF_BODY_FONT_PT = 7
LABEL_PDF_MAX_BARCODE_BYTES = 1_000_000
LABEL_PDF_MAX_BARCODE_PIXELS = 1_000_000


def _format_indian_mrp(amount):
    whole, fraction = amount.split(".", 1)
    if len(whole) <= 3:
        return f"{whole}.{fraction}"
    prefix, final = whole[:-3], whole[-3:]
    groups = []
    while prefix:
        groups.insert(0, prefix[-2:])
        prefix = prefix[:-2]
    return f"{','.join((*groups, final))}.{fraction}"


def _company_return_address(profile):
    name = profile.trade_name or profile.legal_name
    address = ", ".join(
        value
        for value in (
            profile.address_line1,
            profile.address_line2,
            profile.city,
            profile.state_code.name if profile.state_code_id else "",
            profile.pincode,
        )
        if value
    )
    return ", ".join(value for value in (name, address) if value)


def _template_data(template):
    return {
        "id": template.pk,
        "name": template.name,
        "width_mm": template.width_mm,
        "height_mm": template.height_mm,
        "field_toggles": template.field_toggles,
    }


def _label_data(sale, sale_unit, template, profile, invoice):
    unit = sale_unit.finished_unit
    product = unit.product
    packed_at = (
        timezone.localtime(unit.pack_confirmation.created_at)
        if unit.pack_confirmation_id
        else None
    )
    company_return_address = _company_return_address(profile)
    delivery_address = sale.delivery_address or sale.shipping_address
    if sale.shipping_city or sale.shipping_state or sale.shipping_postal_code:
        delivery_address = ", ".join(
            value
            for value in (
                delivery_address,
                sale.shipping_city,
                sale.shipping_state,
                sale.shipping_postal_code,
            )
            if value
        )
    customer_name = sale.customer_name or sale.shipping_name
    phone = sale.customer_phone or sale.shipping_phone
    toggles = template.field_toggles
    missing = []

    def require(value, label):
        if value is None or not str(value).strip():
            missing.append(label)

    require(customer_name, "Customer name")
    require(delivery_address, "Delivery address")
    require(phone, "Customer phone")
    require(
        profile.address_line1 or profile.address_line2,
        "Company return address",
    )
    require(sale_unit.mrp_snapshot, "MRP")
    require(product.net_quantity_text, "Net quantity")
    require(packed_at, "Manufacture / packing month and year")
    require(profile.packer_manufacturer_name, "Packer / manufacturer name")
    require(profile.packer_address, "Packer / manufacturer address")
    require(profile.country_of_origin, "Country of origin")
    require(profile.consumer_care_name, "Consumer care name")
    require(profile.consumer_care_address, "Consumer care address")
    require(profile.consumer_care_phone, "Consumer care phone")
    require(profile.consumer_care_email, "Consumer care email")

    if not product.name:
        missing.append("Product name")
    if not product.sku:
        missing.append("Product SKU")
    if not unit.serial_number:
        missing.append("Unit serial number")
    if not unit.barcode_value:
        missing.append("Unit Code128 barcode value")

    data = {
        "sale_id": sale.pk,
        "sale_unit_id": sale_unit.pk,
        "serial_number": unit.serial_number,
        "barcode_value": unit.barcode_value,
        "customer_name": customer_name,
        "delivery_address": delivery_address,
        "customer_phone": phone,
        "order_reference": sale.order_reference or f"SALE-{sale.pk}",
        "invoice_number": invoice.number if invoice else "",
        "display_number": "",
        "display_number_label": "",
        "sale_date": sale.sale_date.isoformat(),
        "company_return_address": company_return_address,
        "company_trade_name": profile.trade_name,
        "company_logo": profile.logo.url if profile.logo else "",
        "product_name": product.name,
        "product_generic_name": product.generic_name or "",
        "product_sku": product.sku,
        "revision_number": unit.product_revision.revision_number,
        "mrp": f"{sale_unit.mrp_snapshot:.2f}" if sale_unit.mrp_snapshot is not None else "",
        "net_quantity_text": product.net_quantity_text or "",
        "manufactured_month_year": packed_at.strftime("%B %Y") if packed_at else "",
        "packer_manufacturer_name": profile.packer_manufacturer_name,
        "packer_address": profile.packer_address,
        "country_of_origin": profile.country_of_origin,
        "consumer_care_name": profile.consumer_care_name,
        "consumer_care_address": profile.consumer_care_address,
        "consumer_care_phone": profile.consumer_care_phone,
        "consumer_care_email": profile.consumer_care_email,
        "field_toggles": toggles,
        "missing_fields": missing,
    }
    if not toggles.get("show_order_reference", True):
        data["order_reference"] = ""
    if not toggles.get("show_invoice_number", True):
        data["invoice_number"] = ""
    if data["invoice_number"]:
        data["display_number"] = data["invoice_number"]
        data["display_number_label"] = "INVOICE"
    elif toggles.get("show_order_reference", True):
        data["display_number"] = data["order_reference"]
        data["display_number_label"] = "ORDER"
    if not data["display_number"]:
        missing.append("Order or invoice number")
    if not toggles.get("show_company_trade_name", True):
        data["company_trade_name"] = ""
    if not toggles.get("show_company_logo", True):
        data["company_logo"] = ""
    if not toggles.get("show_product_generic_name", True):
        data["product_generic_name"] = ""
    return data


def sale_label_preview(*, sale):
    if sale.platform.label_template_type != SalesPlatform.LabelTemplateType.TAMIRABOT:
        return {
            "generated": False,
            "message": "This platform supplies its own label; Tamirabot does not generate one.",
            "template": None,
            "labels": [],
        }

    template = LabelTemplate.objects.filter(
        platform=sale.platform,
        is_default=True,
    ).first()
    if template is None:
        return {
            "generated": True,
            "message": "",
            "template": None,
            "labels": [],
            "missing_fields": ["No default label template is configured for this platform."],
        }

    profile = CompanyProfile.objects.select_related("state_code").filter(pk=1).first()
    if profile is None:
        return {
            "generated": True,
            "message": "",
            "template": _template_data(template),
            "labels": [],
            "missing_fields": ["Company profile is not configured."],
        }

    invoice = getattr(sale, "invoice", None)
    units = sale.units.select_related(
        "finished_unit__product",
        "finished_unit__product_revision",
        "finished_unit__pack_confirmation",
    ).order_by("finished_unit__serial_number")
    labels = [
        _label_data(sale, sale_unit, template, profile, invoice)
        for sale_unit in units
    ]
    return {
        "generated": True,
        "message": "",
        "template": _template_data(template),
        "labels": labels,
        "can_print": bool(labels) and all(not row["missing_fields"] for row in labels),
    }


@transaction.atomic
def record_label_print(*, sale_unit_id, template_id, actor):
    sale_unit = (
        SaleUnit.objects.select_for_update()
        .select_related("sale", "finished_unit")
        .get(pk=sale_unit_id)
    )
    template = LabelTemplate.objects.select_for_update().get(pk=template_id)
    if template.platform_id != sale_unit.sale.platform_id:
        raise ValidationError({"template": "The label template does not belong to this sale platform."})
    is_reprint = LabelPrintLog.objects.filter(
        sale_unit=sale_unit,
        template=template,
    ).exists()
    log = LabelPrintLog.objects.create(
        sale_unit=sale_unit,
        template=template,
        printed_by=actor,
        is_reprint=is_reprint,
    )
    record_audit(
        actor=actor,
        action="sales.label.reprinted" if is_reprint else "sales.label.printed",
        instance=log,
        after={
            "sale_id": sale_unit.sale_id,
            "serial_number": sale_unit.finished_unit.serial_number,
            "template_id": template.pk,
            "is_reprint": is_reprint,
        },
    )
    return log


def build_label_for_unit(sale, sale_unit, template):
    profile = CompanyProfile.objects.select_related("state_code").filter(pk=1).first()
    if profile is None:
        raise ValidationError({"missing_fields": ["Company profile is not configured."]})
    invoice = getattr(sale, "invoice", None)
    data = _label_data(sale, sale_unit, template, profile, invoice)
    if data["missing_fields"]:
        raise ValidationError({"missing_fields": data["missing_fields"]})
    return data


def _decode_barcode_png(value):
    if not isinstance(value, str) or not value.startswith("data:image/png;base64,"):
        raise ValidationError({"barcodes": "A generated PNG Code128 barcode is required."})
    try:
        image_bytes = base64.b64decode(value.partition(",")[2], validate=True)
        if len(image_bytes) > LABEL_PDF_MAX_BARCODE_BYTES:
            raise ValidationError({"barcodes": "The generated barcode image is too large."})
        if len(image_bytes) < 24 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValidationError({"barcodes": "Barcode images must be PNG."})
        width, height = struct.unpack(">II", image_bytes[16:24])
        if (
            width < 100
            or height < 15
            or width * height > LABEL_PDF_MAX_BARCODE_PIXELS
        ):
            raise ValidationError({"barcodes": "The generated barcode image has invalid dimensions."})
        with Image.open(BytesIO(image_bytes)) as source:
            if source.format != "PNG":
                raise ValidationError({"barcodes": "Barcode images must be PNG."})
            source.load()
            return source.convert("RGB")
    except (binascii.Error, UnidentifiedImageError, OSError) as exc:
        raise ValidationError({"barcodes": "The generated barcode image is invalid."}) from exc


def _label_page(label, barcode, logo, width_mm, height_mm):
    px_per_mm = LABEL_PDF_DPI / 25.4
    width = round(width_mm * px_per_mm)
    height = round(height_mm * px_per_mm)
    padding = round(4 * px_per_mm)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    available_width = width - padding * 2
    text_blocks = []
    toggles = label["field_toggles"]

    def add(text, *, heading=False, optional=False):
        if text and (not optional or text.strip()):
            text_blocks.append((str(text), heading))

    if logo is not None:
        logo.thumbnail((round(24 * px_per_mm), round(12 * px_per_mm)))
        logo_x = width - padding - logo.width
        image.paste(logo, (logo_x, padding), logo if logo.mode == "RGBA" else None)
        logo_bottom = padding + logo.height
    else:
        logo_bottom = padding
    add(label["company_trade_name"] if toggles.get("show_company_trade_name") else "")
    add("SHIP TO", heading=True)
    add(label["customer_name"])
    add(label["delivery_address"])
    add(f"Phone: {label['customer_phone']}")
    add(f"{label['display_number_label']}: {label['display_number']}")
    add(f"Date: {date.fromisoformat(label['sale_date']).strftime('%d-%b-%Y')}")
    add(f"Return address: {label['company_return_address']}")
    add("PRODUCT", heading=True)
    add(label["product_name"])
    if toggles.get("show_product_generic_name"):
        add(label["product_generic_name"])
    add(f"SKU: {label['product_sku']} · Revision: {label['revision_number']}")
    add(f"Serial: {label['serial_number']}")
    add("LEGAL METROLOGY DECLARATIONS", heading=True)
    add(f"MRP: Rs {_format_indian_mrp(label['mrp'])} incl. of all taxes")
    add(f"Net quantity: {label['net_quantity_text']}")
    add(f"Manufactured / packed: {label['manufactured_month_year']}")
    add(f"Mfd/Pkd by: {label['packer_manufacturer_name']}, {label['packer_address']}")
    add(f"Country of origin: {label['country_of_origin']}")
    add(f"Consumer care: {label['consumer_care_name']}, {label['consumer_care_address']}, {label['consumer_care_phone']}, {label['consumer_care_email']}")

    barcode_max_width = round(available_width * 0.88)
    barcode_height = round(8 * px_per_mm)
    barcode_ratio = barcode.width / barcode.height
    barcode_width = min(barcode_max_width, round(barcode_height * barcode_ratio))
    barcode_height = max(1, round(barcode_width / barcode_ratio))
    barcode = barcode.resize((barcode_width, barcode_height), Image.Resampling.NEAREST)

    for font_pt in range(LABEL_PDF_BODY_FONT_PT * 4, LABEL_PDF_MIN_FONT_PT * 4 - 1, -1):
        body_font = ImageFont.load_default(
            size=max(1, round(font_pt / 4 * LABEL_PDF_DPI / 72))
        )
        heading_font = ImageFont.load_default(
            size=max(1, round((font_pt / 4 + 1) * LABEL_PDF_DPI / 72))
        )
        line_height = round(font_pt / 4 * LABEL_PDF_DPI / 72 * 1.35)
        wrapped_blocks = []
        for text, heading in text_blocks:
            font = heading_font if heading else body_font
            words = text.split()
            lines = []
            line = ""
            for word in words:
                candidate = f"{line} {word}".strip()
                if draw.textlength(candidate, font=font) <= available_width:
                    line = candidate
                    continue
                if line:
                    lines.append(line)
                    line = ""
                while word and draw.textlength(word, font=font) > available_width:
                    prefix = word
                    while prefix and draw.textlength(prefix, font=font) > available_width:
                        prefix = prefix[:-1]
                    lines.append(prefix)
                    word = word[len(prefix):]
                line = word
            if line:
                lines.append(line)
            wrapped_blocks.append((lines, heading))

        text_height = sum(
            line_height * max(1, len(lines)) + (line_height // 3 if heading else 0)
            for lines, heading in wrapped_blocks
        )
        estimated_total = text_height + barcode_height + line_height * 2
        if estimated_total <= height - padding * 2:
            break
    else:
        raise ValidationError({
            "label": (
                "Label content does not fit at the minimum 6 pt text size. "
                "Choose a larger paper size or shorten the source addresses."
            )
        })

    y = max(padding, logo_bottom) + line_height // 3
    for index, (lines, heading) in enumerate(wrapped_blocks):
        if heading:
            y += line_height // 3
        font = heading_font if heading else body_font
        for line in lines:
            draw.text((padding, y), line, fill="black", font=font)
            y += line_height
        if heading:
            y += line_height // 4
        if index in (0, 6, 12):
            y += line_height // 3

    barcode_x = (width - barcode.width) // 2
    image.paste(barcode, (barcode_x, y))
    return image


def sale_labels_pdf(*, labels, barcodes, template):
    barcode_by_unit = {
        row["sale_unit_id"]: _decode_barcode_png(row["image"])
        for row in barcodes
    }
    if set(barcode_by_unit) != {label["sale_unit_id"] for label in labels}:
        raise ValidationError({"barcodes": "Provide one generated barcode image for every sale unit."})
    logo = None
    if labels and labels[0]["field_toggles"].get("show_company_logo"):
        profile = CompanyProfile.objects.filter(pk=1).first()
        if profile and profile.logo:
            try:
                with profile.logo.open("rb") as logo_file:
                    with Image.open(logo_file) as source:
                        logo = source.convert("RGBA")
            except (OSError, UnidentifiedImageError) as exc:
                raise ValidationError({"company_logo": "The configured company logo could not be read."}) from exc
    pages = [
        _label_page(
            label,
            barcode_by_unit[label["sale_unit_id"]],
            logo.copy() if logo is not None else None,
            template.width_mm,
            template.height_mm,
        )
        for label in labels
    ]
    output = BytesIO()
    pages[0].save(
        output,
        format="PDF",
        resolution=LABEL_PDF_DPI,
        save_all=True,
        append_images=pages[1:],
    )
    return output.getvalue()
