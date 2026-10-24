from collections import defaultdict
from decimal import Decimal

from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from catalog.models import Product
from workorders.models import FinishedUnit, WorkOrder
from workorders.permissions import SuperAdminOnly, WorkOrderPermission
from workorders.serializers import (
    FinishedUnitSerializer,
    PackConfirmationSerializer,
    PackRequestSerializer,
    WorkOrderCreateSerializer,
    WorkOrderSerializer,
    WorkOrderUpdateSerializer,
)
from workorders.services import (
    audit_label_reprint,
    cancel_work_order,
    confirm_packing,
    create_work_order,
    reduce_work_order,
    reassign_work_order,
    start_work_order,
)


def visible_orders(user):
    orders = WorkOrder.objects.select_related(
        "product", "product_revision", "assigned_admin", "created_by"
    ).prefetch_related("reservations__component", "finished_units")
    orders = orders.prefetch_related("product_revision__bom_lines__component")
    if user.role != User.Role.SUPER_ADMIN:
        orders = orders.filter(assigned_admin=user)
    return orders.annotate(
        priority_rank=Case(
            When(priority=WorkOrder.Priority.HIGH, then=Value(0)),
            When(priority=WorkOrder.Priority.MEDIUM, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by("priority_rank", "due_date", "created_at", "pk")


class WorkOrderListCreateView(APIView):
    permission_classes = [WorkOrderPermission]

    def get(self, request):
        return Response(WorkOrderSerializer(visible_orders(request.user), many=True).data)

    def post(self, request):
        if request.user.role != User.Role.SUPER_ADMIN:
            raise PermissionDenied("Only a Super Admin may create work orders.")
        serializer = WorkOrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        order = create_work_order(
            product_id=values["product"].pk,
            quantity=values["quantity"],
            priority=values["priority"],
            due_date=values["due_date"],
            assigned_admin=values["assigned_admin"],
            actor=request.user,
        )
        order = visible_orders(request.user).get(pk=order.pk)
        return Response(
            WorkOrderSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )


class WorkOrderDetailView(APIView):
    permission_classes = [WorkOrderPermission]

    def get_object(self, request, work_order_id):
        order = get_object_or_404(visible_orders(request.user), pk=work_order_id)
        self.check_object_permissions(request, order)
        return order

    def get(self, request, work_order_id):
        return Response(WorkOrderSerializer(self.get_object(request, work_order_id)).data)

    def patch(self, request, work_order_id):
        if request.user.role != User.Role.SUPER_ADMIN:
            raise PermissionDenied("Only a Super Admin may change work-order assignment or quantity.")
        order = self.get_object(request, work_order_id)
        serializer = WorkOrderUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if "assigned_admin" in values:
            order = reassign_work_order(
                work_order_id=order.pk,
                assigned_admin=values["assigned_admin"],
                actor=request.user,
            )
        elif "quantity" in values:
            order = reduce_work_order(
                work_order_id=order.pk,
                quantity=values["quantity"],
                actor=request.user,
            )
        order = visible_orders(request.user).get(pk=order.pk)
        return Response(WorkOrderSerializer(order).data)


class WorkOrderCancelView(APIView):
    permission_classes = [SuperAdminOnly]

    def post(self, request, work_order_id):
        order = cancel_work_order(work_order_id=work_order_id, actor=request.user)
        order = visible_orders(request.user).get(pk=order.pk)
        return Response(WorkOrderSerializer(order).data)


class WorkOrderPackView(APIView):
    permission_classes = [WorkOrderPermission]

    def post(self, request, work_order_id):
        serializer = PackRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        confirmation = confirm_packing(
            work_order_id=work_order_id,
            quantity=values["quantity"],
            idempotency_key=values["idempotency_key"],
            actor=request.user,
        )
        confirmation = type(confirmation).objects.prefetch_related(
            "finished_units__product",
            "finished_units__product_revision",
            "finished_units__work_order",
        ).get(pk=confirmation.pk)
        return Response(
            PackConfirmationSerializer(confirmation).data,
            status=status.HTTP_200_OK,
        )


class WorkOrderStartView(APIView):
    permission_classes = [WorkOrderPermission]

    def post(self, request, work_order_id):
        order = start_work_order(work_order_id=work_order_id, actor=request.user)
        order = visible_orders(request.user).get(pk=order.pk)
        return Response(WorkOrderSerializer(order).data)


class FinishedUnitLabelView(APIView):
    permission_classes = [WorkOrderPermission]

    def get_unit(self, request, unit_id):
        unit = get_object_or_404(
            FinishedUnit.objects.select_related(
                "product", "product_revision", "work_order", "work_order__assigned_admin"
            ),
            pk=unit_id,
        )
        return unit

    def get(self, request, unit_id):
        unit = self.get_unit(request, unit_id)
        return Response(FinishedUnitSerializer(unit).data)

    def post(self, request, unit_id):
        unit = self.get_unit(request, unit_id)
        audit_label_reprint(unit_id=unit.pk, actor=request.user)
        return Response(FinishedUnitSerializer(unit).data)


def finished_unit_queryset():
    return FinishedUnit.objects.select_related(
        "product",
        "product_revision",
        "work_order",
        "pack_confirmation",
        "pack_confirmation__confirmed_by",
    ).prefetch_related("history__actor")


class FinishedUnitListView(APIView):
    permission_classes = [WorkOrderPermission]

    def get(self, request):
        units = finished_unit_queryset()
        params = request.query_params
        filters = (
            ("product", "product_id"),
            ("revision", "product_revision_id"),
            ("status", "status"),
            ("work_order", "work_order_id"),
        )
        for parameter, field in filters:
            value = params.get(parameter)
            if value:
                if parameter == "status" and value not in FinishedUnit.Status.values:
                    raise ValidationError({"status": "Choose a valid finished-unit status."})
                try:
                    units = units.filter(**{field: int(value) if field.endswith("_id") else value})
                except (TypeError, ValueError) as exc:
                    raise ValidationError({parameter: "Provide a valid filter value."}) from exc

        serial_search = params.get("search", "").strip()
        if serial_search:
            units = units.filter(serial_number__icontains=serial_search)

        for parameter, field in (("date_from", "created_at__date__gte"), ("date_to", "created_at__date__lte")):
            value = params.get(parameter)
            if value:
                parsed = parse_date(value)
                if parsed is None:
                    raise ValidationError({parameter: "Use an ISO date (YYYY-MM-DD)."})
                units = units.filter(**{field: parsed})
        return Response(FinishedUnitSerializer(units, many=True).data)


class FinishedUnitSummaryView(APIView):
    permission_classes = [WorkOrderPermission]

    def get(self, request):
        products = Product.objects.annotate(
            in_stock=Count(
                "finished_units",
                filter=Q(finished_units__status=FinishedUnit.Status.IN_STOCK),
            ),
            sold=Count(
                "finished_units",
                filter=Q(finished_units__status=FinishedUnit.Status.SOLD),
            ),
            returned=Count(
                "finished_units",
                filter=Q(finished_units__status=FinishedUnit.Status.RETURNED),
            ),
        ).order_by("product_number")
        return Response([
            {
                "product_id": product.pk,
                "product_number": product.product_number,
                "product_name": product.name,
                "in_stock": product.in_stock,
                "sold": product.sold,
                "returned": product.returned,
            }
            for product in products
        ])


class FinishedUnitLookupView(APIView):
    permission_classes = [WorkOrderPermission]

    def get(self, request):
        serial_number = request.query_params.get("serial_number", "").strip()
        if not serial_number:
            raise ValidationError({"serial_number": "Enter or scan a serial number."})
        unit = get_object_or_404(
            finished_unit_queryset(),
            serial_number__iexact=serial_number,
        )
        return Response(FinishedUnitSerializer(unit).data)


class FinishedUnitDetailView(APIView):
    permission_classes = [WorkOrderPermission]

    def get(self, request, unit_id):
        unit = get_object_or_404(finished_unit_queryset(), pk=unit_id)
        return Response(FinishedUnitSerializer(unit).data)


class WorkOrderReconciliationView(APIView):
    permission_classes = [SuperAdminOnly]

    def get(self, request):
        orders = (
            WorkOrder.objects.filter(packed_quantity__gt=0)
            .select_related("product", "product_revision")
            .prefetch_related(
                "product_revision__bom_lines__component",
                "pack_confirmations__movements__movement__component",
            )
            .order_by("pk")
        )
        report = []
        for order in orders:
            expected = {
                line.component_id: line.quantity_per_unit * order.packed_quantity
                for line in order.product_revision.bom_lines.all()
            }
            components = {
                line.component_id: line.component
                for line in order.product_revision.bom_lines.all()
            }
            actual = defaultdict(lambda: Decimal("0"))
            for confirmation in order.pack_confirmations.all():
                for confirmation_movement in confirmation.movements.all():
                    movement = confirmation_movement.movement
                    if movement.movement_type == "CONSUME":
                        actual[movement.component_id] += movement.quantity
                        components[movement.component_id] = movement.component

            discrepancies = []
            for component_id in sorted(set(expected) | set(actual)):
                component = components[component_id]
                expected_quantity = expected.get(component_id, Decimal("0"))
                actual_quantity = actual[component_id]
                if expected_quantity != actual_quantity:
                    discrepancies.append({
                        "component_id": component_id,
                        "component_sku": component.sku,
                        "component_name": component.name,
                        "expected": str(expected_quantity),
                        "actual": str(actual_quantity),
                        "difference": str(actual_quantity - expected_quantity),
                    })
            report.append({
                "work_order_id": order.pk,
                "work_order_number": f"WO-{order.pk}",
                "product_number": order.product.product_number,
                "product_name": order.product.name,
                "product_revision": order.product_revision.revision_number,
                "packed_quantity": order.packed_quantity,
                "reconciled": not discrepancies,
                "discrepancies": discrepancies,
            })
        return Response(report)


class WorkOrderAssigneeListView(APIView):
    permission_classes = [SuperAdminOnly]

    def get(self, request):
        admins = User.objects.filter(
            role=User.Role.ADMIN, is_active=True
        ).order_by("username")
        return Response(
            [
                {"id": user.pk, "username": user.username, "email": user.email}
                for user in admins
            ]
        )
