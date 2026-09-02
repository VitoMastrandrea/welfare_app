"""View server-rendered per area dipendente e area amministrazione welfare."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from . import services
from .forms import (
    AllocationForm,
    ConventionForm,
    DeliveryFromRequestForm,
    DirectDeliveryForm,
    EmployeeBudgetForm,
    RejectRequestForm,
    VoucherRequestForm,
    VoucherTypeForm,
    WelfareProgramForm,
)
from .models import (
    Convention,
    EmployeeProfile,
    RequestAttachment,
    VoucherAllocation,
    VoucherDelivery,
    VoucherRequest,
    VoucherType,
    WelfareProgram,
    ZERO,
)
from .permissions import (
    can_access_request,
    employee_required,
    get_employee_profile,
    is_welfare_manager,
    welfare_manager_required,
)


# ---------------------------------------------------------------------------
# Generale
# ---------------------------------------------------------------------------
def health(request):
    """Endpoint di health check per Railway."""
    from django.db import connection

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:  # pragma: no cover - dipende dall'infrastruttura
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def _current_program() -> WelfareProgram | None:
    return WelfareProgram.current()


def _add_form_errors(request, form_or_exc):
    for message in form_or_exc.messages:
        messages.error(request, message)


# ---------------------------------------------------------------------------
# Area dipendente
# ---------------------------------------------------------------------------
@login_required
def dashboard(request):
    profile = get_employee_profile(request.user)
    if profile is None or not profile.active:
        if is_welfare_manager(request.user):
            return redirect("admin_dashboard")
        return render(request, "welfare/no_profile.html", status=403)

    program = _current_program()
    context = {"program": program}
    if program is not None:
        summary = profile.welfare_summary(program)
        allocations = services.employee_allocations(profile, program)
        context.update(
            {
                "summary": summary,
                "allocations": allocations,
                "pending_count": VoucherRequest.objects.filter(
                    allocation__employee=profile,
                    allocation__welfare_program=program,
                    status=VoucherRequest.Status.PENDING,
                ).count(),
                "to_deliver_count": VoucherRequest.objects.filter(
                    allocation__employee=profile,
                    allocation__welfare_program=program,
                    status=VoucherRequest.Status.APPROVED,
                    delivery__isnull=True,
                ).count(),
                "recent_events": services.employee_timeline(profile, program)[:5],
            }
        )
    return render(request, "welfare/employee/dashboard.html", context)


@employee_required
def my_vouchers(request):
    profile = request.employee_profile
    program = _current_program()
    allocations = (
        services.employee_allocations(profile, program) if program else VoucherAllocation.objects.none()
    )
    return render(
        request,
        "welfare/employee/my_vouchers.html",
        {"program": program, "allocations": allocations},
    )


@employee_required
def catalog(request):
    """Catalogo delle convenzioni attive: informativo, non è un marketplace."""
    profile = request.employee_profile
    program = _current_program()
    conventions = (
        Convention.objects.filter(active=True)
        .prefetch_related(
            models.Prefetch(
                "voucher_types",
                queryset=VoucherType.objects.filter(active=True).order_by("name", "unit_value"),
            )
        )
        .order_by("name")
    )
    allocations = {}
    if program is not None:
        for allocation in services.employee_allocations(profile, program):
            allocations[allocation.voucher_type_id] = allocation
    return render(
        request,
        "welfare/employee/catalog.html",
        {"conventions": conventions, "allocations": allocations, "program": program},
    )


def _get_own_allocation(request, allocation_id: int) -> VoucherAllocation:
    return get_object_or_404(
        VoucherAllocation.objects.select_related(
            "voucher_type", "voucher_type__convention", "employee", "welfare_program"
        ),
        pk=allocation_id,
        employee=request.employee_profile,
    )


@employee_required
def request_voucher(request, allocation_id: int):
    allocation = _get_own_allocation(request, allocation_id)
    if not allocation.voucher_type.active or not allocation.voucher_type.convention.active:
        messages.error(request, "Questo voucher non è più richiedibile.")
        return redirect("my_vouchers")

    if request.method == "POST":
        form = VoucherRequestForm(request.POST, request.FILES, allocation=allocation)
        if form.is_valid():
            try:
                services.create_voucher_request(
                    allocation=allocation,
                    quantity=form.cleaned_data["quantity"],
                    actor=request.user,
                    files=form.cleaned_data.get("attachments"),
                )
            except ValidationError as exc:
                _add_form_errors(request, exc)
            else:
                messages.success(
                    request,
                    "Richiesta inviata correttamente: è ora in attesa di approvazione.",
                )
                return redirect("my_requests")
    else:
        form = VoucherRequestForm(allocation=allocation, initial={"quantity": 1})

    return render(
        request,
        "welfare/employee/request_form.html",
        {"allocation": allocation, "form": form},
    )


@employee_required
def request_summary_partial(request, allocation_id: int):
    """Frammento HTMX: valore economico calcolato lato server (Decimal)."""
    allocation = _get_own_allocation(request, allocation_id)
    raw = request.GET.get("quantity") or "0"
    try:
        quantity = int(Decimal(raw))
    except (InvalidOperation, ValueError):
        quantity = 0
    available = allocation.quantity_available
    return render(
        request,
        "welfare/partials/request_summary.html",
        {
            "allocation": allocation,
            "quantity": quantity,
            "available": available,
            "total_value": Decimal(max(quantity, 0)) * allocation.voucher_type.unit_value,
            "exceeds": quantity > available,
        },
    )


@employee_required
def my_requests(request):
    profile = request.employee_profile
    program = _current_program()
    events = services.employee_timeline(profile, program) if program else []
    return render(
        request,
        "welfare/employee/my_requests.html",
        {"events": events, "program": program},
    )


@login_required
def request_detail(request, pk: int):
    voucher_request = get_object_or_404(
        VoucherRequest.objects.select_related(
            "allocation__voucher_type__convention",
            "allocation__employee__user",
            "processed_by",
        ).prefetch_related("attachments"),
        pk=pk,
    )
    if not can_access_request(request.user, voucher_request):
        raise PermissionDenied("Non puoi accedere a questa richiesta.")
    return render(
        request,
        "welfare/employee/request_detail.html",
        {"voucher_request": voucher_request},
    )


@login_required
def attachment_download(request, pk: int):
    """Download autenticato: nessuna URL pubblica o permanente sul bucket privato."""
    attachment = get_object_or_404(
        RequestAttachment.objects.select_related(
            "request__allocation__employee", "request__allocation__voucher_type"
        ),
        pk=pk,
    )
    if not can_access_request(request.user, attachment.request):
        raise PermissionDenied("Non puoi accedere a questo allegato.")
    file_handle = attachment.file.open("rb")
    return FileResponse(
        file_handle, as_attachment=True, filename=attachment.original_filename
    )


# ---------------------------------------------------------------------------
# Area amministrazione welfare
# ---------------------------------------------------------------------------
@welfare_manager_required
def admin_dashboard(request):
    program = _current_program()
    base_requests = VoucherRequest.objects.all()
    if program is not None:
        base_requests = base_requests.filter(allocation__welfare_program=program)

    pending = base_requests.filter(status=VoucherRequest.Status.PENDING).count()
    to_deliver = base_requests.filter(
        status=VoucherRequest.Status.APPROVED, delivery__isnull=True
    ).count()
    employees = EmployeeProfile.objects.filter(active=True).count()

    latest_requests = (
        base_requests.select_related(
            "allocation__voucher_type__convention", "allocation__employee__user"
        )
        .order_by("-requested_at")[:8]
    )
    return render(
        request,
        "welfare/manage/dashboard.html",
        {
            "program": program,
            "pending_count": pending,
            "to_deliver_count": to_deliver,
            "employees_count": employees,
            "conventions_count": Convention.objects.filter(active=True).count(),
            "voucher_types_count": VoucherType.objects.filter(active=True).count(),
            "latest_requests": latest_requests,
        },
    )


@welfare_manager_required
def admin_employees(request):
    program = _current_program()
    query = (request.GET.get("q") or "").strip()
    employees = EmployeeProfile.objects.select_related("user").order_by(
        "user__last_name", "user__first_name", "user__username"
    )
    if query:
        employees = employees.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__username__icontains=query)
            | Q(user__email__icontains=query)
            | Q(employee_code__icontains=query)
        )

    rows = []
    if program is not None:
        for employee in employees:
            summary = employee.welfare_summary(program)
            rows.append({"employee": employee, "summary": summary})
    else:
        rows = [{"employee": employee, "summary": None} for employee in employees]

    return render(
        request,
        "welfare/manage/employees.html",
        {"rows": rows, "program": program, "q": query},
    )


def _employee_or_404(pk: int) -> EmployeeProfile:
    return get_object_or_404(EmployeeProfile.objects.select_related("user"), pk=pk)


@welfare_manager_required
def admin_employee_detail(request, pk: int):
    employee = _employee_or_404(pk)
    program = _current_program()
    context = {"employee": employee, "program": program}
    if program is not None:
        context.update(
            {
                "summary": employee.welfare_summary(program),
                "allocations": services.employee_allocations(employee, program),
                "events": services.employee_timeline(employee, program),
                "has_budget": employee.budget_for(program) is not None,
            }
        )
    return render(request, "welfare/manage/employee_detail.html", context)


@welfare_manager_required
def admin_employee_budget(request, pk: int):
    employee = _employee_or_404(pk)
    program = _current_program()
    if program is None:
        messages.error(request, "Nessun programma welfare attivo.")
        return redirect("admin_employees")

    budget = employee.budget_for(program)
    if request.method == "POST":
        form = EmployeeBudgetForm(request.POST, employee=employee, program=program)
        if form.is_valid():
            try:
                services.set_employee_budget(
                    employee=employee,
                    program=program,
                    amount=form.cleaned_data["amount"],
                    actor=request.user,
                )
            except ValidationError as exc:
                _add_form_errors(request, exc)
            else:
                messages.success(request, "Budget aggiornato.")
                return redirect("admin_employee_detail", pk=employee.pk)
    else:
        form = EmployeeBudgetForm(
            employee=employee,
            program=program,
            initial={"amount": budget.amount if budget else ZERO},
        )

    return render(
        request,
        "welfare/manage/budget_form.html",
        {
            "employee": employee,
            "program": program,
            "form": form,
            "budget": budget,
            "summary": employee.welfare_summary(program),
        },
    )


@welfare_manager_required
def admin_allocate(request, pk: int | None = None):
    """Assegnazione voucher; ``pk`` opzionale blocca il dipendente."""
    program = _current_program()
    if program is None:
        messages.error(request, "Nessun programma welfare attivo.")
        return redirect("admin_dashboard")

    employee = _employee_or_404(pk) if pk else None

    if request.method == "POST":
        form = AllocationForm(request.POST, program=program, employee=employee)
        if form.is_valid():
            target = employee or form.cleaned_data["employee"]
            try:
                services.set_allocation_quantity(
                    employee=target,
                    program=program,
                    voucher_type=form.cleaned_data["voucher_type"],
                    quantity=form.cleaned_data["quantity"],
                    actor=request.user,
                    mode="set",
                )
            except ValidationError as exc:
                _add_form_errors(request, exc)
            else:
                messages.success(request, f"Allocazione aggiornata per {target}.")
                return redirect("admin_employee_detail", pk=target.pk)
    else:
        form = AllocationForm(program=program, employee=employee)

    return render(
        request,
        "welfare/manage/allocation_form.html",
        {"form": form, "employee": employee, "program": program},
    )


@welfare_manager_required
def admin_allocation_summary_partial(request):
    """Frammento HTMX: valore unitario, valore allocazione, budget residuo."""
    program = _current_program()
    employee_id = request.GET.get("employee")
    voucher_type_id = request.GET.get("voucher_type")
    raw_quantity = request.GET.get("quantity") or "0"
    try:
        quantity = max(int(Decimal(raw_quantity)), 0)
    except (InvalidOperation, ValueError):
        quantity = 0

    employee = EmployeeProfile.objects.filter(pk=employee_id).select_related("user").first()
    voucher_type = VoucherType.objects.filter(pk=voucher_type_id).select_related("convention").first()

    context = {
        "employee": employee,
        "voucher_type": voucher_type,
        "quantity": quantity,
        "program": program,
    }
    if employee and program:
        budget = employee.budget_for(program)
        allocated = employee.allocated_value(program)
        current_allocation = (
            VoucherAllocation.objects.filter(
                employee=employee, welfare_program=program, voucher_type=voucher_type
            ).first()
            if voucher_type
            else None
        )
        previous_value = current_allocation.allocated_value if current_allocation else ZERO
        new_value = (Decimal(quantity) * voucher_type.unit_value) if voucher_type else ZERO
        budget_amount = budget.amount if budget else ZERO
        context.update(
            {
                "budget": budget,
                "budget_amount": budget_amount,
                "allocated": allocated,
                "current_allocation": current_allocation,
                "new_value": new_value,
                "allocatable": budget_amount - (allocated - previous_value),
                "projected_unallocated": budget_amount - (allocated - previous_value + new_value),
                "consumed": current_allocation.quantity_consumed if current_allocation else 0,
            }
        )
    return render(request, "welfare/partials/allocation_summary.html", context)


@welfare_manager_required
def admin_direct_delivery(request, pk: int):
    employee = _employee_or_404(pk)
    program = _current_program()
    if program is None:
        messages.error(request, "Nessun programma welfare attivo.")
        return redirect("admin_employees")

    if request.method == "POST":
        form = DirectDeliveryForm(request.POST, employee=employee, program=program)
        if form.is_valid():
            try:
                services.create_direct_delivery(
                    allocation=form.cleaned_data["allocation"],
                    quantity=form.cleaned_data["quantity"],
                    actor=request.user,
                    notes=form.cleaned_data.get("notes", ""),
                )
            except ValidationError as exc:
                _add_form_errors(request, exc)
            else:
                messages.success(request, "Consegna diretta registrata.")
                return redirect("admin_employee_detail", pk=employee.pk)
    else:
        form = DirectDeliveryForm(employee=employee, program=program, initial={"quantity": 1})

    return render(
        request,
        "welfare/manage/direct_delivery_form.html",
        {"form": form, "employee": employee, "program": program},
    )


@welfare_manager_required
def admin_delivery_summary_partial(request):
    allocation_id = request.GET.get("allocation")
    raw_quantity = request.GET.get("quantity") or "0"
    try:
        quantity = max(int(Decimal(raw_quantity)), 0)
    except (InvalidOperation, ValueError):
        quantity = 0
    allocation = (
        VoucherAllocation.objects.filter(pk=allocation_id)
        .select_related("voucher_type", "voucher_type__convention")
        .first()
    )
    available = allocation.quantity_available if allocation else 0
    return render(
        request,
        "welfare/partials/delivery_summary.html",
        {
            "allocation": allocation,
            "quantity": quantity,
            "available": available,
            "total_value": (Decimal(quantity) * allocation.voucher_type.unit_value)
            if allocation
            else ZERO,
            "exceeds": allocation is not None and quantity > available,
        },
    )


@welfare_manager_required
def admin_allocations(request):
    program = _current_program()
    allocations = (
        VoucherAllocation.objects.select_related(
            "employee__user", "voucher_type", "voucher_type__convention"
        )
        .with_counters()
        .order_by("employee__user__last_name", "voucher_type__convention__name")
    )
    if program is not None:
        allocations = allocations.filter(welfare_program=program)
    return render(
        request,
        "welfare/manage/allocations.html",
        {"allocations": allocations, "program": program},
    )


@welfare_manager_required
def admin_requests(request):
    status = (request.GET.get("stato") or "PENDING").upper()
    program = _current_program()
    queryset = VoucherRequest.objects.select_related(
        "allocation__employee__user",
        "allocation__voucher_type",
        "allocation__voucher_type__convention",
        "delivery",
    ).order_by("-requested_at")
    if program is not None:
        queryset = queryset.filter(allocation__welfare_program=program)

    if status == "TO_DELIVER":
        queryset = queryset.filter(
            status=VoucherRequest.Status.APPROVED, delivery__isnull=True
        )
    elif status == "DELIVERED":
        queryset = queryset.filter(delivery__isnull=False)
    elif status in VoucherRequest.Status.values:
        queryset = queryset.filter(status=status)
    elif status != "ALL":
        status = "ALL"

    scoped = VoucherRequest.objects.all()
    if program is not None:
        scoped = scoped.filter(allocation__welfare_program=program)
    counts = scoped.aggregate(
        pending=Count("pk", filter=Q(status=VoucherRequest.Status.PENDING)),
        approved=Count("pk", filter=Q(status=VoucherRequest.Status.APPROVED)),
        rejected=Count("pk", filter=Q(status=VoucherRequest.Status.REJECTED)),
        to_deliver=Count(
            "pk",
            filter=Q(status=VoucherRequest.Status.APPROVED, delivery__isnull=True),
        ),
    )
    return render(
        request,
        "welfare/manage/requests.html",
        {"requests": queryset, "status": status, "counts": counts, "program": program},
    )


@welfare_manager_required
def admin_request_detail(request, pk: int):
    voucher_request = get_object_or_404(
        VoucherRequest.objects.select_related(
            "allocation__employee__user",
            "allocation__voucher_type__convention",
            "processed_by",
        ).prefetch_related("attachments"),
        pk=pk,
    )
    return render(
        request,
        "welfare/manage/request_detail.html",
        {
            "voucher_request": voucher_request,
            "reject_form": RejectRequestForm(),
            "delivery_form": DeliveryFromRequestForm(),
            "allocation": voucher_request.allocation,
        },
    )


@welfare_manager_required
def admin_request_approve(request, pk: int):
    if request.method != "POST":
        return HttpResponse(status=405)
    voucher_request = get_object_or_404(VoucherRequest, pk=pk)
    try:
        services.approve_request(request=voucher_request, actor=request.user)
    except ValidationError as exc:
        _add_form_errors(request, exc)
    else:
        messages.success(request, f"Richiesta #{voucher_request.pk} approvata.")
    return redirect("admin_request_detail", pk=pk)


@welfare_manager_required
def admin_request_reject(request, pk: int):
    if request.method != "POST":
        return HttpResponse(status=405)
    voucher_request = get_object_or_404(VoucherRequest, pk=pk)
    form = RejectRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Inserisci una motivazione per il rifiuto.")
        return redirect("admin_request_detail", pk=pk)
    try:
        services.reject_request(
            request=voucher_request, actor=request.user, reason=form.cleaned_data["reason"]
        )
    except ValidationError as exc:
        _add_form_errors(request, exc)
    else:
        messages.success(request, f"Richiesta #{voucher_request.pk} rifiutata.")
    return redirect("admin_request_detail", pk=pk)


@welfare_manager_required
def admin_request_deliver(request, pk: int):
    if request.method != "POST":
        return HttpResponse(status=405)
    voucher_request = get_object_or_404(VoucherRequest, pk=pk)
    form = DeliveryFromRequestForm(request.POST)
    notes = form.cleaned_data.get("notes", "") if form.is_valid() else ""
    try:
        services.deliver_request(request=voucher_request, actor=request.user, notes=notes)
    except ValidationError as exc:
        _add_form_errors(request, exc)
    else:
        messages.success(request, f"Consegna registrata per la richiesta #{voucher_request.pk}.")
    return redirect("admin_request_detail", pk=pk)


@welfare_manager_required
def admin_deliveries(request):
    program = _current_program()
    deliveries = VoucherDelivery.objects.select_related(
        "allocation__employee__user",
        "allocation__voucher_type__convention",
        "delivered_by",
        "request",
    ).order_by("-delivered_at")
    if program is not None:
        deliveries = deliveries.filter(allocation__welfare_program=program)
    return render(
        request,
        "welfare/manage/deliveries.html",
        {"deliveries": deliveries, "program": program},
    )


# --- Convenzioni, tipi voucher, programmi ----------------------------------
@welfare_manager_required
def admin_conventions(request):
    conventions = Convention.objects.prefetch_related("voucher_types").order_by("name")
    return render(request, "welfare/manage/conventions.html", {"conventions": conventions})


@welfare_manager_required
def admin_convention_form(request, pk: int | None = None):
    instance = get_object_or_404(Convention, pk=pk) if pk else None
    if request.method == "POST":
        form = ConventionForm(request.POST, instance=instance)
        if form.is_valid():
            convention = form.save()
            messages.success(request, f"Convenzione «{convention.name}» salvata.")
            return redirect("admin_conventions")
    else:
        form = ConventionForm(instance=instance)
    return render(
        request,
        "welfare/manage/convention_form.html",
        {"form": form, "instance": instance},
    )


@welfare_manager_required
def admin_voucher_type_form(request, pk: int | None = None):
    instance = get_object_or_404(VoucherType, pk=pk) if pk else None
    if request.method == "POST":
        form = VoucherTypeForm(request.POST, instance=instance)
        if form.is_valid():
            try:
                voucher_type = form.save()
            except ValidationError as exc:
                _add_form_errors(request, exc)
            else:
                messages.success(request, f"Tipo voucher «{voucher_type}» salvato.")
                return redirect("admin_conventions")
    else:
        initial = {}
        convention_id = request.GET.get("convenzione")
        if convention_id:
            initial["convention"] = convention_id
        form = VoucherTypeForm(instance=instance, initial=initial)
    return render(
        request,
        "welfare/manage/voucher_type_form.html",
        {"form": form, "instance": instance},
    )


@welfare_manager_required
def admin_programs(request):
    programs = WelfareProgram.objects.all()
    return render(request, "welfare/manage/programs.html", {"programs": programs})


@welfare_manager_required
def admin_program_form(request, pk: int | None = None):
    instance = get_object_or_404(WelfareProgram, pk=pk) if pk else None
    if request.method == "POST":
        form = WelfareProgramForm(request.POST, instance=instance)
        if form.is_valid():
            program = form.save()
            messages.success(request, f"Programma «{program.name}» salvato.")
            return redirect("admin_programs")
    else:
        form = WelfareProgramForm(instance=instance)
    return render(
        request,
        "welfare/manage/program_form.html",
        {"form": form, "instance": instance},
    )
