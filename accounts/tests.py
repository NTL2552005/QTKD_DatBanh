import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class RegistrationTests(TestCase):
    def registration_data(self, **overrides):
        data = {
            "username": "new_customer",
            "email": "customer@example.com",
            "first_name": "Nguyen",
            "last_name": "An",
            "password1": "A-strong-demo-password-983!",
            "password2": "A-strong-demo-password-983!",
        }
        data.update(overrides)
        return data

    def test_registration_creates_customer_with_hashed_password(self):
        response = self.client.post(
            reverse("accounts:register"), self.registration_data()
        )

        self.assertRedirects(response, reverse("accounts:customer_dashboard"))
        user = User.objects.get(username="new_customer")
        self.assertEqual(user.role, User.Role.CUSTOMER)
        self.assertNotEqual(user.password, "A-strong-demo-password-983!")
        self.assertTrue(user.check_password("A-strong-demo-password-983!"))

    def test_registration_ignores_submitted_owner_role(self):
        response = self.client.post(
            reverse("accounts:register"),
            self.registration_data(role=User.Role.OWNER),
        )

        self.assertRedirects(response, reverse("accounts:customer_dashboard"))
        self.assertEqual(
            User.objects.get(username="new_customer").role,
            User.Role.CUSTOMER,
        )


class AuthenticationAndRoleTests(TestCase):
    password = "A-strong-demo-password-983!"

    def create_role_user(self, username, role):
        return User.objects.create_user(
            username=username,
            password=self.password,
            role=role,
        )

    def test_login_accepts_correct_password_and_rejects_wrong_password(self):
        self.create_role_user("customer", User.Role.CUSTOMER)

        success = self.client.post(
            reverse("accounts:login"),
            {"username": "customer", "password": self.password},
        )
        self.assertRedirects(success, reverse("accounts:customer_dashboard"))

        self.client.logout()
        failure = self.client.post(
            reverse("accounts:login"),
            {"username": "customer", "password": "incorrect"},
        )
        self.assertEqual(failure.status_code, 200)
        self.assertContains(failure, "Tên đăng nhập hoặc mật khẩu không đúng.")

    def test_each_role_redirects_to_its_dashboard_after_login(self):
        role_routes = (
            (User.Role.CUSTOMER, "customer_dashboard"),
            (User.Role.STAFF, "staff_dashboard"),
            (User.Role.OWNER, "owner_dashboard"),
        )
        for index, (role, route_name) in enumerate(role_routes):
            with self.subTest(role=role):
                username = f"user_{index}"
                self.create_role_user(username, role)
                response = self.client.post(
                    reverse("accounts:login"),
                    {"username": username, "password": self.password},
                )
                self.assertRedirects(response, reverse(f"accounts:{route_name}"))
                self.client.logout()

    def test_each_role_can_render_its_own_dashboard(self):
        role_routes = (
            (User.Role.CUSTOMER, "customer_dashboard", "Khách hàng"),
            (User.Role.STAFF, "staff_dashboard", "Nhân viên"),
            (User.Role.OWNER, "owner_dashboard", "Chủ cửa hàng"),
        )
        for index, (role, route_name, role_label) in enumerate(role_routes):
            with self.subTest(role=role):
                user = self.create_role_user(f"dashboard_user_{index}", role)
                self.client.force_login(user)

                response = self.client.get(reverse(f"accounts:{route_name}"))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, role_label)
                if role == User.Role.OWNER:
                    self.assertContains(response, "Quản lý danh mục")
                    self.assertContains(response, "0 tổng")
                elif role == User.Role.CUSTOMER:
                    self.assertContains(response, "Bạn chưa có đơn hàng")
                    self.assertContains(response, "Khám phá mẫu bánh")
                else:
                    self.assertContains(response, "Chưa có đơn cần xử lý")
                self.client.logout()

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(reverse("accounts:customer_dashboard"))

        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('accounts:customer_dashboard')}",
        )

    def test_wrong_role_receives_forbidden_response(self):
        self.create_role_user("staff", User.Role.STAFF)
        self.client.force_login(User.objects.get(username="staff"))

        response = self.client.get(reverse("accounts:customer_dashboard"))

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403.html")

    def test_logout_requires_post(self):
        user = self.create_role_user("customer", User.Role.CUSTOMER)
        self.client.force_login(user)

        response = self.client.get(reverse("accounts:logout"))

        self.assertEqual(response.status_code, 405)

    def test_logout_post_requires_csrf_token(self):
        user = self.create_role_user("customer", User.Role.CUSTOMER)
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(user)

        rejected = csrf_client.post(reverse("accounts:logout"))
        self.assertEqual(rejected.status_code, 403)

        csrf_client.get(reverse("home"))
        token = csrf_client.cookies["csrftoken"].value
        accepted = csrf_client.post(
            reverse("accounts:logout"),
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertRedirects(accepted, reverse("home"))
        self.assertNotIn("_auth_user_id", csrf_client.session)

    def test_unsafe_next_url_is_ignored(self):
        self.create_role_user("customer", User.Role.CUSTOMER)

        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "customer",
                "password": self.password,
                "next": "https://example.invalid/",
            },
        )

        self.assertRedirects(response, reverse("accounts:customer_dashboard"))


class SeedDemoCommandTests(TestCase):
    @patch.dict(os.environ, {"DEMO_PASSWORD": "A-strong-demo-password-983!"})
    def test_command_is_idempotent_and_preserves_existing_password(self):
        existing = User.objects.create_user(
            username="staff_demo",
            password="Original-password-123!",
            role=User.Role.STAFF,
        )

        call_command("seed_demo")
        call_command("seed_demo")

        self.assertEqual(User.objects.filter(username__endswith="_demo").count(), 3)
        existing.refresh_from_db()
        self.assertTrue(existing.check_password("Original-password-123!"))
        self.assertFalse(existing.check_password("A-strong-demo-password-983!"))
        owner = User.objects.get(username="owner_demo")
        self.assertEqual(owner.role, User.Role.OWNER)
        self.assertFalse(owner.is_superuser)
        self.assertFalse(owner.is_staff)

    @patch.dict(os.environ, {}, clear=True)
    def test_command_requires_demo_password(self):
        with self.assertRaisesMessage(CommandError, "DEMO_PASSWORD"):
            call_command("seed_demo")
