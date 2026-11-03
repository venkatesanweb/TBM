from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape

from django.db.models import (
    Count,
    DecimalField,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.dashboard_access import (
    FINISHED_GOODS,
    INVENTORY,
    PAYMENTS,
    PRODUCTION,
    REPORT_SECTIONS,
    RETURNS,
    SALES_OVERVIEW,
    require_dashboard_section,
    require_report_export,
)
from accounts.models import User
from audit.services import record_audit
from catalog.models import Component
from sales.models import Sale, SaleReturn, SaleStatusHistory, SaleUnit
from workorders.models import FinishedUnit, WorkOrder


ZERO_MONEY = Value(Decimal("0.00"), output_field=DecimalField(max_digits=20, decimal_places=2))
REVENUE_FIELD = DecimalField(max_digits=20, decimal_places=2)
SALE_STATUSES = (
    Sale.Status.READY_TO_SHIP,
    Sale.Status.DELIVERED,
    Sale.Status.PAYMENT_PENDING,
    Sale.Status.PAYMENT_RECEIVED,
    Sale.Status.RETURNED,
)
OPEN_WORK_ORDER_STATUSES = (
    WorkOrder.Status.DRAFT,
    WorkOrder.Status.RESERVED,
    WorkOrder.Status.IN_PROGRESS,
    WorkOrder.Status.PACKED,
    WorkOrder.Status.BLOCKED,
)


def dashboard_period(params):
    range_name = params.get("range", "30_days")
    today = timezone.localdate()
    if range_name == "today":
        start, end = today, today
    elif range_name == "7_days":
        start, end = today - timedelta(days=6), today
    elif range_name == "30_days":
        start, end = today - timedelta(days=29), today
    elif range_name == "this_month":
        start, end = today.replace(day=1), today
    elif range_name == "this_year":
        start, end = today.replace(month=1, day=1), today
    elif range_name == "custom":
        start = parse_date(params.get("date_from", ""))
        end = parse_date(params.get("date_to", ""))
        if start is None or end is None:
            raise ValidationError({"date_range": "Choose both dates for a custom range (YYYY-MM-DD)."})
        if end < start:
            raise ValidationError({"date_range": "The end date must be on or after the start date."})
    else:
        raise ValidationError({"range": "Choose today, 7_days, 30_days, this_month, this_year, or custom."})
    return {"range": range_name, "date_from": start.isoformat(), "date_to": end.isoformat(), "_start": start, "_end": end}


def sales_in_period(period):
    return Sale.objects.filter(sale_date__range=(period["_start"], period["_end"]))


def sale_unit_total_subquery():
    return (
        SaleUnit.objects.filter(sale_id=OuterRef("pk"))
        .order_by()
        .values("sale_id")
        .annotate(total=Count("pk"))
        .values("total")[:1]
    )


def sale_return_total_subquery():
    return (
        SaleUnit.objects.filter(sale_id=OuterRef("pk"))
        .order_by()
        .values("sale_id")
        .annotate(total=Count("return_record__pk"))
        .values("total")[:1]
    )


def sale_totals(period):
    return (
        sales_in_period(period)
        .annotate(
            unit_count=Coalesce(
                Subquery(sale_unit_total_subquery(), output_field=IntegerField()),
                Value(0),
            ),
            returned_count=Coalesce(
                Subquery(sale_return_total_subquery(), output_field=IntegerField()),
                Value(0),
            ),
        )
        .filter(unit_count__gt=0)
    )


def sale_revenue_expression():
    return ExpressionWrapper(
        F("price") * (F("unit_count") - F("returned_count")) / F("unit_count"),
        output_field=REVENUE_FIELD,
    )


def unit_rows(period):
    return (
        SaleUnit.objects.filter(sale__sale_date__range=(period["_start"], period["_end"]))
        .annotate(
            sale_unit_count=Subquery(
                SaleUnit.objects.filter(sale_id=OuterRef("sale_id"))
                .order_by()
                .values("sale_id")
                .annotate(total=Count("pk"))
                .values("total")[:1],
                output_field=IntegerField(),
            )
        )
    )


def sales_overview(period):
    trend_function = "day" if (period["_end"] - period["_start"]).days <= 90 else "month"
    units_sold = 0
    returned_units = 0
    revenue = Decimal(0)
    platform_totals = {}
    status_counts = {status: 0 for status in SALE_STATUSES}
    trend_totals = {}
    for sale in (
        sale_totals(period)
        .order_by()
        .values(
            "sale_date",
            "platform_id",
            "platform__name",
            "status",
            "price",
            "unit_count",
            "returned_count",
        )
        .iterator(chunk_size=2000)
    ):
        unit_count = sale["unit_count"]
        returned_count = sale["returned_count"]
        sale_revenue = sale["price"] * Decimal(unit_count - returned_count) / Decimal(unit_count)
        units_sold += unit_count
        returned_units += returned_count
        revenue += sale_revenue
        platform = platform_totals.setdefault(
            sale["platform_id"],
            {
                "platform_id": sale["platform_id"],
                "platform": sale["platform__name"],
                "units_sold": 0,
                "revenue": Decimal(0),
            },
        )
        platform["units_sold"] += unit_count
        platform["revenue"] += sale_revenue
        status_counts[sale["status"]] = status_counts.get(sale["status"], 0) + unit_count
        bucket = sale["sale_date"]
        if trend_function == "month":
            bucket = bucket.replace(day=1)
        trend = trend_totals.setdefault(
            bucket,
            {"date": bucket.isoformat(), "units_sold": 0, "revenue": Decimal(0)},
        )
        trend["units_sold"] += unit_count
        trend["revenue"] += sale_revenue

    products = (
        SaleUnit.objects.filter(sale__sale_date__range=(period["_start"], period["_end"]))
        .values(
            "finished_unit__product_id",
            "finished_unit__product__product_number",
            "finished_unit__product__name",
        )
        .annotate(units_sold=Count("pk"))
        .order_by("-units_sold", "finished_unit__product__product_number")[:10]
    )
    return {
        "units_sold": units_sold,
        "returned_units": returned_units,
        "revenue": revenue,
        "top_products": [
            {
                "product_id": row["finished_unit__product_id"],
                "product_number": row["finished_unit__product__product_number"],
                "product_name": row["finished_unit__product__name"],
                "units_sold": row["units_sold"],
            }
            for row in products
        ],
        "sales_by_platform": [
            row for row in sorted(
                platform_totals.values(),
                key=lambda item: (-item["units_sold"], item["platform"]),
            )
        ],
        "sales_trend": [
            trend_totals[key] for key in sorted(trend_totals)
        ],
        "status_counts": status_counts,
    }


def payments_summary(period):
    pending = sale_totals(period).filter(status=Sale.Status.PAYMENT_PENDING)
    collected = sale_totals(period).filter(status=Sale.Status.PAYMENT_RECEIVED)
    status_rows = dict(
        sale_totals(period)
        .values_list("status")
        .annotate(units=Sum("unit_count"))
    )
    now = timezone.now()
    status_since = Subquery(
        SaleStatusHistory.objects.filter(
            sale_id=OuterRef("pk"),
            to_status=OuterRef("status"),
        ).order_by("-timestamp").values("timestamp")[:1]
    )
    pending_with_age = pending.annotate(
        status_since=Coalesce(status_since, F("created_at"))
    )
    age_7 = now - timedelta(days=7)
    age_30 = now - timedelta(days=30)
    ageing = pending_with_age.aggregate(
        under_7_days=Count("pk", filter=Q(status_since__gte=age_7)),
        from_7_to_30_days=Count("pk", filter=Q(status_since__lt=age_7, status_since__gte=age_30)),
        over_30_days=Count("pk", filter=Q(status_since__lt=age_30)),
    )
    result = pending.aggregate(
        outstanding_amount=Coalesce(Sum(sale_revenue_expression()), ZERO_MONEY),
        pending_sales=Count("pk"),
    )
    result["collected_amount"] = collected.aggregate(
        amount=Coalesce(Sum(sale_revenue_expression()), ZERO_MONEY)
    )["amount"]
    result["status_counts"] = {status: status_rows.get(status, 0) for status in SALE_STATUSES}
    result["unpaid_ageing"] = ageing
    return result


def returns_summary(period):
    units = SaleUnit.objects.filter(sale__sale_date__range=(period["_start"], period["_end"]))
    total_units = units.count()
    returned_units = units.filter(return_record__isnull=False).count()
    grouped = (
        SaleReturn.objects.filter(
            sale_unit__sale__sale_date__range=(period["_start"], period["_end"])
        )
        .values("decision")
        .annotate(units=Count("pk"))
        .order_by("decision")
    )
    reasons = (
        SaleReturn.objects.filter(
            sale_unit__sale__sale_date__range=(period["_start"], period["_end"])
        )
        .values("reason")
        .annotate(units=Count("pk"))
        .order_by("-units", "reason")[:10]
    )
    return {
        "units_sold": total_units,
        "returned_units": returned_units,
        "return_rate": Decimal(returned_units) / Decimal(total_units) if total_units else Decimal(0),
        "decisions": list(grouped),
        "top_return_reasons": list(reasons),
    }


def inventory_summary():
    components = Component.objects.filter(active=True).select_related("stock_balance")
    low_stock = components.filter(
        Q(stock_balance__isnull=True)
        | Q(stock_balance__on_hand__lte=F("stock_balance__reserved") + F("reorder_level"))
    )
    low_stock_rows = [
        {
            "component_id": item.pk,
            "sku": item.sku,
            "name": item.name,
            "unit": item.unit,
            "on_hand": item.stock_balance.on_hand if hasattr(item, "stock_balance") else Decimal(0),
            "reserved": item.stock_balance.reserved if hasattr(item, "stock_balance") else Decimal(0),
            "available": item.stock_balance.available if hasattr(item, "stock_balance") else Decimal(0),
            "reorder_level": item.reorder_level,
        }
        for item in low_stock.order_by("sku")
    ]
    return {
        "low_stock_components": low_stock_rows,
        "low_stock_count": len(low_stock_rows),
        "component_count": components.count(),
    }


def finished_goods_summary():
    rows = (
        FinishedUnit.objects.filter(status=FinishedUnit.Status.IN_STOCK)
        .values("product_id", "product__product_number", "product__name")
        .annotate(units_in_stock=Count("pk"))
        .order_by("product__product_number")
    )
    products = list(rows)
    return {"products": products, "units_in_stock": sum(item["units_in_stock"] for item in products)}


def production_summary(user):
    orders = WorkOrder.objects.filter(status__in=OPEN_WORK_ORDER_STATUSES)
    if user.role != User.Role.SUPER_ADMIN:
        orders = orders.filter(assigned_admin_id=user.pk)
    counts = dict(orders.values_list("priority").annotate(total=Count("pk")))
    due_soon = timezone.localdate() + timedelta(days=7)
    rows = orders.select_related("product", "assigned_admin").order_by("due_date", "pk")
    return {
        "open_by_priority": {
            priority: counts.get(priority, 0)
            for priority, _ in WorkOrder.Priority.choices
        },
        "due_in_7_days": rows.filter(due_date__lte=due_soon).count(),
        "open_work_orders": [
            {
                "id": order.pk,
                "work_order": f"WO-{order.pk}",
                "product_number": order.product.product_number,
                "product_name": order.product.name,
                "quantity": order.quantity,
                "packed_quantity": order.packed_quantity,
                "priority": order.priority,
                "status": order.status,
                "due_date": order.due_date.isoformat(),
            }
            for order in rows[:100]
        ],
    }


def inventory_report_rows(period, offset, limit):
    headers = ["Type", "SKU", "Name", "On hand", "Reserved", "Available", "Reorder level", "Units in stock"]
    components = Component.objects.filter(active=True).select_related("stock_balance").order_by("sku")
    finished = FinishedUnit.objects.filter(status=FinishedUnit.Status.IN_STOCK).values(
        "product__sku", "product__name"
    ).annotate(units=Count("pk")).order_by("product__sku")
    component_count = components.count()
    finished_count = finished.count()
    total = component_count + finished_count
    component_start = offset
    component_end = None if limit is None else min(offset + limit, component_count)
    if limit is not None and offset >= component_count:
        component_start = component_count
        component_end = component_count
    component_rows = components[component_start:component_end]
    rows = []
    for component in component_rows:
        balance = getattr(component, "stock_balance", None)
        rows.append([
            "Raw material",
            component.sku,
            component.name,
            balance.on_hand if balance else Decimal(0),
            balance.reserved if balance else Decimal(0),
            balance.available if balance else Decimal(0),
            component.reorder_level,
            "",
        ])
    if limit is None:
        finished_rows = finished
    else:
        finished_offset = max(0, offset - component_count)
        remaining = limit - len(rows)
        finished_rows = finished[finished_offset:finished_offset + max(0, remaining)]
    for item in finished_rows:
        rows.append(["Finished goods", item["product__sku"], item["product__name"], "", "", "", "", item["units"]])
    return headers, rows, total


def sales_report_rows(period, offset, limit):
    rows = []
    qs = unit_rows(period).select_related(
        "sale__platform",
        "finished_unit__product",
        "return_record",
    ).order_by("sale__sale_date", "sale_id", "pk")
    total = qs.count()
    selected = qs if limit is None else qs[offset:offset + limit]
    for unit in selected.iterator(chunk_size=2000):
        returned = hasattr(unit, "return_record")
        rows.append([
            f"SALE-{unit.sale_id}",
            unit.sale.sale_date.isoformat(),
            unit.sale.platform.name,
            unit.sale.status,
            unit.sale.order_reference,
            unit.finished_unit.serial_number,
            unit.finished_unit.product.product_number,
            unit.finished_unit.product.name,
            Decimal(0) if returned else unit.sale.price / Decimal(unit.sale_unit_count),
            "Returned" if returned else "Sold",
        ])
    return ["Sale", "Date", "Platform", "Status", "Order reference", "Serial", "Product number", "Product", "Revenue", "Unit state"], rows, total


def pending_report_rows(period, offset, limit):
    status_since = Subquery(
        SaleStatusHistory.objects.filter(sale_id=OuterRef("pk"), to_status=OuterRef("status"))
        .order_by("-timestamp").values("timestamp")[:1]
    )
    rows = []
    qs = (
        sale_totals(period)
        .filter(status=Sale.Status.PAYMENT_PENDING)
        .annotate(status_since=Coalesce(status_since, F("created_at")))
        .select_related("platform")
        .order_by("status_since", "pk")
    )
    total = qs.count()
    selected = qs if limit is None else qs[offset:offset + limit]
    for sale in selected.iterator(chunk_size=2000):
        outstanding = sale.price * Decimal(sale.unit_count - sale.returned_count) / Decimal(sale.unit_count)
        rows.append([
            f"SALE-{sale.pk}",
            sale.sale_date.isoformat(),
            sale.platform.name,
            sale.status,
            sale.order_reference,
            sale.unit_count,
            outstanding,
            timezone.localtime(sale.status_since).isoformat(),
        ])
    return ["Sale", "Date", "Platform", "Status", "Order reference", "Units", "Outstanding amount", "Status since"], rows, total


def returns_report_rows(period, offset, limit):
    rows = []
    returns = SaleReturn.objects.filter(
        sale_unit__sale__sale_date__range=(period["_start"], period["_end"])
    ).select_related(
        "sale_unit__sale__platform",
        "sale_unit__finished_unit__product",
        "decided_by",
        "rework_order",
    ).order_by("created_at", "pk")
    total = returns.count()
    selected = returns if limit is None else returns[offset:offset + limit]
    for item in selected.iterator(chunk_size=2000):
        sale = item.sale_unit.sale
        unit = item.sale_unit.finished_unit
        rows.append([
            f"SALE-{sale.pk}",
            sale.sale_date.isoformat(),
            sale.platform.name,
            unit.serial_number,
            unit.product.product_number,
            unit.product.name,
            item.reason,
            item.decision,
            item.decision_note,
            item.decided_by.username if item.decided_by else "",
            item.rework_order_id or "",
        ])
    return ["Sale", "Sale date", "Platform", "Serial", "Product number", "Product", "Reason", "Decision", "Decision note", "Decided by", "Rework work order ID"], rows, total


REPORT_BUILDERS = {
    "sales": sales_report_rows,
    "inventory": inventory_report_rows,
    "payments": pending_report_rows,
    "returns": returns_report_rows,
}

REPORT_TITLES = {
    "sales": "Sales report",
    "inventory": "Inventory report",
    "payments": "Pending payments report",
    "returns": "Returns report",
}


def xlsx_bytes(title, headers, rows):
    def column_name(number):
        name = ""
        while number:
            number, remainder = divmod(number - 1, 26)
            name = chr(65 + remainder) + name
        return name

    def cell_xml(value, row_number, column_number):
        reference = f"{column_name(column_number)}{row_number}"
        value = escape(str(value if value is not None else "")[:32767])
        return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{value}</t></is></c>'

    sheet_rows = [headers, *rows]
    worksheet = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for row_number, row in enumerate(sheet_rows, 1):
        worksheet.append(f'<row r="{row_number}">')
        worksheet.extend(cell_xml(value, row_number, column_number) for column_number, value in enumerate(row, 1))
        worksheet.append("</row>")
    worksheet.append("</sheetData></worksheet>")
    workbook = BytesIO()
    with ZipFile(workbook, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{escape(title[:31])}" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", "".join(worksheet))
    return workbook.getvalue()


def pdf_bytes(title, headers, rows):
    lines = [title, " | ".join(str(value) for value in headers)]
    lines.extend(" | ".join(str(value) for value in row) for row in rows)
    wrapped = []
    for line in lines:
        text = str(line)
        while len(text) > 110:
            wrapped.append(text[:110])
            text = text[110:]
        wrapped.append(text)
    page_lines = [wrapped[index:index + 58] for index in range(0, len(wrapped), 58)] or [[]]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_ids = []
    for index, content_lines in enumerate(page_lines):
        page_id = 4 + index * 2
        content_id = page_id + 1
        page_ids.append(page_id)
        stream_parts = ["BT", "/F1 8 Tf", "36 805 Td", "12 TL"]
        for line in content_lines:
            safe = str(line).encode("latin-1", "replace").decode("latin-1")
            safe = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream_parts.append(f"({safe}) Tj T*")
        stream_parts.append("ET")
        stream = "\n".join(stream_parts).encode("latin-1")
        objects.extend([
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode(),
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        ])
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{item} 0 R' for item in page_ids)}] /Count {len(page_ids)} >>"
    ).encode()
    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(pdf.tell())
        pdf.write(f"{index} 0 obj\n".encode())
        pdf.write(obj)
        pdf.write(b"\nendobj\n")
    xref_at = pdf.tell()
    pdf.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        pdf.write(f"{offset:010d} 00000 n \n".encode())
    pdf.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode())
    return pdf.getvalue()


class DashboardSectionView(APIView):
    section = None

    def get(self, request):
        require_dashboard_section(request.user, self.section)
        period = dashboard_period(request.query_params)
        builders = {
            SALES_OVERVIEW: lambda: sales_overview(period),
            PAYMENTS: lambda: payments_summary(period),
            RETURNS: lambda: returns_summary(period),
            INVENTORY: inventory_summary,
            FINISHED_GOODS: finished_goods_summary,
            PRODUCTION: lambda: production_summary(request.user),
        }
        return Response({"period": {key: value for key, value in period.items() if not key.startswith("_")}, **builders[self.section]()})


class SalesOverviewDashboardView(DashboardSectionView):
    section = SALES_OVERVIEW


class PaymentsDashboardView(DashboardSectionView):
    section = PAYMENTS


class ReturnsDashboardView(DashboardSectionView):
    section = RETURNS


class InventoryDashboardView(DashboardSectionView):
    section = INVENTORY


class FinishedGoodsDashboardView(DashboardSectionView):
    section = FINISHED_GOODS


class ProductionDashboardView(DashboardSectionView):
    section = PRODUCTION


class DashboardReportView(APIView):
    def get(self, request, report_name):
        if report_name not in REPORT_SECTIONS:
            raise ValidationError({"report": "Choose sales, inventory, payments, or returns."})
        require_dashboard_section(request.user, REPORT_SECTIONS[report_name])
        period = dashboard_period(request.query_params)
        export_format = request.query_params.get("export_format", "").lower()
        if export_format:
            if export_format not in {"xlsx", "pdf"}:
                raise ValidationError({"format": "Choose xlsx or pdf."})
            require_report_export(request.user)
            limit = None
            offset = 0
        else:
            try:
                limit = min(1000, max(1, int(request.query_params.get("limit", 500))))
                offset = max(0, int(request.query_params.get("offset", 0)))
            except ValueError as exc:
                raise ValidationError({"pagination": "Limit and offset must be integers."}) from exc
        headers, rows, total = REPORT_BUILDERS[report_name](period, offset, limit)
        if export_format:
            record_audit(
                actor=request.user,
                action="dashboard.report.exported",
                after={
                    "report": report_name,
                    "format": export_format,
                    "date_from": period["date_from"],
                    "date_to": period["date_to"],
                    "rows": total,
                },
            )
            filename = f"{report_name}-report-{period['date_from']}-to-{period['date_to']}.{export_format}"
            if export_format == "xlsx":
                content = xlsx_bytes(REPORT_TITLES[report_name], headers, rows)
                content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                content = pdf_bytes(REPORT_TITLES[report_name], headers, rows)
                content_type = "application/pdf"
            response = HttpResponse(content, content_type=content_type)
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        return Response({
            "period": {key: value for key, value in period.items() if not key.startswith("_")},
            "report": report_name,
            "columns": headers,
            "total": total,
            "rows": rows,
            "limit": limit,
            "offset": offset,
        })
