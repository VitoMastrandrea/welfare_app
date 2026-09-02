"""Business logic riutilizzabile per il welfare.

Tutte le operazioni che modificano budget, allocazioni, disponibilità o
consegne passano da qui: le view e i form non duplicano queste regole.
Ogni operazione che consuma disponibilità è eseguita in transazione con
lock di riga (``select_for_update``) per impedire doppi consumi.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    EmployeeBudget,
    EmployeeProfile,
    RequestAttachment,
    VoucherAllocation,
    VoucherDelivery,
    VoucherRequest,
    VoucherType,
    WelfareProgram,
    ZERO,
)


class WelfareError(ValidationError):
    """Errore di dominio welfare (sottoclasse di ValidationError)."""


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
def _lock_budget(employee: EmployeeProfile, program: WelfareProgram) -> EmployeeBudget | None:
    return (
        EmployeeBudget.objects.select_for_update()
        .filter(employee=employee, welfare_program=program)
        .first()
    )


def allocated_value(employee: EmployeeProfile, program: WelfareProgram) -> Decimal:
    return employee.allocated_value(program)


def budget_unallocated(employee: EmployeeProfile, program: WelfareProgram) -> Decimal:
    return employee.budget_amount(program) - employee.allocated_value(program)


@transaction.atomic
def set_employee_budget(
    *,
    employee: EmployeeProfile,
    program: WelfareProgram,
    amount: Decimal,
    actor,
) -> EmployeeBudget:
    """Crea o aggiorna il budget di un dipendente registrando l'attore."""
    amount = Decimal(amount)
    if amount < ZERO:
        raise WelfareError({"amount": "Il budget non può essere negativo."})

    budget = _lock_budget(employee, program)
    # Le allocazioni sono bloccate per evitare che vengano modificate in parallelo.
    list(
        VoucherAllocation.objects.select_for_update()
        .filter(employee=employee, welfare_program=program)
        .values_list("pk", flat=True)
    )
    already_allocated = employee.allocated_value(program)
    if amount < already_allocated:
        raise WelfareError(
            {
                "amount": (
                    f"Il budget non può essere inferiore al valore già allocato "
                    f"in voucher (€{already_allocated})."
                )
            }
        )

    if budget is None:
        budget = EmployeeBudget(
            employee=employee,
            welfare_program=program,
            amount=amount,
            created_by=actor,
            updated_by=actor,
        )
    else:
        budget.amount = amount
        budget.updated_by = actor
    budget.full_clean(exclude=["created_by", "updated_by"])
    budget.save()
    return budget


# ---------------------------------------------------------------------------
# Allocazioni
# ---------------------------------------------------------------------------
@transaction.atomic
def set_allocation_quantity(
    *,
    employee: EmployeeProfile,
    program: WelfareProgram,
    voucher_type: VoucherType,
    quantity: int,
    actor,
    mode: str = "set",
) -> VoucherAllocation:
    """Assegna (o modifica) la quantità di un tipo voucher per un dipendente.

    ``mode='set'`` imposta la quantità totale, ``mode='add'`` la incrementa.
    Verifica sempre, lato server:
      * il valore complessivo allocato non supera il budget;
      * la quantità non scende sotto quanto già richiesto/approvato/consegnato.
    """
    quantity = int(quantity)
    if quantity < 0:
        raise WelfareError({"quantity": "La quantità non può essere negativa."})

    budget = _lock_budget(employee, program)
    if budget is None:
        raise WelfareError(
            "Il dipendente non ha ancora un budget per questo programma welfare: "
            "impostalo prima di assegnare voucher."
        )

    allocation = (
        VoucherAllocation.objects.select_for_update()
        .filter(employee=employee, welfare_program=program, voucher_type=voucher_type)
        .first()
    )

    if allocation is None:
        if mode == "add" and quantity == 0:
            raise WelfareError({"quantity": "Indica una quantità maggiore di zero."})
        new_quantity = quantity
    else:
        new_quantity = allocation.quantity_assigned + quantity if mode == "add" else quantity

    if allocation is not None:
        consumed = allocation.quantity_consumed
        if new_quantity < consumed:
            raise WelfareError(
                {
                    "quantity": (
                        f"Non puoi scendere sotto {consumed}: sono voucher già richiesti, "
                        f"approvati o consegnati."
                    )
                }
            )

    current_allocated = employee.allocated_value(program)
    previous_value = allocation.allocated_value if allocation else ZERO
    new_value = Decimal(new_quantity) * voucher_type.unit_value
    projected = current_allocated - previous_value + new_value
    if projected > budget.amount:
        residual = budget.amount - (current_allocated - previous_value)
        raise WelfareError(
            {
                "quantity": (
                    f"Allocazione non consentita: supera il budget del dipendente. "
                    f"Valore richiesto €{new_value}, ancora allocabile €{residual}."
                )
            }
        )

    if allocation is None:
        allocation = VoucherAllocation(
            employee=employee,
            welfare_program=program,
            voucher_type=voucher_type,
            quantity_assigned=new_quantity,
            assigned_by=actor,
            updated_by=actor,
        )
    else:
        allocation.quantity_assigned = new_quantity
        allocation.updated_by = actor
    allocation.save()
    return allocation


# ---------------------------------------------------------------------------
# Richieste
# ---------------------------------------------------------------------------
@transaction.atomic
def create_voucher_request(
    *,
    allocation: VoucherAllocation,
    quantity: int,
    actor,
    files=None,
) -> VoucherRequest:
    """Crea una richiesta PENDING riservando immediatamente le quantità."""
    quantity = int(quantity)
    if quantity < 1:
        raise WelfareError({"quantity": "La quantità deve essere almeno 1."})

    locked = VoucherAllocation.objects.select_for_update().get(pk=allocation.pk)
    if not locked.voucher_type.active or not locked.voucher_type.convention.active:
        raise WelfareError("Questo voucher non è più richiedibile.")

    available = locked.quantity_available
    if quantity > available:
        raise WelfareError(
            {"quantity": f"Quantità non disponibile: puoi richiedere al massimo {available}."}
        )

    request = VoucherRequest.objects.create(
        allocation=locked, quantity=quantity, status=VoucherRequest.Status.PENDING
    )
    for uploaded in files or []:
        add_attachment(request=request, uploaded_file=uploaded, actor=actor)
    return request


def add_attachment(*, request: VoucherRequest, uploaded_file, actor) -> RequestAttachment:
    return RequestAttachment.objects.create(
        request=request,
        file=uploaded_file,
        original_filename=uploaded_file.name[:255],
        uploaded_by=actor,
    )


@transaction.atomic
def approve_request(*, request: VoucherRequest, actor) -> VoucherRequest:
    locked = VoucherRequest.objects.select_for_update().get(pk=request.pk)
    if not locked.is_pending:
        raise WelfareError("Solo le richieste in attesa possono essere approvate.")
    locked.status = VoucherRequest.Status.APPROVED
    locked.processed_at = timezone.now()
    locked.processed_by = actor
    locked.rejection_reason = ""
    locked.save(update_fields=["status", "processed_at", "processed_by", "rejection_reason"])
    return locked


@transaction.atomic
def reject_request(*, request: VoucherRequest, actor, reason: str = "") -> VoucherRequest:
    locked = VoucherRequest.objects.select_for_update().get(pk=request.pk)
    if not locked.is_pending:
        raise WelfareError("Solo le richieste in attesa possono essere rifiutate.")
    locked.status = VoucherRequest.Status.REJECTED
    locked.processed_at = timezone.now()
    locked.processed_by = actor
    locked.rejection_reason = (reason or "").strip()
    locked.save(update_fields=["status", "processed_at", "processed_by", "rejection_reason"])
    return locked


# ---------------------------------------------------------------------------
# Consegne
# ---------------------------------------------------------------------------
@transaction.atomic
def deliver_request(*, request: VoucherRequest, actor, notes: str = "") -> VoucherDelivery:
    """Registra la consegna di una richiesta APPROVED (sempre totale)."""
    locked = VoucherRequest.objects.select_for_update().get(pk=request.pk)
    # Il lock sull'allocazione serializza consegne dirette e consegne da richiesta.
    VoucherAllocation.objects.select_for_update().get(pk=locked.allocation_id)
    if not locked.is_approved:
        raise WelfareError("Solo le richieste approvate possono essere consegnate.")
    if VoucherDelivery.objects.filter(request=locked).exists():
        raise WelfareError("Questa richiesta è già stata consegnata.")

    return VoucherDelivery.objects.create(
        allocation_id=locked.allocation_id,
        request=locked,
        quantity=locked.quantity,
        delivered_by=actor,
        notes=(notes or "").strip(),
    )


@transaction.atomic
def create_direct_delivery(
    *,
    allocation: VoucherAllocation,
    quantity: int,
    actor,
    notes: str = "",
) -> VoucherDelivery:
    """Consegna amministrativa diretta, senza richiesta del dipendente."""
    quantity = int(quantity)
    if quantity < 1:
        raise WelfareError({"quantity": "La quantità deve essere almeno 1."})

    locked = VoucherAllocation.objects.select_for_update().get(pk=allocation.pk)
    available = locked.quantity_available
    if quantity > available:
        raise WelfareError(
            {"quantity": f"Quantità non disponibile: puoi consegnare al massimo {available}."}
        )

    return VoucherDelivery.objects.create(
        allocation=locked,
        request=None,
        quantity=quantity,
        delivered_by=actor,
        notes=(notes or "").strip(),
    )


# ---------------------------------------------------------------------------
# Query di supporto
# ---------------------------------------------------------------------------
def employee_allocations(employee: EmployeeProfile, program: WelfareProgram):
    return (
        VoucherAllocation.objects.for_employee(employee)
        .filter(welfare_program=program)
        .select_related("voucher_type", "voucher_type__convention")
        .with_counters()
    )


def employee_timeline(employee: EmployeeProfile, program: WelfareProgram) -> list[dict]:
    """Storico unificato: richieste del dipendente + consegne dirette."""
    requests = (
        VoucherRequest.objects.filter(
            allocation__employee=employee, allocation__welfare_program=program
        )
        .select_related(
            "allocation__voucher_type", "allocation__voucher_type__convention", "delivery"
        )
        .prefetch_related("attachments")
    )
    direct_deliveries = (
        VoucherDelivery.objects.filter(
            allocation__employee=employee,
            allocation__welfare_program=program,
            request__isnull=True,
        )
        .select_related("allocation__voucher_type", "allocation__voucher_type__convention")
    )

    events: list[dict] = []
    for req in requests:
        events.append(
            {
                "kind": "request",
                "date": req.requested_at,
                "voucher_type": req.allocation.voucher_type,
                "quantity": req.quantity,
                "value": req.total_value,
                "request": req,
                "delivery": req.delivery_obj,
            }
        )
    for delivery in direct_deliveries:
        events.append(
            {
                "kind": "direct_delivery",
                "date": delivery.delivered_at,
                "voucher_type": delivery.allocation.voucher_type,
                "quantity": delivery.quantity,
                "value": delivery.total_value,
                "request": None,
                "delivery": delivery,
            }
        )
    events.sort(key=lambda item: item["date"], reverse=True)
    return events
