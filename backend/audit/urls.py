from django.urls import path

from audit.views import AuditLogListView

urlpatterns = [
    path("audit-logs/", AuditLogListView.as_view(), name="audit-log-list"),
]
