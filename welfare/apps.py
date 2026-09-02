from django.apps import AppConfig
from django.db.models.signals import post_migrate


def ensure_welfare_manager_group(sender, **kwargs):
    """Crea il gruppo 'Welfare Managers' con il permesso di amministrazione."""
    from django.contrib.auth.models import Group, Permission

    try:
        permission = Permission.objects.get(
            codename="manage_welfare", content_type__app_label="welfare"
        )
    except Permission.DoesNotExist:  # pragma: no cover - solo durante migrazioni parziali
        return
    group, _ = Group.objects.get_or_create(name="Welfare Managers")
    group.permissions.add(permission)


class WelfareConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "welfare"
    verbose_name = "Welfare aziendale"

    def ready(self):
        post_migrate.connect(ensure_welfare_manager_group, sender=self)
