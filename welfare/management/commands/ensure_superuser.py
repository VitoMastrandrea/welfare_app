"""Crea il primo superuser da variabili d'ambiente, senza terminale interattivo.

Pensato per piattaforme come Railway, dove non c'è una shell sul container:
il comando viene eseguito all'avvio e non è mai bloccante.

Variabili lette (le stesse di `createsuperuser --noinput`):

    DJANGO_SUPERUSER_USERNAME
    DJANGO_SUPERUSER_PASSWORD
    DJANGO_SUPERUSER_EMAIL      (facoltativa)

Se le variabili non ci sono, o l'utente esiste già, il comando non fa nulla.
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Crea il superuser indicato dalle variabili d'ambiente, se non esiste già."

    @transaction.atomic
    def handle(self, *args, **options):
        username = (os.environ.get("DJANGO_SUPERUSER_USERNAME") or "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD") or ""
        email = (os.environ.get("DJANGO_SUPERUSER_EMAIL") or "").strip()

        if not username or not password:
            self.stdout.write(
                "ensure_superuser: DJANGO_SUPERUSER_USERNAME/PASSWORD non impostate, "
                "nessun utente creato."
            )
            return

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(
                f"ensure_superuser: l'utente «{username}» esiste già, nessuna modifica."
            )
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(
            self.style.SUCCESS(f"ensure_superuser: superuser «{username}» creato.")
        )
        self.stdout.write(
            self.style.WARNING(
                "Rimuovi DJANGO_SUPERUSER_PASSWORD dalle variabili d'ambiente "
                "dopo il primo accesso."
            )
        )
