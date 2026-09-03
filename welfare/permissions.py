"""Controlli di autorizzazione, sempre applicati lato server."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .models import EmployeeProfile

MANAGE_PERM = "welfare.manage_welfare"
WELFARE_MANAGERS_GROUP = "Welfare Managers"


def is_welfare_manager(user) -> bool:
    """True se l'utente appartiene ai Welfare Manager (o è superuser).

    Non esiste alcun campo ``user_type``: un Welfare Manager può essere
    contemporaneamente un normale dipendente.
    """
    return bool(user and user.is_authenticated and user.has_perm(MANAGE_PERM))


def is_staff_member(user) -> bool:
    """True se l'utente ha privilegi di staff (gestione degli account)."""
    return bool(user and user.is_authenticated and user.is_staff)


def can_manage_user(actor, target) -> bool:
    """Chi può modificare l'account di un altro utente.

    Serve staff. Un utente staff che non è superuser non può però toccare
    l'account di un superuser: potrebbe cambiargli la password e prenderne
    il posto.
    """
    if not is_staff_member(actor):
        return False
    if target is not None and target.is_superuser and not actor.is_superuser:
        return False
    return True


def get_employee_profile(user) -> EmployeeProfile | None:
    if not user or not user.is_authenticated:
        return None
    return EmployeeProfile.objects.filter(user=user).select_related("user").first()


def require_employee_profile(user) -> EmployeeProfile:
    profile = get_employee_profile(user)
    if profile is None or not profile.active:
        raise PermissionDenied("Nessun profilo dipendente attivo associato a questo utente.")
    return profile


def can_access_request(user, request_obj) -> bool:
    """Un dipendente accede solo alle proprie richieste; i manager a tutte."""
    if is_welfare_manager(user):
        return True
    profile = get_employee_profile(user)
    return profile is not None and request_obj.allocation.employee_id == profile.pk


class EmployeeRequiredMixin(LoginRequiredMixin):
    """Richiede un utente autenticato con profilo dipendente attivo."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.employee = require_employee_profile(request.user)
        return super().dispatch(request, *args, **kwargs)


class WelfareManagerRequiredMixin(LoginRequiredMixin):
    """Richiede il permesso di amministrazione welfare."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_welfare_manager(request.user):
            raise PermissionDenied("Area riservata ai Welfare Manager.")
        return super().dispatch(request, *args, **kwargs)


def employee_required(view_func):
    """Decoratore: utente autenticato con profilo dipendente attivo."""
    from functools import wraps

    from django.contrib.auth.decorators import login_required

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        profile = require_employee_profile(request.user)
        request.employee_profile = profile
        return view_func(request, *args, **kwargs)

    return login_required(_wrapped)


def staff_required(view_func):
    """Decoratore: utente autenticato con privilegi di staff."""
    from functools import wraps

    from django.contrib.auth.decorators import login_required

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_staff_member(request.user):
            raise PermissionDenied(
                "La gestione degli utenti è riservata agli utenti con privilegi di staff."
            )
        return view_func(request, *args, **kwargs)

    return login_required(_wrapped)


def welfare_manager_required(view_func):
    """Decoratore: utente autenticato con permesso di amministrazione welfare."""
    from functools import wraps

    from django.contrib.auth.decorators import login_required

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not is_welfare_manager(request.user):
            raise PermissionDenied("Area riservata ai Welfare Manager.")
        return view_func(request, *args, **kwargs)

    return login_required(_wrapped)
