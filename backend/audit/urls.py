from django.urls import path

from audit.views import AuditLogListView, NotificationListView

urlpatterns = [
    path("audit-logs/", AuditLogListView.as_view(), name="audit-log-list"),
    path("notifications/", NotificationListView.as_view(), name="notification-list"),
]
