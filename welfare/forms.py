"""Form Django con validazione server-side delle regole welfare."""

from __future__ import annotations

from decimal import Decimal
from pathlib import PurePosixPath

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.urls import reverse, reverse_lazy

from .models import (
    Convention,
    EmployeeProfile,
    VoucherAllocation,
    VoucherType,
    WelfareProgram,
)
from .permissions import WELFARE_MANAGERS_GROUP


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


# ---------------------------------------------------------------------------
# Gestione utenti (area staff)
# ---------------------------------------------------------------------------
class BaseUserAccountForm(BootstrapFormMixin, forms.ModelForm):
    """Campi comuni alla creazione e alla modifica di un account."""

    is_welfare_manager = forms.BooleanField(
        label="Welfare Manager",
        required=False,
        help_text="Dà accesso all'area Amministrazione welfare.",
    )
    has_employee_profile = forms.BooleanField(
        label="Profilo dipendente attivo",
        required=False,
        help_text="Necessario per accedere all'area dipendente e ricevere voucher.",
    )
    employee_code = forms.CharField(label="Matricola", max_length=50, required=False)

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active", "is_staff"]
        labels = {
            "username": "Nome utente",
            "first_name": "Nome",
            "last_name": "Cognome",
            "email": "Email",
            "is_active": "Account attivo",
            "is_staff": "Privilegi di staff",
        }
        help_texts = {
            "username": "È il nome con cui l'utente accede. Senza spazi.",
            "is_staff": "Permette di gestire gli utenti dall'area Amministrazione.",
        }

    def __init__(self, *args, actor, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = "È il nome con cui l'utente accede. Senza spazi."

        if actor.is_superuser:
            self.fields["is_superuser"] = forms.BooleanField(
                label="Superuser",
                required=False,
                initial=self.instance.is_superuser if self.instance.pk else False,
                help_text="Accesso completo, compreso il pannello Django.",
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
            )

        if self.instance.pk:
            self.fields["is_welfare_manager"].initial = self.instance.groups.filter(
                name=WELFARE_MANAGERS_GROUP
            ).exists()
            profile = EmployeeProfile.objects.filter(user=self.instance).first()
            self.fields["has_employee_profile"].initial = bool(profile and profile.active)
            if profile:
                self.fields["employee_code"].initial = profile.employee_code

            if self.instance.pk == actor.pk:
                # Non ci si può togliere da soli accesso e privilegi.
                for name in ("is_active", "is_staff"):
                    self.fields[name].disabled = True
                    self.fields[name].help_text = "Non puoi modificarlo sul tuo stesso account."
                if "is_superuser" in self.fields:
                    self.fields["is_superuser"].disabled = True

    def clean_username(self):
        username = (self.cleaned_data["username"] or "").strip()
        queryset = User.objects.filter(username__iexact=username)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise ValidationError("Esiste già un utente con questo nome utente.")
        return username

    def clean_employee_code(self):
        code = (self.cleaned_data.get("employee_code") or "").strip()
        if not code:
            return ""
        queryset = EmployeeProfile.objects.filter(employee_code__iexact=code)
        if self.instance.pk:
            queryset = queryset.exclude(user=self.instance)
        if queryset.exists():
            raise ValidationError("Questa matricola è già assegnata a un altro dipendente.")
        return code


class UserCreateForm(BaseUserAccountForm):
    password1 = forms.CharField(
        label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    password2 = forms.CharField(
        label="Conferma password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["is_active"].initial = True

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2:
            if password1 != password2:
                self.add_error("password2", "Le due password non coincidono.")
            else:
                user = User(
                    username=cleaned.get("username") or "",
                    first_name=cleaned.get("first_name") or "",
                    last_name=cleaned.get("last_name") or "",
                    email=cleaned.get("email") or "",
                )
                try:
                    validate_password(password1, user=user)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        return cleaned


class UserUpdateForm(BaseUserAccountForm):
    """Modifica dei dati dell'account. La password si cambia da una pagina dedicata."""


class UserPasswordForm(BootstrapFormMixin, forms.Form):
    password1 = forms.CharField(
        label="Nuova password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )
    password2 = forms.CharField(
        label="Conferma password",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if password1 and password2:
            if password1 != password2:
                self.add_error("password2", "Le due password non coincidono.")
            else:
                try:
                    validate_password(password1, user=self.user)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        return cleaned
