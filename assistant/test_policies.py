from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase
from django.urls import reverse

from .models import StorePolicy

User = get_user_model()


class StorePolicyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="policy_owner",
            password="test-password",
            role=User.Role.OWNER,
        )
        self.customer = User.objects.create_user(
            username="policy_customer",
            password="test-password",
            role=User.Role.CUSTOMER,
        )
        self.staff = User.objects.create_user(
            username="policy_staff",
            password="test-password",
            role=User.Role.STAFF,
        )
        self.published_policy = StorePolicy.objects.create(
            code="giao-nhan",
            title="Thông tin giao nhận",
            content="Cửa hàng xác nhận thời gian giao theo từng yêu cầu.",
            is_published=True,
            created_by=self.owner,
        )
        self.draft_policy = StorePolicy.objects.create(
            code="ban-nhap",
            title="Nội dung chưa công bố",
            content="Nội dung cần được rà soát.",
            created_by=self.owner,
        )

    def test_public_policy_list_only_shows_published_policies(self):
        response = self.client.get(reverse("assistant:policy_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.published_policy.title)
        self.assertNotContains(response, self.draft_policy.title)

    def test_published_policy_is_public_and_draft_is_hidden(self):
        public_url = reverse(
            "assistant:policy_detail",
            kwargs={"code": self.published_policy.code},
        )
        draft_url = reverse(
            "assistant:policy_detail",
            kwargs={"code": self.draft_policy.code},
        )

        self.assertEqual(self.client.get(public_url).status_code, 200)
        self.assertEqual(self.client.get(draft_url).status_code, 404)

    def test_chatbot_routes_are_removed(self):
        old_paths = (
            "/assistant/",
            "/assistant/start/",
            "/assistant/00000000-0000-0000-0000-000000000000/",
        )

        for path in old_paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_owner_policy_management_requires_owner_role(self):
        url = reverse("assistant:owner_policy_list")
        self.assertEqual(self.client.get(url).status_code, 302)

        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_policy_publish_toggle_is_post_only(self):
        self.client.force_login(self.owner)
        url = reverse(
            "assistant:owner_policy_toggle",
            kwargs={"policy_id": self.published_policy.pk},
        )

        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(
            StorePolicy.objects.get(pk=self.published_policy.pk).is_published
        )

        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            StorePolicy.objects.get(pk=self.published_policy.pk).is_published
        )

    def test_policy_publish_toggle_enforces_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        url = reverse(
            "assistant:owner_policy_toggle",
            kwargs={"policy_id": self.draft_policy.pk},
        )

        self.assertEqual(csrf_client.post(url).status_code, 403)
        self.assertFalse(
            StorePolicy.objects.get(pk=self.draft_policy.pk).is_published
        )

    def test_editing_published_policy_creates_unpublished_revision(self):
        self.client.force_login(self.owner)
        url = reverse(
            "assistant:owner_policy_edit",
            kwargs={"policy_id": self.published_policy.pk},
        )
        response = self.client.post(
            url,
            {
                "code": self.published_policy.code,
                "title": self.published_policy.title,
                "content": "Nội dung mới cần rà soát.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.published_policy.refresh_from_db()
        self.assertEqual(self.published_policy.version, 2)
        self.assertFalse(self.published_policy.is_published)
        self.assertEqual(self.published_policy.content, "Nội dung mới cần rà soát.")

    def test_owner_can_create_policy_as_draft(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("assistant:owner_policy_create"),
            {
                "code": "chinh-sach-moi",
                "title": "Chính sách mới",
                "content": "Nội dung do OWNER nhập.",
            },
        )

        self.assertEqual(response.status_code, 302)
        policy = StorePolicy.objects.get(code="CHINH-SACH-MOI")
        self.assertFalse(policy.is_published)
        self.assertEqual(policy.created_by, self.owner)


class DemoPolicySeedTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="policy_seed_owner",
            password="test-password",
            role=User.Role.OWNER,
        )

    def test_seed_creates_policies_and_rerun_preserves_owner_changes(self):
        first_output = StringIO()
        call_command("seed_demo_policies", stdout=first_output)

        self.assertEqual(StorePolicy.objects.count(), 12)
        self.assertEqual(StorePolicy.objects.filter(is_published=True).count(), 0)
        self.assertIn("Created (12)", first_output.getvalue())

        policy = StorePolicy.objects.get(code="CS04")
        policy.title = "Tiêu đề do OWNER chỉnh sửa"
        policy.content = "Nội dung OWNER đã rà soát."
        policy.is_published = True
        policy.save()

        second_output = StringIO()
        call_command("seed_demo_policies", stdout=second_output)

        self.assertEqual(StorePolicy.objects.count(), 12)
        self.assertIn("Skipped (12)", second_output.getvalue())
        policy.refresh_from_db()
        self.assertEqual(policy.title, "Tiêu đề do OWNER chỉnh sửa")
        self.assertEqual(policy.content, "Nội dung OWNER đã rà soát.")
        self.assertTrue(policy.is_published)

    def test_seed_requires_owner_without_creating_policy_rows(self):
        self.owner.delete()
        with self.assertRaises(CommandError):
            call_command("seed_demo_policies", stdout=StringIO())
        self.assertFalse(StorePolicy.objects.exists())
