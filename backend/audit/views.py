from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware
from rest_framework import generics
from rest_framework.exceptions import ValidationError

from accounts.permissions import IsSuperAdmin
from audit.models import AuditLog
from audit.serializers import AuditLogSerializer


class AuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        queryset = AuditLog.objects.select_related("actor", "content_type")
        params = self.request.query_params
        if params.get("actor"):
            try:
                actor_id = int(params["actor"])
            except ValueError as exc:
                raise ValidationError({"actor": "Provide a valid user ID."}) from exc
            queryset = queryset.filter(actor_id=actor_id)
        if params.get("action"):
            queryset = queryset.filter(action=params["action"])
        if params.get("object_type"):
            try:
                app_label, model = params["object_type"].split(".", maxsplit=1)
            except ValueError as exc:
                raise ValidationError(
                    {"object_type": "Use the format app_label.model."}
                ) from exc
            queryset = queryset.filter(
                content_type__app_label=app_label,
                content_type__model=model.lower(),
            )
        if params.get("object_id"):
            queryset = queryset.filter(object_id=params["object_id"])
        for param, lookup in (
            ("created_after", "timestamp__gte"),
            ("created_before", "timestamp__lte"),
        ):
            if params.get(param):
                value = parse_datetime(params[param])
                if value is None:
                    raise ValidationError({param: "Provide an ISO-8601 datetime."})
                if is_naive(value):
                    value = make_aware(value)
                queryset = queryset.filter(**{lookup: value})
        return queryset
