from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=100)
    content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True
    )
    object_id = models.CharField(max_length=255, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-timestamp", "-id")
        indexes = [
            models.Index(fields=("content_type", "object_id")),
            models.Index(fields=("actor", "timestamp")),
            models.Index(fields=("action", "timestamp")),
        ]

    def __str__(self):
        return f"{self.action} ({self.timestamp:%Y-%m-%d %H:%M:%S})"
