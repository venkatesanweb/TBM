from rest_framework import serializers

from accounts.models import User
from catalog.models import Product
from sales.models import Sale, SaleReturn, SaleStatusHistory, SaleUnit, SalesPlatform
from sales.services import platform_snapshot


class SalesPlatformSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesPlatform
        fields = (
            "id",
            "name",
            "label_template_type",
            "active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class ProductQuantitySerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1, max_value=100000)


class SaleCreateSerializer(serializers.Serializer):
    platform = serializers.PrimaryKeyRelatedField(
        queryset=SalesPlatform.objects.filter(active=True)
    )
    unit_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )
    product_quantities = ProductQuantitySerializer(many=True, required=False)
    order_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    sale_date = serializers.DateField()
    notes = serializers.CharField(required=False, allow_blank=True)
    shipping_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    shipping_address = serializers.CharField(required=False, allow_blank=True)
    shipping_city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_postal_code = serializers.CharField(max_length=24, required=False, allow_blank=True)
    shipping_phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    seller_label_reference = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )

    def validate(self, attrs):
        if not attrs.get("order_reference", "").strip() and not attrs.get(
            "shipping_name", ""
        ).strip():
            raise serializers.ValidationError(
                {"order_reference": "Enter a customer or order reference."}
            )
        unit_ids = attrs.get("unit_ids", [])
        if len(unit_ids) != len(set(unit_ids)):
            raise serializers.ValidationError({"unit_ids": "A finished unit may only be selected once."})
        allocations = attrs.get("product_quantities", [])
        if len({item["product"].pk for item in allocations}) != len(allocations):
            raise serializers.ValidationError(
                {"product_quantities": "Combine quantities for the same product."}
            )
        if not unit_ids and not allocations:
            raise serializers.ValidationError("Select finished units by serial or product quantity.")
        platform = attrs["platform"]
        if platform.label_template_type == SalesPlatform.LabelTemplateType.TAMIRABOT:
            if not attrs.get("shipping_name") or not attrs.get("shipping_address"):
                raise serializers.ValidationError(
                    {"shipping_address": "Enter a recipient name and address for the packing label."}
                )
        elif not attrs.get("seller_label_reference", "").strip():
            raise serializers.ValidationError(
                {"seller_label_reference": "Enter the seller-provided label or reference."}
            )
        return attrs


class SaleUpdateSerializer(serializers.Serializer):
    platform = serializers.PrimaryKeyRelatedField(
        queryset=SalesPlatform.objects.filter(active=True),
        required=False,
    )
    order_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False
    )
    sale_date = serializers.DateField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    shipping_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    shipping_address = serializers.CharField(required=False, allow_blank=True)
    shipping_city = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_state = serializers.CharField(max_length=120, required=False, allow_blank=True)
    shipping_postal_code = serializers.CharField(max_length=24, required=False, allow_blank=True)
    shipping_phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    seller_label_reference = serializers.CharField(
        max_length=500, required=False, allow_blank=True
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one sale detail to update.")
        instance = self.instance
        platform = attrs.get("platform", instance.platform)
        if platform.label_template_type == SalesPlatform.LabelTemplateType.TAMIRABOT:
            name = attrs.get("shipping_name", instance.shipping_name)
            address = attrs.get("shipping_address", instance.shipping_address)
            if not name or not address:
                raise serializers.ValidationError(
                    {"shipping_address": "The generated label requires a recipient name and address."}
                )
        elif not attrs.get("seller_label_reference", instance.seller_label_reference).strip():
            raise serializers.ValidationError(
                {"seller_label_reference": "Enter the seller-provided label or reference."}
            )
        return attrs


class SaleStatusTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Sale.Status.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class SaleReturnRequestSerializer(serializers.Serializer):
    serial_numbers = serializers.ListField(
        child=serializers.CharField(max_length=120, allow_blank=False),
        required=False,
        min_length=1,
        max_length=100,
    )
    reason = serializers.CharField(max_length=2000)

    def validate_reason(self, value):
        if not value.strip():
            raise serializers.ValidationError("Provide the reason for the return.")
        return value.strip()

    def validate_serial_numbers(self, values):
        serials = [value.strip() for value in values]
        if any(not value for value in serials):
            raise serializers.ValidationError("Serial numbers cannot be empty.")
        if len(set(serials)) != len(serials):
            raise serializers.ValidationError("A serial number was scanned more than once.")
        return serials


class SaleReturnDecisionSerializer(serializers.Serializer):
    serial_numbers = serializers.ListField(
        child=serializers.CharField(max_length=120, allow_blank=False),
        required=False,
        min_length=1,
        max_length=100,
    )
    decision = serializers.ChoiceField(
        choices=(
            (SaleReturn.Decision.RESTOCK, "Restock as finished good"),
            (SaleReturn.Decision.REWORK, "Rework"),
            (SaleReturn.Decision.SCRAP, "Scrap"),
        )
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)
    assigned_admin = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.ADMIN, is_active=True),
        required=False,
    )

    def validate(self, attrs):
        serials = attrs.get("serial_numbers", [])
        if len(serials) != len(set(serials)):
            raise serializers.ValidationError(
                {"serial_numbers": "A serial number was scanned more than once."}
            )
        if attrs["decision"] == SaleReturn.Decision.REWORK and not attrs.get("assigned_admin"):
            raise serializers.ValidationError(
                {"assigned_admin": "Choose an Admin for the rework work order."}
            )
        return attrs


class SaleStatusHistorySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.username", read_only=True, default="")
    from_status_label = serializers.SerializerMethodField()
    to_status_label = serializers.CharField(source="get_to_status_display", read_only=True)

    class Meta:
        model = SaleStatusHistory
        fields = (
            "id",
            "from_status",
            "from_status_label",
            "to_status",
            "to_status_label",
            "actor_name",
            "note",
            "timestamp",
        )
        read_only_fields = fields

    def get_from_status_label(self, history):
        if not history.from_status:
            return ""
        return Sale.Status(history.from_status).label


class SaleBulkStatusSerializer(serializers.Serializer):
    serial_numbers = serializers.ListField(
        child=serializers.CharField(max_length=120, allow_blank=False),
        min_length=1,
        max_length=100,
    )
    status = serializers.ChoiceField(
        choices=(
            Sale.Status.DELIVERED,
            Sale.Status.PAYMENT_PENDING,
            Sale.Status.PAYMENT_RECEIVED,
        )
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=2000)

    def validate_serial_numbers(self, values):
        serials = [value.strip() for value in values]
        if any(not value for value in serials):
            raise serializers.ValidationError("Serial numbers cannot be empty.")
        if len(set(serials)) != len(serials):
            raise serializers.ValidationError("A serial number was scanned more than once.")
        return serials


class SaleReturnSerializer(serializers.ModelSerializer):
    serial_number = serializers.CharField(
        source="sale_unit.finished_unit.serial_number", read_only=True
    )
    unit_status = serializers.CharField(
        source="sale_unit.finished_unit.status", read_only=True
    )
    decided_by_name = serializers.CharField(
        source="decided_by.username", read_only=True, default=""
    )
    rework_order_number = serializers.SerializerMethodField()

    class Meta:
        model = SaleReturn
        fields = (
            "id",
            "serial_number",
            "reason",
            "decision",
            "decision_note",
            "unit_status",
            "decided_by_name",
            "rework_order",
            "rework_order_number",
            "created_at",
            "decided_at",
        )
        read_only_fields = fields

    def get_rework_order_number(self, return_record):
        return (
            f"WO-{return_record.rework_order_id}"
            if return_record.rework_order_id
            else ""
        )


class SaleUnitSerializer(serializers.ModelSerializer):
    serial_number = serializers.CharField(source="finished_unit.serial_number", read_only=True)
    barcode_value = serializers.CharField(source="finished_unit.barcode_value", read_only=True)
    product_number = serializers.CharField(
        source="finished_unit.product.product_number", read_only=True
    )
    product_name = serializers.CharField(
        source="finished_unit.product.name", read_only=True
    )
    product_sku = serializers.CharField(source="finished_unit.product.sku", read_only=True)
    revision_number = serializers.IntegerField(
        source="finished_unit.product_revision.revision_number", read_only=True
    )
    status = serializers.CharField(source="finished_unit.status", read_only=True)

    class Meta:
        model = SaleUnit
        fields = (
            "id",
            "finished_unit",
            "serial_number",
            "barcode_value",
            "product_number",
            "product_name",
            "product_sku",
            "revision_number",
            "status",
        )
        read_only_fields = fields


class SaleSerializer(serializers.ModelSerializer):
    platform_name = serializers.CharField(source="platform.name", read_only=True)
    label_template_type = serializers.CharField(
        source="platform.label_template_type", read_only=True
    )
    created_by_name = serializers.CharField(source="created_by.username", read_only=True)
    units = SaleUnitSerializer(many=True, read_only=True)
    label = serializers.SerializerMethodField()
    status_history = SaleStatusHistorySerializer(many=True, read_only=True)
    returns = serializers.SerializerMethodField()

    class Meta:
        model = Sale
        fields = (
            "id",
            "platform",
            "platform_name",
            "label_template_type",
            "created_by",
            "created_by_name",
            "status",
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
            "units",
            "label",
            "status_history",
            "returns",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_by",
            "status",
            "units",
            "created_at",
            "updated_at",
        )

    def get_label(self, sale):
        if sale.platform.label_template_type == SalesPlatform.LabelTemplateType.TAMIRABOT:
            return {
                "mode": "generated",
                "order_reference": sale.order_reference,
                "recipient": {
                    "name": sale.shipping_name,
                    "address": sale.shipping_address,
                    "city": sale.shipping_city,
                    "state": sale.shipping_state,
                    "postal_code": sale.shipping_postal_code,
                    "phone": sale.shipping_phone,
                },
                "units": [
                    {
                        "serial_number": item.finished_unit.serial_number,
                        "barcode_value": item.finished_unit.barcode_value,
                        "product_number": item.finished_unit.product.product_number,
                        "product_name": item.finished_unit.product.name,
                        "product_sku": item.finished_unit.product.sku,
                        "revision_number": item.finished_unit.product_revision.revision_number,
                    }
                    for item in sale.units.select_related(
                        "finished_unit__product",
                        "finished_unit__product_revision",
                    ).all()
                ],
            }
        return {
            "mode": "seller_provided",
            "reference": sale.seller_label_reference,
        }

    def get_returns(self, sale):
        records = SaleReturn.objects.filter(sale_unit__sale=sale).select_related(
            "sale_unit__finished_unit",
            "decided_by",
            "rework_order",
        )
        return SaleReturnSerializer(records, many=True).data
