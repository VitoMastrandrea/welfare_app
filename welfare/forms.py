"""Form Django con validazione server-side delle regole welfare."""

from __future__ import annotations

from decimal import Decimal
from pathlib import PurePosixPath

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse, reverse_lazy

from .models import (
    Convention,
    EmployeeProfile,
    VoucherAllocation,
    VoucherType,
    WelfareProgram,
)


# ---------------------------------------------------------------------------
# Utilità
# ---------------------------------------------------------------------------
class BootstrapFormMixin:
    """Applica le classi Bootstrap ai widget senza sporcare i template."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput,)):
                widget.attrs.setdefault("class", "form-check-input")
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                widget.attrs.setdefault("class", "form-select")
            elif isinstance(widget, forms.FileInput):
                widget.attrs.setdefault("class", "form-control")
            else:
                widget.attrs.setdefault("class", "form-control")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """Campo per il caricamento di più allegati opzionali."""

    widget = MultipleFileInput

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"class": "form-control"}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if data in (None, "", []):
            return []
        if not isinstance(data, (list, tuple)):
            data = [data]
        return [single_clean(item, initial) for item in data if item]


def validate_attachment(uploaded_file) -> None:
    max_bytes = settings.ATTACHMENT_MAX_SIZE_MB * 1024 * 1024
    if uploaded_file.size > max_bytes:
        raise ValidationError(
            f"«{uploaded_file.name}» supera la dimensione massima di "
            f"{settings.ATTACHMENT_MAX_SIZE_MB} MB."
        )
    extension = PurePosixPath(uploaded_file.name).suffix.lower().lstrip(".")
    allowed = [ext.lower() for ext in settings.ATTACHMENT_ALLOWED_EXTENSIONS]
    if extension not in allowed:
        raise ValidationError(
            f"Estensione «.{extension}» non ammessa. Formati consentiti: {', '.join(allowed)}."
        )


# ---------------------------------------------------------------------------
# Area dipendente
# ---------------------------------------------------------------------------
class VoucherRequestForm(BootstrapFormMixin, forms.Form):
    """Richiesta voucher: quantità + allegati opzionali."""

    quantity = forms.IntegerField(
        label="Quantità richiesta",
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "step": "1",
                "min": "1",
                "hx-trigger": "input changed delay:250ms",
                "hx-swap": "innerHTML",
                "hx-target": "#riepilogo-richiesta",
            }
        ),
    )
    attachments = MultipleFileField(
        label="Allegati (facoltativi)",
        help_text="Puoi allegare uno o più documenti a supporto della richiesta.",
    )

    def __init__(self, *args, allocation: VoucherAllocation, **kwargs):
        self.allocation = allocation
        super().__init__(*args, **kwargs)
        available = allocation.quantity_available
        self.fields["quantity"].widget.attrs["max"] = str(available)
        self.fields["quantity"].widget.attrs["hx-get"] = reverse(
            "request_summary_partial", args=[allocation.pk]
        )
        self.fields["quantity"].help_text = f"Disponibili: {available}"

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        available = self.allocation.quantity_available
        if quantity > available:
            raise ValidationError(
                f"Puoi richiedere al massimo {available} voucher di questo tipo."
            )
        return quantity

    def clean_attachments(self):
        files = self.cleaned_data.get("attachments") or []
        for uploaded in files:
            validate_attachment(uploaded)
        return files


# ---------------------------------------------------------------------------
# Area amministrazione
# ---------------------------------------------------------------------------
class EmployeeBudgetForm(BootstrapFormMixin, forms.Form):
    """Creazione/modifica del budget di un dipendente."""

    amount = forms.DecimalField(
        label="Budget assegnato (€)",
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.00"),
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
    )

    def __init__(self, *args, employee: EmployeeProfile, program: WelfareProgram, **kwargs):
        self.employee = employee
        self.program = program
        super().__init__(*args, **kwargs)
        allocated = employee.allocated_value(program)
        self.fields["amount"].help_text = (
            f"Valore già allocato in voucher: €{allocated}. "
            "Il budget non può essere inferiore a questo importo."
        )

    def clean_amount(self):
        amount = self.cleaned_data["amount"]
        allocated = self.employee.allocated_value(self.program)
        if amount < allocated:
            raise ValidationError(
                f"Il budget non può essere inferiore al valore già allocato (€{allocated})."
            )
        return amount


class AllocationForm(BootstrapFormMixin, forms.Form):
    """Assegnazione di voucher a un dipendente."""

    employee = forms.ModelChoiceField(
        label="Dipendente",
        queryset=EmployeeProfile.objects.filter(active=True).select_related("user"),
        widget=forms.Select(
            attrs={
                "hx-get": "",
                "hx-target": "#riepilogo-allocazione",
                "hx-trigger": "change",
                "hx-include": "closest form",
            }
        ),
    )
    voucher_type = forms.ModelChoiceField(
        label="Tipo voucher",
        queryset=VoucherType.objects.filter(active=True).select_related("convention"),
        widget=forms.Select(
            attrs={
                "hx-get": "",
                "hx-target": "#riepilogo-allocazione",
                "hx-trigger": "change",
                "hx-include": "closest form",
            }
        ),
    )
    quantity = forms.IntegerField(
        label="Quantità assegnata (totale)",
        min_value=0,
        widget=forms.NumberInput(
            attrs={
                "step": "1",
                "min": "0",
                "hx-get": "",
                "hx-target": "#riepilogo-allocazione",
                "hx-trigger": "input changed delay:300ms",
                "hx-include": "closest form",
            }
        ),
        help_text="Quantità complessiva assegnata per questo tipo voucher.",
    )

    def __init__(self, *args, program: WelfareProgram, employee: EmployeeProfile | None = None, **kwargs):
        self.program = program
        self.locked_employee = employee
        super().__init__(*args, **kwargs)
        summary_url = str(reverse_lazy("admin_allocation_summary_partial"))
        for name in ("employee", "voucher_type", "quantity"):
            self.fields[name].widget.attrs["hx-get"] = summary_url
        if employee is not None:
            self.fields["employee"].initial = employee.pk
            self.fields["employee"].disabled = True
            self.fields["employee"].widget = forms.HiddenInput()

    def clean(self):
        cleaned = super().clean()
        employee = self.locked_employee or cleaned.get("employee")
        voucher_type = cleaned.get("voucher_type")
        quantity = cleaned.get("quantity")
        if not employee or not voucher_type or quantity is None:
            return cleaned
        cleaned["employee"] = employee
        return cleaned


class RejectRequestForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(
        label="Motivazione del rifiuto",
        widget=forms.Textarea(attrs={"rows": 3}),
        max_length=1000,
    )


class DeliveryFromRequestForm(BootstrapFormMixin, forms.Form):
    notes = forms.CharField(
        label="Note (facoltative)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        max_length=1000,
    )


class DirectDeliveryForm(BootstrapFormMixin, forms.Form):
    """Consegna diretta amministrativa, senza richiesta del dipendente."""

    allocation = forms.ModelChoiceField(
        label="Voucher assegnato",
        queryset=VoucherAllocation.objects.none(),
        widget=forms.Select(
            attrs={
                "hx-get": "",
                "hx-target": "#riepilogo-consegna",
                "hx-trigger": "change",
                "hx-include": "closest form",
            }
        ),
    )
    quantity = forms.IntegerField(
        label="Quantità consegnata",
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "step": "1",
                "min": "1",
                "hx-get": "",
                "hx-target": "#riepilogo-consegna",
                "hx-trigger": "input changed delay:300ms",
                "hx-include": "closest form",
            }
        ),
    )
    notes = forms.CharField(
        label="Note (facoltative)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        max_length=1000,
    )

    def __init__(self, *args, employee: EmployeeProfile, program: WelfareProgram, **kwargs):
        self.employee = employee
        self.program = program
        super().__init__(*args, **kwargs)
        summary_url = str(reverse_lazy("admin_delivery_summary_partial"))
        for name in ("allocation", "quantity"):
            self.fields[name].widget.attrs["hx-get"] = summary_url
        self.fields["allocation"].queryset = (
            VoucherAllocation.objects.filter(employee=employee, welfare_program=program)
            .select_related("voucher_type", "voucher_type__convention")
        )

    def clean(self):
        cleaned = super().clean()
        allocation = cleaned.get("allocation")
        quantity = cleaned.get("quantity")
        if allocation and quantity:
            available = allocation.quantity_available
            if quantity > available:
                self.add_error(
                    "quantity",
                    f"Quantità non disponibile: puoi consegnare al massimo {available}.",
                )
        return cleaned


class ConventionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Convention
        fields = ["name", "description", "active"]
        labels = {"name": "Nome", "description": "Descrizione", "active": "Attiva"}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class VoucherTypeForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = VoucherType
        fields = ["convention", "name", "description", "unit_value", "active"]
        labels = {
            "convention": "Convenzione",
            "name": "Nome",
            "description": "Descrizione",
            "unit_value": "Valore unitario (€)",
            "active": "Attivo",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "unit_value": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.is_used:
            # Il valore unitario di un tipo voucher già allocato è immutabile.
            self.fields["unit_value"].disabled = True
            self.fields["unit_value"].help_text = (
                "Non modificabile: questo tipo voucher è già stato allocato. "
                "Per un taglio diverso crea un nuovo tipo voucher."
            )
            self.fields["convention"].disabled = True


class WelfareProgramForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = WelfareProgram
        fields = ["name", "description", "start_date", "end_date", "active"]
        labels = {
            "name": "Nome",
            "description": "Descrizione",
            "start_date": "Data inizio",
            "end_date": "Data fine",
            "active": "Attivo",
        }
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
