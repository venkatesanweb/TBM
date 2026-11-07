from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import CharField, Count, Q
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsSuperAdmin
from accounts.models import User
from audit.services import record_audit
from catalog.barcodes import normalize_barcode
from catalog.models import (
    BOMLine,
    CompanyProfile,
    Component,
    IndianState,
    Product,
    ProductRevision,
    TaxRate,
)
from catalog.permissions import BarcodeLookupThrottle, SuperAdminWriteAdminReadOnly
from catalog.pricing import (
    component_price_preview,
    component_pricing_rows,
    product_costs_for_component,
    set_component_price,
)
from catalog.money import money_string
from catalog.serializers import (
    BOMLineSerializer,
    ComponentPriceBatchSerializer,
    ComponentQuickCreateSerializer,
    ComponentSerializer,
    ComponentUseSerializer,
    CompanyProfileSerializer,
    ProductRevisionSerializer,
    ProductSerializer,
    ProductPriceCreateSerializer,
    ProductPricePreviewSerializer,
    RevisionCreateSerializer,
    TaxRateSerializer,
)
from inventory.models import StockBalance
from inventory.permissions import InventoryReadWritePermission
from inventory.serializers import ScannedReceiveSerializer
from inventory.services import receive_scanned
from workorders.models import FinishedUnit
from catalog.services import (
    bom_line_snapshot,
    component_snapshot,
    product_snapshot,
    revision_snapshot,
)
from catalog.product_pricing import (
    append_product_price,
    can_view_product_prices,
    product_cost_breakdown,
    product_price_summary,
    product_pricing_rows,
)
from catalog.company import (
    company_profile_snapshot,
    get_company_profile,
)


def search_term(queryset, term, *fields):
    if term:
        query = Q()
        for field in fields:
            query |= Q(**{f"{field}__icontains": term})
        queryset = queryset.filter(query)
    return queryset


class BarcodeLookupView(APIView):
    permission_classes = [SuperAdminWriteAdminReadOnly]
    throttle_classes = [BarcodeLookupThrottle]

    def get(self, request):
        barcode = normalize_barcode(request.query_params.get("code", ""))
        if barcode is None:
            return Response(
                {"found": False, "type": "none", "matched_by": None}
            )

        component = Component.objects.filter(barcode=barcode).first()
        if component:
            return Response(self._component_response(request, component, "barcode"))

        product = Product.objects.select_related("current_revision").filter(
            barcode=barcode
        ).first()
        if product:
            return Response(self._product_response(request, product, "barcode"))

        component = Component.objects.filter(sku=barcode).first()
        if component:
            return Response(self._component_response(request, component, "sku"))

        product = Product.objects.select_related("current_revision").filter(
            product_number=barcode
        ).first()
        if product:
            return Response(self._product_response(request, product, "sku"))

        unit = FinishedUnit.objects.select_related(
            "product", "product_revision"
        ).filter(serial_number=barcode).first()
        if unit:
            return Response(
                {
                    "found": True,
                    "type": "finished_unit",
                    "matched_by": "serial",
                    "id": unit.pk,
                    "serial_number": unit.serial_number,
                    "status": unit.status,
                    "product_id": unit.product_id,
                    "product_number": unit.product.product_number,
                    "product_name": unit.product.name,
                    "revision_number": unit.product_revision.revision_number,
                }
            )

        return Response({"found": False, "type": "none", "matched_by": None})

    @staticmethod
    def _photo_url(request, photo):
        return request.build_absolute_uri(photo.url) if photo else None

    def _component_response(self, request, component, matched_by):
        balance = StockBalance.objects.filter(component=component).first()
        on_hand = balance.on_hand if balance else Decimal("0")
        reserved = balance.reserved if balance else Decimal("0")
        available = balance.available if balance else Decimal("0")
        return {
            "found": True,
            "type": "component",
            "matched_by": matched_by,
            "id": component.pk,
            "name": component.name,
            "sku": component.sku,
            "description": component.description,
            "category": component.category,
            "unit": component.unit,
            "revision_number": component.revision_number,
            "reorder_level": str(component.reorder_level),
            "photo": self._photo_url(request, component.photo),
            "active": component.active,
            "stock": {
                "on_hand": str(on_hand),
                "reserved": str(reserved),
                "available": str(available),
                "unit": component.unit,
            },
        }

    def _product_response(self, request, product, matched_by):
        revision = product.current_revision
        bom = (
            [
                {
                    "component_id": line.component_id,
                    "sku": line.component.sku,
                    "name": line.component.name,
                    "quantity_per_unit": str(line.quantity_per_unit),
                    "unit": line.component.unit,
                }
                for line in revision.bom_lines.select_related("component").all()
            ]
            if revision
            else []
        )
        return {
            "found": True,
            "type": "product",
            "matched_by": matched_by,
            "id": product.pk,
            "name": product.name,
            "product_number": product.product_number,
            "sku": product.sku,
            "description": product.description,
            "current_revision": (
                {
                    "id": revision.pk,
                    "revision_number": revision.revision_number,
                }
                if revision
                else None
            ),
            "bom": bom,
            "photo": self._photo_url(request, product.photo),
            "active": product.active,
            "finished_goods": {
                "in_stock": FinishedUnit.objects.filter(
                    product=product, status=FinishedUnit.Status.IN_STOCK
                ).count()
            },
        }


class ScannedComponentReceiptView(APIView):
    permission_classes = [InventoryReadWritePermission]

    def post(self, request):
        serializer = ScannedReceiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = receive_scanned(
            items=[
                {
                    "component_id": item["component_id"].pk,
                    "quantity": item["quantity"],
                }
                for item in serializer.validated_data["items"]
            ],
            idempotency_key=serializer.validated_data["idempotency_key"],
            note=serializer.validated_data.get("note", ""),
            actor=request.user,
        )
        return Response({"items": result})


class ProductListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]
    queryset = Product.objects.select_related("current_revision").prefetch_related(
        "revisions"
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        if params.get("search"):
            queryset = queryset.annotate(
                revision_number_text=Cast(
                    "revisions__revision_number", output_field=CharField()
                )
            )
        queryset = search_term(
            queryset,
            params.get("search"),
            "product_number",
            "sku",
            "name",
            "revision_number_text",
        )
        if params.get("name"):
            queryset = queryset.filter(name__icontains=params["name"])
        if params.get("product_number"):
            queryset = queryset.filter(
                product_number__icontains=params["product_number"]
            )
        if params.get("sku"):
            queryset = queryset.filter(sku__icontains=params["sku"])
        if params.get("revision_number"):
            try:
                revision_number = int(params["revision_number"])
            except ValueError as exc:
                raise ValidationError(
                    {"revision_number": "Provide a whole revision number."}
                ) from exc
            queryset = queryset.filter(revisions__revision_number=revision_number)
        if params.get("active") in {"true", "false"}:
            queryset = queryset.filter(active=params["active"] == "true")
        return queryset.distinct()

    @transaction.atomic
    def perform_create(self, serializer):
        product = serializer.save()
        revision = product.current_revision
        record_audit(
            actor=self.request.user,
            action="catalog.product.created",
            instance=product,
            after=product_snapshot(product),
        )
        record_audit(
            actor=self.request.user,
            action="catalog.revision.created",
            instance=revision,
            after=revision_snapshot(revision),
        )


class ProductDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]
    queryset = Product.objects.select_related("current_revision").prefetch_related(
        "revisions"
    )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["include_product_price_history"] = (
            self.request.user.role == User.Role.SUPER_ADMIN
        )
        return context

    @transaction.atomic
    def perform_update(self, serializer):
        product = serializer.instance
        before = product_snapshot(product)
        product = serializer.save()
        record_audit(
            actor=self.request.user,
            action="catalog.product.updated",
            instance=product,
            before=before,
            after=product_snapshot(product),
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        product = self.get_object()
        product = Product.objects.select_for_update().get(pk=product.pk)
        if product.price_history.exists():
            raise ValidationError(
                "This product has price history and cannot be deleted."
            )
        if product.revisions.filter(is_frozen=True).exists():
            raise ValidationError(
                "This product has revisions used by work orders and cannot be deleted."
            )
        snapshot = product_snapshot(product)
        record_audit(
            actor=request.user,
            action="catalog.product.deleted",
            instance=product,
            before=snapshot,
        )
        product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProductPricingView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response({"products": product_pricing_rows()})


class ProductPriceCreateView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, product_id):
        serializer = ProductPriceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            price = append_product_price(
                product_id=product_id,
                actor=request.user,
                **serializer.validated_data,
            )
        except Product.DoesNotExist as exc:
            from rest_framework.exceptions import NotFound

            raise NotFound("Product not found.") from exc
        except TaxRate.DoesNotExist as exc:
            raise ValidationError(
                {"tax_rate_id": "Choose an active tax rate."}
            ) from exc
        except ValueError as exc:
            raise ValidationError({"price": str(exc)}) from exc
        return Response(
            product_price_summary(price.product, include_history=True),
            status=status.HTTP_201_CREATED,
        )


class ProductPricePreviewView(APIView):
    def get(self, request, product_id):
        if not can_view_product_prices(request.user):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "A Sales overview, Payments, or Returns section is required."
            )
        serializer = ProductPricePreviewSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        product = get_object_or_404(
            Product.objects.select_related("current_revision").prefetch_related(
                "current_revision__bom_lines__component"
            ),
            pk=product_id,
        )
        tax_rate = get_object_or_404(
            TaxRate,
            pk=serializer.validated_data["tax_rate_id"],
            active=True,
        )
        return Response(
            product_cost_breakdown(
                product,
                margin_percent=serializer.validated_data["margin_percent"],
                gst_rate_percent=tax_rate.rate_percent,
            )
        )


class TaxRateListCreateView(generics.ListCreateAPIView):
    serializer_class = TaxRateSerializer
    permission_classes = [IsSuperAdmin]
    queryset = TaxRate.objects.all()

    @transaction.atomic
    def perform_create(self, serializer):
        tax_rate = serializer.save()
        record_audit(
            actor=self.request.user,
            action="catalog.tax_rate.created",
            instance=tax_rate,
            after={
                "rate_percent": money_string(tax_rate.rate_percent),
                "label": tax_rate.label,
                "active": tax_rate.active,
            },
        )


class TaxRateDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaxRateSerializer
    permission_classes = [IsSuperAdmin]
    queryset = TaxRate.objects.all()

    @transaction.atomic
    def perform_update(self, serializer):
        tax_rate = TaxRate.objects.select_for_update().get(
            pk=serializer.instance.pk
        )
        before = {
            "rate_percent": money_string(tax_rate.rate_percent),
            "label": tax_rate.label,
            "active": tax_rate.active,
        }
        serializer.instance = tax_rate
        tax_rate = serializer.save()
        record_audit(
            actor=self.request.user,
            action="catalog.tax_rate.updated",
            instance=tax_rate,
            before=before,
            after={
                "rate_percent": money_string(tax_rate.rate_percent),
                "label": tax_rate.label,
                "active": tax_rate.active,
            },
        )

    @transaction.atomic
    def perform_destroy(self, instance):
        before = {
            "rate_percent": money_string(instance.rate_percent),
            "label": instance.label,
            "active": instance.active,
        }
        record_audit(
            actor=self.request.user,
            action="catalog.tax_rate.deleted",
            instance=instance,
            before=before,
        )
        instance.delete()


class CompanyProfileView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        profile = get_company_profile()
        return Response(
            {
                "profile": CompanyProfileSerializer(
                    profile, context={"request": request}
                ).data,
                "states": list(
                    IndianState.objects.values("state_code", "name").order_by(
                        "state_code"
                    )
                ),
            }
        )

    @transaction.atomic
    def patch(self, request):
        profile = CompanyProfile.objects.select_for_update().get_or_create(pk=1)[0]
        before = company_profile_snapshot(profile)
        serializer = CompanyProfileSerializer(
            profile,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        profile = serializer.save()
        after = company_profile_snapshot(profile)
        if before != after:
            record_audit(
                actor=request.user,
                action="company.profile.updated",
                instance=profile,
                before=before,
                after=after,
            )
        return Response(
            {
                "profile": CompanyProfileSerializer(
                    profile, context={"request": request}
                ).data,
                "states": list(
                    IndianState.objects.values("state_code", "name").order_by(
                        "state_code"
                    )
                ),
            }
        )


class ProductRevisionListCreateView(generics.ListCreateAPIView):
    serializer_class = ProductRevisionSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]

    def get_product(self):
        return get_object_or_404(Product, pk=self.kwargs["product_id"])

    def get_queryset(self):
        queryset = ProductRevision.objects.select_related("product").filter(
            product_id=self.kwargs["product_id"]
        )
        params = self.request.query_params
        if params.get("revision_number"):
            try:
                number = int(params["revision_number"])
            except ValueError as exc:
                raise ValidationError(
                    {"revision_number": "Provide a whole revision number."}
                ) from exc
            queryset = queryset.filter(revision_number=number)
        return queryset

    def get_serializer_class(self):
        if self.request.method == "POST":
            return RevisionCreateSerializer
        return super().get_serializer_class()

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.request.method == "POST":
            context["product"] = self.get_product()
        return context

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        product = Product.objects.select_for_update().get(pk=self.kwargs["product_id"])
        serializer = self.get_serializer(data=request.data, context={
            **self.get_serializer_context(),
            "product": product,
        })
        serializer.is_valid(raise_exception=True)
        revision = serializer.save()
        record_audit(
            actor=request.user,
            action="catalog.revision.created",
            instance=revision,
            after={
                **revision_snapshot(revision),
                "copied_bom": True,
                "bom_line_count": revision.bom_lines.count(),
            },
        )
        return Response(
            ProductRevisionSerializer(revision, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class ProductRevisionDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ProductRevisionSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]
    queryset = ProductRevision.objects.select_related("product")
    http_method_names = ["get", "patch", "delete", "head", "options"]

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        revision = self.get_object()
        revision = ProductRevision.objects.select_for_update().get(pk=revision.pk)
        if revision.is_frozen:
            raise ValidationError("A product revision used by a work order is read-only.")
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def perform_update(self, serializer):
        revision = serializer.instance
        before = revision_snapshot(revision)
        revision = serializer.save()
        record_audit(
            actor=self.request.user,
            action="catalog.revision.updated",
            instance=revision,
            before=before,
            after=revision_snapshot(revision),
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        revision = self.get_object()
        revision = ProductRevision.objects.select_for_update().get(pk=revision.pk)
        if revision.is_frozen:
            raise ValidationError("A product revision used by a work order cannot be deleted.")
        if revision.is_current:
            raise ValidationError("The current product revision cannot be deleted.")
        snapshot = revision_snapshot(revision)
        record_audit(
            actor=request.user,
            action="catalog.revision.deleted",
            instance=revision,
            before=snapshot,
        )
        revision.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ComponentListCreateView(generics.ListCreateAPIView):
    serializer_class = ComponentSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]
    queryset = Component.objects.annotate(
        used_in_count=Count(
            "bom_lines__product_revision__product",
            distinct=True,
        )
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        queryset = search_term(
            queryset,
            params.get("search"),
            "sku",
            "name",
            "revision_number",
        )
        for param, field in (("sku", "sku"), ("name", "name")):
            if params.get(param):
                queryset = queryset.filter(**{f"{field}__icontains": params[param]})
        if params.get("category"):
            queryset = queryset.filter(category__icontains=params["category"])
        if params.get("revision"):
            queryset = queryset.filter(revision_number__icontains=params["revision"])
        if params.get("active") in {"true", "false"}:
            queryset = queryset.filter(active=params["active"] == "true")
        return queryset

    def get_serializer_class(self):
        if self.request.path.endswith("/quick-create/"):
            return ComponentQuickCreateSerializer
        return super().get_serializer_class()

    @transaction.atomic
    def perform_create(self, serializer):
        component = serializer.save()
        record_audit(
            actor=self.request.user,
            action="catalog.component.created",
            instance=component,
            after=component_snapshot(component),
        )


class ComponentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ComponentSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]
    queryset = Component.objects.annotate(
        used_in_count=Count(
            "bom_lines__product_revision__product",
            distinct=True,
        )
    )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["include_used_in"] = True
        return context

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        component = self.get_object()
        Component.objects.select_for_update().get(pk=component.pk)
        return super().update(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        component = self.get_object()
        serializer = self.get_serializer(component)
        response_data = {
            **serializer.data,
            "used_in": ComponentUseSerializer(
                component.bom_lines.select_related(
                    "product_revision__product"
                ).order_by(
                    "product_revision__product__product_number",
                    "product_revision__revision_number",
                ),
                many=True,
            ).data,
        }
        if request.user.role == User.Role.SUPER_ADMIN:
            response_data["product_costs"] = product_costs_for_component(component)
        return Response(response_data)

    @transaction.atomic
    def perform_update(self, serializer):
        component = serializer.instance
        before = component_snapshot(component)
        component = serializer.save()
        record_audit(
            actor=self.request.user,
            action="catalog.component.updated",
            instance=component,
            before=before,
            after=component_snapshot(component),
        )

    @transaction.atomic
    def perform_destroy(self, instance):
        instance = Component.objects.select_for_update().get(pk=instance.pk)
        if instance.bom_lines.exists():
            raise ValidationError(
                "This component is used in a BOM and cannot be deleted. Deactivate it instead."
            )
        snapshot = component_snapshot(instance)
        record_audit(
            actor=self.request.user,
            action="catalog.component.deleted",
            instance=instance,
            before=snapshot,
        )
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ValidationError(
                "This component is used in a BOM. Deactivate it instead."
            ) from exc


class ComponentPricingView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        price_not_set = request.query_params.get("price_not_set")
        if price_not_set not in (None, "true", "false"):
            raise ValidationError(
                {"price_not_set": "Use true or false."}
            )
        return Response(
            component_pricing_rows(price_not_set=price_not_set == "true")
        )

    @transaction.atomic
    def post(self, request):
        serializer = ComponentPriceBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["items"]
        component_ids = sorted(item["component_id"] for item in items)
        components = list(
            Component.objects.select_for_update()
            .filter(pk__in=component_ids)
            .order_by("pk")
        )
        components_by_id = {component.pk: component for component in components}
        missing_ids = sorted(set(component_ids) - set(components_by_id))
        if missing_ids:
            raise ValidationError(
                {"items": f"Unknown component IDs: {', '.join(map(str, missing_ids))}."}
            )

        errors = {}
        price_changes = {}
        reasons = {}
        for index, item in enumerate(items):
            component = components_by_id[item["component_id"]]
            new_price = item["cost_price"]
            reason = item.get("reason", "").strip()
            if (
                component.cost_price is not None
                and component.cost_price != new_price
                and not reason
            ):
                errors[index] = {
                    "reason": "Provide a reason when changing an existing price."
                }
            price_changes[component.pk] = new_price
            reasons[component.pk] = reason
        if errors:
            raise ValidationError({"items": errors})

        impacts = component_price_preview(price_changes)
        updated_count = 0
        for component in components:
            history = set_component_price(
                component=component,
                new_price=price_changes[component.pk],
                actor=request.user,
                reason=reasons[component.pk],
            )
            if history is not None:
                updated_count += 1
        return Response(
            {
                "updated_count": updated_count,
                "items": [
                    {
                        "component_id": component.pk,
                        "cost_price": money_string(component.cost_price),
                    }
                    for component in components
                ],
                "product_impacts": impacts,
            }
        )


class ComponentPricePreviewView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        serializer = ComponentPriceBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["items"]
        component_ids = [item["component_id"] for item in items]
        if len(items) != 1:
            raise ValidationError(
                {"items": "Preview one component price at a time."}
            )
        component = get_object_or_404(Component, pk=component_ids[0])
        price = items[0]["cost_price"]
        return Response(
            {
                "component_id": component.pk,
                "products": component_price_preview({component.pk: price}),
            }
        )


class ComponentQuickCreateView(ComponentListCreateView):
    http_method_names = ["post", "options"]


class BOMLineListCreateView(generics.ListCreateAPIView):
    serializer_class = BOMLineSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]

    def get_revision(self, *, lock=False):
        queryset = ProductRevision.objects
        if lock:
            queryset = queryset.select_for_update()
        return get_object_or_404(
            queryset.select_related("product"), pk=self.kwargs["revision_id"]
        )

    def get_queryset(self):
        revision = self.get_revision()
        queryset = BOMLine.objects.select_related(
            "component", "product_revision", "product_revision__product"
        ).filter(product_revision=revision)
        params = self.request.query_params
        if params.get("sku"):
            queryset = queryset.filter(component__sku__icontains=params["sku"])
        if params.get("component_name"):
            queryset = queryset.filter(
                component__name__icontains=params["component_name"]
            )
        if params.get("product_number"):
            queryset = queryset.filter(
                product_revision__product__product_number__icontains=params["product_number"]
            )
        if params.get("revision_number"):
            try:
                number = int(params["revision_number"])
            except ValueError as exc:
                raise ValidationError(
                    {"revision_number": "Provide a whole revision number."}
                ) from exc
            queryset = queryset.filter(product_revision__revision_number=number)
        units = self.request.query_params.get("units", "1")
        try:
            units_decimal = Decimal(units)
        except InvalidOperation as exc:
            raise ValidationError({"units": "Provide a positive number of units."}) from exc
        if (
            not units_decimal.is_finite()
            or units_decimal <= 0
            or units_decimal > 1_000_000
            or units_decimal != units_decimal.to_integral_value()
        ):
            raise ValidationError(
                {"units": "Units must be a whole number from 1 to 1,000,000."}
            )
        self.units = units_decimal
        return queryset

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["revision"] = self.get_revision()
        try:
            context["units"] = Decimal(self.request.query_params.get("units", "1"))
        except InvalidOperation:
            context["units"] = Decimal(1)
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "product_revision": ProductRevisionSerializer(
                    self.get_revision(), context=self.get_serializer_context()
                ).data,
                "units": str(self.units),
                "results": serializer.data,
            }
        )

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        revision = self.get_revision(lock=True)
        serializer = self.get_serializer(
            data=request.data,
            context={**self.get_serializer_context(), "revision": revision},
        )
        serializer.is_valid(raise_exception=True)
        line = serializer.save()
        record_audit(
            actor=request.user,
            action="catalog.bom_line.created",
            instance=line,
            after=bom_line_snapshot(line),
        )
        return Response(
            BOMLineSerializer(line, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class BOMLineDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = BOMLineSerializer
    permission_classes = [SuperAdminWriteAdminReadOnly]
    queryset = BOMLine.objects.select_related(
        "component", "product_revision", "product_revision__product"
    )
    http_method_names = ["get", "patch", "delete", "head", "options"]

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        line = self.get_object()
        revision = ProductRevision.objects.select_for_update().get(
            pk=line.product_revision_id
        )
        if revision.is_frozen:
            raise ValidationError("The BOM for a revision used by a work order is read-only.")
        return super().update(request, *args, **kwargs)

    @transaction.atomic
    def perform_update(self, serializer):
        line = serializer.instance
        before = bom_line_snapshot(line)
        line = serializer.save()
        record_audit(
            actor=self.request.user,
            action="catalog.bom_line.updated",
            instance=line,
            before=before,
            after=bom_line_snapshot(line),
        )

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        line = self.get_object()
        revision = ProductRevision.objects.select_for_update().get(
            pk=line.product_revision_id
        )
        if revision.is_frozen:
            raise ValidationError("The BOM for a revision used by a work order is read-only.")
        if revision.bom_lines.count() <= 1:
            raise ValidationError("A product revision must retain at least one BOM line.")
        snapshot = bom_line_snapshot(line)
        record_audit(
            actor=request.user,
            action="catalog.bom_line.deleted",
            instance=line,
            before=snapshot,
        )
        line.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
