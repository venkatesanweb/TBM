import json

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSuperAdmin
from accounts.dashboard_access import SALES_OVERVIEW
from accounts.models import User
from audit.services import record_audit
from catalog.models import IndianState, Product
from sales.models import (
    CreditNote,
    Invoice,
    LabelTemplate,
    Sale,
    SaleUnit,
    SalesPlatform,
)
from sales.serializers import (
    LabelTemplateSerializer,
    SaleBulkStatusSerializer,
    SaleCreateSerializer,
    SaleReturnDecisionSerializer,
    SaleReturnRequestSerializer,
    SaleSerializer,
    SaleStatusTransitionSerializer,
    SaleUpdateSerializer,
    SalesPlatformSerializer,
)
from sales.services import (
    bulk_transition_by_serials,
    create_sale,
    decide_sale_return,
    platform_snapshot,
    return_sale,
    transition_sale,
    update_sale,
)
from sales.invoices import (
    available_credit_note_returns,
    credit_note_pdf,
    invoice_pdf,
    issue_credit_note,
    issue_invoice,
)
from sales.labels import (
    build_label_for_unit,
    record_label_print,
    sale_labels_pdf,
    sale_label_preview,
)


def sale_queryset():
    return (
        Sale.objects.select_related("platform", "created_by")
        .prefetch_related(
            "units__finished_unit__product",
            "units__finished_unit__product_revision",
            "status_history__actor",
            "units__return_record__decided_by",
            "units__return_record__rework_order",
        )
    )


def _can_access_sale_invoice(user, sale):
    return user.role == User.Role.SUPER_ADMIN or (
        user.role == User.Role.ADMIN
        and user.pk == sale.created_by_id
        and SALES_OVERVIEW in user.dashboard_sections
    )


def _invoice_data(invoice):
    return {
        "id": invoice.pk,
        "number": invoice.number,
        "issue_date": invoice.issue_date.isoformat(),
        "supplier_snapshot": invoice.supplier_snapshot,
        "buyer_snapshot": invoice.buyer_snapshot,
        "line_items": invoice.line_items,
        "tax_breakup": invoice.tax_breakup,
        "subtotal": f"{invoice.subtotal:.2f}",
        "total_tax": f"{invoice.total_tax:.2f}",
        "total_amount": f"{invoice.total_amount:.2f}",
        "amount_in_words": invoice.amount_in_words,
        "is_cancelled": invoice.is_cancelled,
        "credit_notes": [
            {
                "id": note.pk,
                "number": note.number,
                "date": note.note_date.isoformat(),
                "reason": note.reason,
                "amounts": note.amounts,
            }
            for note in invoice.credit_notes.all()
        ],
        "available_return_serials": [
            item.sale_unit.finished_unit.serial_number
            for item in available_credit_note_returns(invoice)
        ],
    }


class SalesStateListView(APIView):
    def get(self, request):
        return Response(
            list(
                IndianState.objects.values("state_code", "name").order_by(
                    "state_code"
                )
            )
        )


class SalesPlatformListCreateView(APIView):
    def get(self, request):
        platforms = SalesPlatform.objects.all()
        if request.user.role != User.Role.SUPER_ADMIN:
            platforms = platforms.filter(active=True)
        return Response(SalesPlatformSerializer(platforms, many=True).data)

    def post(self, request):
        if request.user.role != User.Role.SUPER_ADMIN:
            raise PermissionDenied("Only a Super Admin may manage selling platforms.")
        serializer = SalesPlatformSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        platform = serializer.save()
        record_audit(
            actor=request.user,
            action="sales.platform.created",
            instance=platform,
            after=platform_snapshot(platform),
        )
        return Response(
            SalesPlatformSerializer(platform).data,
            status=status.HTTP_201_CREATED,
        )


class SalesPlatformDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    @transaction.atomic
    def patch(self, request, platform_id):
        platform = get_object_or_404(
            SalesPlatform.objects.select_for_update(), pk=platform_id
        )
        before = platform_snapshot(platform)
        serializer = SalesPlatformSerializer(
            platform, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        platform = serializer.save()
        record_audit(
            actor=request.user,
            action="sales.platform.updated",
            instance=platform,
            before=before,
            after=platform_snapshot(platform),
        )
        return Response(SalesPlatformSerializer(platform).data)


class LabelTemplateListCreateView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        templates = LabelTemplate.objects.select_related("platform").all()
        return Response(LabelTemplateSerializer(templates, many=True).data)

    @transaction.atomic
    def post(self, request):
        serializer = LabelTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        platform = SalesPlatform.objects.select_for_update().get(
            pk=serializer.validated_data["platform"].pk
        )
        if serializer.validated_data.get("is_default"):
            LabelTemplate.objects.select_for_update().filter(
                platform=platform,
                is_default=True,
            ).update(is_default=False)
        template = serializer.save()
        record_audit(
            actor=request.user,
            action="sales.label_template.created",
            instance=template,
            after=LabelTemplateSerializer(template).data,
        )
        return Response(
            LabelTemplateSerializer(template).data,
            status=status.HTTP_201_CREATED,
        )


class LabelTemplateDetailView(APIView):
    permission_classes = [IsSuperAdmin]

    @transaction.atomic
    def patch(self, request, template_id):
        current_platform_id = get_object_or_404(
            LabelTemplate.objects.only("platform_id"),
            pk=template_id,
        ).platform_id
        SalesPlatform.objects.select_for_update().get(pk=current_platform_id)
        template = get_object_or_404(
            LabelTemplate.objects.select_for_update().select_related("platform"),
            pk=template_id,
        )
        before = LabelTemplateSerializer(template).data
        serializer = LabelTemplateSerializer(
            template,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        if serializer.validated_data.get("is_default"):
            LabelTemplate.objects.select_for_update().filter(
                platform=template.platform,
                is_default=True,
            ).exclude(pk=template.pk).update(is_default=False)
        template = serializer.save()
        record_audit(
            actor=request.user,
            action="sales.label_template.updated",
            instance=template,
            before=before,
            after=LabelTemplateSerializer(template).data,
        )
        return Response(LabelTemplateSerializer(template).data)


class SaleLabelListView(APIView):
    def get(self, request, sale_id):
        sale = get_object_or_404(
            Sale.objects.select_related("platform", "created_by").prefetch_related(
                "units__finished_unit__product",
                "units__finished_unit__product_revision",
                "units__finished_unit__pack_confirmation",
            ),
            pk=sale_id,
        )
        if not _can_access_sale_invoice(request.user, sale):
            raise PermissionDenied(
                "Only the Super Admin or responsible Sales admin may view legal labels."
            )
        return Response(sale_label_preview(sale=sale))


class SaleLabelPrintView(APIView):
    def post(self, request, sale_id, sale_unit_id):
        sale = get_object_or_404(
            Sale.objects.select_related("platform", "created_by"),
            pk=sale_id,
        )
        if not _can_access_sale_invoice(request.user, sale):
            raise PermissionDenied(
                "Only the Super Admin or responsible Sales admin may print legal labels."
            )
        if sale.platform.label_template_type != SalesPlatform.LabelTemplateType.TAMIRABOT:
            raise ValidationError(
                {"label": "This platform supplies its own label; Tamirabot does not generate one."}
            )
        template = get_object_or_404(
            LabelTemplate,
            platform=sale.platform,
            is_default=True,
        )
        sale_unit = get_object_or_404(
            SaleUnit.objects.select_related(
                "sale",
                "finished_unit__product",
                "finished_unit__product_revision",
                "finished_unit__pack_confirmation",
            ),
            pk=sale_unit_id,
            sale=sale,
        )
        label = build_label_for_unit(sale, sale_unit, template)
        log = record_label_print(
            sale_unit_id=sale_unit.pk,
            template_id=template.pk,
            actor=request.user,
        )
        return Response(
            {
                "label": label,
                "is_reprint": log.is_reprint,
                "printed_at": log.printed_at.isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )


class SaleLabelsPdfView(APIView):
    @transaction.atomic
    def post(self, request, sale_id):
        sale = get_object_or_404(
            Sale.objects.select_related("platform", "created_by"),
            pk=sale_id,
        )
        if not _can_access_sale_invoice(request.user, sale):
            raise PermissionDenied(
                "Only the Super Admin or responsible Sales admin may download legal labels."
            )
        if sale.platform.label_template_type != SalesPlatform.LabelTemplateType.TAMIRABOT:
            raise ValidationError(
                {"label": "This platform supplies its own label; Tamirabot does not generate one."}
            )
        template = get_object_or_404(
            LabelTemplate,
            platform=sale.platform,
            is_default=True,
        )
        preview = sale_label_preview(sale=sale)
        if not preview.get("can_print"):
            missing = list(preview.get("missing_fields", []))
            missing.extend(
                field
                for label in preview.get("labels", [])
                for field in label["missing_fields"]
            )
            raise ValidationError({
                "missing_fields": missing or ["No finished units are available for this sale."]
            })

        sale_units = list(
            SaleUnit.objects.select_related("finished_unit")
            .filter(sale=sale)
            .order_by("finished_unit__serial_number")
        )
        barcode_rows = request.data.get("barcodes")
        if not isinstance(barcode_rows, list):
            raise ValidationError({"barcodes": "Provide the generated Code128 barcode images."})
        expected_barcodes = {
            unit.pk: unit.finished_unit.barcode_value for unit in sale_units
        }
        provided_barcodes = {}
        for row in barcode_rows:
            if not isinstance(row, dict):
                raise ValidationError({"barcodes": "Each barcode must include its sale unit and image."})
            try:
                sale_unit_id = int(row["sale_unit_id"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError({"barcodes": "Each barcode must identify a valid sale unit."}) from exc
            if (
                sale_unit_id not in expected_barcodes
                or row.get("barcode_value") != expected_barcodes[sale_unit_id]
                or sale_unit_id in provided_barcodes
            ):
                raise ValidationError({"barcodes": "Barcode values must match each sale unit exactly once."})
            provided_barcodes[sale_unit_id] = row.get("image")

        labels_by_unit = {label["sale_unit_id"]: label for label in preview["labels"]}
        pdf = sale_labels_pdf(
            labels=[labels_by_unit[unit.pk] for unit in sale_units],
            barcodes=[
                {"sale_unit_id": unit.pk, "image": provided_barcodes.get(unit.pk)}
                for unit in sale_units
            ],
            template=template,
        )
        print_results = []
        for unit in sale_units:
            log = record_label_print(
                sale_unit_id=unit.pk,
                template_id=template.pk,
                actor=request.user,
            )
            print_results.append({
                "serial_number": unit.finished_unit.serial_number,
                "is_reprint": log.is_reprint,
            })

        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="SALE-{sale.pk}-labels.pdf"'
        )
        response["X-Label-Print-Results"] = json.dumps(print_results)
        return response


class SaleListCreateView(APIView):
    def get(self, request):
        sales = sale_queryset()
        params = request.query_params
        if params.get("status"):
            if params["status"] not in Sale.Status.values:
                raise ValidationError({"status": "Choose a valid sale status."})
            sales = sales.filter(status=params["status"])
        if params.get("platform"):
            try:
                sales = sales.filter(platform_id=int(params["platform"]))
            except ValueError as exc:
                raise ValidationError({"platform": "Provide a valid platform ID."}) from exc
        if params.get("product"):
            try:
                product = Product.objects.get(pk=int(params["product"]))
            except (ValueError, Product.DoesNotExist) as exc:
                raise ValidationError({"product": "Choose a valid product."}) from exc
            sales = sales.filter(units__finished_unit__product=product)
        for param, lookup in (
            ("date_from", "sale_date__gte"),
            ("date_to", "sale_date__lte"),
        ):
            value = params.get(param)
            if value:
                parsed = parse_date(value)
                if parsed is None:
                    raise ValidationError({param: "Use an ISO date (YYYY-MM-DD)."})
                sales = sales.filter(**{lookup: parsed})
        return Response(
            SaleSerializer(
                sales.distinct(),
                many=True,
                context={"request": request},
            ).data
        )

    def post(self, request):
        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = create_sale(values=serializer.validated_data, actor=request.user)
        sale = sale_queryset().get(pk=sale.pk)
        return Response(
            SaleSerializer(sale, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class SaleDetailView(APIView):
    def get(self, request, sale_id):
        sale = get_object_or_404(sale_queryset(), pk=sale_id)
        return Response(SaleSerializer(sale, context={"request": request}).data)

    def patch(self, request, sale_id):
        sale = get_object_or_404(sale_queryset(), pk=sale_id)
        serializer = SaleUpdateSerializer(
            instance=sale,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        sale = update_sale(
            sale_id=sale.pk,
            values=serializer.validated_data,
            actor=request.user,
        )
        sale = sale_queryset().get(pk=sale.pk)
        return Response(SaleSerializer(sale, context={"request": request}).data)


class SaleScanLookupView(APIView):
    def get(self, request):
        serial_number = request.query_params.get("serial_number", "").strip()
        if not serial_number:
            raise ValidationError({"serial_number": "Scan or enter a finished-unit serial."})
        sale_unit = (
            SaleUnit.objects.select_related("sale")
            .filter(finished_unit__serial_number__iexact=serial_number)
            .first()
        )
        if sale_unit is None:
            raise NotFound("This finished-unit serial is not linked to a sale.")
        return Response(
            SaleSerializer(
                sale_queryset().get(pk=sale_unit.sale_id),
                context={"request": request},
            ).data
        )


class SaleStatusView(APIView):
    def post(self, request, sale_id):
        serializer = SaleStatusTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = transition_sale(
            sale_id=sale_id,
            to_status=serializer.validated_data["status"],
            actor=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(
            SaleSerializer(
                sale_queryset().get(pk=sale.pk),
                context={"request": request},
            ).data
        )


class SaleReturnView(APIView):
    def post(self, request, sale_id):
        serializer = SaleReturnRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = return_sale(
            sale_id=sale_id,
            reason=serializer.validated_data["reason"],
            serial_numbers=serializer.validated_data.get("serial_numbers"),
            actor=request.user,
        )
        return Response(
            SaleSerializer(
                sale_queryset().get(pk=sale.pk),
                context={"request": request},
            ).data
        )


class SaleReturnDecisionView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, sale_id):
        serializer = SaleReturnDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = decide_sale_return(
            sale_id=sale_id,
            decision=serializer.validated_data["decision"],
            actor=request.user,
            note=serializer.validated_data.get("note", ""),
            assigned_admin=serializer.validated_data.get("assigned_admin"),
            serial_numbers=serializer.validated_data.get("serial_numbers"),
        )
        return Response(
            SaleSerializer(
                sale_queryset().get(pk=sale.pk),
                context={"request": request},
            ).data
        )


class SaleInvoiceView(APIView):
    def get_sale(self, request, sale_id):
        sale = get_object_or_404(
            Sale.objects.select_related("platform", "created_by"),
            pk=sale_id,
        )
        if not _can_access_sale_invoice(request.user, sale):
            raise PermissionDenied(
                "Only the Super Admin or the responsible Admin with Sales overview access may view this invoice."
            )
        return sale

    def get(self, request, sale_id):
        sale = self.get_sale(request, sale_id)
        invoice = (
            Invoice.objects.prefetch_related("credit_notes")
            .filter(sale=sale)
            .first()
        )
        if invoice is None and not sale.platform.issue_tamirabot_invoice:
            return Response(
                {
                    "marketplace_invoice": True,
                    "message": "The marketplace issues the invoice.",
                    "invoice": None,
                }
            )
        return Response(
            {
                "marketplace_invoice": False,
                "message": "",
                "invoice": _invoice_data(invoice) if invoice else None,
            }
        )

    def post(self, request, sale_id):
        sale = self.get_sale(request, sale_id)
        result = issue_invoice(sale_id=sale.pk, actor=request.user)
        if result is None:
            return Response(
                {
                    "marketplace_invoice": True,
                    "message": "The marketplace issues the invoice.",
                    "invoice": None,
                }
            )
        invoice, created = result
        invoice = Invoice.objects.prefetch_related("credit_notes").get(pk=invoice.pk)
        return Response(
            {
                "marketplace_invoice": False,
                "created": created,
                "invoice": _invoice_data(invoice),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class SaleCreditNoteView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, sale_id):
        sale = get_object_or_404(Sale.objects.select_related("platform"), pk=sale_id)
        invoice = get_object_or_404(Invoice, sale=sale)
        serial_numbers = request.data.get("serial_numbers")
        reason = request.data.get("reason", "")
        if not isinstance(serial_numbers, list) or not serial_numbers:
            raise ValidationError(
                {"serial_numbers": "Select at least one returned unit."}
            )
        if any(not isinstance(value, str) or not value.strip() for value in serial_numbers):
            raise ValidationError({"serial_numbers": "Provide valid unit serial numbers."})
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError({"reason": "Enter a reason for the credit note."})
        note = issue_credit_note(
            invoice_id=invoice.pk,
            serial_numbers=[value.strip() for value in serial_numbers],
            reason=reason,
            actor=request.user,
        )
        return Response(
            {
                "id": note.pk,
                "number": note.number,
                "date": note.note_date.isoformat(),
                "reason": note.reason,
                "amounts": note.amounts,
            },
            status=status.HTTP_201_CREATED,
        )


class InvoicePdfView(APIView):
    def get(self, request, invoice_id):
        invoice = get_object_or_404(
            Invoice.objects.select_related("sale__created_by"),
            pk=invoice_id,
        )
        if not _can_access_sale_invoice(request.user, invoice.sale):
            raise PermissionDenied(
                "You are not allowed to view or download this invoice."
            )
        record_audit(
            actor=request.user,
            action="sales.invoice.downloaded",
            instance=invoice,
            after={"number": invoice.number, "sale_id": invoice.sale_id},
        )
        response = HttpResponse(invoice_pdf(invoice), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{invoice.number.replace("/", "-")}.pdf"'
        )
        return response


class CreditNotePdfView(APIView):
    def get(self, request, credit_note_id):
        note = get_object_or_404(
            CreditNote.objects.select_related("invoice__sale__created_by"),
            pk=credit_note_id,
        )
        if not _can_access_sale_invoice(request.user, note.invoice.sale):
            raise PermissionDenied(
                "You are not allowed to view or download this credit note."
            )
        record_audit(
            actor=request.user,
            action="sales.credit_note.downloaded",
            instance=note,
            after={
                "number": note.number,
                "invoice_number": note.invoice.number,
            },
        )
        response = HttpResponse(
            credit_note_pdf(note), content_type="application/pdf"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{note.number.replace("/", "-")}.pdf"'
        )
        return response


class SalesBulkStatusView(APIView):
    def post(self, request):
        serializer = SaleBulkStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sales = bulk_transition_by_serials(
            serial_numbers=serializer.validated_data["serial_numbers"],
            to_status=serializer.validated_data["status"],
            actor=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(
            SaleSerializer(
                sale_queryset().filter(pk__in=[sale.pk for sale in sales]).order_by("pk"),
                many=True,
                context={"request": request},
            ).data
        )
