from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSuperAdmin
from accounts.models import User
from audit.services import record_audit
from catalog.models import Product
from sales.models import Sale, SaleUnit, SalesPlatform
from sales.serializers import (
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
        return Response(SaleSerializer(sales.distinct(), many=True).data)

    def post(self, request):
        serializer = SaleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sale = create_sale(values=serializer.validated_data, actor=request.user)
        sale = sale_queryset().get(pk=sale.pk)
        return Response(SaleSerializer(sale).data, status=status.HTTP_201_CREATED)


class SaleDetailView(APIView):
    def get(self, request, sale_id):
        sale = get_object_or_404(sale_queryset(), pk=sale_id)
        return Response(SaleSerializer(sale).data)

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
        return Response(SaleSerializer(sale).data)


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
        return Response(SaleSerializer(sale_queryset().get(pk=sale_unit.sale_id)).data)


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
        return Response(SaleSerializer(sale_queryset().get(pk=sale.pk)).data)


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
        return Response(SaleSerializer(sale_queryset().get(pk=sale.pk)).data)


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
        return Response(SaleSerializer(sale_queryset().get(pk=sale.pk)).data)


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
            ).data
        )
