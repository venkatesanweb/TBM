from django.db.models import Q
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from accounts.permissions import IsSuperAdmin
from audit.models import AuditLog
from audit.serializers import AuditLogSerializer
from catalog.models import Component, Product, ProductRevision
from inventory.models import StockAdjustment, StockMovement
from workorders.models import WorkOrder


STOCK_ACTIONS = {
    "inventory.stock.receive": ("Stock received", "component", "movement"),
    "inventory.stock.reserve": ("Stock reserved", "component", "movement"),
    "inventory.stock.release": ("Stock reservation released", "component", "movement"),
    "inventory.stock.consume": ("Stock consumed", "component", "movement"),
    "inventory.stock.produce": ("Stock produced", "component", "movement"),
    "inventory.stock.return": ("Stock returned", "component", "movement"),
    "inventory.stock.adjust": ("Stock adjusted", "component", "movement"),
    "inventory.adjustment.requested": ("Stock adjustment requested", "component", "adjustment"),
    "inventory.adjustment.approved": ("Stock adjustment approved", "component", "adjustment"),
    "inventory.adjustment.rejected": ("Stock adjustment rejected", "component", "adjustment"),
}

WORK_ORDER_ACTIONS = {
    "workorder.created": "Work order created",
    "workorder.reassigned": "Work order reassigned",
    "workorder.started": "Work order started",
    "workorder.quantity.reduced": "Work order quantity reduced",
    "workorder.cancelled": "Work order cancelled",
    "workorder.stock.reserved": "Work order stock reserved",
    "workorder.stock.preempted": "Work order reservation reallocated",
    "workorder.packing.confirmed": "Work order packed",
}

SUPER_ADMIN_ACTIONS = {
    "catalog.product.created": ("Product created", "product"),
    "catalog.product.updated": ("Product updated", "product"),
    "catalog.product.deleted": ("Product deleted", "product"),
    "catalog.revision.created": ("Product revision created", "revision"),
    "catalog.revision.updated": ("Product revision updated", "revision"),
    "catalog.revision.deleted": ("Product revision deleted", "revision"),
    "catalog.component.created": ("Component created", "component"),
    "catalog.component.updated": ("Component updated", "component"),
    "catalog.component.deleted": ("Component deleted", "component"),
    "catalog.bom_line.created": ("BOM component added", "bom"),
    "catalog.bom_line.updated": ("BOM component updated", "bom"),
    "catalog.bom_line.deleted": ("BOM component removed", "bom"),
    "catalog.revision.frozen": ("Product revision frozen", "revision"),
    "user.created": ("Admin account created", "user"),
    "user.updated": ("User account updated", "user"),
    "user.super_admin_seeded": ("Super Admin account created", "user"),
}


def _notification_queryset(user):
    queryset = AuditLog.objects.exclude(actor_id=user.pk)
    if user.role == User.Role.SUPER_ADMIN:
        return queryset.filter(
            action__in=(
                *STOCK_ACTIONS,
                *WORK_ORDER_ACTIONS,
                "finished_unit.label.reprinted",
                *SUPER_ADMIN_ACTIONS,
            )
        )

    assigned_work_orders = [
        str(pk)
        for pk in WorkOrder.objects.filter(assigned_admin_id=user.pk)
        .values_list("pk", flat=True)
    ]
    return queryset.filter(
        action__in=(*STOCK_ACTIONS, *WORK_ORDER_ACTIONS),
    ).filter(
        # Stock events are shared, while work-order events are scoped to the
        # currently assigned Admin's work orders.
        Q(action__in=STOCK_ACTIONS)
        | (
            Q(action__in=WORK_ORDER_ACTIONS)
            & Q(
                content_type__app_label="workorders",
                content_type__model="workorder",
                object_id__in=assigned_work_orders,
            )
        )
    )


def _notification_payload(event):
    action = event.action
    object_reference = {"type": "event", "id": event.object_id or None}
    message = "A system change was recorded."

    if action in STOCK_ACTIONS:
        label, reference_type, target_type = STOCK_ACTIONS[action]
        target = event.content_object
        if target_type == "movement" and isinstance(target, StockMovement):
            component = target.component
            object_reference = {"type": reference_type, "id": component.pk}
            message = f"{label}: {target.quantity} for {component.name} ({component.sku})."
        elif target_type == "adjustment" and isinstance(target, StockAdjustment):
            component = target.component
            object_reference = {"type": reference_type, "id": component.pk}
            message = f"{label}: {target.delta} for {component.name} ({component.sku})."
        event_type = action
    elif action in WORK_ORDER_ACTIONS:
        order = event.content_object
        event_type = action
        if isinstance(order, WorkOrder):
            object_reference = {"type": "work_order", "id": order.pk}
            message = f"{WORK_ORDER_ACTIONS[action]}: WO-{order.pk}, {order.product.name}."
        else:
            message = WORK_ORDER_ACTIONS[action]
    elif action == "finished_unit.label.reprinted":
        event_type = action
        message = "A finished-unit label was reprinted."
        object_reference = {"type": "finished_unit", "id": _safe_integer(event.object_id)}
    else:
        label, reference_type = SUPER_ADMIN_ACTIONS[action]
        event_type = action
        target = event.content_object
        object_reference = {"type": reference_type, "id": _safe_integer(event.object_id)}
        if reference_type == "product" and isinstance(target, Product):
            message = f"{label}: {target.product_number} · {target.name}."
        elif reference_type == "component" and isinstance(target, Component):
            message = f"{label}: {target.name} ({target.sku})."
        elif reference_type == "revision" and isinstance(target, ProductRevision):
            message = f"{label}: revision {target.revision_number}."
        elif reference_type == "bom":
            message = label
        elif reference_type == "user":
            message = label
        else:
            message = label

    return {
        "id": event.pk,
        "time": event.timestamp,
        "event_type": event_type,
        "message": message,
        "object": object_reference,
    }


def _safe_integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]
    page_size = 50

    def get(self, request):
        after_value = request.query_params.get("after")
        if after_value is None:
            return Response({
                "results": [],
                "cursor": AuditLog.objects.order_by("-pk").values_list("pk", flat=True).first() or 0,
            })
        try:
            after = int(after_value)
            if after < 0:
                raise ValueError
        except ValueError as exc:
            raise ValidationError({"after": "Provide a non-negative event ID."}) from exc

        queryset = _notification_queryset(request.user).filter(pk__gt=after).order_by("pk")
        rows = list(queryset.select_related("content_type")[: self.page_size + 1])
        has_more = len(rows) > self.page_size
        rows = rows[: self.page_size]
        if has_more and rows:
            cursor = rows[-1].pk
        else:
            cursor = AuditLog.objects.order_by("-pk").values_list("pk", flat=True).first() or after
        return Response({
            "results": [_notification_payload(row) for row in rows],
            "cursor": cursor,
        })


class AuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        queryset = AuditLog.objects.select_related("actor", "content_type")
        params = self.request.query_params
        if params.get("actor"):
            try:
                actor_id = int(params["actor"])
            except ValueError as exc:
                raise ValidationError({"actor": "Provide a valid user ID."}) from exc
            queryset = queryset.filter(actor_id=actor_id)
        if params.get("action"):
            queryset = queryset.filter(action=params["action"])
        if params.get("object_type"):
            try:
                app_label, model = params["object_type"].split(".", maxsplit=1)
            except ValueError as exc:
                raise ValidationError(
                    {"object_type": "Use the format app_label.model."}
                ) from exc
            queryset = queryset.filter(
                content_type__app_label=app_label,
                content_type__model=model.lower(),
            )
        if params.get("object_id"):
            queryset = queryset.filter(object_id=params["object_id"])
        for param, lookup in (
            ("created_after", "timestamp__gte"),
            ("created_before", "timestamp__lte"),
        ):
            if params.get(param):
                value = parse_datetime(params[param])
                if value is None:
                    raise ValidationError({param: "Provide an ISO-8601 datetime."})
                if is_naive(value):
                    value = make_aware(value)
                queryset = queryset.filter(**{lookup: value})
        return queryset
