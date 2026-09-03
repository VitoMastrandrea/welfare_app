from .models import VoucherRequest, WelfareProgram
from .permissions import get_employee_profile, is_welfare_manager


def welfare_context(request):
    """Espone ai template il ruolo dell'utente, il suo profilo e le pratiche aperte."""
    user = getattr(request, "user", None)
    manager = is_welfare_manager(user)

    pending_requests_count = 0
    if manager:
        queryset = VoucherRequest.objects.filter(status=VoucherRequest.Status.PENDING)
        program = WelfareProgram.current()
        if program is not None:
            queryset = queryset.filter(allocation__welfare_program=program)
        pending_requests_count = queryset.count()

    return {
        "is_welfare_manager": manager,
        "employee_profile": get_employee_profile(user),
        "pending_requests_count": pending_requests_count,
    }
