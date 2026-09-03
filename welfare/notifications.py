"""Notifiche via email verso l'indirizzo amministrativo configurato.

Regole di fondo:

* c'è **un solo destinatario**, ``settings.WELFARE_NOTIFICATION_EMAIL``;
  ai dipendenti non viene inviato nulla;
* un invio che fallisce non deve mai far fallire l'operazione dell'utente:
  ogni errore viene registrato nei log e ignorato;
* senza SMTP configurato le notifiche restano inerti.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

logger = logging.getLogger(__name__)


def notification_recipients() -> list[str]:
    address = (getattr(settings, "WELFARE_NOTIFICATION_EMAIL", "") or "").strip()
    return [address] if address else []


def absolute_url(path: str) -> str:
    base = getattr(settings, "SITE_BASE_URL", "") or ""
    return f"{base}{path}" if base else ""


def _send(subject: str, body: str) -> bool:
    """Invia una email amministrativa. Non solleva mai eccezioni."""
    recipients = notification_recipients()
    if not recipients:
        logger.warning("Notifica non inviata: WELFARE_NOTIFICATION_EMAIL non impostata.")
        return False
    if not getattr(settings, "EMAIL_CONFIGURED", False) and not settings.DEBUG:
        logger.warning(
            "Notifica non inviata (%s): SMTP non configurato, imposta EMAIL_HOST.", subject
        )
        return False
    try:
        send_mail(
            subject=f"{settings.EMAIL_SUBJECT_PREFIX}{subject}",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 - una email persa non deve bloccare l'app
        logger.exception("Invio della notifica «%s» non riuscito.", subject)
        return False
    logger.info("Notifica «%s» inviata a %s.", subject, ", ".join(recipients))
    return True


def notify_new_request(voucher_request) -> bool:
    """Avvisa l'amministrazione che è arrivata una nuova richiesta da approvare."""
    employee = voucher_request.allocation.employee
    voucher_type = voucher_request.allocation.voucher_type
    body = render_to_string(
        "welfare/emails/new_request.txt",
        {
            "request": voucher_request,
            "employee": employee,
            "voucher_type": voucher_type,
            "has_attachments": voucher_request.attachments.exists(),
            "url": absolute_url(reverse("admin_request_detail", args=[voucher_request.pk])),
        },
    )
    subject = f"Nuova richiesta voucher da {employee.display_name}"
    return _send(subject, body)


def send_pending_digest(*, force: bool = False) -> dict:
    """Riepilogo delle pratiche ancora aperte. Restituisce cosa è stato fatto."""
    from .models import VoucherRequest, WelfareProgram

    program = WelfareProgram.current()
    base = VoucherRequest.objects.select_related(
        "allocation__employee__user", "allocation__voucher_type__convention"
    )
    if program is not None:
        base = base.filter(allocation__welfare_program=program)

    pending = list(base.filter(status=VoucherRequest.Status.PENDING).order_by("requested_at"))
    to_deliver = list(
        base.filter(status=VoucherRequest.Status.APPROVED, delivery__isnull=True).order_by(
            "processed_at"
        )
    )

    if not pending and not to_deliver and not force:
        logger.info("Riepilogo giornaliero non inviato: nessuna pratica aperta.")
        return {"sent": False, "pending": 0, "to_deliver": 0}

    body = render_to_string(
        "welfare/emails/pending_digest.txt",
        {
            "program": program,
            "pending": pending,
            "to_deliver": to_deliver,
            "requests_url": absolute_url(reverse("admin_requests")),
        },
    )
    subject = (
        f"Riepilogo welfare: {len(pending)} da approvare, {len(to_deliver)} da consegnare"
    )
    sent = _send(subject, body)
    return {"sent": sent, "pending": len(pending), "to_deliver": len(to_deliver)}
