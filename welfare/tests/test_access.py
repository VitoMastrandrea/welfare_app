"""Test di isolamento dei dati, permessi e accesso agli allegati."""

import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from welfare import services
from welfare.models import EmployeeProfile, RequestAttachment, VoucherRequest
from welfare.permissions import is_welfare_manager
from welfare.tests.factories import (
    PASSWORD,
    create_employee,
    create_program,
    create_voucher_type,
    setup_allocation,
)

TEMP_MEDIA = tempfile.mkdtemp(prefix="welfare-test-media-")

TEST_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": TEMP_MEDIA},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class AccessTestCase(TestCase):
    """Base con due dipendenti, un Welfare Manager e le relative allocazioni."""

    @classmethod
    def setUpTestData(cls):
        cls.program = create_program()
        cls.giuseppe = create_employee("giuseppe")
        cls.mario = create_employee("mario")
        cls.antonia = create_employee("antonia", manager=True)
        cls.voucher_type = create_voucher_type(unit_value="100.00")

        cls.giuseppe_allocation = setup_allocation(
            employee=cls.giuseppe,
            program=cls.program,
            voucher_type=cls.voucher_type,
            quantity=10,
            actor=cls.antonia.user,
        )
        cls.mario_allocation = setup_allocation(
            employee=cls.mario,
            program=cls.program,
            voucher_type=cls.voucher_type,
            quantity=4,
            budget="1000.00",
            actor=cls.antonia.user,
        )
        cls.giuseppe_request = services.create_voucher_request(
            allocation=cls.giuseppe_allocation, quantity=2, actor=cls.giuseppe.user
        )

    def login(self, employee):
        self.assertTrue(
            self.client.login(username=employee.user.username, password=PASSWORD)
        )


class DataIsolationTests(AccessTestCase):
    def test_employee_cannot_see_other_employee_request(self):
        """1. Un dipendente non può vedere dati di altri dipendenti."""
        self.login(self.mario)
        response = self.client.get(
            reverse("request_detail", args=[self.giuseppe_request.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_employee_cannot_request_other_employee_allocation(self):
        """Nemmeno modificando la URL si può usare l'allocazione altrui."""
        self.login(self.mario)
        url = reverse("request_voucher", args=[self.giuseppe_allocation.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        response = self.client.post(url, {"quantity": 1})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            VoucherRequest.objects.filter(allocation=self.giuseppe_allocation).count(), 1
        )

    def test_employee_only_sees_own_data_in_dashboard(self):
        self.login(self.mario)
        response = self.client.get(reverse("my_vouchers"))
        self.assertEqual(response.status_code, 200)
        allocations = list(response.context["allocations"])
        self.assertEqual([a.pk for a in allocations], [self.mario_allocation.pk])

    def test_employee_summary_shows_own_budget(self):
        self.login(self.mario)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["summary"]["budget_assigned"], Decimal("1000.00"))

    def test_htmx_summary_of_other_allocation_is_denied(self):
        self.login(self.mario)
        response = self.client.get(
            reverse("request_summary_partial", args=[self.giuseppe_allocation.pk]),
            {"quantity": 1},
        )
        self.assertEqual(response.status_code, 404)


class AdminAreaPermissionTests(AccessTestCase):
    ADMIN_URLS = [
        ("admin_dashboard", []),
        ("admin_employees", []),
        ("admin_allocations", []),
        ("admin_requests", []),
        ("admin_deliveries", []),
        ("admin_conventions", []),
        ("admin_programs", []),
        ("admin_allocate", []),
    ]

    def test_plain_employee_cannot_access_admin_area(self):
        """2. Un normale dipendente non può accedere alle funzioni amministrative."""
        self.login(self.giuseppe)
        for name, args in self.ADMIN_URLS:
            with self.subTest(url=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 403)

        detail_urls = [
            reverse("admin_employee_detail", args=[self.mario.pk]),
            reverse("admin_employee_budget", args=[self.mario.pk]),
            reverse("admin_allocate_employee", args=[self.mario.pk]),
            reverse("admin_direct_delivery", args=[self.mario.pk]),
            reverse("admin_request_detail", args=[self.giuseppe_request.pk]),
        ]
        for url in detail_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_plain_employee_cannot_post_admin_actions(self):
        self.login(self.giuseppe)
        for name in ("admin_request_approve", "admin_request_reject", "admin_request_deliver"):
            with self.subTest(action=name):
                response = self.client.post(
                    reverse(name, args=[self.giuseppe_request.pk]), {"reason": "x"}
                )
                self.assertEqual(response.status_code, 403)
        self.giuseppe_request.refresh_from_db()
        self.assertEqual(self.giuseppe_request.status, VoucherRequest.Status.PENDING)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_manager_can_access_admin_area(self):
        self.login(self.antonia)
        for name, args in self.ADMIN_URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)


class ManagerIsAlsoEmployeeTests(AccessTestCase):
    def test_manager_can_have_employee_profile(self):
        """3. Un Welfare Manager può essere contemporaneamente EmployeeProfile."""
        self.assertTrue(is_welfare_manager(self.antonia.user))
        self.assertIsInstance(self.antonia, EmployeeProfile)
        self.login(self.antonia)
        employee_area = self.client.get(reverse("dashboard"))
        admin_area = self.client.get(reverse("admin_dashboard"))
        self.assertEqual(employee_area.status_code, 200)
        self.assertEqual(admin_area.status_code, 200)
        self.assertTrue(employee_area.context["is_welfare_manager"])
        self.assertIsNotNone(employee_area.context["employee_profile"])

    def test_no_user_type_field_on_profile(self):
        self.assertFalse(hasattr(EmployeeProfile, "user_type"))

    def test_manager_can_administer_own_position_with_audit(self):
        """4. Un Welfare Manager può amministrare la propria posizione, tracciata."""
        self.login(self.antonia)
        response = self.client.post(
            reverse("admin_employee_budget", args=[self.antonia.pk]),
            {"amount": "2000.00"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        budget = self.antonia.budget_for(self.program)
        self.assertEqual(budget.amount, Decimal("2000.00"))
        self.assertEqual(budget.updated_by, self.antonia.user)

        response = self.client.post(
            reverse("admin_allocate_employee", args=[self.antonia.pk]),
            {"employee": self.antonia.pk, "voucher_type": self.voucher_type.pk, "quantity": 3},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        allocation = self.antonia.allocations.get(voucher_type=self.voucher_type)
        self.assertEqual(allocation.quantity_assigned, 3)
        self.assertEqual(allocation.assigned_by, self.antonia.user)

        # E la consegna diretta a sé stessa resta comunque tracciata.
        response = self.client.post(
            reverse("admin_direct_delivery", args=[self.antonia.pk]),
            {"allocation": allocation.pk, "quantity": 1, "notes": "Autoconsegna"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        delivery = allocation.deliveries.get()
        self.assertEqual(delivery.delivered_by, self.antonia.user)
        self.assertTrue(delivery.is_direct)


@override_settings(STORAGES=TEST_STORAGES, MEDIA_ROOT=TEMP_MEDIA)
class AttachmentAccessTests(AccessTestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA, ignore_errors=True)

    def _create_attachment(self):
        return RequestAttachment.objects.create(
            request=self.giuseppe_request,
            file=SimpleUploadedFile("riservato.txt", b"contenuto riservato"),
            original_filename="riservato.txt",
            uploaded_by=self.giuseppe.user,
        )

    def test_owner_can_download_attachment(self):
        attachment = self._create_attachment()
        self.login(self.giuseppe)
        response = self.client.get(reverse("attachment_download", args=[attachment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"contenuto riservato")

    def test_other_employee_cannot_download_attachment(self):
        """16. Gli allegati non sono accessibili da utenti non autorizzati."""
        attachment = self._create_attachment()
        self.login(self.mario)
        response = self.client.get(reverse("attachment_download", args=[attachment.pk]))
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_download_attachment(self):
        attachment = self._create_attachment()
        response = self.client.get(reverse("attachment_download", args=[attachment.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_manager_can_download_attachment(self):
        attachment = self._create_attachment()
        self.login(self.antonia)
        response = self.client.get(reverse("attachment_download", args=[attachment.pk]))
        self.assertEqual(response.status_code, 200)

    def test_attachment_path_is_not_guessable(self):
        attachment = self._create_attachment()
        self.assertNotIn("riservato", attachment.file.name)
        self.assertTrue(attachment.file.name.startswith("attachments/"))

    def test_upload_via_request_form_stores_attachment(self):
        self.login(self.giuseppe)
        upload = SimpleUploadedFile("documento.pdf", b"%PDF-1.4 finto", content_type="application/pdf")
        response = self.client.post(
            reverse("request_voucher", args=[self.giuseppe_allocation.pk]),
            {"quantity": 1, "attachments": [upload]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        new_request = (
            VoucherRequest.objects.filter(allocation=self.giuseppe_allocation)
            .order_by("-pk")
            .first()
        )
        attachment = new_request.attachments.get()
        self.assertEqual(attachment.original_filename, "documento.pdf")
        self.assertEqual(attachment.uploaded_by, self.giuseppe.user)

    def test_rejects_disallowed_extension(self):
        self.login(self.giuseppe)
        upload = SimpleUploadedFile("script.exe", b"MZ", content_type="application/octet-stream")
        response = self.client.post(
            reverse("request_voucher", args=[self.giuseppe_allocation.pk]),
            {"quantity": 1, "attachments": [upload]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "non ammessa")


class SuperuserAndProfileTests(TestCase):
    def test_superuser_without_profile_is_redirected_to_admin_area(self):
        User.objects.create_superuser("root", "root@example.com", PASSWORD)
        self.client.login(username="root", password=PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, reverse("admin_dashboard"))

    def test_user_without_profile_and_without_permission_gets_notice(self):
        User.objects.create_user("nessuno", password=PASSWORD)
        self.client.login(username="nessuno", password=PASSWORD)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "profilo dipendente", status_code=403)

    def test_health_endpoint(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
