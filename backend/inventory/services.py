import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.db import connection, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.models import User
from audit.models import AuditLog
from audit.services import record_audit
from catalog.models import Component
from inventory.models import StockAdjustment, StockBalance, StockMovement


def _positive_quantity(value):
    try:
        quantity = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Quantity must be a valid positive number.") from exc
    if (
        not quantity.is_finite()
        or quantity <= 0
        or quantity.as_tuple().exponent < -3
        or quantity > Decimal("999999999.999")
    ):
        raise ValidationError("Quantity must be positive and fit 9 digits plus 3 decimals.")
    return quantity


def _adjustment_delta(value):
    try:
        delta = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Adjustment must be a valid number.") from exc
    if (
        not delta.is_finite()
        or delta == 0
        or delta.as_tuple().exponent < -3
        or abs(delta) > Decimal("999999999.999")
    ):
        raise ValidationError("Adjustment must be non-zero and fit 9 digits plus 3 decimals.")
    return delta


def _locked_balance(component_id):
    Component.objects.select_for_update().get(pk=component_id)
    StockBalance.objects.get_or_create(component_id=component_id)
    return StockBalance.objects.select_for_update().get(component_id=component_id)


def _record_movement(
    *,
    component_id,
    movement_type,
    quantity,
    delta_on_hand,
    delta_reserved,
    actor,
    note="",
    source=None,
    work_order_reference="",
):
    balance = _locked_balance(component_id)
    new_on_hand = balance.on_hand + delta_on_hand
    new_reserved = balance.reserved + delta_reserved
    if new_on_hand < 0:
        raise ValidationError("This movement would make on-hand stock negative.")
    if new_reserved < 0:
        raise ValidationError("This movement would make reserved stock negative.")
    if new_reserved > new_on_hand:
        raise ValidationError("This movement would make available stock negative.")

    movement = StockMovement.objects.create(
        component_id=component_id,
        movement_type=movement_type,
        quantity=quantity,
        delta_on_hand=delta_on_hand,
        delta_reserved=delta_reserved,
        user=actor,
        note=note,
        work_order_reference=work_order_reference,
    )
    balance.on_hand = new_on_hand
    balance.reserved = new_reserved
    balance.save(update_fields=("on_hand", "reserved", "updated_at"))
    audit_after = {
        "component_id": component_id,
        "movement_type": movement_type,
        "quantity": str(quantity),
        "delta_on_hand": str(delta_on_hand),
        "delta_reserved": str(delta_reserved),
        "work_order_reference": work_order_reference,
        "note": note,
    }
    if source:
        audit_after["source"] = source
    record_audit(
        actor=actor,
        action=f"inventory.stock.{movement_type.lower()}",
        instance=movement,
        after=audit_after,
    )
    return movement


@transaction.atomic
def receive(*, component_id, quantity, actor, note="", source=None):
    quantity = _positive_quantity(quantity)
    movement = _record_movement(
        component_id=component_id,
        movement_type=StockMovement.Type.RECEIVE,
        quantity=quantity,
        delta_on_hand=quantity,
        delta_reserved=Decimal("0"),
        actor=actor,
        note=note,
        source=source,
    )
    from workorders.services import retry_blocked_orders

    transaction.on_commit(lambda: retry_blocked_orders(actor=actor))
    return movement


@transaction.atomic
def receive_scanned(*, items, idempotency_key, actor, note=""):
    if actor.role != User.Role.SUPER_ADMIN:
        raise ValidationError("Only a Super Admin may receive scanned stock.")
    if not idempotency_key or len(idempotency_key) > 100:
        raise ValidationError(
            {"idempotency_key": "A key of 1–100 characters is required."}
        )
    if not isinstance(items, list) or not items or len(items) > 200:
        raise ValidationError({"items": "Provide between 1 and 200 component lines."})

    clean_items = []
    seen_component_ids = set()
    for item in items:
        try:
            component_id = int(item["component_id"])
            quantity = _positive_quantity(item["quantity"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(
                {"items": "Each line needs a component and positive quantity."}
            ) from exc
        if component_id in seen_component_ids:
            raise ValidationError(
                {"items": f"Component {component_id} appears more than once."}
            )
        seen_component_ids.add(component_id)
        clean_items.append({"component_id": component_id, "quantity": quantity})

    note = str(note or "").strip()
    if len(note) > 2000:
        raise ValidationError({"note": "The note cannot exceed 2000 characters."})

    canonical_request = {
        "items": [
            {
                "component_id": item["component_id"],
                "quantity": format(item["quantity"].normalize(), "f"),
            }
            for item in clean_items
        ],
        "note": note,
    }
    request_fingerprint = hashlib.sha256(
        json.dumps(canonical_request, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    if connection.vendor != "postgresql":
        raise RuntimeError("Scanned receipt idempotency requires PostgreSQL.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"barcode-receipt:{actor.pk}:{idempotency_key}"],
        )

    previous = AuditLog.objects.filter(
        actor=actor,
        action="inventory.scanned_receipt.confirmed",
        after__idempotency_key=idempotency_key,
    ).first()
    if previous:
        if previous.after["request_fingerprint"] != request_fingerprint:
            raise ValidationError(
                "This idempotency key was already used for a different receipt."
            )
        return previous.after["result"]

    component_ids = sorted(seen_component_ids)
    locked_components = {
        component.pk: component
        for component in Component.objects.select_for_update()
        .filter(pk__in=component_ids)
        .order_by("pk")
    }
    missing_ids = [component_id for component_id in component_ids if component_id not in locked_components]
    if missing_ids:
        raise ValidationError(
            {"items": f"Component {missing_ids[0]} does not exist."}
        )
    inactive = [component for component in locked_components.values() if not component.active]
    if inactive:
        raise ValidationError(
            {"items": f"{inactive[0].name} ({inactive[0].sku}) is inactive."}
        )

    result = []
    for item in clean_items:
        component = locked_components[item["component_id"]]
        before = (
            StockBalance.objects.filter(component_id=component.pk)
            .values_list("on_hand", flat=True)
            .first()
            or Decimal("0")
        )
        receive(
            component_id=component.pk,
            quantity=item["quantity"],
            actor=actor,
            note=note,
            source="barcode scan session",
        )
        balance = StockBalance.objects.get(component_id=component.pk)
        result.append(
            {
                "component_id": component.pk,
                "component_name": component.name,
                "sku": component.sku,
                "unit": component.unit,
                "quantity_received": str(item["quantity"]),
                "on_hand_before": str(before),
                "on_hand_after": str(balance.on_hand),
                "available_after": str(balance.available),
            }
        )

    record_audit(
        actor=actor,
        action="inventory.scanned_receipt.confirmed",
        after={
            "source": "barcode scan session",
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "result": result,
        },
    )
    return result


@transaction.atomic
def reserve(*, component_id, quantity, actor, work_order_reference, note=""):
    quantity = _positive_quantity(quantity)
    return _record_movement(
        component_id=component_id,
        movement_type=StockMovement.Type.RESERVE,
        quantity=quantity,
        delta_on_hand=Decimal("0"),
        delta_reserved=quantity,
        actor=actor,
        note=note,
        work_order_reference=work_order_reference,
    )


@transaction.atomic
def release(
    *,
    component_id,
    quantity,
    actor,
    work_order_reference,
    note="",
    retry_queue=True,
):
    quantity = _positive_quantity(quantity)
    movement = _record_movement(
        component_id=component_id,
        movement_type=StockMovement.Type.RELEASE,
        quantity=quantity,
        delta_on_hand=Decimal("0"),
        delta_reserved=-quantity,
        actor=actor,
        note=note,
        work_order_reference=work_order_reference,
    )
    if retry_queue:
        from workorders.services import retry_blocked_orders

        transaction.on_commit(lambda: retry_blocked_orders(actor=actor))
    return movement


@transaction.atomic
def consume(
    *, component_id, quantity, actor, work_order_reference="", from_reserved=False, note=""
):
    quantity = _positive_quantity(quantity)
    reserved_delta = -quantity if from_reserved else Decimal("0")
    return _record_movement(
        component_id=component_id,
        movement_type=StockMovement.Type.CONSUME,
        quantity=quantity,
        delta_on_hand=-quantity,
        delta_reserved=reserved_delta,
        actor=actor,
        note=note,
        work_order_reference=work_order_reference,
    )


@transaction.atomic
def return_stock(*, component_id, quantity, actor, work_order_reference="", note=""):
    quantity = _positive_quantity(quantity)
    movement = _record_movement(
        component_id=component_id,
        movement_type=StockMovement.Type.RETURN,
        quantity=quantity,
        delta_on_hand=quantity,
        delta_reserved=Decimal("0"),
        actor=actor,
        note=note,
        work_order_reference=work_order_reference,
    )
    from workorders.services import retry_blocked_orders

    transaction.on_commit(lambda: retry_blocked_orders(actor=actor))
    return movement


@transaction.atomic
def request_adjustment(*, component_id, delta, reason, actor):
    delta = _adjustment_delta(delta)
    reason = reason.strip()
    if not reason:
        raise ValidationError("A reason is required for a stock adjustment.")
    Component.objects.select_for_update().get(pk=component_id)
    adjustment = StockAdjustment.objects.create(
        component_id=component_id,
        delta=delta,
        reason=reason,
        requested_by=actor,
    )
    record_audit(
        actor=actor,
        action="inventory.adjustment.requested",
        instance=adjustment,
        after={
            "component_id": component_id,
            "delta": str(delta),
            "reason": reason,
            "status": adjustment.status,
        },
    )
    return adjustment


def adjust(*, component_id, delta, reason, actor):
    return request_adjustment(
        component_id=component_id,
        delta=delta,
        reason=reason,
        actor=actor,
    )


@transaction.atomic
def approve_adjustment(*, adjustment_id, actor):
    adjustment = (
        StockAdjustment.objects.select_for_update()
        .select_related("component")
        .get(pk=adjustment_id)
    )
    if adjustment.status != StockAdjustment.Status.PENDING:
        raise ValidationError("This stock adjustment has already been reviewed.")
    movement = _record_movement(
        component_id=adjustment.component_id,
        movement_type=StockMovement.Type.ADJUST,
        quantity=abs(adjustment.delta),
        delta_on_hand=adjustment.delta,
        delta_reserved=Decimal("0"),
        actor=actor,
        note=adjustment.reason,
    )
    adjustment.status = StockAdjustment.Status.APPROVED
    adjustment.reviewed_by = actor
    adjustment.reviewed_at = timezone.now()
    adjustment.movement = movement
    adjustment.save(
        update_fields=("status", "reviewed_by", "reviewed_at", "movement")
    )
    record_audit(
        actor=actor,
        action="inventory.adjustment.approved",
        instance=adjustment,
        before={"status": StockAdjustment.Status.PENDING},
        after={"status": adjustment.status, "movement_id": movement.pk},
    )
    if adjustment.delta > 0:
        from workorders.services import retry_blocked_orders

        transaction.on_commit(lambda: retry_blocked_orders(actor=actor))
    return adjustment


@transaction.atomic
def reject_adjustment(*, adjustment_id, actor):
    adjustment = StockAdjustment.objects.select_for_update().get(pk=adjustment_id)
    if adjustment.status != StockAdjustment.Status.PENDING:
        raise ValidationError("This stock adjustment has already been reviewed.")
    adjustment.status = StockAdjustment.Status.REJECTED
    adjustment.reviewed_by = actor
    adjustment.reviewed_at = timezone.now()
    adjustment.save(update_fields=("status", "reviewed_by", "reviewed_at"))
    record_audit(
        actor=actor,
        action="inventory.adjustment.rejected",
        instance=adjustment,
        before={"status": StockAdjustment.Status.PENDING},
        after={"status": adjustment.status},
    )
    return adjustment
