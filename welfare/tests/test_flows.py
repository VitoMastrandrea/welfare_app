"""Test dei flussi applicativi principali attraverso le view HTTP."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from welfare import services
from welfare.models import VoucherDelivery, VoucherRequest
from welfare.tests.factories import (
    PASSWORD,
    create_employee,
    create_program,
    create_voucher_type,
    setup_allocation,
)


class EndToEndFlowTests(TestCase):
    def setUp(self):
        self.program = create_program()
        self.giuseppe = create_employee("giuseppe")
        self.antonia = create_employee("antonia", manager=True)
        self.voucher_100 = create_voucher_type(unit_value="100.00")
        self.voucher_50 = create_voucher_type(unit_value="50.00")
        self.allocation = setup_allocation(
            employee=self.giuseppe,
            program=self.program,
            voucher_type=self.voucher_100,
            quantity=10,
            actor=self.antonia.user,
        )

    def login(self, employee):
        self.client.login(username=employee.user.username, password=PASSWORD)

    def test_request_approve_deliver_flow(self):
        self.login(self.giuseppe)
        response = self.client.post(
            reverse("request_voucher", args=[self.allocation.pk]),
            {"quantity": 3},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        voucher_request = VoucherRequest.objects.get()
        self.assertEqual(voucher_request.status, VoucherRequest.Status.PENDING)
        self.assertEqual(voucher_request.total_value, Decimal("300.00"))

        # Il dipendente vede la richiesta nel proprio storico.
        history = self.client.get(reverse("my_requests"))
        self.assertContains(history, "IN ATTESA")

        # Il manager approva e registra la consegna.
        self.client.logout()
        self.login(self.antonia)
        self.client.post(reverse("admin_request_approve", args=[voucher_request.pk]))
        voucher_request.refresh_from_db()
        self.assertEqual(voucher_request.status, VoucherRequest.Status.APPROVED)
        self.assertEqual(voucher_request.processed_by, self.antonia.user)

        self.client.post(
            reverse("admin_request_deliver", args=[voucher_request.pk]),
            {"notes": "Consegnati in sede"},
        )
        delivery = VoucherDelivery.objects.get()
        self.assertEqual(delivery.request_id, voucher_request.pk)
        self.assertEqual(delivery.quantity, 3)
        self.assertEqual(delivery.delivered_by, self.antonia.user)

        allocation = self.allocation.__class__.objects.get(pk=self.allocation.pk)
        self.assertEqual(allocation.quantity_delivered, 3)
        self.assertEqual(allocation.quantity_available, 7)

    def test_reject_flow_releases_quantity(self):
        request_obj = services.create_voucher_request(
            allocation=self.allocation, quantity=4, actor=self.giuseppe.user
        )
        self.login(self.antonia)
        self.client.post(
            reverse("admin_request_reject", args=[request_obj.pk]),
            {"reason": "Documentazione non conforme"},
        )
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, VoucherRequest.Status.REJECTED)
        allocation = self.allocation.__class__.objects.get(pk=self.allocation.pk)
        self.assertEqual(allocation.quantity_available, 10)

    def test_reject_without_reason_is_refused(self):
        request_obj = services.create_voucher_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        self.login(self.antonia)
        self.client.post(reverse("admin_request_reject", args=[request_obj.pk]), {"reason": ""})
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, VoucherRequest.Status.PENDING)

    def test_request_form_rejects_excessive_quantity(self):
        self.login(self.giuseppe)
        response = self.client.post(
            reverse("request_voucher", args=[self.allocation.pk]), {"quantity": 99}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "al massimo 10")
        self.assertEqual(VoucherRequest.objects.count(), 0)

    def test_allocation_form_blocks_over_budget(self):
        self.login(self.antonia)
        response = self.client.post(
            reverse("admin_allocate_employee", args=[self.giuseppe.pk]),
            {"employee": self.giuseppe.pk, "voucher_type": self.voucher_50.pk, "quantity": 100},
            follow=True,
        )
        self.assertContains(response, "supera il budget")
        self.assertFalse(
            self.giuseppe.allocations.filter(voucher_type=self.voucher_50).exists()
        )

    def test_direct_delivery_form_blocks_over_availability(self):
        self.login(self.antonia)
        response = self.client.post(
            reverse("admin_direct_delivery", args=[self.giuseppe.pk]),
            {"allocation": self.allocation.pk, "quantity": 50},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "al massimo 10")
        self.assertEqual(VoucherDelivery.objects.count(), 0)

    def test_direct_delivery_appears_in_employee_history(self):
        services.create_direct_delivery(
            allocation=self.allocation, quantity=2, actor=self.antonia.user
        )
        self.login(self.giuseppe)
        response = self.client.get(reverse("my_requests"))
        self.assertContains(response, "Consegna diretta dall'amministrazione")

    def test_catalog_shows_unassigned_voucher_without_request_button(self):
        self.login(self.giuseppe)
        response = self.client.get(reverse("catalog"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Non assegnato")
        self.assertContains(response, reverse("request_voucher", args=[self.allocation.pk]))

    def test_request_button_hidden_when_nothing_available(self):
        services.create_voucher_request(
            allocation=self.allocation, quantity=10, actor=self.giuseppe.user
        )
        self.login(self.giuseppe)
        response = self.client.get(reverse("my_vouchers"))
        self.assertNotContains(response, reverse("request_voucher", args=[self.allocation.pk]))

    def test_htmx_request_summary_computes_value(self):
        self.login(self.giuseppe)
        response = self.client.get(
            reverse("request_summary_partial", args=[self.allocation.pk]), {"quantity": "3"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "300,00")

    def test_htmx_allocation_summary_shows_allocatable_budget(self):
        self.login(self.antonia)
        response = self.client.get(
            reverse("admin_allocation_summary_partial"),
            {"employee": self.giuseppe.pk, "voucher_type": self.voucher_100.pk, "quantity": "2"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "200,00")
        self.assertContains(response, "5.000,00")

    def test_employee_cannot_use_admin_htmx_endpoints(self):
        self.login(self.giuseppe)
        self.assertEqual(
            self.client.get(reverse("admin_allocation_summary_partial")).status_code, 403
        )
        self.assertEqual(
            self.client.get(reverse("admin_delivery_summary_partial")).status_code, 403
        )

    def test_request_cannot_be_modified_by_employee(self):
        """Il dipendente non ha alcuna via per modificare o eliminare una richiesta."""
        request_obj = services.create_voucher_request(
            allocation=self.allocation, quantity=1, actor=self.giuseppe.user
        )
        self.login(self.giuseppe)
        url = reverse("request_detail", args=[request_obj.pk])
        detail = self.client.get(url)
        self.assertNotContains(detail, "Elimina")
        self.assertNotContains(detail, "Modifica richiesta")
        # Anche forzando una POST sulla pagina di dettaglio nulla cambia.
        self.client.post(url, {"quantity": 9, "status": "APPROVED"})
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.quantity, 1)
        self.assertEqual(request_obj.status, VoucherRequest.Status.PENDING)

    def test_inactive_voucher_type_cannot_be_requested(self):
        self.voucher_100.active = False
        self.voucher_100.save()
        self.login(self.giuseppe)
        response = self.client.post(
            reverse("request_voucher", args=[self.allocation.pk]), {"quantity": 1}, follow=True
        )
        self.assertContains(response, "non è più richiedibile")
        self.assertEqual(VoucherRequest.objects.count(), 0)


class SeedDemoCommandTests(TestCase):
    def test_seed_demo_creates_expected_scenario(self):
        from io import StringIO

        from django.core.management import call_command

        from welfare.models import EmployeeProfile, VoucherType, WelfareProgram
        from welfare.permissions import is_welfare_manager

        call_command("seed_demo", stdout=StringIO())

        program = WelfareProgram.objects.get(name="Piano Welfare")
        giuseppe = EmployeeProfile.objects.get(user__username="giuseppe")
        antonia = EmployeeProfile.objects.get(user__username="antonia")

        summary = giuseppe.welfare_summary(program)
        self.assertEqual(summary["budget_assigned"], Decimal("5000.00"))
        self.assertEqual(summary["budget_allocated"], Decimal("3670.00"))
        self.assertEqual(summary["budget_unallocated"], Decimal("1330.00"))

        self.assertTrue(is_welfare_manager(antonia.user))
        self.assertFalse(is_welfare_manager(giuseppe.user))
        # Il taglio da 50 € di Muraglia esiste a catalogo ma non è allocato a Giuseppe.
        muraglia_50 = VoucherType.objects.get(
            convention__name="Muraglia Srlrs", unit_value=Decimal("50.00")
        )
        self.assertFalse(giuseppe.allocations.filter(voucher_type=muraglia_50).exists())

        # Il comando è idempotente.
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(EmployeeProfile.objects.filter(user__username="giuseppe").count(), 1)
