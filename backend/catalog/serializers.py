import json
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from catalog.images import resize_image
from catalog.models import BOMLine, Component, Product, ProductRevision


class ResizedImageField(serializers.ImageField):
    def to_internal_value(self, data):
        image = super().to_internal_value(data)
        return resize_image(image)


class ComponentSerializer(serializers.ModelSerializer):
    photo = ResizedImageField(required=False, allow_null=True)
    used_in_count = serializers.SerializerMethodField()
    used_in = serializers.SerializerMethodField()

    class Meta:
        model = Component
        fields = (
            "id",
            "sku",
            "name",
            "description",
            "category",
            "unit",
            "revision_number",
            "photo",
            "reorder_level",
            "active",
            "used_in_count",
            "used_in",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_used_in_count(self, obj):
        annotated = getattr(obj, "used_in_count", None)
        if annotated is not None:
            return annotated
        return obj.bom_lines.values("product_revision__product_id").distinct().count()

    def get_used_in(self, obj):
        if not self.context.get("include_used_in"):
            return None
        return [
            {
                "product_id": line.product_revision.product_id,
                "product_number": line.product_revision.product.product_number,
                "product_name": line.product_revision.product.name,
                "revision_id": line.product_revision_id,
                "revision_number": line.product_revision.revision_number,
                "quantity_per_unit": str(line.quantity_per_unit),
            }
            for line in obj.bom_lines.select_related(
                "product_revision__product"
            ).order_by(
                "product_revision__product__product_number",
                "product_revision__revision_number",
            )
        ]


class ComponentUseSerializer(serializers.ModelSerializer):
    product_number = serializers.CharField(source="product_revision.product.product_number")
    product_name = serializers.CharField(source="product_revision.product.name")
    revision_number = serializers.IntegerField(source="product_revision.revision_number")
    quantity_per_unit = serializers.DecimalField(max_digits=12, decimal_places=3)

    class Meta:
        model = BOMLine
        fields = (
            "product_number",
            "product_name",
            "revision_number",
            "quantity_per_unit",
        )


class ComponentQuickCreateSerializer(ComponentSerializer):
    class Meta(ComponentSerializer.Meta):
        fields = tuple(
            field
            for field in ComponentSerializer.Meta.fields
            if field not in ("used_in", "used_in_count", "created_at", "updated_at")
        )


class ProductRevisionSummarySerializer(serializers.ModelSerializer):
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProductRevision
        fields = ("id", "revision_number", "notes", "created_at", "is_frozen", "is_current")
        read_only_fields = fields


class ProductSerializer(serializers.ModelSerializer):
    photo = ResizedImageField(required=False, allow_null=True)
    current_revision = ProductRevisionSummarySerializer(read_only=True)
    revisions = ProductRevisionSummarySerializer(many=True, read_only=True)
    bom_lines = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=True,
        allow_empty=False,
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "product_number",
            "name",
            "description",
            "sku",
            "photo",
            "active",
            "current_revision",
            "revisions",
            "bom_lines",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def to_internal_value(self, data):
        if hasattr(data, "get") and isinstance(data.get("bom_lines"), str):
            parsed = data.copy()
            try:
                parsed["bom_lines"] = json.loads(parsed["bom_lines"])
            except json.JSONDecodeError as exc:
                raise serializers.ValidationError(
                    {"bom_lines": "Provide BOM lines as a JSON array."}
                ) from exc
            data = parsed
        return super().to_internal_value(data)

    def validate_bom_lines(self, lines):
        if not isinstance(lines, list):
            raise serializers.ValidationError("Provide a list of BOM lines.")
        seen = set()
        validated = []
        errors = {}
        for index, line in enumerate(lines):
            try:
                component_id = int(line["component"])
                quantity = Decimal(str(line["quantity_per_unit"]))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                errors[index] = "Each BOM line needs a valid component and quantity."
                continue
            if (
                not quantity.is_finite()
                or quantity <= 0
                or quantity.as_tuple().exponent < -3
                or quantity > Decimal("999999999.999")
            ):
                errors[index] = "Quantity must be greater than zero and fit 9 digits plus 3 decimals."
                continue
            if component_id in seen:
                errors[index] = "Duplicate components are not allowed in one BOM."
                continue
            seen.add(component_id)
            try:
                component = Component.objects.get(pk=component_id)
            except (Component.DoesNotExist, ValueError):
                errors[index] = f"Component {component_id} does not exist."
                continue
            if not component.active:
                errors[index] = f"Component {component.sku} is inactive."
                continue
            validated.append(
                {"component": component, "quantity_per_unit": quantity}
            )
        if errors:
            raise serializers.ValidationError(errors)
        if not validated:
            raise serializers.ValidationError("At least one BOM component is required.")
        return validated

    @transaction.atomic
    def create(self, validated_data):
        bom_lines = validated_data.pop("bom_lines")
        component_ids = sorted(line["component"].pk for line in bom_lines)
        locked_components = {
            component.pk: component
            for component in Component.objects.select_for_update()
            .filter(pk__in=component_ids)
            .order_by("pk")
        }
        for line in bom_lines:
            component = locked_components.get(line["component"].pk)
            if component is None:
                raise serializers.ValidationError(
                    {"bom_lines": "A referenced component does not exist."}
                )
            if not component.active:
                raise serializers.ValidationError(
                    {"bom_lines": f"Component {component.sku} is inactive."}
                )
            line["component"] = component

        product = Product.objects.create(**validated_data)
        revision = ProductRevision.create_next(product=product)
        BOMLine.objects.bulk_create(
            [
                BOMLine(
                    product_revision=revision,
                    component=line["component"],
                    quantity_per_unit=line["quantity_per_unit"],
                )
                for line in bom_lines
            ]
        )
        product.current_revision = revision
        return product


class RevisionCreateSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)

    def create(self, validated_data):
        try:
            return ProductRevision.create_next(
                product=self.context["product"],
                notes=validated_data.get("notes", ""),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc


class ProductRevisionSerializer(serializers.ModelSerializer):
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = ProductRevision
        fields = ("id", "product", "revision_number", "notes", "created_at", "is_frozen", "is_current")
        read_only_fields = ("id", "product", "revision_number", "created_at", "is_frozen", "is_current")

    def validate(self, attrs):
        if self.instance and self.instance.is_frozen:
            raise serializers.ValidationError(
                "A product revision used by a work order is read-only."
            )
        return attrs


class BOMLineSerializer(serializers.ModelSerializer):
    component_detail = ComponentSerializer(source="component", read_only=True)
    total_required = serializers.SerializerMethodField()
    quantity_per_unit = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=Decimal("0.001"),
    )

    class Meta:
        model = BOMLine
        fields = (
            "id",
            "product_revision",
            "component",
            "component_detail",
            "quantity_per_unit",
            "total_required",
        )
        read_only_fields = ("id", "product_revision", "component_detail", "total_required")

    def get_total_required(self, obj):
        units = self.context.get("units", 1)
        total = obj.quantity_per_unit * Decimal(units)
        return format(total.normalize(), "f")

    def validate_quantity_per_unit(self, value):
        if value <= 0:
            raise serializers.ValidationError("Quantity per unit must be greater than zero.")
        return value

    def validate(self, attrs):
        revision = self.context.get("revision") or self.instance.product_revision
        if revision.is_frozen:
            raise serializers.ValidationError(
                "The BOM for a revision used by a work order is read-only."
            )
        component = attrs.get("component", getattr(self.instance, "component", None))
        if component and not component.active:
            raise serializers.ValidationError(
                {"component": "Inactive components cannot be added to a BOM."}
            )
        if component and BOMLine.objects.filter(
            product_revision=revision, component=component
        ).exclude(pk=getattr(self.instance, "pk", None)).exists():
            raise serializers.ValidationError(
                {"component": "This component already exists in the BOM."}
            )
        attrs["product_revision"] = revision
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        component = Component.objects.select_for_update().get(
            pk=validated_data["component"].pk
        )
        if not component.active:
            raise serializers.ValidationError(
                {"component": "Inactive components cannot be added to a BOM."}
            )
        revision = validated_data["product_revision"]
        if BOMLine.objects.filter(
            product_revision=revision, component=component
        ).exists():
            raise serializers.ValidationError(
                {"component": "This component already exists in the BOM."}
            )
        validated_data["component"] = component
        try:
            line = super().create(validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                "This component already exists in the BOM."
            ) from exc
        return line

    @transaction.atomic
    def update(self, instance, validated_data):
        component = validated_data.get("component", instance.component)
        component = Component.objects.select_for_update().get(pk=component.pk)
        if not component.active:
            raise serializers.ValidationError(
                {"component": "Inactive components cannot be added to a BOM."}
            )
        validated_data["component"] = component
        return super().update(instance, validated_data)
