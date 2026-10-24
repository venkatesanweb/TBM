from decimal import Decimal

from rest_framework import serializers

from catalog.models import Component
from inventory.models import StockAdjustment, StockBalance, StockMovement


class InventoryComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Component
        fields = (
            "id",
            "sku",
            "name",
            "unit",
            "category",
            "revision_number",
            "reorder_level",
            "active",
        )


class ComponentStockSerializer(serializers.Serializer):
    component = InventoryComponentSerializer(source="*")
    on_hand = serializers.SerializerMethodField()
    reserved = serializers.SerializerMethodField()
    available = serializers.SerializerMethodField()
    low_stock = serializers.SerializerMethodField()

    @staticmethod
    def _balance(obj):
        try:
            return obj.stock_balance
        except StockBalance.DoesNotExist:
            return None

    def get_on_hand(self, obj):
        balance = self._balance(obj)
        return balance.on_hand if balance else Decimal("0")

    def get_reserved(self, obj):
        balance = self._balance(obj)
        return balance.reserved if balance else Decimal("0")

    def get_available(self, obj):
        balance = self._balance(obj)
        return balance.available if balance else Decimal("0")

    def get_low_stock(self, obj):
        return self.get_available(obj) <= obj.reorder_level


class StockMovementSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True, default=None)
    component_sku = serializers.CharField(source="component.sku", read_only=True)

    class Meta:
        model = StockMovement
        fields = (
            "id",
            "component",
            "component_sku",
            "movement_type",
            "quantity",
            "delta_on_hand",
            "delta_reserved",
            "work_order_reference",
            "username",
            "note",
            "timestamp",
        )
        read_only_fields = fields


class ReceiveStockSerializer(serializers.Serializer):
    component_id = serializers.PrimaryKeyRelatedField(
        source="component",
        queryset=Component.objects.filter(active=True),
    )
    quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, min_value=Decimal("0.001")
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class AdjustmentRequestSerializer(serializers.Serializer):
    component_id = serializers.PrimaryKeyRelatedField(
        source="component",
        queryset=Component.objects.all(),
    )
    delta = serializers.DecimalField(max_digits=12, decimal_places=3)
    reason = serializers.CharField(max_length=2000, trim_whitespace=True)

    def validate_delta(self, value):
        if value == 0:
            raise serializers.ValidationError("Adjustment must be greater or less than zero.")
        return value

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("A reason is required for a stock adjustment.")
        return value.strip()


class StockAdjustmentSerializer(serializers.ModelSerializer):
    component = InventoryComponentSerializer(read_only=True)
    requested_by_username = serializers.CharField(
        source="requested_by.username", read_only=True, default=None
    )
    reviewed_by_username = serializers.CharField(
        source="reviewed_by.username", read_only=True, default=None
    )

    class Meta:
        model = StockAdjustment
        fields = (
            "id",
            "component",
            "delta",
            "reason",
            "status",
            "requested_by_username",
            "reviewed_by_username",
            "created_at",
            "reviewed_at",
            "movement_id",
        )
        read_only_fields = fields
