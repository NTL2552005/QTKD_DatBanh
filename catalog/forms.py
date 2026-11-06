from decimal import Decimal

from django import forms

from .models import CakeTemplate, Category


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "description")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Tên danh mục không được để trống.")
        return name


class CakeTemplateForm(forms.ModelForm):
    ADVISORY_FIELDS = (
        "suitable_occasions",
        "flavor_profile",
        "style_tags",
        "serving_information",
        "ingredient_information",
        "allergen_information",
    )

    reference_price = forms.DecimalField(
        label="Giá tham khảo (VNĐ)",
        max_digits=12,
        decimal_places=0,
        min_value=Decimal("0"),
        help_text="Nhập số nguyên đồng Việt Nam; đây chưa phải giá chốt đơn.",
    )

    class Meta:
        model = CakeTemplate
        fields = (
            "category",
            "name",
            "description",
            "suitable_occasions",
            "flavor_profile",
            "style_tags",
            "serving_information",
            "ingredient_information",
            "allergen_information",
            "advisory_data_verified",
            "reference_price",
            "image",
            "is_available",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "image": forms.ClearableFileInput(
                attrs={"accept": "image/jpeg,image/png,image/webp"}
            ),
        }
        help_texts = {
            "image": "JPEG, PNG hoặc WebP; tối đa 5 MiB và 20 megapixel.",
            "is_available": "Trạng thái nhận yêu cầu theo mẫu, không phải số lượng tồn kho.",
            "suitable_occasions": "Ví dụ: sinh nhật, kỷ niệm. Chỉ nhập thông tin cửa hàng đã xác nhận.",
            "flavor_profile": "Có thể nhập nhiều hương vị, ngăn cách bằng dấu phẩy.",
            "style_tags": "Ví dụ: tối giản, hoa kem. Chỉ nhập đặc điểm có thật của mẫu.",
            "serving_information": "Không tự ước lượng số người; để trống nếu cửa hàng chưa xác nhận.",
            "ingredient_information": "Thông tin thành phần cần được cửa hàng xác nhận.",
            "allergen_information": "Không cam kết an toàn với dị ứng khi chưa xác minh đầy đủ thành phần.",
            "advisory_data_verified": (
                "Chỉ đánh dấu sau khi cửa hàng kiểm tra và xác nhận toàn bộ thông tin "
                "đã nhập. Gợi ý demo chưa xác nhận phải để bỏ chọn."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.advisory_verification_reset = False
        self.fields["category"].queryset = Category.objects.all().order_by("name")
        self.fields["category"].label = "Danh mục bánh"
        self.fields["category"].empty_label = "Chọn danh mục"
        self.fields["name"].label = "Tên mẫu bánh"
        self.fields["description"].label = "Mô tả chi tiết"
        self.fields["ingredient_information"].widget = forms.Textarea(
            attrs={"rows": 3}
        )
        self.fields["allergen_information"].widget = forms.Textarea(
            attrs={"rows": 3}
        )
        self.fields["advisory_data_verified"].label = (
            "Thông tin tư vấn đã được cửa hàng xác nhận"
        )
        self.fields["image"].label = "Ảnh mẫu bánh"
        self.fields["is_available"].label = "Đang nhận đặt theo mẫu"

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Tên mẫu bánh không được để trống.")
        return name

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and self.instance.advisory_data_verified:
            if any(
                cleaned_data.get(field) != getattr(self.instance, field)
                for field in self.ADVISORY_FIELDS
            ):
                cleaned_data["advisory_data_verified"] = False
                self.advisory_verification_reset = True
        category = cleaned_data.get("category")
        is_available = cleaned_data.get("is_available")
        if (
            self.instance._state.adding
            and category
            and not category.is_active
            and is_available
        ):
            self.add_error(
                "category",
                "Không thể tạo mẫu đang nhận đặt trong danh mục ngừng hoạt động.",
            )
        if cleaned_data.get("advisory_data_verified") and not any(
            cleaned_data.get(field)
            for field in self.ADVISORY_FIELDS
        ):
            self.add_error(
                "advisory_data_verified",
                "Chưa có thông tin tư vấn để xác nhận.",
            )
        return cleaned_data
