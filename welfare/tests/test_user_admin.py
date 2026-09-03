"""Test della gestione utenti dal frontend (area riservata allo staff)."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from welfare import services
from welfare.models import EmployeeProfile
from welfare.permissions import WELFARE_MANAGERS_GROUP, can_manage_user, is_welfare_manager
from welfare.tests.factories import PASSWORD, create_employee, create_user

STRONG_PASSWORD = "Welfare!2026#ok"


class UserAdminAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = create_user("staff", is_staff=True)
        cls.manager = create_employee("antonia", manager=True)  # manager ma non staff
        cls.employee = create_employee("giuseppe")

    URLS = ["admin_users", "admin_user_create"]

    def login(self, user):
        username = getattr(user, "username", None) or user.user.username
        self.assertTrue(self.client.login(username=username, password=PASSWORD))

    def test_staff_can_open_user_management(self):
        self.login(self.staff)
        for name in self.URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_welfare_manager_without_staff_cannot(self):
        """La gestione utenti è riservata allo staff, non ai Welfare Manager."""
        self.login(self.manager)
        for name in self.URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_plain_employee_cannot(self):
        self.login(self.employee)
        for name in self.URLS:
            with self.subTest(url=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        target = self.employee.user
        self.assertEqual(
            self.client.get(reverse("admin_user_edit", args=[target.pk])).status_code, 403
        )
        self.assertEqual(
            self.client.post(
                reverse("admin_user_password", args=[target.pk]),
                {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
            ).status_code,
            403,
        )

    def test_anonymous_is_redirected(self):
        response = self.client.get(reverse("admin_users"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_nav_shows_users_only_to_staff(self):
        self.login(self.manager)
        response = self.client.get(reverse("admin_dashboard"))
        self.assertNotContains(response, reverse("admin_users"))

        self.client.logout()
        staff_manager = create_user("capo", is_staff=True)
        staff_manager.groups.add(self.manager.user.groups.get(name=WELFARE_MANAGERS_GROUP))
        self.client.login(username="capo", password=PASSWORD)
        response = self.client.get(reverse("admin_dashboard"))
        self.assertContains(response, reverse("admin_users"))


class UserCreationTests(TestCase):
    def setUp(self):
        self.staff = create_user("staff", is_staff=True)
        self.client.login(username="staff", password=PASSWORD)

    def _payload(self, **overrides):
        data = {
            "username": "mario",
            "first_name": "Mario",
            "last_name": "Bianchi",
            "email": "mario@example.com",
            "password1": STRONG_PASSWORD,
            "password2": STRONG_PASSWORD,
            "is_active": "on",
        }
        data.update(overrides)
        return data

    def test_create_plain_employee(self):
        response = self.client.post(
            reverse("admin_user_create"),
            self._payload(has_employee_profile="on", employee_code="EMP99"),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username="mario")
        self.assertTrue(user.check_password(STRONG_PASSWORD))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(is_welfare_manager(user))
        profile = EmployeeProfile.objects.get(user=user)
        self.assertTrue(profile.active)
        self.assertEqual(profile.employee_code, "EMP99")

    def test_created_user_can_log_in(self):
        self.client.post(reverse("admin_user_create"), self._payload(has_employee_profile="on"))
        self.client.logout()
        self.assertTrue(self.client.login(username="mario", password=STRONG_PASSWORD))
        self.assertEqual(self.client.get(reverse("my_vouchers")).status_code, 200)

    def test_create_welfare_manager_with_profile(self):
        self.client.post(
            reverse("admin_user_create"),
            self._payload(username="lucia", is_welfare_manager="on", has_employee_profile="on"),
        )
        user = User.objects.get(username="lucia")
        self.assertTrue(is_welfare_manager(user))
        self.assertTrue(EmployeeProfile.objects.filter(user=user, active=True).exists())

    def test_password_mismatch_is_rejected(self):
        response = self.client.post(
            reverse("admin_user_create"), self._payload(password2="altra-password")
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "non coincidono")
        self.assertFalse(User.objects.filter(username="mario").exists())

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            reverse("admin_user_create"), self._payload(password1="1234", password2="1234")
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="mario").exists())

    def test_duplicate_username_is_rejected(self):
        response = self.client.post(reverse("admin_user_create"), self._payload(username="STAFF"))
        self.assertContains(response, "Esiste già un utente")
        self.assertEqual(User.objects.filter(username__iexact="staff").count(), 1)

    def test_duplicate_employee_code_is_rejected(self):
        create_employee("mario2")  # matricola MARIO2
        response = self.client.post(
            reverse("admin_user_create"),
            self._payload(has_employee_profile="on", employee_code="MARIO2"),
        )
        self.assertContains(response, "matricola è già assegnata")
        self.assertFalse(User.objects.filter(username="mario").exists())

    def test_non_superuser_staff_cannot_create_superuser(self):
        response = self.client.post(
            reverse("admin_user_create"), self._payload(is_superuser="on")
        )
        # Il campo non esiste per chi non è superuser: viene semplicemente ignorato.
        self.assertIn(response.status_code, (200, 302))
        user = User.objects.get(username="mario")
        self.assertFalse(user.is_superuser)


class UserUpdateTests(TestCase):
    def setUp(self):
        self.staff = create_user("staff", is_staff=True)
        self.target = create_employee("giuseppe")
        self.client.login(username="staff", password=PASSWORD)

    def _edit(self, **overrides):
        data = {
            "username": self.target.user.username,
            "first_name": "Giuseppe",
            "last_name": "Verdi",
            "email": "giuseppe@example.com",
            "is_active": "on",
            "has_employee_profile": "on",
        }
        data.update(overrides)
        return self.client.post(
            reverse("admin_user_edit", args=[self.target.user.pk]), data, follow=True
        )

    def test_update_personal_data(self):
        self._edit(first_name="Giuseppino")
        self.target.user.refresh_from_db()
        self.assertEqual(self.target.user.first_name, "Giuseppino")

    def test_promote_to_welfare_manager(self):
        self._edit(is_welfare_manager="on")
        self.target.user.refresh_from_db()
        self.assertTrue(is_welfare_manager(self.target.user))
        self.client.logout()
        self.client.login(username="giuseppe", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)

    def test_revoke_welfare_manager(self):
        services.set_welfare_manager(user=self.target.user, enabled=True)
        self._edit()  # senza la spunta
        self.target.user.refresh_from_db()
        self.assertFalse(is_welfare_manager(self.target.user))

    def test_deactivating_employee_profile_blocks_employee_area(self):
        self._edit(has_employee_profile="")
        profile = EmployeeProfile.objects.get(user=self.target.user)
        self.assertFalse(profile.active)
        self.client.logout()
        self.client.login(username="giuseppe", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("my_vouchers")).status_code, 403)

    def test_profile_is_never_deleted(self):
        self._edit(has_employee_profile="")
        self.assertTrue(EmployeeProfile.objects.filter(user=self.target.user).exists())

    def test_set_password_from_frontend(self):
        response = self.client.post(
            reverse("admin_user_password", args=[self.target.user.pk]),
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.client.logout()
        self.assertTrue(self.client.login(username="giuseppe", password=STRONG_PASSWORD))

    def test_deactivate_and_reactivate_account(self):
        url = reverse("admin_user_toggle_active", args=[self.target.user.pk])
        self.client.post(url, {"active": "0"}, follow=True)
        self.target.user.refresh_from_db()
        self.assertFalse(self.target.user.is_active)
        self.assertFalse(EmployeeProfile.objects.get(user=self.target.user).active)
        self.client.logout()
        self.assertFalse(self.client.login(username="giuseppe", password=PASSWORD))

        self.client.login(username="staff", password=PASSWORD)
        self.client.post(url, {"active": "1"}, follow=True)
        self.target.user.refresh_from_db()
        self.assertTrue(self.target.user.is_active)

    def test_toggle_requires_post(self):
        response = self.client.get(reverse("admin_user_toggle_active", args=[self.target.user.pk]))
        self.assertEqual(response.status_code, 405)


class PrivilegeEscalationTests(TestCase):
    def setUp(self):
        self.staff = create_user("staff", is_staff=True)
        self.root = User.objects.create_superuser("root", "root@example.com", PASSWORD)

    def test_staff_cannot_edit_a_superuser(self):
        """Impedisce a uno staff di impossessarsi di un account superuser."""
        self.client.login(username="staff", password=PASSWORD)
        self.assertFalse(can_manage_user(self.staff, self.root))
        self.assertEqual(
            self.client.get(reverse("admin_user_edit", args=[self.root.pk])).status_code, 403
        )
        response = self.client.post(
            reverse("admin_user_password", args=[self.root.pk]),
            {"password1": STRONG_PASSWORD, "password2": STRONG_PASSWORD},
        )
        self.assertEqual(response.status_code, 403)
        self.root.refresh_from_db()
        self.assertTrue(self.root.check_password(PASSWORD))

    def test_staff_cannot_grant_superuser_via_service(self):
        with self.assertRaises(Exception):
            services.create_user_account(
                actor=self.staff,
                username="cattivo",
                password=STRONG_PASSWORD,
                is_superuser=True,
            )
        self.assertFalse(User.objects.filter(username="cattivo").exists())

    def test_superuser_can_manage_everyone(self):
        self.client.login(username="root", password=PASSWORD)
        self.assertEqual(
            self.client.get(reverse("admin_user_edit", args=[self.staff.pk])).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("admin_user_create")).status_code, 200
        )

    def test_cannot_lock_yourself_out(self):
        self.client.login(username="staff", password=PASSWORD)
        self.client.post(
            reverse("admin_user_edit", args=[self.staff.pk]),
            {"username": "staff", "first_name": "", "last_name": "", "email": ""},
            follow=True,
        )
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active, "l'account non deve potersi disattivare da solo")
        self.assertTrue(self.staff.is_staff, "i privilegi di staff non si tolgono da soli")

    def test_cannot_deactivate_own_account(self):
        self.client.login(username="staff", password=PASSWORD)
        self.client.post(
            reverse("admin_user_toggle_active", args=[self.staff.pk]), {"active": "0"}, follow=True
        )
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
