import json
import re
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from accounts.models import User
from catalog.barcodes import (
    lock_and_validate_barcode,
    normalize_barcode,
    validate_barcode_owner,
)
from catalog.images import resize_image
from catalog.models import (
    BOMLine,
    Component,
    CompanyProfile,
    IndianState,
    Product,
    ProductRevision,
    TaxRate,
)
from catalog.pricing import set_component_price
from catalog.product_pricing import can_view_product_prices, product_price_summary
from catalog.company import company_profile_snapshot


class ResizedImageField(serializers.ImageField):
    def to_internal_value(self, data):
        image = super().to_internal_value(data)
        return resize_image(image)


class BarcodeField(serializers.CharField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        return normalize_barcode(value) or ""


class MoneyDecimalField(serializers.DecimalField):
    def to_internal_value(self, data):
        if isinstance(data, float):
            raise serializers.ValidationError("Provide money as a decimal string.")
        return super().to_internal_value(data)


class ComponentSerializer(serializers.ModelSerializer):
    photo = ResizedImageField(required=False, allow_null=True)
    barcode = BarcodeField(
        required=False, allow_null=True, allow_blank=True, trim_whitespace=False
    )
    used_in_count = serializers.SerializerMethodField()
    used_in = serializers.SerializerMethodField()
    cost_price_set = serializers.SerializerMethodField()
    cost_price = MoneyDecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        allow_null=True,
    )
    cost_price_reason = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=500
    )

    class Meta:
        model = Component
        fields = (
            "id",
            "sku",
            "barcode",
            "name",
            "description",
            "category",
            "unit",
            "revision_number",
            "photo",
            "reorder_level",
            "cost_price",
            "cost_price_reason",
            "cost_price_set",
            "active",
            "used_in_count",
            "used_in",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if getattr(user, "role", None) != User.Role.SUPER_ADMIN:
            fields.pop("cost_price", None)
            fields.pop("cost_price_reason", None)
        return fields

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if self.instance is not None and "cost_price" in attrs:
            old_price = self.instance.cost_price
            new_price = attrs["cost_price"]
            reason = attrs.get("cost_price_reason", "").strip()
            if old_price is not None and old_price != new_price and not reason:
                raise serializers.ValidationError(
                    {"cost_price_reason": "Provide a reason when changing an existing price."}
                )
        return attrs

    def get_cost_price_set(self, obj):
        return obj.cost_price is not None

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

    def validate_barcode(self, value):
        return validate_barcode_owner(value, instance=self.instance)

    @transaction.atomic
    def create(self, validated_data):
        price = validated_data.pop("cost_price", None)
        reason = validated_data.pop("cost_price_reason", "")
        if "barcode" in validated_data:
            validated_data["barcode"] = lock_and_validate_barcode(
                validated_data["barcode"]
            )
        component = super().create(validated_data)
        if price is not None:
            set_component_price(
                component=component,
                new_price=price,
                actor=self.context["request"].user,
                reason=reason.strip(),
            )
        return component

    @transaction.atomic
    def update(self, instance, validated_data):
        price = validated_data.pop("cost_price", serializers.empty)
        reason = validated_data.pop("cost_price_reason", "").strip()
        if "barcode" in validated_data:
            validated_data["barcode"] = lock_and_validate_barcode(
                validated_data["barcode"], instance=instance
            )
        component = super().update(instance, validated_data)
        if price is not serializers.empty:
            set_component_price(
                component=component,
                new_price=price,
                actor=self.context["request"].user,
                reason=reason,
            )
        return component


class ComponentPriceUpdateSerializer(serializers.Serializer):
    component_id = serializers.IntegerField(min_value=1)
    cost_price = MoneyDecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0"),
        allow_null=True,
    )
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class ComponentPriceBatchSerializer(serializers.Serializer):
    items = ComponentPriceUpdateSerializer(many=True, allow_empty=False)

    def validate_items(self, items):
        ids = [item["component_id"] for item in items]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each component may appear only once.")
        return items


class ComponentUseSerializer(serializers.ModelSerializer):
    product_number = serializers.CharField(source="product_revision.product.product_number")
    product_name = serializers.CharField(source="product_revision.product.name")
    revision_number = serializers.IntegerField(source="product_revision.revision_number")
    quantity_per_unit = serializers.DecimalField(max_digits=12, decimal_places=3)
    product_id = serializers.IntegerField(source="product_revision.product_id", read_only=True)
    revision_id = serializers.IntegerField(source="product_revision_id", read_only=True)

    class Meta:
        model = BOMLine
        fields = (
            "product_number",
            "product_name",
            "revision_number",
            "quantity_per_unit",
            "product_id",
            "revision_id",
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
    barcode = BarcodeField(
        required=False, allow_null=True, allow_blank=True, trim_whitespace=False
    )
    current_revision = ProductRevisionSummarySerializer(read_only=True)
    revisions = ProductRevisionSummarySerializer(many=True, read_only=True)
    product_price_summary = serializers.SerializerMethodField()
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
            "barcode",
            "name",
            "description",
            "generic_name",
            "net_quantity_text",
            "sku",
            "photo",
            "active",
            "current_revision",
            "revisions",
            "product_price_summary",
            "bom_lines",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_barcode(self, value):
        return validate_barcode_owner(value, instance=self.instance)

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if not request or not can_view_product_prices(request.user):
            fields.pop("product_price_summary", None)
        return fields

    def get_product_price_summary(self, product):
        return product_price_summary(
            product,
            include_history=bool(
                self.context.get("include_product_price_history")
            ),
        )

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
        if "barcode" in validated_data:
            validated_data["barcode"] = lock_and_validate_barcode(
                validated_data["barcode"]
            )
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

    @transaction.atomic
    def update(self, instance, validated_data):
        if "barcode" in validated_data:
            validated_data["barcode"] = lock_and_validate_barcode(
                validated_data["barcode"], instance=instance
            )
        return super().update(instance, validated_data)


class ProductPriceCreateSerializer(serializers.Serializer):
    margin_percent = MoneyDecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0")
    )
    selling_price_incl_tax = MoneyDecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    mrp = MoneyDecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0.01")
    )
    hsn_code = serializers.RegexField(
        regex=r"^(?:\d{4}|\d{6}|\d{8})$",
        error_messages={
            "invalid": "HSN code must contain exactly 4, 6, or 8 digits."
        },
    )
    tax_rate_id = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    def validate(self, attrs):
        if attrs["selling_price_incl_tax"] > attrs["mrp"]:
            raise serializers.ValidationError(
                {
                    "selling_price_incl_tax": (
                        "Selling price inclusive of tax cannot exceed MRP."
                    )
                }
            )
        return attrs


class ProductPricePreviewSerializer(serializers.Serializer):
    margin_percent = MoneyDecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0")
    )
    tax_rate_id = serializers.IntegerField(min_value=1)


class TaxRateSerializer(serializers.ModelSerializer):
    rate_percent = MoneyDecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0")
    )

    class Meta:
        model = TaxRate
        fields = ("id", "rate_percent", "label", "active")
        read_only_fields = ("id",)

    def validate_rate_percent(self, value):
        if not value.is_finite():
            raise serializers.ValidationError("Provide a finite tax rate.")
        existing = TaxRate.objects.filter(rate_percent=value)
        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)
        if existing.exists():
            raise serializers.ValidationError("This tax rate already exists.")
        return value


class CompanyStateField(serializers.PrimaryKeyRelatedField):
    def to_internal_value(self, data):
        if data == "":
            return None
        return super().to_internal_value(data)


class CompanyProfileSerializer(serializers.ModelSerializer):
    logo = ResizedImageField(required=False)
    signature_image = ResizedImageField(required=False)
    state_code = CompanyStateField(
        queryset=IndianState.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = CompanyProfile
        fields = (
            "legal_name",
            "trade_name",
            "address_line1",
            "address_line2",
            "city",
            "state_code",
            "pincode",
            "gstin",
            "pan",
            "phone",
            "email",
            "packer_manufacturer_name",
            "packer_address",
            "country_of_origin",
            "consumer_care_name",
            "consumer_care_address",
            "consumer_care_phone",
            "consumer_care_email",
            "invoice_prefix",
            "credit_note_prefix",
            "logo",
            "authorised_signatory_name",
            "signature_image",
            "updated_at",
        )
        read_only_fields = ("updated_at",)

    @staticmethod
    def _validate_phone(value, field_name):
        if value and not re.fullmatch(r"\+?[0-9][0-9\s().-]{6,19}", value):
            raise serializers.ValidationError(
                f"Enter a valid phone number for {field_name}."
            )
        return value

    def validate_phone(self, value):
        return self._validate_phone(value, "company phone")

    def validate_consumer_care_phone(self, value):
        return self._validate_phone(value, "consumer care")

    def validate_pincode(self, value):
        if value and not re.fullmatch(r"\d{6}", value):
            raise serializers.ValidationError("PIN code must contain exactly 6 digits.")
        return value

    def validate_gstin(self, value):
        value = value.strip().upper()
        if not value:
            return value
        if not re.fullmatch(
            r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", value
        ):
            raise serializers.ValidationError(
                "GSTIN must be 15 characters in the valid GSTIN format."
            )
        state_code = self.initial_data.get(
            "state_code",
            self.instance.state_code_id if self.instance else "",
        )
        if not state_code or value[:2] != str(state_code):
            raise serializers.ValidationError(
                "The first two GSTIN digits must match the selected state code."
            )
        return value

    def validate_pan(self, value):
        value = value.strip().upper()
        if value and not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", value):
            raise serializers.ValidationError(
                "PAN must contain 5 letters, 4 digits, and 1 letter."
            )
        return value

    def validate(self, attrs):
        state = attrs.get(
            "state_code",
            self.instance.state_code if self.instance else None,
        )
        gstin = attrs.get("gstin", self.instance.gstin if self.instance else "")
        state_code = state.pk if isinstance(state, IndianState) else state
        if gstin and (not state_code or gstin[:2] != state_code):
            raise serializers.ValidationError(
                {
                    "gstin": (
                        "The first two GSTIN digits must match the selected state code."
                    )
                }
            )
        return attrs


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
