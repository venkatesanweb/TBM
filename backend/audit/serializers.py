from rest_framework import serializers

from audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(
        source="actor.username", read_only=True, allow_null=True
    )
    object_type = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "actor",
            "actor_username",
            "action",
            "object_type",
            "object_id",
            "before",
            "after",
            "timestamp",
        )

    def get_object_type(self, obj):
        if obj.content_type is None:
            return None
        return f"{obj.content_type.app_label}.{obj.content_type.model}"
