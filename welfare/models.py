"""Modelli di dominio per la gestione del welfare aziendale."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Coalesce as CoalesceFn
from django.utils import timezone

ZERO = Decimal("0.00")


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("creato il", auto_now_add=True)
    updated_at = models.DateTimeField("aggiornato il", auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Anagrafiche
# ---------------------------------------------------------------------------
class EmployeeProfile(TimeStampedModel):
    """Profilo welfare di un dipendente.

    Volutamente non contiene alcun dato usato per calcolare il budget
    (composizione familiare, figli a carico, ...): quel calcolo è fuori scope.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_profile",
        verbose_name="utente",
    )
    employee_code = models.CharField(
        "matricola", max_length=50, blank=True, null=True, unique=True
    )
    active = models.BooleanField("attivo", default=True)

    class Meta:
        verbose_name = "dipendente"
        verbose_name_plural = "dipendenti"
        ordering = ["user__last_name", "user__first_name", "user__username"]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        full_name = self.user.get_full_name().strip()
        return full_name or self.user.get_username()

    def budget_for(self, program: "WelfareProgram") -> "EmployeeBudget | None":
        return self.budgets.filter(welfare_program=program).first()

    def budget_amount(self, program: "WelfareProgram") -> Decimal:
        budget = self.budget_for(program)
        return budget.amount if budget else ZERO

    def allocated_value(self, program: "WelfareProgram") -> Decimal:
        """Valore economico complessivo dei voucher allocati al dipendente."""
        total = self.allocations.filter(welfare_program=program).aggregate(
            total=CoalesceFn(
                models.Sum(
                    models.F("quantity_assigned") * models.F("voucher_type__unit_value"),
                    output_field=models.DecimalField(max_digits=14, decimal_places=2),
                ),
                models.Value(ZERO, output_field=models.DecimalField(max_digits=14, decimal_places=2)),
            )
        )["total"]
        return total or ZERO

    def delivered_value(self, program: "WelfareProgram") -> Decimal:
        total = VoucherDelivery.objects.filter(
            allocation__employee=self, allocation__welfare_program=program
        ).aggregate(
            total=CoalesceFn(
                models.Sum(
                    models.F("quantity") * models.F("allocation__voucher_type__unit_value"),
                    output_field=models.DecimalField(max_digits=14, decimal_places=2),
                ),
                models.Value(ZERO, output_field=models.DecimalField(max_digits=14, decimal_places=2)),
            )
        )["total"]
        return total or ZERO

    def welfare_summary(self, program: "WelfareProgram") -> dict:
        assigned = self.budget_amount(program)
        allocated = self.allocated_value(program)
        return {
            "program": program,
            "budget_assigned": assigned,
            "budget_allocated": allocated,
            "budget_unallocated": assigned - allocated,
            "delivered_value": self.delivered_value(program),
        }


class WelfareProgram(TimeStampedModel):
    """Iniziativa welfare (una tantum). Nessuna logica annuale o di rinnovo."""

    name = models.CharField("nome", max_length=150, unique=True)
    description = models.TextField("descrizione", blank=True)
    start_date = models.DateField("data inizio", null=True, blank=True)
    end_date = models.DateField("data fine", null=True, blank=True)
    active = models.BooleanField("attivo", default=True)

    class Meta:
        verbose_name = "programma welfare"
        verbose_name_plural = "programmi welfare"
        ordering = ["-active", "name"]
        permissions = [
            ("manage_welfare", "Può amministrare il welfare aziendale"),
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError(
                {"end_date": "La data di fine non può precedere la data di inizio."}
            )

    @classmethod
    def current(cls) -> "WelfareProgram | None":
        """Programma welfare attivo di riferimento."""
        return cls.objects.filter(active=True).order_by("-start_date", "-created_at").first()


class Convention(TimeStampedModel):
    """Soggetto esterno convenzionato con l'azienda."""

    name = models.CharField("nome", max_length=150, unique=True)
    description = models.TextField("descrizione", blank=True)
    active = models.BooleanField("attiva", default=True)

    class Meta:
        verbose_name = "convenzione"
        verbose_name_plural = "convenzioni"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class VoucherType(TimeStampedModel):
    """Tipo di voucher offerto da una convenzione, con il suo valore unitario."""

    convention = models.ForeignKey(
        Convention,
        on_delete=models.PROTECT,
        related_name="voucher_types",
        verbose_name="convenzione",
    )
    name = models.CharField("nome", max_length=150)
    description = models.TextField("descrizione", blank=True)
    unit_value = models.DecimalField(
        "valore unitario",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    active = models.BooleanField("attivo", default=True)

    class Meta:
        verbose_name = "tipo voucher"
        verbose_name_plural = "tipi voucher"
        ordering = ["convention__name", "name", "unit_value"]
        constraints = [
            models.UniqueConstraint(
                fields=["convention", "name", "unit_value"],
                name="uniq_vouchertype_convention_name_value",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_value__gt=0),
                name="vouchertype_unit_value_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.convention.name} — {self.name} €{self.unit_value}"

    @property
    def label(self) -> str:
        return f"{self.name} €{self.unit_value}"

    @property
    def is_used(self) -> bool:
        """True se il tipo voucher è già stato usato in un'allocazione."""
        if not self.pk:
            return False
        return self.allocations.exists()

    def clean(self):
        super().clean()
        self._check_unit_value_immutability()

    def _check_unit_value_immutability(self):
        if not self.pk:
            return
        previous = (
            VoucherType.objects.filter(pk=self.pk).values_list("unit_value", flat=True).first()
        )
        if previous is None:
            return
        if Decimal(previous) != Decimal(self.unit_value) and self.is_used:
            raise ValidationError(
                {
                    "unit_value": (
                        "Il valore unitario non può essere modificato: questo tipo voucher "
                        "è già stato utilizzato in un'allocazione. Crea un nuovo tipo voucher."
                    )
                }
            )

    def save(self, *args, **kwargs):
        # La regola vale anche per salvataggi che non passano da un form
        # (Django admin, shell, management command).
        self._check_unit_value_immutability()
        return super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Budget e allocazioni
# ---------------------------------------------------------------------------
class EmployeeBudget(TimeStampedModel):
    """Budget welfare assegnato a un dipendente per un programma."""

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name="budgets",
        verbose_name="dipendente",
    )
    welfare_program = models.ForeignKey(
        WelfareProgram,
        on_delete=models.PROTECT,
        related_name="budgets",
        verbose_name="programma welfare",
    )
    amount = models.DecimalField(
        "importo",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(ZERO)],
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="budgets_created",
        verbose_name="creato da",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="budgets_updated",
        verbose_name="aggiornato da",
    )

    class Meta:
        verbose_name = "budget dipendente"
        verbose_name_plural = "budget dipendenti"
        ordering = ["employee__user__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "welfare_program"], name="uniq_budget_employee_program"
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="budget_amount_not_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee} — {self.welfare_program}: €{self.amount}"


class VoucherAllocationQuerySet(models.QuerySet):
    def _request_sum(self, **filters):
        subquery = (
            VoucherRequest.objects.filter(allocation=models.OuterRef("pk"), **filters)
            .order_by()
            .values("allocation")
            .annotate(total=models.Sum("quantity"))
            .values("total")[:1]
        )
        return CoalesceFn(models.Subquery(subquery, output_field=models.IntegerField()), 0)

    def with_counters(self):
        """Annota i contatori quantità evitando join moltiplicativi."""
        delivered = (
            VoucherDelivery.objects.filter(allocation=models.OuterRef("pk"))
            .order_by()
            .values("allocation")
            .annotate(total=models.Sum("quantity"))
            .values("total")[:1]
        )
        qs = self.annotate(
            pending_qty=self._request_sum(status=VoucherRequest.Status.PENDING),
            approved_waiting_qty=self._request_sum(
                status=VoucherRequest.Status.APPROVED, delivery__isnull=True
            ),
            delivered_qty=CoalesceFn(
                models.Subquery(delivered, output_field=models.IntegerField()), 0
            ),
        )
        return qs.annotate(
            available_qty=models.F("quantity_assigned")
            - models.F("pending_qty")
            - models.F("approved_waiting_qty")
            - models.F("delivered_qty"),
            allocated_value_ann=models.ExpressionWrapper(
                models.F("quantity_assigned") * models.F("voucher_type__unit_value"),
                output_field=models.DecimalField(max_digits=14, decimal_places=2),
            ),
        )

    def for_employee(self, employee: EmployeeProfile):
        return self.filter(employee=employee)


class VoucherAllocation(models.Model):
    """Quantità di un tipo voucher assegnata preventivamente a un dipendente."""

    employee = models.ForeignKey(
        EmployeeProfile,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="dipendente",
    )
    welfare_program = models.ForeignKey(
        WelfareProgram,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="programma welfare",
    )
    voucher_type = models.ForeignKey(
        VoucherType,
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name="tipo voucher",
    )
    quantity_assigned = models.PositiveIntegerField("quantità assegnata", default=0)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocations_created",
        verbose_name="assegnato da",
    )
    assigned_at = models.DateTimeField("assegnato il", auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="allocations_updated",
        verbose_name="aggiornato da",
    )
    updated_at = models.DateTimeField("aggiornato il", auto_now=True)

    objects = VoucherAllocationQuerySet.as_manager()

    class Meta:
        verbose_name = "allocazione voucher"
        verbose_name_plural = "allocazioni voucher"
        ordering = ["voucher_type__convention__name", "voucher_type__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "welfare_program", "voucher_type"],
                name="uniq_allocation_employee_program_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee} — {self.voucher_type} × {self.quantity_assigned}"

    # -- valore economico ---------------------------------------------------
    @property
    def allocated_value(self) -> Decimal:
        return Decimal(self.quantity_assigned) * self.voucher_type.unit_value

    # -- contatori quantità -------------------------------------------------
    def _sum_requests(self, **filters) -> int:
        return (
            self.requests.filter(**filters).aggregate(total=models.Sum("quantity"))["total"] or 0
        )

    @property
    def quantity_pending(self) -> int:
        if hasattr(self, "pending_qty"):
            return self.pending_qty
        return self._sum_requests(status=VoucherRequest.Status.PENDING)

    @property
    def quantity_approved_waiting_delivery(self) -> int:
        if hasattr(self, "approved_waiting_qty"):
            return self.approved_waiting_qty
        return self._sum_requests(
            status=VoucherRequest.Status.APPROVED, delivery__isnull=True
        )

    @property
    def quantity_delivered(self) -> int:
        if hasattr(self, "delivered_qty"):
            return self.delivered_qty
        return self.deliveries.aggregate(total=models.Sum("quantity"))["total"] or 0

    @property
    def quantity_available(self) -> int:
        if hasattr(self, "available_qty"):
            return self.available_qty
        return (
            self.quantity_assigned
            - self.quantity_pending
            - self.quantity_approved_waiting_delivery
            - self.quantity_delivered
        )

    @property
    def quantity_consumed(self) -> int:
        """Quantità già riservate o consegnate: non possono essere rimosse."""
        return (
            self.quantity_pending
            + self.quantity_approved_waiting_delivery
            + self.quantity_delivered
        )

    @property
    def counters(self) -> dict:
        return {
            "assigned": self.quantity_assigned,
            "pending": self.quantity_pending,
            "approved_waiting_delivery": self.quantity_approved_waiting_delivery,
            "delivered": self.quantity_delivered,
            "available": self.quantity_available,
        }


# ---------------------------------------------------------------------------
# Richieste, allegati, consegne
# ---------------------------------------------------------------------------
class VoucherRequest(models.Model):
    """Richiesta di uno o più voucher da parte del dipendente."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "In attesa"
        APPROVED = "APPROVED", "Approvata"
        REJECTED = "REJECTED", "Rifiutata"

    allocation = models.ForeignKey(
        VoucherAllocation,
        on_delete=models.PROTECT,
        related_name="requests",
        verbose_name="allocazione",
    )
    quantity = models.PositiveIntegerField(
        "quantità", validators=[MinValueValidator(1)]
    )
    status = models.CharField(
        "stato", max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    requested_at = models.DateTimeField("richiesta il", auto_now_add=True)
    processed_at = models.DateTimeField("elaborata il", null=True, blank=True)
    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requests_processed",
        verbose_name="elaborata da",
    )
    rejection_reason = models.TextField("motivazione del rifiuto", blank=True)

    class Meta:
        verbose_name = "richiesta voucher"
        verbose_name_plural = "richieste voucher"
        ordering = ["-requested_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="request_quantity_at_least_one"
            ),
        ]

    def __str__(self) -> str:
        return f"Richiesta #{self.pk} — {self.allocation.voucher_type} × {self.quantity}"

    @property
    def employee(self) -> EmployeeProfile:
        return self.allocation.employee

    @property
    def voucher_type(self) -> VoucherType:
        return self.allocation.voucher_type

    @property
    def total_value(self) -> Decimal:
        return Decimal(self.quantity) * self.allocation.voucher_type.unit_value

    @property
    def is_pending(self) -> bool:
        return self.status == self.Status.PENDING

    @property
    def is_approved(self) -> bool:
        return self.status == self.Status.APPROVED

    @property
    def is_rejected(self) -> bool:
        return self.status == self.Status.REJECTED

    @property
    def delivery_obj(self) -> "VoucherDelivery | None":
        return getattr(self, "delivery", None)

    @property
    def is_delivered(self) -> bool:
        return self.delivery_obj is not None

    @property
    def can_be_delivered(self) -> bool:
        return self.is_approved and not self.is_delivered

    @property
    def status_label(self) -> str:
        if self.is_approved and self.is_delivered:
            return "Consegnata"
        if self.is_approved:
            return "Da consegnare"
        return self.get_status_display()

    @property
    def status_css(self) -> str:
        if self.is_pending:
            return "warning text-dark"
        if self.is_rejected:
            return "danger"
        if self.is_delivered:
            return "success"
        return "primary"


def attachment_upload_path(instance: "RequestAttachment", filename: str) -> str:
    """Percorso non indovinabile all'interno del bucket privato."""
    suffix = PurePosixPath(filename).suffix.lower()[:12]
    return f"attachments/{instance.request_id}/{uuid.uuid4().hex}{suffix}"


class RequestAttachment(models.Model):
    """Documento opzionale allegato a una richiesta voucher."""

    request = models.ForeignKey(
        VoucherRequest,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="richiesta",
    )
    file = models.FileField("file", upload_to=attachment_upload_path, max_length=255)
    original_filename = models.CharField("nome file originale", max_length=255)
    uploaded_at = models.DateTimeField("caricato il", auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments_uploaded",
        verbose_name="caricato da",
    )

    class Meta:
        verbose_name = "allegato"
        verbose_name_plural = "allegati"
        ordering = ["uploaded_at"]

    def __str__(self) -> str:
        return self.original_filename


class VoucherDelivery(models.Model):
    """Registrazione della consegna fisica dei voucher (avvenuta fuori piattaforma)."""

    allocation = models.ForeignKey(
        VoucherAllocation,
        on_delete=models.PROTECT,
        related_name="deliveries",
        verbose_name="allocazione",
    )
    request = models.OneToOneField(
        VoucherRequest,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="delivery",
        verbose_name="richiesta",
    )
    quantity = models.PositiveIntegerField("quantità", validators=[MinValueValidator(1)])
    delivered_at = models.DateTimeField("consegnato il", default=timezone.now)
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="deliveries_registered",
        verbose_name="consegnato da",
    )
    notes = models.TextField("note", blank=True)

    class Meta:
        verbose_name = "consegna voucher"
        verbose_name_plural = "consegne voucher"
        ordering = ["-delivered_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=1), name="delivery_quantity_at_least_one"
            ),
        ]

    def __str__(self) -> str:
        return f"Consegna #{self.pk} — {self.allocation.voucher_type} × {self.quantity}"

    @property
    def is_direct(self) -> bool:
        """Consegna diretta amministrativa, senza richiesta del dipendente."""
        return self.request_id is None

    @property
    def total_value(self) -> Decimal:
        return Decimal(self.quantity) * self.allocation.voucher_type.unit_value

    def clean(self):
        super().clean()
        if self.request_id and self.request.allocation_id != self.allocation_id:
            raise ValidationError(
                {"request": "La richiesta non appartiene all'allocazione indicata."}
            )
