"""Test della rimozione dei dati dimostrativi."""

from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.test.utils import override_settings

from welfare import services
from welfare.models import (
    Convention,
    EmployeeBudget,
    EmployeeProfile,
    VoucherAllocation,
    VoucherDelivery,
    VoucherRequest,
    VoucherType,
    WelfareProgram,
)
from welfare.tests.factories import create_employee


def seed():
    call_command("seed_demo", stdout=StringIO())


def clear(**kwargs):
    out = StringIO()
    call_command("clear_demo_data", stdout=out, **kwargs)
    return out.getvalue()


class ClearDemoDataTests(TestCase):
    def test_removes_everything_created_by_seed_demo(self):
        seed()
        self.assertTrue(User.objects.filter(username="giuseppe").exists())

        clear(yes=True)

        for username in ("antonia", "giuseppe"):
            self.assertFalse(User.objects.filter(username=username).exists(), username)
        self.assertEqual(EmployeeProfile.objects.count(), 0)
        self.assertEqual(EmployeeBudget.objects.count(), 0)
        self.assertEqual(VoucherAllocation.objects.count(), 0)
        self.assertEqual(Convention.objects.count(), 0)
        self.assertEqual(VoucherType.objects.count(), 0)
        self.assertEqual(WelfareProgram.objects.count(), 0)

    def test_removes_requests_deliveries_and_attachments(self):
        seed()
        giuseppe = EmployeeProfile.objects.get(user__username="giuseppe")
        antonia = User.objects.get(username="antonia")
        allocation = giuseppe.allocations.first()
        request = services.create_voucher_request(
            allocation=allocation, quantity=1, actor=giuseppe.user
        )
        services.approve_request(request=request, actor=antonia)
        services.deliver_request(request=request, actor=antonia)
        services.create_direct_delivery(allocation=allocation, quantity=1, actor=antonia)
        self.assertTrue(VoucherRequest.objects.exists())

        clear(yes=True)

        self.assertEqual(VoucherRequest.objects.count(), 0)
        self.assertEqual(VoucherDelivery.objects.count(), 0)

    def test_requires_explicit_confirmation(self):
        seed()
        with self.assertRaises(CommandError):
            call_command("clear_demo_data", stdout=StringIO())
        self.assertTrue(User.objects.filter(username="giuseppe").exists())

    def test_dry_run_changes_nothing(self):
        seed()
        output = clear(dry_run=True)
        self.assertIn("SIMULAZIONE", output)
        self.assertIn("giuseppe", output)
        self.assertTrue(User.objects.filter(username="giuseppe").exists())
        self.assertTrue(WelfareProgram.objects.filter(name="Piano Welfare").exists())
        self.assertEqual(VoucherAllocation.objects.filter(employee__user__username="giuseppe").count(), 3)

    def test_dry_run_predicts_the_real_outcome(self):
        """La simulazione deve dire esattamente ciò che poi accade davvero."""
        seed()
        program = WelfareProgram.objects.get(name="Piano Welfare")
        real = create_employee("mario")
        admin = User.objects.create_superuser("root", "root@example.com", "x")
        voucher_type = VoucherType.objects.filter(convention__name="OroDance").first()
        services.set_employee_budget(
            employee=real, program=program, amount=Decimal("500.00"), actor=admin
        )
        services.set_allocation_quantity(
            employee=real, program=program, voucher_type=voucher_type, quantity=1, actor=admin
        )

        def significant(output: str) -> list[str]:
            return [
                line.strip()
                for line in output.splitlines()
                if line.strip() and "SIMULAZIONE" not in line
            ]

        preview = clear(dry_run=True)
        executed = clear(yes=True)
        self.assertEqual(significant(preview), significant(executed))
        # Le convenzioni non usate da dipendenti reali erano annunciate come eliminate.
        self.assertIn("convenzione «Muraglia Srlrs»", preview)
        self.assertNotIn("«Muraglia Srlrs» mantenuta", preview)
        self.assertFalse(Convention.objects.filter(name="Muraglia Srlrs").exists())
        self.assertTrue(Convention.objects.filter(name="OroDance").exists())

    def test_is_idempotent(self):
        seed()
        clear(yes=True)
        clear(yes=True)  # non deve sollevare nulla
        self.assertEqual(User.objects.count(), 0)

    def test_works_on_an_empty_database(self):
        clear(yes=True)
        self.assertEqual(User.objects.count(), 0)


class SafetyTests(TestCase):
    """Il comando non deve mai portarsi via dati reali."""

    def test_never_deletes_a_superuser(self):
        seed()
        antonia = User.objects.get(username="antonia")
        antonia.is_superuser = True
        antonia.is_staff = True
        antonia.save()

        output = clear(yes=True)

        self.assertIn("superuser", output)
        self.assertTrue(User.objects.filter(username="antonia").exists())
        self.assertFalse(User.objects.filter(username="giuseppe").exists())

    def test_keeps_conventions_used_by_real_employees(self):
        seed()
        program = WelfareProgram.objects.get(name="Piano Welfare")
        real = create_employee("mario")
        voucher_type = VoucherType.objects.filter(convention__name="Muraglia Srlrs").first()
        admin = User.objects.create_superuser("root", "root@example.com", "x")
        services.set_employee_budget(
            employee=real, program=program, amount=Decimal("500.00"), actor=admin
        )
        services.set_allocation_quantity(
            employee=real, program=program, voucher_type=voucher_type, quantity=2, actor=admin
        )

        output = clear(yes=True)

        self.assertIn("mantenuta", output)
        self.assertTrue(Convention.objects.filter(name="Muraglia Srlrs").exists())
        self.assertTrue(VoucherType.objects.filter(pk=voucher_type.pk).exists())
        # Il dipendente reale e i suoi dati restano intatti.
        self.assertTrue(EmployeeProfile.objects.filter(user__username="mario").exists())
        self.assertEqual(
            VoucherAllocation.objects.filter(employee=real).count(), 1
        )
        # Gli utenti demo però se ne sono andati.
        self.assertFalse(User.objects.filter(username="giuseppe").exists())

    def test_keeps_program_used_by_real_employees(self):
        seed()
        program = WelfareProgram.objects.get(name="Piano Welfare")
        real = create_employee("mario")
        admin = User.objects.create_superuser("root", "root@example.com", "x")
        services.set_employee_budget(
            employee=real, program=program, amount=Decimal("300.00"), actor=admin
        )

        output = clear(yes=True)

        self.assertIn("mantenuto", output)
        self.assertTrue(WelfareProgram.objects.filter(name="Piano Welfare").exists())
        self.assertEqual(EmployeeBudget.objects.filter(employee=real).count(), 1)

    def test_does_not_touch_users_outside_the_demo_list(self):
        seed()
        create_employee("mario")
        clear(yes=True)
        self.assertTrue(User.objects.filter(username="mario").exists())
        self.assertTrue(EmployeeProfile.objects.filter(user__username="mario").exists())


class EnvironmentTriggerTests(TestCase):
    """Attivazione da variabile d'ambiente, per le piattaforme senza shell."""

    def test_does_nothing_without_the_flag(self):
        seed()
        call_command("clear_demo_data", "--if-requested", stdout=StringIO())
        self.assertTrue(User.objects.filter(username="giuseppe").exists())

    def test_runs_with_the_flag(self):
        seed()
        with override_settings():
            import os

            os.environ["CLEAR_DEMO_DATA"] = "true"
            try:
                call_command("clear_demo_data", "--if-requested", stdout=StringIO())
            finally:
                del os.environ["CLEAR_DEMO_DATA"]
        self.assertFalse(User.objects.filter(username="giuseppe").exists())

    def test_refuses_when_seed_demo_is_still_active(self):
        import os

        seed()
        os.environ["CLEAR_DEMO_DATA"] = "true"
        os.environ["SEED_DEMO"] = "true"
        try:
            with self.assertRaises(CommandError) as ctx:
                call_command("clear_demo_data", "--if-requested", stdout=StringIO())
            self.assertIn("SEED_DEMO", str(ctx.exception))
        finally:
            del os.environ["CLEAR_DEMO_DATA"]
            del os.environ["SEED_DEMO"]
        self.assertTrue(User.objects.filter(username="giuseppe").exists())
