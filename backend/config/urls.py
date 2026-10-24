from django.urls import include, path
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
def health_check(request):
    return Response({"status": "ok"})


urlpatterns = [
    path("api/health/", health_check, name="health"),
    path("api/", include("accounts.urls")),
    path("api/", include("audit.urls")),
    path("api/", include("catalog.urls")),
    path("api/", include("inventory.urls")),
    path("api/", include("workorders.urls")),
]
