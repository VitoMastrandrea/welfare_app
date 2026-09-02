from .permissions import get_employee_profile, is_welfare_manager


def welfare_context(request):
    """Espone ai template il ruolo dell'utente e il suo profilo dipendente."""
    user = getattr(request, "user", None)
    return {
        "is_welfare_manager": is_welfare_manager(user),
        "employee_profile": get_employee_profile(user),
    }
