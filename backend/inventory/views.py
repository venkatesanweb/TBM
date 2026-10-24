from decimal import Decimal

from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from catalog.models import Component
from inventory.models import StockAdjustment, StockBalance, StockMovement
from inventory.permissions import InventoryReadWritePermission
from inventory.serializers import (
    AdjustmentRequestSerializer,
    ComponentStockSerializer,
    ReceiveStockSerializer,
    StockAdjustmentSerializer,
    StockMovementSerializer,
)
from inventory.services import (
    approve_adjustment,
    receive,
    reject_adjustment,
    request_adjustment,
)


def component_queryset():
    return Component.objects.select_related("stock_balance")


def filter_components(queryset, params):
    search = params.get("search", "").strip()
    if search:
        queryset = queryset.filter(
            Q(sku__icontains=search)
            | Q(name__icontains=search)
            | Q(category__icontains=search)
        )
    return queryset


class InventoryListView(APIView):
    permission_classes = [InventoryReadWritePermission]

    def get(self, request):
        components = filter_components(component_queryset(), request.query_params)
        if request.query_params.get("low_stock") == "true":
            components = components.filter(
                Q(stock_balance__isnull=True)
                | Q(stock_balance__on_hand__lt=F("stock_balance__reserved") + F("reorder_level"))
                | Q(stock_balance__on_hand=F("stock_balance__reserved") + F("reorder_level"))
            )
        if request.query_params.get("active") in {"true", "false"}:
            components = components.filter(active=request.query_params["active"] == "true")
        return Response(ComponentStockSerializer(components, many=True).data)


class ComponentStockDetailView(APIView):
    permission_classes = [InventoryReadWritePermission]

    def get(self, request, component_id):
        component = get_object_or_404(component_queryset(), pk=component_id)
        balance = getattr(component, "stock_balance", None)
        movements = StockMovement.objects.filter(component=component).select_related(
            "user", "component"
        )
        return Response(
            {
                "component": ComponentStockSerializer(component).data["component"],
                "balance": {
                    "on_hand": balance.on_hand if balance else Decimal("0"),
                    "reserved": balance.reserved if balance else Decimal("0"),
                    "available": balance.available if balance else Decimal("0"),
                },
                "movements": StockMovementSerializer(movements, many=True).data,
            }
        )


class ReceiveStockView(APIView):
    permission_classes = [InventoryReadWritePermission]

    def post(self, request):
        serializer = ReceiveStockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        movement = receive(
            component_id=serializer.validated_data["component"].pk,
            quantity=serializer.validated_data["quantity"],
            actor=request.user,
            note=serializer.validated_data.get("note", ""),
        )
        return Response(
            StockMovementSerializer(movement).data,
            status=status.HTTP_201_CREATED,
        )


class AdjustmentListCreateView(APIView):
    permission_classes = [InventoryReadWritePermission]

    def get(self, request):
        if request.user.role != User.Role.SUPER_ADMIN:
            return Response(
                {"detail": "Only a Super Admin may view stock adjustment requests."},
                status=status.HTTP_403_FORBIDDEN,
            )
        adjustments = StockAdjustment.objects.select_related(
            "component", "requested_by", "reviewed_by"
        )
        if request.query_params.get("status"):
            adjustments = adjustments.filter(status=request.query_params["status"].upper())
        return Response(StockAdjustmentSerializer(adjustments, many=True).data)

    def post(self, request):
        serializer = AdjustmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        adjustment = request_adjustment(
            component_id=serializer.validated_data["component"].pk,
            delta=serializer.validated_data["delta"],
            reason=serializer.validated_data["reason"],
            actor=request.user,
        )
        return Response(
            StockAdjustmentSerializer(adjustment).data,
            status=status.HTTP_201_CREATED,
        )


class LowStockAlertListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if (
            not request.user.is_active
            or request.user.role != User.Role.SUPER_ADMIN
        ):
            return Response(
                {"detail": "Only a Super Admin may view low-stock alerts."},
                status=status.HTTP_403_FORBIDDEN,
            )
        components = component_queryset().filter(
            active=True,
        ).filter(
            Q(stock_balance__isnull=True)
            | Q(stock_balance__on_hand__lte=F("stock_balance__reserved") + F("reorder_level"))
        )
        return Response(ComponentStockSerializer(components, many=True).data)


class AdjustmentReviewView(APIView):
    permission_classes = [InventoryReadWritePermission]

    def post(self, request, adjustment_id, decision):
        if decision not in {"approve", "reject"}:
            raise ValidationError({"decision": "Choose approve or reject."})
        try:
            if decision == "approve":
                adjustment = approve_adjustment(
                    adjustment_id=adjustment_id,
                    actor=request.user,
                )
            else:
                adjustment = reject_adjustment(
                    adjustment_id=adjustment_id,
                    actor=request.user,
                )
        except StockAdjustment.DoesNotExist as exc:
            raise ValidationError({"adjustment": "Adjustment request does not exist."}) from exc
        adjustment = StockAdjustment.objects.select_related(
            "component", "requested_by", "reviewed_by"
        ).get(pk=adjustment.pk)
        return Response(StockAdjustmentSerializer(adjustment).data)
