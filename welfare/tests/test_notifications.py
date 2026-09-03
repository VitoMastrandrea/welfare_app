"""Test delle notifiche: email amministrative e badge in-app."""

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from welfare import notifications, services
from welfare.models import VoucherRequest
from welfare.tests.factories import (
    PASSWORD,
    create_employee,
    create_program,
    create_voucher_type,
    setup_allocation,
)

NOTIFICATION_EMAIL = "agevolazioni@studiobirardi.it"


class OnCommitMixin:
    """Le notifiche partono con ``transaction.on_commit``: nei TestCase le
    callback vanno eseguite esplicitamente."""

    def create_request(self, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return services.create_voucher_request(**kwargs)

    def post_committed(self, *args, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(*args, **kwargs)

EMAIL_SETTINGS = dict(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_CONFIGURED=True,
    WELFARE_NOTIFICATION_EMAIL=NOTIFICATION_EMAIL,
    DEFAULT_FROM_EMAIL="welfare@example.com",
    EMAIL_SUBJECT_PREFIX="[Welfare] ",
    SITE_BASE_URL="https://welfare.example.com",
)


@override_settings(**EMAIL_SETTINGS)
class NewRequestNotificationTests(OnCommitMixin, TestCase):
    def setUp(self):
        self.program = create_program()
        self.giuseppe = create_employee("giuseppe", first_name="Giuseppe", last_name="Verdi")
        self.antonia = create_employee("antonia", manager=True)
        self.voucher_type = create_voucher_type(unit_value="100.00")
        self.allocation = setup_allocation(
            employee=self.giuseppe,
            program=self.program,
            voucher_type=self.voucher_type,
            quantity=10,
            actor=self.antonia.user,
        )
        mail.outbox = []

    def test_new_request_sends_one_email_to_the_configured_address(self):
        self.create_request(
            allocation=self.allocation, quantity=3, actor=self.giuseppe.user
        )
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [NOTIFICATION_EMAIL])
        self.assertNotIn(self.giuseppe.user.email, message.to)

    def test_email_contains_the_useful_details(self):
        self.create_request(
            allocation=self.allocation, quantity=3, actor=self.giuseppe.user
        )
        message = mail.outbox[0]
        self.assertIn("Giuseppe Verdi", message.subject)
        self.assertIn("Muraglia Srlrs", message.body)
        self.assertIn("Buono spesa", message.body)
        self.assertIn("300,00", message.body)
        self.assertIn("nessuno", message.body)  # allegati
        request = VoucherRequest.objects.get()
        self.assertIn(
            f"https://welfare.example.com{reverse('admin_request_detail', args=[request.pk])}",
            message.body,
        )

    def test_email_is_sent_also_through_the_web_flow(self):
        self.client.login(username="giuseppe", password=PASSWORD)
        self.post_committed(
            reverse("request_voucher", args=[self.allocation.pk]), {"quantity": 2}, follow=True
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [NOTIFICATION_EMAIL])

    def test_no_email_for_administrative_actions(self):
        """Approvazione, rifiuto e consegna le fa l'amministrazione: nessun avviso."""
        request = self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        mail.outbox = []
        services.approve_request(request=request, actor=self.antonia.user)
        services.deliver_request(request=request, actor=self.antonia.user)
        other = self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        mail.outbox = []
        services.reject_request(request=other, actor=self.antonia.user, reason="No")
        services.create_direct_delivery(
            allocation=self.allocation, quantity=1, actor=self.antonia.user
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_failing_smtp_does_not_break_the_request(self):
        with mock.patch(
            "welfare.notifications.send_mail", side_effect=OSError("SMTP non raggiungibile")
        ):
            request = services.create_voucher_request(
                allocation=self.allocation, quantity=4, actor=self.giuseppe.user
            )
        request.refresh_from_db()
        self.assertEqual(request.status, VoucherRequest.Status.PENDING)
        self.assertEqual(request.quantity, 4)

    def test_no_request_no_email_when_validation_fails(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            with self.captureOnCommitCallbacks(execute=True):
                services.create_voucher_request(
                    allocation=self.allocation, quantity=99, actor=self.giuseppe.user
                )
        self.assertEqual(len(mail.outbox), 0)


@override_settings(**EMAIL_SETTINGS)
class DigestTests(OnCommitMixin, TestCase):
    def setUp(self):
        self.program = create_program()
        self.giuseppe = create_employee("giuseppe", first_name="Giuseppe", last_name="Verdi")
        self.antonia = create_employee("antonia", manager=True)
        self.voucher_type = create_voucher_type(unit_value="100.00")
        self.allocation = setup_allocation(
            employee=self.giuseppe,
            program=self.program,
            voucher_type=self.voucher_type,
            quantity=10,
            actor=self.antonia.user,
        )
        mail.outbox = []

    def test_digest_is_not_sent_when_nothing_is_open(self):
        result = notifications.send_pending_digest()
        self.assertFalse(result["sent"])
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_lists_pending_and_to_deliver(self):
        self.create_request(
            allocation=self.allocation, quantity=2, actor=self.giuseppe.user
        )
        approved = self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        services.approve_request(request=approved, actor=self.antonia.user)
        mail.outbox = []

        result = notifications.send_pending_digest()
        self.assertTrue(result["sent"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(result["to_deliver"], 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [NOTIFICATION_EMAIL])
        self.assertIn("1 da approvare", message.subject)
        self.assertIn("1 da consegnare", message.subject)
        self.assertIn("Giuseppe Verdi", message.body)
        self.assertIn("200,00", message.body)

    def test_delivered_requests_are_not_in_the_digest(self):
        request = self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        services.approve_request(request=request, actor=self.antonia.user)
        services.deliver_request(request=request, actor=self.antonia.user)
        result = notifications.send_pending_digest()
        self.assertFalse(result["sent"])

    def test_force_sends_an_empty_digest(self):
        result = notifications.send_pending_digest(force=True)
        self.assertTrue(result["sent"])
        self.assertIn("nessuna", mail.outbox[0].body)

    def test_management_command(self):
        self.create_request(
            allocation=self.allocation, quantity=2, actor=self.giuseppe.user
        )
        mail.outbox = []
        out = StringIO()
        call_command("send_pending_digest", stdout=out)
        self.assertIn("Riepilogo inviato", out.getvalue())
        self.assertEqual(len(mail.outbox), 1)

    def test_management_command_is_quiet_when_nothing_to_do(self):
        out = StringIO()
        call_command("send_pending_digest", stdout=out)
        self.assertIn("Nessun invio", out.getvalue())
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_CONFIGURED=False,
    DEBUG=False,
    WELFARE_NOTIFICATION_EMAIL=NOTIFICATION_EMAIL,
)
class EmailNotConfiguredTests(OnCommitMixin, TestCase):
    def test_nothing_is_sent_and_nothing_breaks(self):
        program = create_program()
        giuseppe = create_employee("giuseppe")
        antonia = create_employee("antonia", manager=True)
        allocation = setup_allocation(
            employee=giuseppe,
            program=program,
            voucher_type=create_voucher_type(unit_value="100.00"),
            quantity=5,
            actor=antonia.user,
        )
        mail.outbox = []
        request = self.create_request(
            allocation=allocation, quantity=2, actor=giuseppe.user
        )
        self.assertEqual(request.status, VoucherRequest.Status.PENDING)
        self.assertEqual(len(mail.outbox), 0)


class PendingBadgeTests(OnCommitMixin, TestCase):
    def setUp(self):
        self.program = create_program()
        self.giuseppe = create_employee("giuseppe")
        self.antonia = create_employee("antonia", manager=True)
        self.allocation = setup_allocation(
            employee=self.giuseppe,
            program=self.program,
            voucher_type=create_voucher_type(unit_value="100.00"),
            quantity=10,
            actor=self.antonia.user,
        )

    def test_badge_counts_pending_requests_for_managers(self):
        self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        self.client.login(username="antonia", password=PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["pending_requests_count"], 2)
        self.assertContains(response, "in attesa di approvazione")

    def test_badge_disappears_once_requests_are_processed(self):
        request = self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        services.approve_request(request=request, actor=self.antonia.user)
        self.client.login(username="antonia", password=PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["pending_requests_count"], 0)

    def test_plain_employee_has_no_badge(self):
        self.create_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        self.client.login(username="giuseppe", password=PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["pending_requests_count"], 0)
        self.assertNotContains(response, "in attesa di approvazione")


@override_settings(**EMAIL_SETTINGS)
class RecipientIsolationTests(OnCommitMixin, TestCase):
    def test_only_the_configured_address_receives_notifications(self):
        program = create_program()
        giuseppe = create_employee("giuseppe", email="giuseppe@example.com")
        antonia = create_employee("antonia", manager=True, email="antonia@example.com")
        allocation = setup_allocation(
            employee=giuseppe,
            program=program,
            voucher_type=create_voucher_type(unit_value="50.00"),
            quantity=4,
            actor=antonia.user,
        )
        mail.outbox = []
        self.create_request(
            allocation=allocation, quantity=2, actor=giuseppe.user
        )
        notifications.send_pending_digest()

        self.assertEqual(len(mail.outbox), 2)
        for message in mail.outbox:
            self.assertEqual(message.to, [NOTIFICATION_EMAIL])
            self.assertEqual(message.cc, [])
            self.assertEqual(message.bcc, [])

    def test_recipients_helper_ignores_empty_configuration(self):
        with override_settings(WELFARE_NOTIFICATION_EMAIL=""):
            self.assertEqual(notifications.notification_recipients(), [])

    def test_total_value_is_decimal_in_the_email(self):
        program = create_program()
        giuseppe = create_employee("giuseppe")
        antonia = create_employee("antonia", manager=True)
        allocation = setup_allocation(
            employee=giuseppe,
            program=program,
            voucher_type=create_voucher_type(unit_value="440.00", convention_name="OroDance",
                                             name="Abbonamento annuale"),
            quantity=3,
            actor=antonia.user,
        )
        mail.outbox = []
        request = self.create_request(
            allocation=allocation, quantity=2, actor=giuseppe.user
        )
        self.assertEqual(request.total_value, Decimal("880.00"))
        self.assertIn("880,00", mail.outbox[0].body)
