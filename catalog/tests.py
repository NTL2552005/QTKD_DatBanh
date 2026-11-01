import io
import tempfile
from io import StringIO
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from .forms import CakeTemplateForm, CategoryForm
from .models import CakeTemplate, Category

User = get_user_model()


def image_upload(name="cake.png", image_format="PNG", size=(20, 20)):
    image = Image.new("RGB", size, (230, 150, 170))
    data = io.BytesIO()
    image.save(data, format=image_format)
    content_type = {
        "JPEG": "image/jpeg",
        "PNG": "image/png",
        "WEBP": "image/webp",
    }.get(image_format, "application/octet-stream")
    return SimpleUploadedFile(name, data.getvalue(), content_type=content_type)


class CatalogTestCase(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.settings(MEDIA_ROOT=self.media_dir.name)
        self.owner = User.objects.create_user(
            username="catalog_owner",
            password="A-strong-demo-password-983!",
            role=User.Role.OWNER,
        )
        self.customer = User.objects.create_user(
            username="catalog_customer",
            password="A-strong-demo-password-983!",
            role=User.Role.CUSTOMER,
        )
        self.staff = User.objects.create_user(
            username="catalog_staff",
            password="A-strong-demo-password-983!",
            role=User.Role.STAFF,
        )
        self.category = Category.objects.create(name="Bánh sinh nhật")

    def category_data(self, **overrides):
        data = {"name": "Bánh kỷ niệm", "description": "Mẫu danh mục"}
        data.update(overrides)
        return data

    def cake_data(self, **overrides):
        data = {
            "category": str(self.category.pk),
            "name": "Bánh kem dâu",
            "description": "Mô tả dạng văn bản thuần.",
            "reference_price": "250000",
            "is_available": "on",
        }
        data.update(overrides)
        return data

    def create_cake(self, **overrides):
        values = {
            "category": self.category,
            "name": "Bánh kem mẫu",
            "description": "Bánh minh họa.",
            "reference_price": Decimal("250000"),
        }
        values.update(overrides)
        return CakeTemplate.objects.create(**values)

    def owner_post(self, url, data=None, files=None):
        self.client.force_login(self.owner)
        payload = dict(data or {})
        payload.update(files or {})
        return self.client.post(url, payload)


class CatalogModelTests(CatalogTestCase):
    def test_category_trims_name_and_generates_stable_unique_slug(self):
        category = Category.objects.create(name="  Bánh kem  ")
        collision = Category.objects.create(name="Banh kem")
        original_slug = category.slug

        self.assertEqual(category.name, "Bánh kem")
        self.assertNotEqual(category.slug, collision.slug)
        category.name = "Bánh kem sinh nhật"
        category.save()
        self.assertEqual(category.slug, original_slug)

    def test_blank_names_are_rejected_after_trimming(self):
        category_form = CategoryForm({"name": "   ", "description": ""})
        cake_form = CakeTemplateForm(
            self.cake_data(name="   "),
        )

        self.assertFalse(category_form.is_valid())
        self.assertIn("name", category_form.errors)
        self.assertFalse(cake_form.is_valid())
        self.assertIn("name", cake_form.errors)

    def test_cake_slug_is_unique_and_does_not_change_after_rename(self):
        first = self.create_cake(name="Bánh dâu")
        second = self.create_cake(name="Bánh dâu")
        original_slug = first.slug

        self.assertNotEqual(first.slug, second.slug)
        first.name = "Bánh dâu trang trí hoa"
        first.save()
        self.assertEqual(first.slug, original_slug)

    def test_negative_price_is_rejected_by_form_and_database(self):
        form = CakeTemplateForm(self.cake_data(reference_price="-1"))
        self.assertFalse(form.is_valid())
        self.assertIn("reference_price", form.errors)

        with self.assertRaises(IntegrityError), transaction.atomic():
            CakeTemplate.objects.create(
                category=self.category,
                name="Giá không hợp lệ",
                reference_price=Decimal("-1"),
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            CakeTemplate.objects.create(
                category=self.category,
                name="Giá không nguyên",
                reference_price=Decimal("100.5"),
            )

    def test_category_protects_existing_cakes_from_deletion(self):
        self.create_cake()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.category.delete()

    def test_creating_available_cake_in_inactive_category_is_rejected(self):
        self.category.is_active = False
        self.category.save()
        form = CakeTemplateForm(
            self.cake_data(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("category", form.errors)

        with self.assertRaisesMessage(
            ValidationError,
            "Không thể tạo mẫu đang nhận đặt trong danh mục ngừng hoạt động.",
        ):
            self.create_cake()

    def test_existing_cake_in_inactive_category_can_move_to_active_category(self):
        inactive = Category.objects.create(name="Danh mục đã ngừng", is_active=False)
        cake = self.create_cake(category=inactive, is_available=False)
        CakeTemplate.objects.filter(pk=cake.pk).update(is_available=True)
        cake.refresh_from_db()
        form = CakeTemplateForm(
            self.cake_data(category=str(self.category.pk), name=cake.name),
            instance=cake,
        )

        self.assertTrue(form.is_valid(), form.errors)
        moved = form.save()
        self.assertEqual(moved.category, self.category)


class CatalogOwnerPermissionTests(CatalogTestCase):
    def test_owner_dashboard_uses_live_catalog_counts(self):
        self.create_cake(name="Mẫu công khai")
        self.create_cake(name="Mẫu ngừng nhận", is_available=False)
        Category.objects.create(name="Danh mục ngừng", is_active=False)
        self.client.force_login(self.owner)

        response = self.client.get(reverse("accounts:owner_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["catalog_counts"],
            {
                "categories_total": 2,
                "categories_active": 1,
                "cakes_total": 2,
                "cakes_public": 1,
            },
        )
        self.assertContains(response, "2 tổng, 1 đang hiển thị công khai")

    def test_anonymous_owner_page_redirects_to_login(self):
        response = self.client.get(reverse("catalog:owner_category_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_customer_staff_and_superuser_without_owner_role_receive_403(self):
        for user in (self.customer, self.staff):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                response = self.client.get(reverse("catalog:owner_category_list"))
                self.assertEqual(response.status_code, 403)

        superuser = User.objects.create_superuser(
            username="not_owner_superuser",
            email="super@example.com",
            password="A-strong-demo-password-983!",
        )
        superuser.role = User.Role.CUSTOMER
        superuser.save(update_fields=["role"])
        self.client.force_login(superuser)
        response = self.client.get(reverse("catalog:owner_category_list"))
        self.assertEqual(response.status_code, 403)

    def test_owner_can_create_edit_categories_and_cakes(self):
        response = self.owner_post(
            reverse("catalog:owner_category_create"),
            self.category_data(name="  Bánh sự kiện  "),
        )
        self.assertRedirects(response, reverse("catalog:owner_category_list"))
        category = Category.objects.get(name="Bánh sự kiện")

        response = self.owner_post(
            reverse("catalog:owner_category_edit", args=[category.pk]),
            self.category_data(name="Bánh tiệc"),
        )
        self.assertRedirects(response, reverse("catalog:owner_category_list"))
        category.refresh_from_db()
        self.assertEqual(category.name, "Bánh tiệc")

        response = self.owner_post(
            reverse("catalog:owner_cake_create"),
            self.cake_data(),
        )
        self.assertRedirects(response, reverse("catalog:owner_cake_list"))
        cake = CakeTemplate.objects.get(name="Bánh kem dâu")
        slug = cake.slug

        response = self.owner_post(
            reverse("catalog:owner_cake_edit", args=[cake.pk]),
            self.cake_data(
                name="Bánh kem dâu sửa",
                suitable_occasions="Sinh nhật, kỷ niệm",
                flavor_profile="Dâu, vani",
                style_tags="Hoa kem",
                serving_information="Cửa hàng xác nhận phù hợp 2-3 người",
                ingredient_information="Kem tươi, dâu tươi",
                allergen_information="Có sữa",
                advisory_data_verified="on",
            ),
        )
        self.assertRedirects(response, reverse("catalog:owner_cake_list"))
        cake.refresh_from_db()
        self.assertEqual(cake.name, "Bánh kem dâu sửa")
        self.assertEqual(cake.slug, slug)
        self.assertEqual(cake.suitable_occasions, "Sinh nhật, kỷ niệm")
        self.assertEqual(cake.flavor_profile, "Dâu, vani")
        self.assertEqual(cake.style_tags, "Hoa kem")
        self.assertEqual(cake.serving_information, "Cửa hàng xác nhận phù hợp 2-3 người")
        self.assertEqual(cake.ingredient_information, "Kem tươi, dâu tươi")
        self.assertEqual(cake.allergen_information, "Có sữa")
        self.assertTrue(cake.advisory_data_verified)

    def test_owner_cannot_mark_empty_advisory_data_as_verified(self):
        form = CakeTemplateForm(
            self.cake_data(advisory_data_verified="on"),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("advisory_data_verified", form.errors)

    def test_editing_verified_advisory_data_requires_reconfirmation(self):
        cake = self.create_cake(
            flavor_profile="Dâu",
            advisory_data_verified=True,
        )

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("catalog:owner_cake_edit", args=[cake.pk]),
            self.cake_data(
                name=cake.name,
                flavor_profile="Chocolate",
                advisory_data_verified="on",
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.redirect_chain[-1][0],
            reverse("catalog:owner_cake_list"),
        )
        cake.refresh_from_db()
        self.assertEqual(cake.flavor_profile, "Chocolate")
        self.assertFalse(cake.advisory_data_verified)
        self.assertContains(
            response,
            "trạng thái xác nhận được gỡ",
        )

    def test_public_cake_detail_shows_only_confirmed_advisory_attributes(self):
        cake = self.create_cake(
            suitable_occasions="Sinh nhật",
            flavor_profile="Chocolate",
            serving_information="Cửa hàng xác nhận phù hợp 2 người",
            advisory_data_verified=True,
        )

        response = self.client.get(reverse("catalog:cake_detail", args=[cake.slug]))

        self.assertContains(response, "Dịp phù hợp")
        self.assertContains(response, "Chocolate")
        self.assertContains(response, "Cửa hàng xác nhận phù hợp 2 người")
        self.assertContains(response, "đã được cửa hàng xác nhận")
        self.assertContains(response, "cần nhân viên xác nhận trực tiếp")

    def test_public_detail_labels_demo_advisory_data_unverified(self):
        cake = self.create_cake(
            suitable_occasions="Sinh nhật",
            flavor_profile="Dâu",
        )

        response = self.client.get(reverse("catalog:cake_detail", args=[cake.slug]))

        self.assertContains(response, "Gợi ý minh họa, chưa được cửa hàng xác nhận")

    def test_toggle_endpoints_are_post_only_and_get_does_not_change_state(self):
        cake = self.create_cake()
        category_toggle = reverse(
            "catalog:owner_category_toggle", args=[self.category.pk]
        )
        cake_toggle = reverse("catalog:owner_cake_toggle", args=[cake.pk])
        self.client.force_login(self.owner)

        category_response = self.client.get(category_toggle)
        cake_response = self.client.get(cake_toggle)

        self.assertEqual(category_response.status_code, 405)
        self.assertEqual(cake_response.status_code, 405)
        self.category.refresh_from_db()
        cake.refresh_from_db()
        self.assertTrue(self.category.is_active)
        self.assertTrue(cake.is_available)

    def test_owner_can_toggle_category_and_cake_with_post(self):
        cake = self.create_cake()
        self.client.force_login(self.owner)

        self.client.post(reverse("catalog:owner_category_toggle", args=[self.category.pk]))
        self.client.post(reverse("catalog:owner_cake_toggle", args=[cake.pk]))

        self.category.refresh_from_db()
        cake.refresh_from_db()
        self.assertFalse(self.category.is_active)
        self.assertFalse(cake.is_available)

    def test_category_edit_form_cannot_bypass_status_toggle(self):
        response = self.owner_post(
            reverse("catalog:owner_category_edit", args=[self.category.pk]),
            self.category_data(name="Bánh sinh nhật đổi tên", is_active="off"),
        )

        self.assertRedirects(response, reverse("catalog:owner_category_list"))
        self.category.refresh_from_db()
        self.assertTrue(self.category.is_active)

    def test_management_post_requires_csrf(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)

        response = csrf_client.post(
            reverse("catalog:owner_category_create"),
            self.category_data(name="No CSRF"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Category.objects.filter(name="No CSRF").exists())

    def test_owner_management_lists_show_search_filters_and_pagination(self):
        for index in range(13):
            Category.objects.create(name=f"Nhóm {index:02d}")
        cake = self.create_cake(name="Mẫu để lọc")
        self.client.force_login(self.owner)

        category_page = self.client.get(
            reverse("catalog:owner_category_list"),
            {"q": "Nhóm", "status": "active"},
        )
        cake_page = self.client.get(
            reverse("catalog:owner_cake_list"),
            {"q": "Mẫu", "category": self.category.pk, "status": "available"},
        )

        self.assertEqual(category_page.status_code, 200)
        self.assertContains(category_page, "page=2")
        self.assertIn("q=Nh%C3%B3m", category_page.context["pagination_query"])
        self.assertEqual(cake_page.status_code, 200)
        self.assertContains(cake_page, cake.name)
        self.assertContains(cake_page, "Giá tham khảo")


class PublicCatalogTests(CatalogTestCase):
    def test_public_list_and_detail_are_available_without_login(self):
        cake = self.create_cake()

        listing = self.client.get(reverse("catalog:cake_list"))
        detail = self.client.get(reverse("catalog:cake_detail", args=[cake.slug]))

        self.assertContains(listing, cake.name)
        self.assertContains(listing, "250.000 đ")
        self.assertContains(detail, "Giá cuối cùng được cửa hàng xác nhận")
        self.assertContains(detail, cake.description)

    def test_unavailable_or_inactive_category_cakes_are_not_public(self):
        unavailable = self.create_cake(name="Đã ngừng nhận", is_available=False)
        inactive_category = Category.objects.create(
            name="Ngừng hoạt động",
            is_active=False,
        )
        hidden_by_category = self.create_cake(
            category=inactive_category,
            name="Ẩn theo danh mục",
            is_available=False,
        )
        CakeTemplate.objects.filter(pk=hidden_by_category.pk).update(is_available=True)

        listing = self.client.get(reverse("catalog:cake_list"))
        search_hidden = self.client.get(
            reverse("catalog:cake_list"),
            {"q": "Ẩn theo danh mục", "category": inactive_category.slug},
        )
        unavailable_detail = self.client.get(
            reverse("catalog:cake_detail", args=[unavailable.slug])
        )
        inactive_detail = self.client.get(
            reverse("catalog:cake_detail", args=[hidden_by_category.slug])
        )

        self.assertNotContains(listing, unavailable.name)
        self.assertNotContains(listing, hidden_by_category.name)
        self.assertFalse(search_hidden.context["page_obj"].object_list)
        self.assertNotContains(
            search_hidden,
            f'href="/cakes/{hidden_by_category.slug}/"',
        )
        self.assertEqual(unavailable_detail.status_code, 404)
        self.assertEqual(inactive_detail.status_code, 404)

    def test_category_deactivation_hides_cakes_and_reactivation_only_shows_available(self):
        available = self.create_cake(name="Mẫu đang nhận")
        stopped = self.create_cake(name="Mẫu đã ngừng", is_available=False)
        self.category.is_active = False
        self.category.save()
        hidden_listing = self.client.get(reverse("catalog:cake_list"))
        self.category.is_active = True
        self.category.save()
        active_listing = self.client.get(reverse("catalog:cake_list"))

        self.assertNotContains(hidden_listing, available.name)
        self.assertContains(active_listing, available.name)
        self.assertNotContains(active_listing, stopped.name)

    def test_search_filter_and_sort_do_not_expose_hidden_items(self):
        public = self.create_cake(name="Bánh công khai", reference_price=Decimal("100"))
        self.create_cake(
            name="Bánh bí mật",
            reference_price=Decimal("999999"),
            is_available=False,
        )

        for params in (
            {"q": "Bánh"},
            {"category": self.category.slug},
            {"sort": "price_desc"},
            {"sort": "price_asc", "q": "Bánh"},
        ):
            with self.subTest(params=params):
                response = self.client.get(reverse("catalog:cake_list"), params)
                self.assertContains(response, public.name)
                self.assertNotContains(response, "Bánh bí mật")

    def test_public_sort_is_allowlisted_and_prices_sort_numerically(self):
        expensive = self.create_cake(
            name="Mẫu đắt",
            reference_price=Decimal("900000"),
        )
        cheap = self.create_cake(
            name="Mẫu rẻ",
            reference_price=Decimal("100000"),
        )

        ascending = self.client.get(
            reverse("catalog:cake_list"),
            {"sort": "price_asc"},
        )
        descending = self.client.get(
            reverse("catalog:cake_list"),
            {"sort": "price_desc"},
        )
        invalid = self.client.get(
            reverse("catalog:cake_list"),
            {"sort": "created_at"},
        )

        self.assertEqual(ascending.context["page_obj"].object_list[0], cheap)
        self.assertEqual(descending.context["page_obj"].object_list[0], expensive)
        self.assertEqual(invalid.context["selected_sort"], "newest")

    def test_public_pagination_preserves_search_category_and_sort(self):
        for index in range(10):
            self.create_cake(name=f"Bánh trang {index:02d}")

        response = self.client.get(
            reverse("catalog:cake_list"),
            {
                "q": "Bánh trang",
                "category": self.category.slug,
                "sort": "price_asc",
            },
        )

        self.assertEqual(response.context["page_obj"].paginator.num_pages, 2)
        self.assertContains(
            response,
            "q=B%C3%A1nh+trang&amp;category=banh-sinh-nhat&amp;sort=price_asc&amp;page=2",
        )


class CatalogImageTests(CatalogTestCase):
    def test_fake_image_file_is_rejected(self):
        fake = SimpleUploadedFile("fake.png", b"not an image", "image/png")
        form = CakeTemplateForm(self.cake_data(), {"image": fake})

        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_unsupported_image_format_is_rejected(self):
        gif = image_upload(name="not-allowed.gif", image_format="GIF")
        form = CakeTemplateForm(self.cake_data(), {"image": gif})

        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_svg_upload_is_rejected(self):
        svg = SimpleUploadedFile(
            "vector.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            "image/svg+xml",
        )
        form = CakeTemplateForm(self.cake_data(), {"image": svg})

        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_oversized_upload_and_excessive_pixel_image_are_rejected(self):
        oversized = SimpleUploadedFile(
            "large.png",
            b"x" * (5 * 1024 * 1024 + 1),
            "image/png",
        )
        oversized_form = CakeTemplateForm(
            self.cake_data(),
            {"image": oversized},
        )
        pixel_form = CakeTemplateForm(
            self.cake_data(name="Ảnh quá lớn theo pixel"),
            {"image": image_upload(size=(4500, 4500))},
        )

        self.assertFalse(oversized_form.is_valid())
        self.assertIn("image", oversized_form.errors)
        self.assertFalse(pixel_form.is_valid())
        self.assertIn("image", pixel_form.errors)

    def test_valid_images_get_random_safe_names_and_supported_formats(self):
        for index, image_format in enumerate(("JPEG", "PNG", "WEBP")):
            with self.subTest(image_format=image_format):
                form = CakeTemplateForm(
                    self.cake_data(name=f"Ảnh hợp lệ {index}"),
                    {"image": image_upload(image_format=image_format)},
                )
                self.assertTrue(form.is_valid(), form.errors)
                cake = form.save()
                self.assertTrue(cake.image.name.startswith("cakes/"))
                self.assertNotIn("cake.png", cake.image.name)

    def test_edit_without_new_image_preserves_existing_image(self):
        cake = self.create_cake()
        cake.image.save("existing.png", image_upload(), save=True)
        original_name = cake.image.name
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:owner_cake_edit", args=[cake.pk]),
            self.cake_data(name=cake.name, reference_price="350000"),
        )

        self.assertRedirects(response, reverse("catalog:owner_cake_list"))
        cake.refresh_from_db()
        self.assertEqual(cake.image.name, original_name)
        self.assertEqual(cake.reference_price, Decimal("350000"))


class SeedCatalogTests(CatalogTestCase):
    def test_seed_demo_advisory_data_only_fills_empty_unverified_fields(self):
        call_command("seed_catalog")
        chocolate = CakeTemplate.objects.get(name="Cupcake chocolate")
        chocolate.style_tags = "OWNER đã nhập"
        chocolate.advisory_data_verified = True
        chocolate.save(update_fields=("style_tags", "advisory_data_verified"))

        first_output = StringIO()
        call_command("seed_demo_advisory_data", stdout=first_output)
        call_command("seed_demo_advisory_data", stdout=StringIO())

        chocolate.refresh_from_db()
        strawberry = CakeTemplate.objects.get(name="Bánh kem dâu tươi")
        vanilla = CakeTemplate.objects.get(name="Cupcake kem vani")
        self.assertIn("Updated 5 demo cake records", first_output.getvalue())
        self.assertEqual(chocolate.style_tags, "OWNER đã nhập")
        self.assertTrue(chocolate.advisory_data_verified)
        self.assertEqual(chocolate.flavor_profile, "")
        self.assertEqual(strawberry.flavor_profile, "Dâu tươi")
        self.assertFalse(strawberry.advisory_data_verified)
        self.assertEqual(vanilla.flavor_profile, "Vani")
        self.assertEqual(vanilla.serving_information, "")
        self.assertEqual(vanilla.ingredient_information, "")
        self.assertEqual(vanilla.allergen_information, "")

    def test_seed_catalog_is_idempotent_and_preserves_existing_edits(self):
        from django.core.management import call_command

        existing_category = self.category
        existing_category.description = "Đã được sửa"
        existing_category.is_active = False
        existing_category.save()
        existing_cake = CakeTemplate.objects.create(
            category=existing_category,
            name="Bánh kem dâu tươi",
            description="Mô tả đã sửa",
            reference_price=Decimal("999000"),
            is_available=False,
        )

        call_command("seed_catalog")
        call_command("seed_catalog")

        existing_category.refresh_from_db()
        existing_cake.refresh_from_db()
        self.assertEqual(Category.objects.filter(name="Bánh sinh nhật").count(), 1)
        self.assertEqual(
            CakeTemplate.objects.filter(
                category=existing_category,
                name="Bánh kem dâu tươi",
            ).count(),
            1,
        )
        self.assertEqual(existing_category.description, "Đã được sửa")
        self.assertFalse(existing_category.is_active)
        self.assertEqual(existing_cake.reference_price, Decimal("999000"))
        self.assertEqual(existing_cake.description, "Mô tả đã sửa")
        self.assertFalse(existing_cake.is_available)
        seeded_in_inactive_category = CakeTemplate.objects.filter(
            category=existing_category
        ).exclude(pk=existing_cake.pk)
        self.assertFalse(seeded_in_inactive_category.filter(is_available=True).exists())
        self.assertEqual(Category.objects.count(), 3)
        self.assertEqual(CakeTemplate.objects.count(), 6)
