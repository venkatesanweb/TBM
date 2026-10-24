from django.contrib.contenttypes.models import ContentType

from audit.models import AuditLog


def record_audit(*, actor, action, instance=None, before=None, after=None):
    content_type = None
    object_id = ""
    if instance is not None:
        content_type = ContentType.objects.get_for_model(instance, for_concrete_model=False)
        object_id = str(instance.pk)
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        content_type=content_type,
        object_id=object_id,
        before=before,
        after=after,
    )
