from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.models import User
from audit.services import record_audit
from catalog.models import Product
from inventory import services as inventory_services
from inventory.models import StockBalance, StockMovement
from workorders.models import (
    FinishedUnit,
    FinishedUnitHistory,
    PackConfirmation,
    PackConfirmationMovement,
    SerialSequence,
    WorkOrder,
    WorkOrderQueueLock,
    WorkOrderReservation,
)


PRIORITY_ORDER = Case(
    When(priority=WorkOrder.Priority.HIGH, then=Value(0)),
    When(priority=WorkOrder.Priority.MEDIUM, then=Value(1)),
    default=Value(2),
    output_field=IntegerField(),
)


def _work_order_reference(work_order):
    return f"WO-{work_order.pk}"


def _format_quantity(value):
    return format(value.normalize(), "f")


def _requirements(work_order):
    return list(
        work_order.product_revision.bom_lines.select_related("component")
        .order_by("component_id")
        .values_list("component_id", "component__sku", "quantity_per_unit")
    )


def _try_reserve(work_order, *, actor):
    work_order = WorkOrder.objects.select_for_update().get(pk=work_order.pk)
    if work_order.status != WorkOrder.Status.BLOCKED:
        return work_order
    requirements = _requirements(work_order)
    if not requirements:
        work_order.blocked_reason = "The frozen product revision has no BOM components."
        work_order.save(update_fields=("blocked_reason", "updated_at"))
        return work_order

    balances = {}
    for component_id, _sku, _per_unit in requirements:
        from catalog.models import Component

        Component.objects.select_for_update().get(pk=component_id)
        StockBalance.objects.get_or_create(component_id=component_id)
        balances[component_id] = StockBalance.objects.select_for_update().get(
            component_id=component_id
        )

    shortages = []
    for component_id, sku, per_unit in requirements:
        required = per_unit * work_order.quantity
        available = balances[component_id].available
        if available < required:
            shortages.append(
                f"short by {_format_quantity(required - available)} of component {sku}"
            )

    if shortages:
        work_order.blocked_reason = "; ".join(shortages)
        work_order.save(update_fields=("blocked_reason", "updated_at"))
        return work_order

    reference = _work_order_reference(work_order)
    for component_id, _sku, per_unit in requirements:
        quantity = per_unit * work_order.quantity
        inventory_services.reserve(
            component_id=component_id,
            quantity=quantity,
            actor=actor,
            work_order_reference=reference,
            note=f"Reservation for {reference}",
        )
        reservation, created = WorkOrderReservation.objects.get_or_create(
            work_order=work_order,
            component_id=component_id,
            defaults={"quantity": quantity},
        )
        if not created:
            reservation.quantity += quantity
            reservation.save(update_fields=("quantity",))

    work_order.status = WorkOrder.Status.RESERVED
    work_order.blocked_reason = ""
    work_order.save(update_fields=("status", "blocked_reason", "updated_at"))
    record_audit(
        actor=actor,
        action="workorder.stock.reserved",
        instance=work_order,
        after={"status": work_order.status, "reservation_count": len(requirements)},
    )
    return work_order


@transaction.atomic
def retry_blocked_orders(*, actor):
    WorkOrderQueueLock.objects.get_or_create(pk=1)
    WorkOrderQueueLock.objects.select_for_update().get(pk=1)
    orders = list(
        WorkOrder.objects.select_for_update()
        .filter(status__in=(WorkOrder.Status.BLOCKED, WorkOrder.Status.RESERVED))
        .annotate(priority_rank=PRIORITY_ORDER)
        .order_by("priority_rank", "due_date", "created_at", "pk")
    )
    blocked = [order for order in orders if order.status == WorkOrder.Status.BLOCKED]
    if blocked:
        first_blocked = blocked[0]
        blocked_key = (
            first_blocked.priority_rank,
            first_blocked.due_date,
            first_blocked.created_at,
            first_blocked.pk,
        )
        for order in orders:
            order_key = (
                order.priority_rank,
                order.due_date,
                order.created_at,
                order.pk,
            )
            if (
                order.status != WorkOrder.Status.RESERVED
                or order.packed_quantity
                or order_key <= blocked_key
            ):
                continue
            for reservation in order.reservations.select_for_update().order_by("component_id"):
                remaining = reservation.remaining
                if remaining > 0:
                    inventory_services.release(
                        component_id=reservation.component_id,
                        quantity=remaining,
                        actor=actor,
                        work_order_reference=_work_order_reference(order),
                        note=f"Released for higher-priority queue allocation",
                        retry_queue=False,
                    )
                    reservation.released_quantity += remaining
                    reservation.save(update_fields=("released_quantity",))
            order.status = WorkOrder.Status.BLOCKED
            order.save(update_fields=("status", "updated_at"))
            record_audit(
                actor=actor,
                action="workorder.stock.preempted",
                instance=order,
                after={"status": order.status, "priority_order_id": first_blocked.pk},
            )

    orders = (
        WorkOrder.objects.select_for_update()
        .filter(status=WorkOrder.Status.BLOCKED)
        .annotate(priority_rank=PRIORITY_ORDER)
        .order_by("priority_rank", "due_date", "created_at", "pk")
    )
    for order in orders:
        _try_reserve(order, actor=actor)


@transaction.atomic
def create_work_order(
    *,
    product_id,
    quantity,
    priority,
    due_date,
    assigned_admin,
    actor,
):
    if assigned_admin.role != User.Role.ADMIN or not assigned_admin.is_active:
        raise ValidationError({"assigned_admin": "Choose an active Admin account."})
    product = Product.objects.select_for_update().select_related("current_revision").get(
        pk=product_id
    )
    if not product.active:
        raise ValidationError({"product": "Inactive products cannot be scheduled."})
    revision = product.current_revision
    if revision is None:
        raise ValidationError({"product": "This product has no current BOM revision."})
    if not revision.bom_lines.exists():
        raise ValidationError({"product": "The current revision has no BOM components."})
    revision.freeze_for_work_order(
        actor=actor,
        work_order_reference=f"new order for {product.product_number}",
    )
    work_order = WorkOrder.objects.create(
        product=product,
        product_revision=revision,
        quantity=quantity,
        priority=priority,
        due_date=due_date,
        assigned_admin=assigned_admin,
        created_by=actor,
        status=WorkOrder.Status.BLOCKED,
    )
    retry_blocked_orders(actor=actor)
    work_order.refresh_from_db()
    record_audit(
        actor=actor,
        action="workorder.created",
        instance=work_order,
        after={
            "product_id": product.pk,
            "product_revision_id": revision.pk,
            "quantity": quantity,
            "priority": priority,
            "due_date": due_date.isoformat(),
            "assigned_admin_id": assigned_admin.pk,
            "status": work_order.status,
            "blocked_reason": work_order.blocked_reason,
        },
    )
    return work_order


@transaction.atomic
def reassign_work_order(*, work_order_id, assigned_admin, actor):
    if assigned_admin.role != User.Role.ADMIN or not assigned_admin.is_active:
        raise ValidationError({"assigned_admin": "Choose an active Admin account."})
    order = WorkOrder.objects.select_for_update().get(pk=work_order_id)
    if order.status in (WorkOrder.Status.CANCELLED, WorkOrder.Status.COMPLETED):
        raise ValidationError("Completed or cancelled work orders cannot be reassigned.")
    before = {"assigned_admin_id": order.assigned_admin_id}
    order.assigned_admin = assigned_admin
    order.save(update_fields=("assigned_admin", "updated_at"))
    record_audit(
        actor=actor,
        action="workorder.reassigned",
        instance=order,
        before=before,
        after={"assigned_admin_id": assigned_admin.pk},
    )
    return order


@transaction.atomic
def start_work_order(*, work_order_id, actor):
    order = WorkOrder.objects.select_for_update().get(pk=work_order_id)
    if actor.role != User.Role.ADMIN or actor.pk != order.assigned_admin_id:
        raise ValidationError("Only the assigned Admin may start this work order.")
    if order.status != WorkOrder.Status.RESERVED:
        raise ValidationError("Only a reserved work order can be started.")
    order.status = WorkOrder.Status.IN_PROGRESS
    order.save(update_fields=("status", "updated_at"))
    record_audit(
        actor=actor,
        action="workorder.started",
        instance=order,
        after={"status": order.status},
    )
    return order


@transaction.atomic
def reduce_work_order(*, work_order_id, quantity, actor):
    order = WorkOrder.objects.select_for_update().get(pk=work_order_id)
    if quantity < 1 or quantity >= order.quantity:
        raise ValidationError({"quantity": "Enter a smaller positive quantity."})
    if quantity < order.packed_quantity:
        raise ValidationError({"quantity": "Quantity cannot be less than units already packed."})
    if order.status in (WorkOrder.Status.CANCELLED, WorkOrder.Status.COMPLETED):
        raise ValidationError("Completed or cancelled work orders cannot be reduced.")

    before_quantity = order.quantity
    per_unit = {
        component_id: per_unit
        for component_id, _sku, per_unit in _requirements(order)
    }
    for reservation in order.reservations.select_for_update().order_by("component_id"):
        target_remaining = per_unit[reservation.component_id] * (
            quantity - order.packed_quantity
        )
        excess = reservation.remaining - target_remaining
        if excess > 0:
            inventory_services.release(
                component_id=reservation.component_id,
                quantity=excess,
                actor=actor,
                work_order_reference=_work_order_reference(order),
                note=f"Released after reducing {_work_order_reference(order)}",
                retry_queue=False,
            )
            reservation.released_quantity += excess
            reservation.save(update_fields=("released_quantity",))

    order.quantity = quantity
    if order.packed_quantity:
        order.status = WorkOrder.Status.PACKED
    order.save(update_fields=("quantity", "status", "updated_at"))
    record_audit(
        actor=actor,
        action="workorder.quantity.reduced",
        instance=order,
        before={"quantity": before_quantity},
        after={"quantity": quantity},
    )
    transaction.on_commit(lambda: retry_blocked_orders(actor=actor))
    return order


@transaction.atomic
def cancel_work_order(*, work_order_id, actor):
    order = WorkOrder.objects.select_for_update().get(pk=work_order_id)
    if order.status in (WorkOrder.Status.COMPLETED, WorkOrder.Status.CANCELLED):
        raise ValidationError("This work order cannot be cancelled.")
    for reservation in order.reservations.select_for_update().order_by("component_id"):
        remaining = reservation.remaining
        if remaining > 0:
            inventory_services.release(
                component_id=reservation.component_id,
                quantity=remaining,
                actor=actor,
                work_order_reference=_work_order_reference(order),
                note=f"Released after cancelling {_work_order_reference(order)}",
                retry_queue=False,
            )
            reservation.released_quantity += remaining
            reservation.save(update_fields=("released_quantity",))
    order.status = WorkOrder.Status.CANCELLED
    order.blocked_reason = ""
    order.save(update_fields=("status", "blocked_reason", "updated_at"))
    record_audit(
        actor=actor,
        action="workorder.cancelled",
        instance=order,
        after={"status": order.status},
    )
    transaction.on_commit(lambda: retry_blocked_orders(actor=actor))
    return order


def _next_serial(product_code):
    year_month = timezone.localdate().strftime("%y%m")
    sequence = (
        SerialSequence.objects.select_for_update()
        .filter(product_code=product_code, year_month=year_month)
        .first()
    )
    if sequence is None:
        try:
            with transaction.atomic():
                sequence = SerialSequence.objects.create(
                    product_code=product_code,
                    year_month=year_month,
                    last_value=0,
                )
        except IntegrityError:
            sequence = (
                SerialSequence.objects.select_for_update()
                .get(product_code=product_code, year_month=year_month)
            )
    sequence.last_value += 1
    sequence.save(update_fields=("last_value",))
    return f"{product_code}-{year_month}-{sequence.last_value:06d}"


@transaction.atomic
def confirm_packing(*, work_order_id, quantity, idempotency_key, actor):
    if not idempotency_key or len(idempotency_key) > 100:
        raise ValidationError({"idempotency_key": "A key of 1–100 characters is required."})
    order = (
        WorkOrder.objects.select_for_update()
        .select_related("product", "product_revision", "assigned_admin")
        .get(pk=work_order_id)
    )
    if actor.role != User.Role.ADMIN or actor.pk != order.assigned_admin_id:
        raise ValidationError("Only the assigned Admin may pack this work order.")
    previous = PackConfirmation.objects.filter(
        work_order=order, idempotency_key=idempotency_key
    ).first()
    if previous:
        if previous.quantity != quantity:
            raise ValidationError("This idempotency key was already used for another quantity.")
        return previous
    if quantity < 1:
        raise ValidationError({"quantity": "Pack quantity must be positive."})
    if order.status in (WorkOrder.Status.CANCELLED, WorkOrder.Status.COMPLETED):
        raise ValidationError("This work order is not open for packing.")
    if order.status == WorkOrder.Status.BLOCKED:
        raise ValidationError({"blocked_reason": order.blocked_reason})
    if order.status not in (WorkOrder.Status.IN_PROGRESS, WorkOrder.Status.PACKED):
        raise ValidationError("Start this work order before packing.")
    if order.packed_quantity + quantity > order.quantity:
        raise ValidationError({"quantity": "Pack quantity exceeds the order quantity remaining."})

    confirmation = PackConfirmation.objects.create(
        work_order=order,
        idempotency_key=idempotency_key,
        quantity=quantity,
        confirmed_by=actor,
    )
    reference = _work_order_reference(order)
    for component_id, _sku, per_unit in _requirements(order):
        required = per_unit * quantity
        reservation = (
            WorkOrderReservation.objects.select_for_update()
            .filter(work_order=order, component_id=component_id)
            .first()
        )
        if reservation is None or reservation.remaining < required:
            raise ValidationError(
                f"Insufficient reservation for component {_sku}; cancel or re-reserve the order."
            )
        movement = inventory_services.consume(
            component_id=component_id,
            quantity=required,
            actor=actor,
            work_order_reference=reference,
            from_reserved=True,
            note=f"Consumed for packing {reference}",
        )
        reservation.consumed_quantity += required
        reservation.save(update_fields=("consumed_quantity",))
        PackConfirmationMovement.objects.create(
            confirmation=confirmation,
            movement=movement,
        )

    units = [
        FinishedUnit(
            serial_number=_next_serial(order.product.product_number),
            barcode_value="",
            product=order.product,
            product_revision=order.product_revision,
            work_order=order,
            pack_confirmation=confirmation,
        )
        for _ in range(quantity)
    ]
    for unit in units:
        unit.barcode_value = unit.serial_number
    FinishedUnit.objects.bulk_create(units)
    saved_units = list(
        FinishedUnit.objects.filter(pack_confirmation=confirmation).order_by("pk")
    )
    FinishedUnitHistory.objects.bulk_create(
        [
            FinishedUnitHistory(
                unit=unit,
                event_type=FinishedUnitHistory.EventType.PACKED,
                to_status=unit.status,
                actor=actor,
                note=f"Created by packing confirmation {confirmation.pk}.",
            )
            for unit in saved_units
        ]
    )

    order.packed_quantity += quantity
    order.status = (
        WorkOrder.Status.COMPLETED
        if order.packed_quantity == order.quantity
        else WorkOrder.Status.PACKED
    )
    order.blocked_reason = ""
    order.save(update_fields=("packed_quantity", "status", "blocked_reason", "updated_at"))
    record_audit(
        actor=actor,
        action="workorder.packing.confirmed",
        instance=order,
        after={
            "confirmation_id": confirmation.pk,
            "idempotency_key": idempotency_key,
            "quantity": quantity,
            "serial_numbers": [unit.serial_number for unit in units],
            "status": order.status,
        },
    )
    return confirmation


@transaction.atomic
def audit_label_reprint(*, unit_id, actor):
    unit = FinishedUnit.objects.select_related(
        "product", "product_revision", "work_order"
    ).get(pk=unit_id)
    record_audit(
        actor=actor,
        action="finished_unit.label.reprinted",
        instance=unit,
        after={"serial_number": unit.serial_number, "work_order_id": unit.work_order_id},
    )
    return unit


@transaction.atomic
def update_finished_unit_status(
    *,
    unit_id,
    status,
    actor,
    event_type=FinishedUnitHistory.EventType.STATUS_CHANGED,
    sale_reference="",
    note="",
):
    unit = FinishedUnit.objects.select_for_update().get(pk=unit_id)
    if status not in FinishedUnit.Status.values:
        raise ValidationError({"status": "Choose a valid finished-unit status."})

    allowed_transitions = {
        FinishedUnit.Status.IN_STOCK: {
            FinishedUnit.Status.RESERVED_FOR_SALE,
            FinishedUnit.Status.RETURNED,
            FinishedUnit.Status.UNDER_INSPECTION,
        },
        FinishedUnit.Status.RESERVED_FOR_SALE: {
            FinishedUnit.Status.IN_STOCK,
            FinishedUnit.Status.SOLD,
            FinishedUnit.Status.RETURNED,
        },
        FinishedUnit.Status.SOLD: {
            FinishedUnit.Status.RETURNED,
            FinishedUnit.Status.UNDER_INSPECTION,
        },
        FinishedUnit.Status.RETURNED: {
            FinishedUnit.Status.UNDER_INSPECTION,
            FinishedUnit.Status.IN_STOCK,
        },
        FinishedUnit.Status.UNDER_INSPECTION: {
            FinishedUnit.Status.RETURNED,
            FinishedUnit.Status.IN_STOCK,
        },
    }
    if status == unit.status or status not in allowed_transitions[unit.status]:
        raise ValidationError(
            {"status": f"Cannot change unit status from {unit.status} to {status}."}
        )

    previous_status = unit.status
    unit.status = status
    unit.save(update_fields=("status",))
    history = FinishedUnitHistory.objects.create(
        unit=unit,
        event_type=event_type,
        from_status=previous_status,
        to_status=status,
        actor=actor,
        sale_reference=sale_reference,
        note=note,
    )
    record_audit(
        actor=actor,
        action=f"finished_unit.{event_type.lower()}",
        instance=unit,
        before={"status": previous_status},
        after={
            "status": status,
            "sale_reference": sale_reference,
            "note": note,
            "history_id": history.pk,
        },
    )
    return unit
