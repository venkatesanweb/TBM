from django.urls import include, path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.db import connection
from django.db.utils import OperationalError


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    try:
        connection.ensure_connection()
    except OperationalError:
        return Response({"status": "unavailable"}, status=503)
    return Response({"status": "ok"})


urlpatterns = [
    path("api/health/", health_check, name="health"),
    path("api/", include("accounts.urls")),
    path("api/", include("audit.urls")),
    path("api/", include("catalog.urls")),
    path("api/", include("inventory.urls")),
    path("api/", include("workorders.urls")),
    path("api/", include("sales.urls")),
    path("api/", include("backups.urls")),
]
