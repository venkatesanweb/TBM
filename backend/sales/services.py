from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from audit.services import record_audit
from accounts.models import User
from catalog.models import Product
from workorders.models import FinishedUnit, FinishedUnitHistory, WorkOrder

from sales.models import (
    Sale,
    SaleReturn,
    SaleStatusHistory,
    SaleUnit,
    SalesPlatform,
)


STATUS_TRANSITIONS = {
    Sale.Status.READY_TO_SHIP: {Sale.Status.DELIVERED},
    Sale.Status.DELIVERED: {Sale.Status.PAYMENT_PENDING},
    Sale.Status.PAYMENT_PENDING: {Sale.Status.PAYMENT_RECEIVED},
    Sale.Status.PAYMENT_RECEIVED: set(),
    Sale.Status.RETURNED: set(),
}
RETURNABLE_STATUSES = {
    Sale.Status.DELIVERED,
    Sale.Status.PAYMENT_PENDING,
    Sale.Status.PAYMENT_RECEIVED,
}


def _sale_snapshot(sale):
    return {
        "platform": sale.platform.name,
        "status": sale.status,
        "order_reference": sale.order_reference,
        "price": str(sale.price),
        "sale_date": sale.sale_date.isoformat(),
        "notes": sale.notes,
        "shipping_name": sale.shipping_name,
        "shipping_address": sale.shipping_address,
        "shipping_city": sale.shipping_city,
        "shipping_state": sale.shipping_state,
        "shipping_postal_code": sale.shipping_postal_code,
        "shipping_phone": sale.shipping_phone,
        "finished_units": list(
            sale.units.order_by("finished_unit__serial_number").values_list(
                "finished_unit__serial_number", flat=True
            )
        ),
        "seller_label_reference": sale.seller_label_reference,
    }


def _record_sale_status(*, sale, to_status, actor, note=""):
    from_status = sale.status
    sale.status = to_status
    sale.save(update_fields=("status", "updated_at"))
    SaleStatusHistory.objects.create(
        sale=sale,
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        note=note,
    )
    return from_status


def platform_snapshot(platform):
    return {
        "name": platform.name,
        "label_template_type": platform.label_template_type,
        "active": platform.active,
    }


@transaction.atomic
def create_sale(*, values, actor):
    try:
        platform = SalesPlatform.objects.select_for_update().get(
            pk=values["platform"].pk,
            active=True,
        )
    except SalesPlatform.DoesNotExist as exc:
        raise ValidationError({"platform": "Choose an active selling platform."}) from exc

    requested_ids = values.get("unit_ids", [])
    locked_units = list(
        FinishedUnit.objects.select_for_update()
        .select_related("product", "product_revision")
        .filter(pk__in=requested_ids)
        .order_by("pk")
    )
    if len(locked_units) != len(requested_ids):
        raise ValidationError({"unit_ids": "One or more selected finished units were not found."})
    unavailable = [
        unit.serial_number
        for unit in locked_units
        if unit.status != FinishedUnit.Status.IN_STOCK
    ]
    if unavailable:
        raise ValidationError(
            {"units": f"These units are no longer in stock: {', '.join(unavailable)}."}
        )

    product_quantities = values.get("product_quantities", [])
    selected_ids = {unit.pk for unit in locked_units}
    for allocation in product_quantities:
        product = allocation["product"]
        quantity = allocation["quantity"]
        candidates = list(
            FinishedUnit.objects.select_for_update()
            .select_related("product", "product_revision")
            .filter(product=product, status=FinishedUnit.Status.IN_STOCK)
            .exclude(pk__in=selected_ids)
            .order_by("created_at", "pk")[:quantity]
        )
        if len(candidates) != quantity:
            raise ValidationError(
                {
                    "product_quantities": (
                        f"Only {len(candidates)} units of {product.product_number} "
                        f"are currently in stock; {quantity} requested."
                    )
                }
            )
        locked_units.extend(candidates)
        selected_ids.update(unit.pk for unit in candidates)

    if not locked_units:
        raise ValidationError({"units": "Select at least one finished unit."})

    sale = Sale.objects.create(
        platform=platform,
        created_by=actor,
        order_reference=values.get("order_reference", ""),
        price=values["price"],
        sale_date=values.get("sale_date") or timezone.localdate(),
        notes=values.get("notes", ""),
        shipping_name=values.get("shipping_name", ""),
        shipping_address=values.get("shipping_address", ""),
        shipping_city=values.get("shipping_city", ""),
        shipping_state=values.get("shipping_state", ""),
        shipping_postal_code=values.get("shipping_postal_code", ""),
        shipping_phone=values.get("shipping_phone", ""),
        seller_label_reference=values.get("seller_label_reference", ""),
    )
    SaleUnit.objects.bulk_create(
        [SaleUnit(sale=sale, finished_unit=unit) for unit in locked_units]
    )

    sale_reference = f"SALE-{sale.pk}"
    for unit in locked_units:
        from_status = unit.status
        unit.status = FinishedUnit.Status.RESERVED_FOR_SALE
        unit.save(update_fields=("status",))
        FinishedUnitHistory.objects.create(
            unit=unit,
            event_type=FinishedUnitHistory.EventType.STATUS_CHANGED,
            from_status=from_status,
            to_status=unit.status,
            actor=actor,
            sale_reference=sale_reference,
            note="Reserved as part of sale creation.",
        )
        unit.status = FinishedUnit.Status.SOLD
        unit.save(update_fields=("status",))
        FinishedUnitHistory.objects.create(
            unit=unit,
            event_type=FinishedUnitHistory.EventType.SALE,
            from_status=FinishedUnit.Status.RESERVED_FOR_SALE,
            to_status=unit.status,
            actor=actor,
            sale_reference=sale_reference,
            note="Sale created; unit is ready to ship.",
        )

    record_audit(
        actor=actor,
        action="sale.created",
        instance=sale,
        after=_sale_snapshot(sale),
    )
    SaleStatusHistory.objects.create(
        sale=sale,
        from_status="",
        to_status=sale.status,
        actor=actor,
        note="Sale created and marked Ready to Ship.",
    )
    return sale


@transaction.atomic
def update_sale(*, sale_id, values, actor):
    sale = (
        Sale.objects.select_for_update()
        .select_related("platform")
        .get(pk=sale_id)
    )
    before = _sale_snapshot(sale)
    editable_fields = (
        "platform",
        "order_reference",
        "price",
        "sale_date",
        "notes",
        "shipping_name",
        "shipping_address",
        "shipping_city",
        "shipping_state",
        "shipping_postal_code",
        "shipping_phone",
        "seller_label_reference",
    )
    changed = []
    for field in editable_fields:
        if field not in values:
            continue
        value = values[field]
        if field == "platform":
            value = SalesPlatform.objects.select_for_update().get(
                pk=value.pk, active=True
            )
        current = getattr(sale, f"{field}_id") if field == "platform" else getattr(sale, field)
        comparable = value.pk if field == "platform" else value
        if current != comparable:
            setattr(sale, field, value)
            changed.append(field)
    if changed:
        sale.save(update_fields=(*changed, "updated_at"))
        record_audit(
            actor=actor,
            action="sale.updated",
            instance=sale,
            before=before,
            after=_sale_snapshot(sale),
        )
    return sale


@transaction.atomic
def transition_sale(*, sale_id, to_status, actor, note=""):
    sale = Sale.objects.select_for_update().select_related("platform").get(pk=sale_id)
    if to_status not in STATUS_TRANSITIONS:
        raise ValidationError({"status": "Choose a valid sale status."})
    allowed = STATUS_TRANSITIONS[sale.status]
    if to_status not in allowed:
        raise ValidationError(
            {"status": f"A sale cannot move from {sale.get_status_display()} to {Sale.Status(to_status).label}."}
        )
    from_status = _record_sale_status(
        sale=sale,
        to_status=to_status,
        actor=actor,
        note=note,
    )
    record_audit(
        actor=actor,
        action="sale.status.changed",
        instance=sale,
        before={"status": from_status},
        after={"status": to_status, "note": note},
    )
    return sale


@transaction.atomic
def return_sale(*, sale_id, reason, actor, serial_numbers=None):
    sale = (
        Sale.objects.select_for_update()
        .select_related("platform")
        .get(pk=sale_id)
    )
    if sale.status not in RETURNABLE_STATUSES and sale.status != Sale.Status.RETURNED:
        raise ValidationError(
            {"status": "A return is allowed only after delivery or during a payment stage."}
        )
    sale_units_query = SaleUnit.objects.select_for_update().select_related("finished_unit").filter(sale=sale)
    if serial_numbers:
        sale_units_query = sale_units_query.filter(
            finished_unit__serial_number__in=serial_numbers
        )
    sale_units = list(sale_units_query.order_by("finished_unit_id"))
    if serial_numbers and len(sale_units) != len(serial_numbers):
        raise ValidationError(
            {"serial_numbers": "One or more scanned serials do not belong to this sale."}
        )
    if not sale_units:
        raise ValidationError({"units": "This sale has no finished units to return."})
    already_returned = SaleReturn.objects.filter(
        sale_unit__in=sale_units
    ).values_list("sale_unit__finished_unit__serial_number", flat=True)
    if already_returned:
        raise ValidationError(
            {"serial_numbers": f"{next(iter(already_returned))} has already been returned."}
        )

    returns = []
    for sale_unit in sale_units:
        unit = sale_unit.finished_unit
        if unit.status != FinishedUnit.Status.SOLD:
            raise ValidationError(
                {"units": f"{unit.serial_number} is not in Sold status and cannot be returned."}
            )
        unit.status = FinishedUnit.Status.UNDER_INSPECTION
        unit.save(update_fields=("status",))
        FinishedUnitHistory.objects.create(
            unit=unit,
            event_type=FinishedUnitHistory.EventType.RETURN,
            from_status=FinishedUnit.Status.SOLD,
            to_status=FinishedUnit.Status.UNDER_INSPECTION,
            actor=actor,
            sale_reference=f"SALE-{sale.pk}",
            note=reason,
        )
        returns.append(SaleReturn(sale_unit=sale_unit, reason=reason))
    SaleReturn.objects.bulk_create(returns)
    if sale.status != Sale.Status.RETURNED:
        _record_sale_status(
            sale=sale,
            to_status=Sale.Status.RETURNED,
            actor=actor,
            note=reason,
        )
    record_audit(
        actor=actor,
        action="sale.returned",
        instance=sale,
        after={
            "status": sale.status,
            "reason": reason,
            "serial_numbers": [item.finished_unit.serial_number for item in sale_units],
        },
    )
    return sale


@transaction.atomic
def decide_sale_return(
    *, sale_id, decision, actor, note="", assigned_admin=None, serial_numbers=None
):
    if actor.role != User.Role.SUPER_ADMIN:
        raise ValidationError("Only a Super Admin may decide a returned unit's disposition.")
    sale = Sale.objects.select_for_update().get(pk=sale_id)
    if sale.status != Sale.Status.RETURNED:
        raise ValidationError({"sale": "Only returned sales can be dispositioned."})
    returns_query = (
        SaleReturn.objects.select_for_update()
        .select_related("sale_unit__finished_unit")
        .filter(sale_unit__sale=sale, decision=SaleReturn.Decision.PENDING)
    )
    if serial_numbers:
        returns_query = returns_query.filter(
            sale_unit__finished_unit__serial_number__in=serial_numbers
        )
    returns = list(
        returns_query.order_by(
            "sale_unit__finished_unit__product_id",
            "sale_unit__finished_unit_id",
        )
    )
    if serial_numbers and len(returns) != len(serial_numbers):
        raise ValidationError(
            {"serial_numbers": "One or more scanned serials have no pending return for this sale."}
        )
    if not returns:
        raise ValidationError({"sale": "There are no returned units awaiting a decision."})
    if decision not in {
        SaleReturn.Decision.RESTOCK,
        SaleReturn.Decision.REWORK,
        SaleReturn.Decision.SCRAP,
    }:
        raise ValidationError({"decision": "Choose Restock, Rework, or Scrap."})

    work_orders = {}
    if decision == SaleReturn.Decision.REWORK:
        if assigned_admin is None:
            raise ValidationError({"assigned_admin": "Choose an Admin for the rework order."})
        if assigned_admin.role != User.Role.ADMIN or not assigned_admin.is_active:
            raise ValidationError({"assigned_admin": "Choose an active Admin account."})
        unit_counts = {}
        for return_record in returns:
            product_id = return_record.sale_unit.finished_unit.product_id
            unit_counts[product_id] = unit_counts.get(product_id, 0) + 1
        from datetime import timedelta

        from workorders.services import create_work_order

        due_date = timezone.localdate() + timedelta(days=7)
        for product_id, quantity in sorted(unit_counts.items()):
            work_orders[product_id] = create_work_order(
                product_id=product_id,
                quantity=quantity,
                priority=WorkOrder.Priority.MEDIUM,
                due_date=due_date,
                assigned_admin=assigned_admin,
                actor=actor,
            )

    for return_record in returns:
        unit = return_record.sale_unit.finished_unit
        if unit.status != FinishedUnit.Status.UNDER_INSPECTION:
            raise ValidationError(
                {"units": f"{unit.serial_number} is not under inspection."}
            )
        before = {
            "decision": return_record.decision,
            "unit_status": unit.status,
        }
        if decision == SaleReturn.Decision.RESTOCK:
            unit.status = FinishedUnit.Status.IN_STOCK
            FinishedUnitHistory.objects.create(
                unit=unit,
                event_type=FinishedUnitHistory.EventType.INSPECTION,
                from_status=FinishedUnit.Status.UNDER_INSPECTION,
                to_status=FinishedUnit.Status.IN_STOCK,
                actor=actor,
                sale_reference=f"SALE-{sale.pk}",
                note=note or "Returned unit approved for finished-goods restock.",
            )
        elif decision == SaleReturn.Decision.SCRAP:
            unit.status = FinishedUnit.Status.SCRAPPED
            FinishedUnitHistory.objects.create(
                unit=unit,
                event_type=FinishedUnitHistory.EventType.INSPECTION,
                from_status=FinishedUnit.Status.UNDER_INSPECTION,
                to_status=FinishedUnit.Status.SCRAPPED,
                actor=actor,
                sale_reference=f"SALE-{sale.pk}",
                note=note or "Returned unit scrapped after inspection.",
            )
        unit.save(update_fields=("status",))
        return_record.decision = decision
        return_record.decision_note = note
        return_record.decided_by = actor
        return_record.decided_at = timezone.now()
        if decision == SaleReturn.Decision.REWORK:
            return_record.rework_order = work_orders[unit.product_id]
        return_record.save(
            update_fields=(
                "decision",
                "decision_note",
                "decided_by",
                "decided_at",
                "rework_order",
            )
        )
        record_audit(
            actor=actor,
            action="sale.return.disposition",
            instance=return_record,
            before=before,
            after={
                "decision": decision,
                "unit_status": unit.status,
                "work_order_id": return_record.rework_order_id,
                "note": note,
            },
        )
    return sale


@transaction.atomic
def bulk_transition_by_serials(*, serial_numbers, to_status, actor, note=""):
    units = list(
        FinishedUnit.objects.select_for_update()
        .filter(serial_number__in=serial_numbers)
        .order_by("pk")
    )
    found = {unit.serial_number for unit in units}
    missing = [serial for serial in serial_numbers if serial not in found]
    if missing:
        raise ValidationError({"serial_numbers": f"Serial not found: {missing[0]}."})
    sale_links = dict(
        SaleUnit.objects.filter(finished_unit__in=units).values_list(
            "finished_unit_id", "sale_id"
        )
    )
    if set(sale_links) != {unit.pk for unit in units}:
        raise ValidationError(
            {"serial_numbers": "Every scanned unit must belong to a sale."}
        )
    sale_ids = sorted(set(sale_links.values()))
    sales = list(
        Sale.objects.select_for_update()
        .filter(pk__in=sale_ids)
        .order_by("pk")
    )
    invalid = [
        sale for sale in sales
        if to_status not in STATUS_TRANSITIONS.get(sale.status, set())
    ]
    if invalid:
        raise ValidationError(
            {"status": f"SALE-{invalid[0].pk} cannot move from {invalid[0].get_status_display()} to {Sale.Status(to_status).label}."}
        )
    for sale in sales:
        from_status = _record_sale_status(
            sale=sale,
            to_status=to_status,
            actor=actor,
            note=note,
        )
        record_audit(
            actor=actor,
            action="sale.status.changed",
            instance=sale,
            before={"status": from_status},
            after={"status": to_status, "note": note, "source": "bulk serial scan"},
        )
    return sales
