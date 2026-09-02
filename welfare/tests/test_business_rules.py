"""Test delle regole di budget, allocazione, richieste e consegne."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from welfare import services
from welfare.models import VoucherDelivery, VoucherRequest
from welfare.tests.factories import (
    create_employee,
    create_program,
    create_voucher_type,
    setup_allocation,
)


class BudgetRulesTests(TestCase):
    def setUp(self):
        self.program = create_program()
        self.employee = create_employee("giuseppe")
        self.manager = create_employee("antonia", manager=True)
        self.voucher_100 = create_voucher_type(unit_value="100.00")

    def test_allocation_cannot_exceed_budget(self):
        """5. Non è possibile allocare voucher oltre il budget."""
        services.set_employee_budget(
            employee=self.employee,
            program=self.program,
            amount=Decimal("1000.00"),
            actor=self.manager.user,
        )
        with self.assertRaises(ValidationError):
            services.set_allocation_quantity(
                employee=self.employee,
                program=self.program,
                voucher_type=self.voucher_100,
                quantity=11,  # 1.100 € > 1.000 €
                actor=self.manager.user,
            )
        self.assertEqual(self.employee.allocated_value(self.program), Decimal("0.00"))

    def test_allocation_up_to_budget_is_allowed(self):
        services.set_employee_budget(
            employee=self.employee,
            program=self.program,
            amount=Decimal("1000.00"),
            actor=self.manager.user,
        )
        allocation = services.set_allocation_quantity(
            employee=self.employee,
            program=self.program,
            voucher_type=self.voucher_100,
            quantity=10,
            actor=self.manager.user,
        )
        self.assertEqual(allocation.allocated_value, Decimal("1000.00"))
        self.assertEqual(services.budget_unallocated(self.employee, self.program), Decimal("0.00"))

    def test_budget_cannot_go_below_allocated_value(self):
        """6. Non è possibile ridurre il budget sotto il valore già allocato."""
        setup_allocation(
            employee=self.employee,
            program=self.program,
            voucher_type=self.voucher_100,
            quantity=10,
            budget="5000.00",
            actor=self.manager.user,
        )
        with self.assertRaises(ValidationError):
            services.set_employee_budget(
                employee=self.employee,
                program=self.program,
                amount=Decimal("900.00"),
                actor=self.manager.user,
            )
        self.assertEqual(self.employee.budget_amount(self.program), Decimal("5000.00"))

    def test_budget_cannot_be_negative(self):
        with self.assertRaises(ValidationError):
            services.set_employee_budget(
                employee=self.employee,
                program=self.program,
                amount=Decimal("-1.00"),
                actor=self.manager.user,
            )

    def test_allocation_cannot_drop_below_consumed_quantity(self):
        """16. Non si possono rimuovere quantità già riservate o consegnate."""
        allocation = setup_allocation(
            employee=self.employee,
            program=self.program,
            voucher_type=self.voucher_100,
            quantity=10,
            actor=self.manager.user,
        )
        services.create_voucher_request(
            allocation=allocation, quantity=4, actor=self.employee.user
        )
        with self.assertRaises(ValidationError):
            services.set_allocation_quantity(
                employee=self.employee,
                program=self.program,
                voucher_type=self.voucher_100,
                quantity=3,
                actor=self.manager.user,
            )
        allocation.refresh_from_db()
        self.assertEqual(allocation.quantity_assigned, 10)

    def test_allocation_records_actor(self):
        """27. Le operazioni amministrative registrano attore e timestamp."""
        allocation = setup_allocation(
            employee=self.employee,
            program=self.program,
            voucher_type=self.voucher_100,
            quantity=2,
            actor=self.manager.user,
        )
        self.assertEqual(allocation.assigned_by, self.manager.user)
        self.assertEqual(allocation.updated_by, self.manager.user)
        self.assertIsNotNone(allocation.assigned_at)
        budget = self.employee.budget_for(self.program)
        self.assertEqual(budget.created_by, self.manager.user)
        self.assertEqual(budget.updated_by, self.manager.user)


class VoucherTypeRulesTests(TestCase):
    def setUp(self):
        self.program = create_program()
        self.employee = create_employee("giuseppe")
        self.manager = create_employee("antonia", manager=True)

    def test_unit_value_immutable_once_allocated(self):
        """15. Il valore unitario di un VoucherType già allocato non è modificabile."""
        voucher_type = create_voucher_type(unit_value="100.00")
        setup_allocation(
            employee=self.employee,
            program=self.program,
            voucher_type=voucher_type,
            quantity=1,
            actor=self.manager.user,
        )
        voucher_type.unit_value = Decimal("120.00")
        with self.assertRaises(ValidationError):
            voucher_type.save()
        voucher_type.refresh_from_db()
        self.assertEqual(voucher_type.unit_value, Decimal("100.00"))

    def test_unit_value_editable_when_not_allocated(self):
        voucher_type = create_voucher_type(unit_value="100.00")
        voucher_type.unit_value = Decimal("120.00")
        voucher_type.save()
        voucher_type.refresh_from_db()
        self.assertEqual(voucher_type.unit_value, Decimal("120.00"))

    def test_description_and_active_editable_when_allocated(self):
        voucher_type = create_voucher_type(unit_value="100.00")
        setup_allocation(
            employee=self.employee,
            program=self.program,
            voucher_type=voucher_type,
            quantity=1,
            actor=self.manager.user,
        )
        voucher_type.description = "Nuova descrizione"
        voucher_type.active = False
        voucher_type.save()
        voucher_type.refresh_from_db()
        self.assertEqual(voucher_type.description, "Nuova descrizione")
        self.assertFalse(voucher_type.active)

    def test_same_convention_can_have_two_values(self):
        first = create_voucher_type(unit_value="100.00")
        second = create_voucher_type(unit_value="50.00")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.convention, second.convention)


class RequestAndDeliveryTests(TestCase):
    def setUp(self):
        self.program = create_program()
        self.employee = create_employee("giuseppe")
        self.manager = create_employee("antonia", manager=True)
        self.voucher_type = create_voucher_type(unit_value="100.00")
        self.allocation = setup_allocation(
            employee=self.employee,
            program=self.program,
            voucher_type=self.voucher_type,
            quantity=10,
            actor=self.manager.user,
        )

    def _counters(self):
        allocation = self.allocation.__class__.objects.get(pk=self.allocation.pk)
        return allocation.counters

    def test_cannot_request_more_than_available(self):
        """7. Non è possibile richiedere più voucher di quelli disponibili."""
        with self.assertRaises(ValidationError):
            services.create_voucher_request(
                allocation=self.allocation, quantity=11, actor=self.employee.user
            )
        self.assertEqual(VoucherRequest.objects.count(), 0)

    def test_request_quantity_must_be_at_least_one(self):
        with self.assertRaises(ValidationError):
            services.create_voucher_request(
                allocation=self.allocation, quantity=0, actor=self.employee.user
            )

    def test_pending_request_reserves_quantities(self):
        """8. Una richiesta PENDING riserva immediatamente le quantità."""
        services.create_voucher_request(
            allocation=self.allocation, quantity=3, actor=self.employee.user
        )
        counters = self._counters()
        self.assertEqual(counters["pending"], 3)
        self.assertEqual(counters["available"], 7)
        self.assertEqual(counters["delivered"], 0)

    def test_rejected_request_releases_quantities(self):
        """9. Una richiesta REJECTED libera nuovamente i voucher."""
        request = services.create_voucher_request(
            allocation=self.allocation, quantity=3, actor=self.employee.user
        )
        services.reject_request(
            request=request, actor=self.manager.user, reason="Documentazione mancante"
        )
        counters = self._counters()
        self.assertEqual(counters["pending"], 0)
        self.assertEqual(counters["available"], 10)
        request.refresh_from_db()
        self.assertEqual(request.rejection_reason, "Documentazione mancante")
        self.assertEqual(request.processed_by, self.manager.user)
        self.assertIsNotNone(request.processed_at)

    def test_approved_request_keeps_reservation(self):
        """10. Una richiesta APPROVED continua a riservare i voucher."""
        request = services.create_voucher_request(
            allocation=self.allocation, quantity=3, actor=self.employee.user
        )
        services.approve_request(request=request, actor=self.manager.user)
        counters = self._counters()
        self.assertEqual(counters["pending"], 0)
        self.assertEqual(counters["approved_waiting_delivery"], 3)
        self.assertEqual(counters["available"], 7)

    def test_delivery_of_approved_request_updates_counters(self):
        """11. La consegna di una richiesta APPROVED produce i conteggi corretti."""
        request = services.create_voucher_request(
            allocation=self.allocation, quantity=3, actor=self.employee.user
        )
        services.approve_request(request=request, actor=self.manager.user)
        delivery = services.deliver_request(request=request, actor=self.manager.user)

        counters = self._counters()
        self.assertEqual(counters["approved_waiting_delivery"], 0)
        self.assertEqual(counters["delivered"], 3)
        self.assertEqual(counters["available"], 7)  # invariato: nessun doppio consumo
        self.assertEqual(delivery.quantity, 3)
        self.assertEqual(delivery.delivered_by, self.manager.user)
        self.assertFalse(delivery.is_direct)

    def test_cannot_deliver_same_request_twice(self):
        """12. Non è possibile consegnare due volte la stessa richiesta."""
        request = services.create_voucher_request(
            allocation=self.allocation, quantity=2, actor=self.employee.user
        )
        services.approve_request(request=request, actor=self.manager.user)
        services.deliver_request(request=request, actor=self.manager.user)
        with self.assertRaises(ValidationError):
            services.deliver_request(request=request, actor=self.manager.user)
        self.assertEqual(VoucherDelivery.objects.filter(request=request).count(), 1)

    def test_cannot_deliver_pending_request(self):
        request = services.create_voucher_request(
            allocation=self.allocation, quantity=2, actor=self.employee.user
        )
        with self.assertRaises(ValidationError):
            services.deliver_request(request=request, actor=self.manager.user)

    def test_cannot_approve_already_processed_request(self):
        request = services.create_voucher_request(
            allocation=self.allocation, quantity=2, actor=self.employee.user
        )
        services.reject_request(request=request, actor=self.manager.user, reason="No")
        with self.assertRaises(ValidationError):
            services.approve_request(request=request, actor=self.manager.user)

    def test_direct_delivery_reduces_availability(self):
        """13. La consegna diretta riduce correttamente la disponibilità."""
        delivery = services.create_direct_delivery(
            allocation=self.allocation,
            quantity=2,
            actor=self.manager.user,
            notes="Consegnati a mano",
        )
        counters = self._counters()
        self.assertTrue(delivery.is_direct)
        self.assertIsNone(delivery.request)
        self.assertEqual(counters["delivered"], 2)
        self.assertEqual(counters["available"], 8)
        self.assertEqual(delivery.notes, "Consegnati a mano")
        self.assertEqual(delivery.delivered_by, self.manager.user)

    def test_direct_delivery_cannot_exceed_availability(self):
        """14. Non è possibile una consegna diretta oltre la disponibilità."""
        services.create_voucher_request(
            allocation=self.allocation, quantity=9, actor=self.employee.user
        )
        with self.assertRaises(ValidationError):
            services.create_direct_delivery(
                allocation=self.allocation, quantity=2, actor=self.manager.user
            )
        self.assertEqual(VoucherDelivery.objects.count(), 0)

    def test_derived_quantities_and_amounts(self):
        """17. Quantità e importi derivati restituiscono i valori corretti."""
        pending = services.create_voucher_request(
            allocation=self.allocation, quantity=2, actor=self.employee.user
        )
        approved = services.create_voucher_request(
            allocation=self.allocation, quantity=1, actor=self.employee.user
        )
        services.approve_request(request=approved, actor=self.manager.user)
        delivered = services.create_voucher_request(
            allocation=self.allocation, quantity=1, actor=self.employee.user
        )
        services.approve_request(request=delivered, actor=self.manager.user)
        services.deliver_request(request=delivered, actor=self.manager.user)
        rejected = services.create_voucher_request(
            allocation=self.allocation, quantity=5, actor=self.employee.user
        )
        services.reject_request(request=rejected, actor=self.manager.user, reason="No")

        counters = self._counters()
        self.assertEqual(counters["assigned"], 10)
        self.assertEqual(counters["pending"], 2)
        self.assertEqual(counters["approved_waiting_delivery"], 1)
        self.assertEqual(counters["delivered"], 1)
        self.assertEqual(counters["available"], 6)

        # Gli stessi valori devono arrivare dalle annotazioni SQL.
        annotated = (
            self.allocation.__class__.objects.with_counters().get(pk=self.allocation.pk)
        )
        self.assertEqual(annotated.pending_qty, 2)
        self.assertEqual(annotated.approved_waiting_qty, 1)
        self.assertEqual(annotated.delivered_qty, 1)
        self.assertEqual(annotated.available_qty, 6)

        # Importi in Decimal.
        self.assertEqual(pending.total_value, Decimal("200.00"))
        self.assertEqual(self.allocation.allocated_value, Decimal("1000.00"))
        summary = self.employee.welfare_summary(self.program)
        self.assertEqual(summary["budget_assigned"], Decimal("5000.00"))
        self.assertEqual(summary["budget_allocated"], Decimal("1000.00"))
        self.assertEqual(summary["budget_unallocated"], Decimal("4000.00"))
        self.assertEqual(summary["delivered_value"], Decimal("100.00"))
        self.assertIsInstance(summary["budget_unallocated"], Decimal)

    def test_scenario_from_specification(self):
        """Scenario Giuseppe: 5.000 € di budget, 3.670 € allocati, 1.330 € liberi."""
        employee = create_employee("giuseppe2")
        program = self.program
        manager = self.manager.user
        services.set_employee_budget(
            employee=employee, program=program, amount=Decimal("5000.00"), actor=manager
        )
        muraglia_100 = create_voucher_type(unit_value="100.00")
        orodance = create_voucher_type(
            convention_name="OroDance", name="Abbonamento annuale", unit_value="440.00"
        )
        yoga = create_voucher_type(
            convention_name="Associazione Yoga", name="Singola lezione", unit_value="50.00"
        )
        for voucher_type, quantity in ((muraglia_100, 10), (orodance, 3), (yoga, 27)):
            services.set_allocation_quantity(
                employee=employee,
                program=program,
                voucher_type=voucher_type,
                quantity=quantity,
                actor=manager,
            )
        summary = employee.welfare_summary(program)
        self.assertEqual(summary["budget_allocated"], Decimal("3670.00"))
        self.assertEqual(summary["budget_unallocated"], Decimal("1330.00"))
