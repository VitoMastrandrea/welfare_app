"""Rimuove i dati dimostrativi creati da ``seed_demo``.

Cancella **solo** gli oggetti demo, e si ferma davanti a qualunque cosa sia
stata nel frattempo collegata a dati reali:

* non tocca mai un superuser;
* non elimina una convenzione o un tipo voucher usato da dipendenti non demo;
* non elimina il programma welfare se contiene budget o allocazioni di altri.

Senza terminale (per esempio su Railway) si attiva con la variabile
d'ambiente ``CLEAR_DEMO_DATA=true`` insieme a ``--if-requested``, che è già
nel comando di avvio.

    python manage.py clear_demo_data --dry-run    # mostra soltanto cosa farebbe
    python manage.py clear_demo_data --yes        # esegue
"""

from __future__ import annotations

import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from welfare.models import (
    Convention,
    EmployeeBudget,
    EmployeeProfile,
    RequestAttachment,
    VoucherAllocation,
    VoucherDelivery,
    VoucherRequest,
    VoucherType,
    WelfareProgram,
)

DEMO_USERNAMES = ["antonia", "giuseppe"]
DEMO_CONVENTIONS = ["Muraglia Srlrs", "OroDance", "Associazione Yoga"]
DEMO_PROGRAMS = ["Piano Welfare"]


def env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


class Command(BaseCommand):
    help = "Elimina i dati dimostrativi creati da seed_demo, lasciando intatti quelli reali."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes", action="store_true", help="Conferma l'eliminazione (obbligatorio)."
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Mostra che cosa verrebbe eliminato senza toccare nulla.",
        )
        parser.add_argument(
            "--if-requested",
            action="store_true",
            help="Esegue solo se la variabile d'ambiente CLEAR_DEMO_DATA è attiva.",
        )

    def handle(self, *args, **options):
        if options["if_requested"]:
            if not env_flag("CLEAR_DEMO_DATA"):
                return
            if env_flag("SEED_DEMO"):
                raise CommandError(
                    "SEED_DEMO e CLEAR_DEMO_DATA sono entrambe attive: i dati demo "
                    "verrebbero ricreati subito dopo. Imposta prima SEED_DEMO=false."
                )
            options["yes"] = True

        if not options["yes"] and not options["dry_run"]:
            raise CommandError(
                "Operazione non eseguita: aggiungi --yes per confermare "
                "(oppure --dry-run per vedere cosa verrebbe eliminato)."
            )

        self.dry_run = options["dry_run"]
        if self.dry_run:
            self.stdout.write(self.style.WARNING("SIMULAZIONE: nessun dato verrà eliminato.\n"))

        with transaction.atomic():
            self._clear()
            if self.dry_run:
                transaction.set_rollback(True)

    # -- esecuzione ---------------------------------------------------------
    def _clear(self):
        profiles = self._demo_profiles()
        # Gli id vanno letti prima della cancellazione: dopo ``delete()``
        # l'istanza perde la chiave primaria.
        demo_ids = [profile.pk for profile in profiles]
        self._delete_employee_data(profiles)
        self._delete_profiles_and_users(profiles)
        self._delete_catalog(demo_ids)
        self._delete_programs(demo_ids)
        self.stdout.write(self.style.SUCCESS("\nPulizia dei dati demo completata."))

    def _demo_profiles(self) -> list[EmployeeProfile]:
        profiles = []
        for username in DEMO_USERNAMES:
            user = User.objects.filter(username=username).first()
            if user is None:
                continue
            if user.is_superuser:
                self.stdout.write(
                    self.style.WARNING(
                        f"· utente «{username}» saltato: è un superuser, non lo tocco."
                    )
                )
                continue
            profile = EmployeeProfile.objects.filter(user=user).first()
            if profile is not None:
                profiles.append(profile)
        return profiles

    def _delete_employee_data(self, profiles: list[EmployeeProfile]):
        if not profiles:
            return
        allocations = VoucherAllocation.objects.filter(employee__in=profiles)
        requests = VoucherRequest.objects.filter(allocation__in=allocations)

        counts = {
            "allegati": RequestAttachment.objects.filter(request__in=requests).count(),
            "consegne": VoucherDelivery.objects.filter(allocation__in=allocations).count(),
            "richieste": requests.count(),
            "allocazioni": allocations.count(),
            "budget": EmployeeBudget.objects.filter(employee__in=profiles).count(),
        }
        for label, count in counts.items():
            if count:
                self.stdout.write(f"· {count} {label}")

        if self.dry_run:
            return
        # L'ordine conta: le foreign key sono PROTECT.
        RequestAttachment.objects.filter(request__in=requests).delete()
        VoucherDelivery.objects.filter(allocation__in=allocations).delete()
        requests.delete()
        allocations.delete()
        EmployeeBudget.objects.filter(employee__in=profiles).delete()

    def _delete_profiles_and_users(self, profiles: list[EmployeeProfile]):
        for profile in profiles:
            user = profile.user
            self.stdout.write(f"· dipendente «{profile.display_name}» e utente «{user.username}»")
            if self.dry_run:
                continue
            profile.delete()
            user.delete()

    def _delete_catalog(self, demo_ids: list[int]):
        for convention in Convention.objects.filter(name__in=DEMO_CONVENTIONS):
            # Contano solo le allocazioni di dipendenti non demo: le altre
            # spariscono insieme ai dati demo (o sono già sparite).
            blocking = (
                VoucherAllocation.objects.filter(voucher_type__convention=convention)
                .exclude(employee_id__in=demo_ids)
                .count()
            )
            if blocking:
                self.stdout.write(
                    self.style.WARNING(
                        f"· convenzione «{convention.name}» mantenuta: è usata da "
                        f"{blocking} allocazioni di dipendenti reali."
                    )
                )
                continue
            types = VoucherType.objects.filter(convention=convention).count()
            self.stdout.write(f"· convenzione «{convention.name}» e {types} tipi voucher")
            if self.dry_run:
                continue
            VoucherType.objects.filter(convention=convention).delete()
            convention.delete()

    def _delete_programs(self, demo_ids: list[int]):
        for program in WelfareProgram.objects.filter(name__in=DEMO_PROGRAMS):
            budgets = (
                EmployeeBudget.objects.filter(welfare_program=program)
                .exclude(employee_id__in=demo_ids)
                .count()
            )
            allocations = (
                VoucherAllocation.objects.filter(welfare_program=program)
                .exclude(employee_id__in=demo_ids)
                .count()
            )
            if budgets or allocations:
                self.stdout.write(
                    self.style.WARNING(
                        f"· programma «{program.name}» mantenuto: contiene {budgets} budget "
                        f"e {allocations} allocazioni di dipendenti reali."
                    )
                )
                continue
            self.stdout.write(f"· programma welfare «{program.name}»")
            if self.dry_run:
                continue
            program.delete()
