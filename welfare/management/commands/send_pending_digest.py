"""Invia il riepilogo giornaliero delle pratiche welfare ancora aperte.

Pensato per un cron job (su Railway: un servizio con Cron Schedule):

    python manage.py send_pending_digest

Se non ci sono richieste da approvare né consegne da registrare non invia
nulla, per non riempire la casella di messaggi inutili. Con ``--force``
il riepilogo viene inviato comunque.
"""

from django.core.management.base import BaseCommand

from welfare import notifications


class Command(BaseCommand):
    help = "Invia all'indirizzo amministrativo il riepilogo delle pratiche aperte."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Invia il riepilogo anche quando non c'è nulla in sospeso.",
        )

    def handle(self, *args, **options):
        result = notifications.send_pending_digest(force=options["force"])
        if result["sent"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Riepilogo inviato: {result['pending']} da approvare, "
                    f"{result['to_deliver']} da consegnare."
                )
            )
        else:
            self.stdout.write(
                f"Nessun invio ({result['pending']} da approvare, "
                f"{result['to_deliver']} da consegnare)."
            )
