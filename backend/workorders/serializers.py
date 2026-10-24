from rest_framework import serializers

from accounts.models import User
from catalog.models import Product
from workorders.models import (
    FinishedUnit,
    FinishedUnitHistory,
    PackConfirmation,
    WorkOrder,
    WorkOrderReservation,
)


class WorkOrderReservationSerializer(serializers.ModelSerializer):
    component_sku = serializers.CharField(source="component.sku", read_only=True)
    component_name = serializers.CharField(source="component.name", read_only=True)
    remaining = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrderReservation
        fields = (
            "component",
            "component_sku",
            "component_name",
            "quantity",
            "released_quantity",
            "consumed_quantity",
            "remaining",
        )

    def get_remaining(self, obj):
        return str(obj.remaining)


class FinishedUnitSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    product_number = serializers.CharField(source="product.product_number", read_only=True)
    revision_number = serializers.IntegerField(
        source="product_revision.revision_number", read_only=True
    )
    work_order_number = serializers.SerializerMethodField()
    packed_by = serializers.CharField(
        source="pack_confirmation.confirmed_by.username", read_only=True
    )
    packed_at = serializers.DateTimeField(
        source="pack_confirmation.created_at", read_only=True
    )
    history = serializers.SerializerMethodField()

    class Meta:
        model = FinishedUnit
        fields = (
            "id",
            "serial_number",
            "barcode_value",
            "status",
            "product_name",
            "product_sku",
            "product_number",
            "revision_number",
            "work_order",
            "work_order_number",
            "packed_by",
            "packed_at",
            "history",
            "created_at",
        )
        read_only_fields = fields

    def get_work_order_number(self, obj):
        return f"WO-{obj.work_order_id}"

    def get_history(self, obj):
        history = obj.history.all()
        return FinishedUnitHistorySerializer(history, many=True).data


class FinishedUnitHistorySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True, default="")

    class Meta:
        model = FinishedUnitHistory
        fields = (
            "id",
            "event_type",
            "from_status",
            "to_status",
            "actor",
            "actor_name",
            "sale_reference",
            "note",
            "timestamp",
        )
        read_only_fields = fields


class WorkOrderSerializer(serializers.ModelSerializer):
    work_order_number = serializers.SerializerMethodField()
    product_number = serializers.CharField(source="product.product_number", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_sku = serializers.CharField(source="product.sku", read_only=True)
    revision_number = serializers.IntegerField(
        source="product_revision.revision_number", read_only=True
    )
    assigned_admin_name = serializers.CharField(
        source="assigned_admin.username", read_only=True
    )
    reservations = WorkOrderReservationSerializer(many=True, read_only=True)
    finished_units = FinishedUnitSerializer(many=True, read_only=True)
    bom_components = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = (
            "id",
            "work_order_number",
            "product",
            "product_number",
            "product_name",
            "product_sku",
            "product_revision",
            "revision_number",
            "quantity",
            "packed_quantity",
            "priority",
            "due_date",
            "status",
            "assigned_admin",
            "assigned_admin_name",
            "blocked_reason",
            "bom_components",
            "reservations",
            "finished_units",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "work_order_number",
            "product_revision",
            "packed_quantity",
            "status",
            "blocked_reason",
            "bom_components",
            "reservations",
            "finished_units",
            "created_at",
            "updated_at",
        )

    def get_work_order_number(self, obj):
        return f"WO-{obj.pk}"

    def get_bom_components(self, obj):
        return [
            {
                "component": line.component_id,
                "sku": line.component.sku,
                "name": line.component.name,
                "unit": line.component.unit,
                "quantity_per_unit": str(line.quantity_per_unit),
                "total_required": str(line.quantity_per_unit * obj.quantity),
            }
            for line in obj.product_revision.bom_lines.all()
        ]


class WorkOrderCreateSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(active=True))
    quantity = serializers.IntegerField(min_value=1, max_value=100000)
    priority = serializers.ChoiceField(choices=WorkOrder.Priority.choices)
    due_date = serializers.DateField()
    assigned_admin = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.ADMIN, is_active=True)
    )


class WorkOrderUpdateSerializer(serializers.Serializer):
    assigned_admin = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.ADMIN, is_active=True),
        required=False,
    )
    quantity = serializers.IntegerField(min_value=1, max_value=100000, required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide an assignment or smaller quantity.")
        if "quantity" in attrs and "assigned_admin" in attrs:
            raise serializers.ValidationError(
                "Reassignment and quantity reduction must be submitted separately."
            )
        return attrs


class PackRequestSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=100000)
    idempotency_key = serializers.CharField(max_length=100, allow_blank=False)


class PackConfirmationSerializer(serializers.ModelSerializer):
    finished_units = FinishedUnitSerializer(many=True, read_only=True)

    class Meta:
        model = PackConfirmation
        fields = ("id", "quantity", "idempotency_key", "created_at", "finished_units")
        read_only_fields = fields
