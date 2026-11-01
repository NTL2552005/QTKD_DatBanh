from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import BigIntegerField, F, Q
from django.db.models.functions import Cast
from django.utils.text import slugify

from .validators import validate_catalog_image


def cake_image_upload_to(instance, filename):
    extension = getattr(instance.image, "catalog_image_extension", ".jpg")
    return f"cakes/{uuid4().hex}{extension}"


class PublicCakeTemplateQuerySet(models.QuerySet):
    def public(self):
        return self.filter(is_available=True, category__is_active=True)


class Category(models.Model):
    name = models.CharField("tên danh mục", max_length=100, unique=True)
    slug = models.SlugField("đường dẫn", max_length=120, unique=True, blank=True)
    description = models.TextField("mô tả", blank=True)
    is_active = models.BooleanField("đang hoạt động", default=True)
    created_at = models.DateTimeField("ngày tạo", auto_now_add=True)
    updated_at = models.DateTimeField("cập nhật lần cuối", auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "danh mục bánh"
        verbose_name_plural = "danh mục bánh"

    def clean(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "Tên danh mục không được để trống."})

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        if not self.slug:
            base_slug = slugify(self.name) or "danh-muc"
            self.slug = base_slug
            suffix = 2
            while Category.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{base_slug}-{suffix}"
                suffix += 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class CakeTemplate(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="cake_templates",
        verbose_name="danh mục",
    )
    name = models.CharField("tên mẫu bánh", max_length=150)
    slug = models.SlugField("đường dẫn", max_length=170, unique=True, blank=True)
    description = models.TextField("mô tả chi tiết", blank=True)
    suitable_occasions = models.CharField(
        "dịp phù hợp (gợi ý)",
        max_length=250,
        blank=True,
    )
    flavor_profile = models.CharField(
        "hương vị (gợi ý)",
        max_length=250,
        blank=True,
    )
    style_tags = models.CharField(
        "phong cách (gợi ý)",
        max_length=250,
        blank=True,
    )
    serving_information = models.CharField(
        "thông tin khẩu phần",
        max_length=250,
        blank=True,
    )
    ingredient_information = models.TextField(
        "thành phần",
        blank=True,
    )
    allergen_information = models.TextField(
        "thông tin dị ứng",
        blank=True,
    )
    advisory_data_verified = models.BooleanField(
        "thông tin tư vấn đã được cửa hàng xác nhận",
        default=False,
    )
    reference_price = models.DecimalField(
        "giá tham khảo (VNĐ)",
        max_digits=12,
        decimal_places=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    image = models.ImageField(
        "ảnh mẫu",
        upload_to=cake_image_upload_to,
        blank=True,
        validators=[validate_catalog_image],
    )
    is_available = models.BooleanField("đang nhận đặt", default=True)
    created_at = models.DateTimeField("ngày tạo", auto_now_add=True)
    updated_at = models.DateTimeField("cập nhật lần cuối", auto_now=True)

    objects = PublicCakeTemplateQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "mẫu bánh"
        verbose_name_plural = "mẫu bánh"
        constraints = [
            models.CheckConstraint(
                condition=Q(reference_price__gte=0),
                name="catalog_cake_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(
                    reference_price=Cast(
                        F("reference_price"),
                        output_field=BigIntegerField(),
                    )
                ),
                name="catalog_cake_price_whole_vnd",
            ),
        ]

    def clean(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValidationError({"name": "Tên mẫu bánh không được để trống."})
        if self.reference_price is not None and self.reference_price < 0:
            raise ValidationError(
                {"reference_price": "Giá tham khảo không được là số âm."}
            )
        if (
            self._state.adding
            and self.is_available
            and self.category_id
            and not self.category.is_active
        ):
            raise ValidationError(
                {
                    "category": (
                        "Không thể tạo mẫu đang nhận đặt trong danh mục ngừng hoạt động."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        if (
            self._state.adding
            and self.is_available
            and self.category_id
            and not Category.objects.filter(
                pk=self.category_id,
                is_active=True,
            ).exists()
        ):
            raise ValidationError(
                {
                    "category": (
                        "Không thể tạo mẫu đang nhận đặt trong danh mục ngừng hoạt động."
                    )
                }
            )
        if not self.slug:
            base_slug = slugify(self.name) or "mau-banh"
            self.slug = base_slug
            suffix = 2
            while (
                CakeTemplate.objects.filter(slug=self.slug).exclude(pk=self.pk).exists()
            ):
                self.slug = f"{base_slug}-{suffix}"
                suffix += 1
        super().save(*args, **kwargs)

    @property
    def is_public(self):
        return self.is_available and self.category.is_active

    def __str__(self):
        return self.name
