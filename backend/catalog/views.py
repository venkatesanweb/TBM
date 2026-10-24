from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import CharField, Count, Q
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Cast
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from accounts.models import User
from audit.services import record_audit
from catalog.models import BOMLine, Component, Product, ProductRevision
from catalog.permissions import SuperAdminWriteAdminReadOnly
from catalog.serializers import (
    BOMLineSerializer,
    ComponentQuickCreateSerializer,
    ComponentSerializer,
    ComponentUseSerializer,
    ProductRevisionSerializer,
    ProductSerializer,
    RevisionCreateSerializer,
)
from catalog.services import (
    bom_line_snapshot,
    component_snapshot,
    product_snapshot,
    revision_snapshot,
)


def search_term(queryset, term, *fields):
    if term:
        query = Q()
        for field in fields:
            query |= Q(**{f"{field}__icontains": term})
        queryset = queryset.filter(query)
    return queryset


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

    def retrieve(self, request, *args, **kwargs):
        component = self.get_object()
        serializer = self.get_serializer(component)
        return Response(
            {
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
        )

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
