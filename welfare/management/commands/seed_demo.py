"""Genera dati dimostrativi coerenti con lo scenario descritto nelle specifiche.

Uso::

    python manage.py seed_demo
    python manage.py seed_demo --password segreta
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction

from welfare import services
from welfare.models import (
    Convention,
    EmployeeProfile,
    VoucherType,
    WelfareProgram,
)
from welfare.permissions import WELFARE_MANAGERS_GROUP

DEFAULT_PASSWORD = "welfare2026"


class Command(BaseCommand):
    help = "Crea utenti, programma, convenzioni, voucher, budget e allocazioni di esempio."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Password assegnata agli utenti demo (default: %(default)s).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        password = options["password"]

        program, _ = WelfareProgram.objects.get_or_create(
            name="Piano Welfare",
            defaults={
                "description": "Iniziativa welfare una tantum riservata ai dipendenti.",
                "active": True,
            },
        )

        managers_group, _ = Group.objects.get_or_create(name=WELFARE_MANAGERS_GROUP)

        antonia = self._create_user(
            "antonia", "Antonia", "Rossi", "antonia@example.com", password
        )
        antonia.groups.add(managers_group)
        giuseppe = self._create_user(
            "giuseppe", "Giuseppe", "Verdi", "giuseppe@example.com", password
        )

        antonia_profile, _ = EmployeeProfile.objects.get_or_create(
            user=antonia, defaults={"employee_code": "EMP001"}
        )
        giuseppe_profile, _ = EmployeeProfile.objects.get_or_create(
            user=giuseppe, defaults={"employee_code": "EMP002"}
        )

        muraglia, _ = Convention.objects.get_or_create(
            name="Muraglia Srlrs",
            defaults={"description": "Punto vendita convenzionato per la spesa alimentare."},
        )
        orodance, _ = Convention.objects.get_or_create(
            name="OroDance",
            defaults={"description": "Scuola di danza convenzionata."},
        )
        yoga, _ = Convention.objects.get_or_create(
            name="Associazione Yoga",
            defaults={"description": "Associazione sportiva convenzionata."},
        )

        buono_100 = self._voucher_type(
            muraglia, "Buono spesa", "Buono spesa da utilizzare presso il punto vendita.", "100.00"
        )
        self._voucher_type(
            muraglia,
            "Buono spesa",
            "Buono spesa di taglio ridotto, cumulabile.",
            "50.00",
        )
        abbonamento = self._voucher_type(
            orodance,
            "Abbonamento annuale",
            "Abbonamento annuale ai corsi, da attivare in segreteria.",
            "440.00",
        )
        lezione = self._voucher_type(
            yoga, "Singola lezione", "Voucher valido per una singola lezione.", "50.00"
        )

        # Budget e allocazioni: l'attore è Antonia (Welfare Manager).
        services.set_employee_budget(
            employee=giuseppe_profile, program=program, amount=Decimal("5000.00"), actor=antonia
        )
        services.set_allocation_quantity(
            employee=giuseppe_profile, program=program, voucher_type=buono_100, quantity=10, actor=antonia
        )
        services.set_allocation_quantity(
            employee=giuseppe_profile, program=program, voucher_type=abbonamento, quantity=3, actor=antonia
        )
        services.set_allocation_quantity(
            employee=giuseppe_profile, program=program, voucher_type=lezione, quantity=27, actor=antonia
        )

        # Antonia è contemporaneamente dipendente: ha una propria posizione welfare.
        services.set_employee_budget(
            employee=antonia_profile, program=program, amount=Decimal("2000.00"), actor=antonia
        )
        services.set_allocation_quantity(
            employee=antonia_profile, program=program, voucher_type=abbonamento, quantity=2, actor=antonia
        )

        summary = giuseppe_profile.welfare_summary(program)
        self.stdout.write(self.style.SUCCESS("Dati demo creati/aggiornati."))
        self.stdout.write(f"  Programma: {program.name}")
        self.stdout.write(
            f"  Giuseppe — budget €{summary['budget_assigned']}, "
            f"allocato €{summary['budget_allocated']}, "
            f"non allocato €{summary['budget_unallocated']}"
        )
        self.stdout.write(
            "  Utenti: antonia (dipendente + Welfare Manager), giuseppe (dipendente)"
        )
        self.stdout.write(self.style.WARNING(f"  Password demo: {password}"))

    def _create_user(self, username, first_name, last_name, email, password) -> User:
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"first_name": first_name, "last_name": last_name, "email": email},
        )
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
        return user

    def _voucher_type(self, convention, name, description, unit_value) -> VoucherType:
        voucher_type, _ = VoucherType.objects.get_or_create(
            convention=convention,
            name=name,
            unit_value=Decimal(unit_value),
            defaults={"description": description},
        )
        return voucher_type
