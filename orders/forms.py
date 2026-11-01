import uuid
from decimal import Decimal

from django import forms
from django.utils import timezone

from catalog.validators import validate_catalog_image

from catalog.models import CakeTemplate

from .models import (
    DeliveryAttempt,
    Order,
    OrderAlternative,
    OrderIncident,
    Payment,
    Quote,
    Refund,
    RevisionRequest,
    StaffAvailabilityCheck,
)

DATETIME_INPUT_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S")


class OrderRequestFieldsMixin:
    def clean_size(self):
        value = self.cleaned_data["size"].strip()
        if not value:
            raise forms.ValidationError("Vui lòng nhập kích thước bánh.")
        return value

    def clean_flavor(self):
        value = self.cleaned_data["flavor"].strip()
        if not value:
            raise forms.ValidationError("Vui lòng nhập hương vị.")
        return value

    def clean_recipient_name(self):
        value = self.cleaned_data["recipient_name"].strip()
        if not value:
            raise forms.ValidationError("Vui lòng nhập tên người nhận.")
        return value

    def clean_recipient_phone(self):
        value = self.cleaned_data["recipient_phone"].strip()
        if len("".join(character for character in value if character.isdigit())) < 8:
            raise forms.ValidationError("Số điện thoại cần có ít nhất 8 chữ số.")
        return value

    def clean_delivery_address(self):
        value = self.cleaned_data["delivery_address"].strip()
        if not value:
            raise forms.ValidationError("Vui lòng nhập địa chỉ giao bánh.")
        return value

    def clean_inscription(self):
        return self.cleaned_data["inscription"].strip()

    def clean_decoration_request(self):
        return self.cleaned_data["decoration_request"].strip()

    def clean_note(self):
        return self.cleaned_data["note"].strip()

    def clean_desired_delivery_at(self):
        value = self.cleaned_data["desired_delivery_at"]
        if value <= timezone.now():
            raise forms.ValidationError("Thời gian giao mong muốn phải ở tương lai.")
        return value


class OrderCreateForm(OrderRequestFieldsMixin, forms.Form):
    request_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    draft_id = forms.UUIDField(widget=forms.HiddenInput, required=False)
    size = forms.CharField(label="Kích thước", max_length=80)
    flavor = forms.CharField(label="Hương vị", max_length=100)
    quantity = forms.IntegerField(
        label="Số lượng",
        min_value=1,
        max_value=100,
        help_text="Số lượng nguyên dương, tối đa 100 chiếc mỗi yêu cầu.",
    )
    inscription = forms.CharField(
        label="Nội dung viết trên bánh",
        max_length=250,
        required=False,
    )
    decoration_request = forms.CharField(
        label="Yêu cầu trang trí",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    recipient_name = forms.CharField(label="Tên người nhận", max_length=150)
    recipient_phone = forms.CharField(label="Số điện thoại người nhận", max_length=30)
    delivery_address = forms.CharField(
        label="Địa chỉ giao",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    desired_delivery_at = forms.DateTimeField(
        label="Thời gian giao mong muốn",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    note = forms.CharField(
        label="Ghi chú",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class OrderSupplementForm(OrderRequestFieldsMixin, forms.ModelForm):
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    class Meta:
        model = Order
        fields = (
            "size",
            "flavor",
            "quantity",
            "inscription",
            "decoration_request",
            "recipient_name",
            "recipient_phone",
            "delivery_address",
            "desired_delivery_at",
            "note",
        )
        widgets = {
            "decoration_request": forms.Textarea(attrs={"rows": 4}),
            "delivery_address": forms.Textarea(attrs={"rows": 3}),
            "desired_delivery_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "note": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "size": "Kích thước",
            "flavor": "Hương vị",
            "quantity": "Số lượng",
            "inscription": "Nội dung viết trên bánh",
            "decoration_request": "Yêu cầu trang trí",
            "recipient_name": "Tên người nhận",
            "recipient_phone": "Số điện thoại người nhận",
            "delivery_address": "Địa chỉ giao",
            "desired_delivery_at": "Thời gian giao mong muốn",
            "note": "Ghi chú",
        }
        field_classes = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["desired_delivery_at"].input_formats = DATETIME_INPUT_FORMATS
        self.fields["quantity"].min_value = 1
        self.fields["quantity"].max_value = 100
        if not self.is_bound and self.instance:
            self.initial["expected_version"] = self.instance.version

    def clean_expected_version(self):
        return self.cleaned_data["expected_version"]


class StaffCheckForm(forms.Form):
    materials_available = forms.ChoiceField(
        label="Nguyên liệu có thể đáp ứng",
        choices=(("", "Chưa kiểm tra"), ("yes", "Có"), ("no", "Không")),
        required=False,
        widget=forms.RadioSelect(attrs={"class": "availability-choice-options"}),
    )
    schedule_available = forms.ChoiceField(
        label="Lịch làm có thể đáp ứng",
        choices=(("", "Chưa kiểm tra"), ("yes", "Có"), ("no", "Không")),
        required=False,
        widget=forms.RadioSelect(attrs={"class": "availability-choice-options"}),
    )
    notes = forms.CharField(
        label="Kết quả kiểm tra / ghi chú",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    reason = forms.ChoiceField(
        label="Nguyên nhân chưa đáp ứng",
        choices=StaffAvailabilityCheck.Reason.choices,
        required=False,
    )
    reason_details = forms.CharField(
        label="Mô tả nguyên nhân khác",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    authority_classification = forms.ChoiceField(
        label="Phạm vi quyền hạn",
        choices=(
            ("", "Chọn kết quả phân loại"),
            (
                StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
                "Trong quyền nhân viên",
            ),
            (
                StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED,
                "Cần chủ phê duyệt",
            ),
            (
                StaffAvailabilityCheck.AuthorityClassification.NEEDS_INFO,
                "Chưa đủ thông tin để phân loại",
            ),
        ),
        required=True,
    )
    authority_reasons = forms.MultipleChoiceField(
        label="Lý do cần chủ phê duyệt",
        choices=StaffAvailabilityCheck.AuthorityReason.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    authority_details = forms.CharField(
        label="Lý do vượt quyền hoặc thông tin còn thiếu",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    authority_basis = forms.CharField(
        label="Căn cứ xác định trong quyền nhân viên",
        max_length=1000,
        required=False,
        help_text="Ghi căn cứ ngắn, ví dụ đối chiếu mẫu và quy định cửa hàng. Không có ngưỡng tự động; cần kiểm tra quy định thực tế.",
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def clean(self):
        cleaned_data = super().clean()
        classification = cleaned_data.get("authority_classification")
        materials_available = cleaned_data.get("materials_available", "")
        schedule_available = cleaned_data.get("schedule_available", "")
        notes = cleaned_data.get("notes", "").strip()
        if classification in (
            StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
            StaffAvailabilityCheck.AuthorityClassification.NEEDS_INFO,
        ) and not notes:
            self.add_error("notes", "Vui lòng ghi nội dung kiểm tra.")
        if classification == StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE:
            if materials_available not in ("yes", "no"):
                self.add_error("materials_available", "Chọn kết quả kiểm tra nguyên liệu.")
            if schedule_available not in ("yes", "no"):
                self.add_error("schedule_available", "Chọn kết quả kiểm tra thời gian.")
        unavailable = (
            classification == StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE
            and (materials_available == "no" or schedule_available == "no")
        )
        if unavailable and not cleaned_data.get("reason"):
            self.add_error("reason", "Chọn nguyên nhân cụ thể khi chưa đáp ứng.")
        if (
            unavailable
            and cleaned_data.get("reason") == StaffAvailabilityCheck.Reason.OTHER
            and not cleaned_data.get("reason_details", "").strip()
        ):
            self.add_error("reason_details", "Vui lòng mô tả nguyên nhân khác.")
        cleaned_data["reason_details"] = cleaned_data.get(
            "reason_details", ""
        ).strip()
        authority_reasons = cleaned_data.get("authority_reasons", [])
        authority_details = cleaned_data.get("authority_details", "").strip()
        authority_basis = cleaned_data.get("authority_basis", "").strip()
        if classification == StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE:
            if not authority_basis:
                self.add_error(
                    "authority_basis",
                    "Ghi căn cứ ngắn để xác nhận yêu cầu nằm trong quyền nhân viên.",
                )
            if authority_reasons:
                self.add_error(
                    "authority_reasons",
                    "Không chọn lý do vượt quyền khi phân loại trong quyền.",
                )
        elif classification == StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED:
            if not authority_reasons:
                self.add_error(
                    "authority_reasons",
                    "Chọn ít nhất một lý do cần chủ phê duyệt.",
                )
            if not authority_details:
                self.add_error(
                    "authority_details",
                    "Mô tả cụ thể yêu cầu vượt quyền.",
                )
        elif classification == StaffAvailabilityCheck.AuthorityClassification.NEEDS_INFO:
            if not authority_details:
                self.add_error(
                    "authority_details",
                    "Ghi rõ thông tin cần khách bổ sung để phân loại.",
                )
            if authority_reasons or authority_basis:
                self.add_error(
                    "authority_details",
                    "Chưa đủ thông tin thì không chọn lý do vượt quyền hoặc căn cứ trong quyền.",
                )
        cleaned_data["notes"] = notes
        cleaned_data["authority_reasons"] = authority_reasons
        cleaned_data["authority_details"] = authority_details
        cleaned_data["authority_basis"] = authority_basis
        return cleaned_data


class OwnerIntakeCheckForm(forms.Form):
    materials_available = forms.ChoiceField(
        label="Nguyên liệu có thể đáp ứng",
        choices=(("yes", "Đáp ứng"), ("no", "Không đáp ứng")),
        widget=forms.RadioSelect,
    )
    schedule_available = forms.ChoiceField(
        label="Thời gian làm và giao có thể đáp ứng",
        choices=(("yes", "Đáp ứng"), ("no", "Không đáp ứng")),
        widget=forms.RadioSelect,
    )
    notes = forms.CharField(
        label="Ghi chú kiểm tra",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)

    def clean_notes(self):
        notes = self.cleaned_data["notes"].strip()
        if not notes:
            raise forms.ValidationError("Ghi nội dung kiểm tra.")
        return notes


class OwnerIntakeRejectionForm(forms.Form):
    reason = forms.CharField(
        label="Lý do dành cho khách",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    internal_note = forms.CharField(
        label="Ghi chú nội bộ",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    confirm_rejection = forms.BooleanField(
        label="Xác nhận kết thúc tiếp nhận đơn",
    )
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Nhập lý do từ chối hiển thị cho khách.")
        return reason

    def clean_internal_note(self):
        return self.cleaned_data["internal_note"].strip()


class OwnerClaimIntakeOrderForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)


class OrderAlternativeForm(forms.Form):
    proposed_cake = forms.ModelChoiceField(
        label="Mẫu bánh đề xuất",
        queryset=CakeTemplate.objects.none(),
    )
    size = forms.CharField(label="Kích thước", max_length=80)
    flavor = forms.CharField(label="Hương vị", max_length=100)
    quantity = forms.IntegerField(label="Số lượng", min_value=1, max_value=100)
    inscription = forms.CharField(
        label="Nội dung viết trên bánh",
        max_length=250,
        required=False,
    )
    decoration_request = forms.CharField(
        label="Yêu cầu trang trí",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    desired_delivery_at = forms.DateTimeField(
        label="Thời gian giao đề xuất",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    difference_summary = forms.CharField(
        label="Giải thích khác biệt và phương án",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    estimated_unit_price = forms.DecimalField(
        label="Đơn giá dự kiến (không phải báo giá)",
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=0,
        required=False,
    )
    requires_owner_approval = forms.BooleanField(
        label="Phương án vượt quyền nhân viên, cần OWNER duyệt",
        required=False,
    )
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["proposed_cake"].queryset = CakeTemplate.objects.public().order_by(
            "name", "pk"
        )
        if order:
            if not self.is_bound:
                self.initial.update(
                    proposed_cake=order.cake_template_id,
                    size=order.size,
                    flavor=order.flavor,
                    quantity=order.quantity,
                    inscription=order.inscription,
                    decoration_request=order.decoration_request,
                    desired_delivery_at=order.desired_delivery_at,
                    expected_order_version=order.version,
                )
            self.fields["desired_delivery_at"].input_formats = DATETIME_INPUT_FORMATS

    def clean(self):
        cleaned_data = super().clean()
        for field in ("size", "flavor", "difference_summary"):
            value = cleaned_data.get(field)
            if value is not None:
                cleaned_data[field] = value.strip()
                if not cleaned_data[field]:
                    self.add_error(field, "Trường này không được để trống.")
        for field in ("inscription", "decoration_request"):
            cleaned_data[field] = cleaned_data.get(field, "").strip()
        delivery_at = cleaned_data.get("desired_delivery_at")
        if delivery_at and delivery_at <= timezone.now():
            self.add_error(
                "desired_delivery_at",
                "Thời gian giao đề xuất phải ở tương lai.",
            )
        return cleaned_data


class AlternativeActionForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_alternative_revision = forms.IntegerField(widget=forms.HiddenInput)


class AlternativeOwnerDecisionForm(AlternativeActionForm):
    decision = forms.ChoiceField(
        label="Quyết định",
        choices=(("approve", "Duyệt phương án"), ("reject", "Từ chối")),
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        label="Lý do quyết định",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Vui lòng ghi lý do quyết định.")
        return reason


class IntakeQuoteApprovalDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        label="Quyết định",
        choices=(("approve", "Duyệt báo giá"), ("reject", "Từ chối tiếp nhận")),
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        label="Lý do quyết định",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_approval_revision = forms.IntegerField(widget=forms.HiddenInput)

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Vui lòng ghi lý do quyết định.")
        return reason


class IntakeRejectionNoticeForm(forms.Form):
    message = forms.CharField(
        label="Thông báo cho khách",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_approval_revision = forms.IntegerField(widget=forms.HiddenInput)

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError("Vui lòng nhập nội dung thông báo khách.")
        return message


class AlternativeCustomerResponseForm(AlternativeActionForm):
    decision = forms.ChoiceField(
        choices=(("accept", "Đồng ý phương án"), ("decline", "Từ chối phương án")),
        widget=forms.HiddenInput,
    )
    response = forms.CharField(
        label="Ghi chú phản hồi (không bắt buộc)",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean_response(self):
        return self.cleaned_data["response"].strip()


class StaffMessageForm(forms.Form):
    message = forms.CharField(
        label="Nội dung",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def clean_message(self):
        value = self.cleaned_data["message"].strip()
        if not value:
            raise forms.ValidationError("Vui lòng nhập nội dung.")
        return value


class QuoteForm(forms.Form):
    unit_price = forms.DecimalField(
        label="Đơn giá bánh (VNĐ)",
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=0,
    )
    delivery_fee = forms.DecimalField(
        label="Phí giao hàng (VNĐ)",
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=0,
        initial=Decimal("0"),
    )
    proposed_delivery_at = forms.DateTimeField(
        label="Thời gian giao đề xuất",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    expires_at = forms.DateTimeField(
        label="Hạn chấp nhận báo giá",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    notes = forms.CharField(
        label="Ghi chú báo giá",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    revision_reason = forms.CharField(
        label="Lý do gửi báo giá mới",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = order
        if not self.is_bound and order:
            self.initial["expected_version"] = order.version
        if order and order.current_quote:
            self.fields["revision_reason"].required = True
            self.fields["revision_reason"].help_text = (
                "Bắt buộc khi thay thế báo giá đã gửi trước đó."
            )

    def clean(self):
        cleaned_data = super().clean()
        now = timezone.now()
        proposed = cleaned_data.get("proposed_delivery_at")
        expiry = cleaned_data.get("expires_at")
        if proposed and proposed <= now:
            self.add_error("proposed_delivery_at", "Thời gian giao đề xuất phải ở tương lai.")
        if expiry and expiry <= now:
            self.add_error("expires_at", "Hạn chấp nhận báo giá phải ở tương lai.")
        if proposed and expiry and expiry >= proposed:
            self.add_error(
                "expires_at",
                "Hạn chấp nhận phải trước thời gian giao đề xuất.",
            )
        cleaned_data["notes"] = cleaned_data.get("notes", "").strip()
        cleaned_data["revision_reason"] = cleaned_data.get(
            "revision_reason", ""
        ).strip()
        return cleaned_data


class QuoteResponseForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_quote_id = forms.IntegerField(widget=forms.HiddenInput)
    expected_quote_revision = forms.IntegerField(widget=forms.HiddenInput)


class QuoteChangeRequestForm(QuoteResponseForm):
    message = forms.CharField(
        label="Nội dung cần trao đổi",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def clean_message(self):
        value = self.cleaned_data["message"].strip()
        if not value:
            raise forms.ValidationError("Vui lòng ghi rõ nội dung cần trao đổi.")
        return value


class ProductionCheckForm(forms.Form):
    passed = forms.ChoiceField(
        label="Kết quả kiểm tra",
        choices=(("yes", "Đạt"), ("no", "Chưa đạt")),
        widget=forms.RadioSelect,
    )
    notes = forms.CharField(
        label="Ghi chú / lý do",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def clean(self):
        cleaned_data = super().clean()
        notes = cleaned_data.get("notes", "").strip()
        cleaned_data["notes"] = notes
        if cleaned_data.get("passed") == "no" and not notes:
            self.add_error("notes", "Khi chưa đạt, phải ghi rõ lý do.")
        return cleaned_data


class CakeReviewForm(forms.Form):
    photo = forms.ImageField(
        label="Ảnh bánh",
        validators=[validate_catalog_image],
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/webp"}
        ),
        help_text="JPEG, PNG hoặc WebP; tối đa 5 MiB và 20 megapixel.",
    )
    description = forms.CharField(
        label="Mô tả ảnh gửi khách",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    replacement_reason = forms.CharField(
        label="Lý do thay ảnh đang chờ duyệt",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, order=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and order:
            self.initial["expected_version"] = order.version
            if order.status == Order.Status.CHO_DUYET_ANH:
                self.fields["replacement_reason"].required = True
                self.fields["replacement_reason"].help_text = (
                    "Bắt buộc khi thay thế ảnh phiên bản đang chờ khách duyệt."
                )

    def clean_description(self):
        return self.cleaned_data["description"].strip()

    def clean_replacement_reason(self):
        return self.cleaned_data["replacement_reason"].strip()


class RevisionRequestForm(forms.Form):
    message = forms.CharField(
        label="Nội dung muốn chỉnh sửa",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_review_id = forms.IntegerField(widget=forms.HiddenInput)
    expected_review_revision = forms.IntegerField(widget=forms.HiddenInput)

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError("Vui lòng mô tả nội dung cần chỉnh sửa.")
        return message


class RevisionClassificationForm(forms.Form):
    scope = forms.ChoiceField(
        label="Phạm vi chỉnh sửa",
        choices=(
            (RevisionRequest.Scope.WITHIN, "Trong phạm vi đã thống nhất"),
            (RevisionRequest.Scope.OUTSIDE, "Vượt phạm vi đã thống nhất"),
        ),
    )
    reason = forms.CharField(
        label="Lý do phân loại",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    approval_summary = forms.CharField(
        label="Tóm tắt gửi chủ duyệt",
        max_length=500,
        required=False,
    )
    proposed_plan = forms.CharField(
        label="Phương án đề xuất",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def clean(self):
        cleaned_data = super().clean()
        for field in ("reason", "approval_summary", "proposed_plan"):
            if field in cleaned_data:
                cleaned_data[field] = cleaned_data[field].strip()
        if cleaned_data.get("scope") == RevisionRequest.Scope.OUTSIDE:
            if not cleaned_data.get("approval_summary"):
                self.add_error("approval_summary", "Nhập tóm tắt gửi chủ duyệt.")
            if not cleaned_data.get("proposed_plan"):
                self.add_error("proposed_plan", "Nhập phương án đề xuất.")
        if cleaned_data.get("scope") == RevisionRequest.Scope.WITHIN:
            if not cleaned_data.get("reason"):
                self.add_error("reason", "Nhập lý do xác định chỉnh sửa trong phạm vi.")
        elif not cleaned_data.get("reason"):
            self.add_error("reason", "Nhập lý do xác định chỉnh sửa vượt phạm vi.")
        return cleaned_data


class OwnerApprovalForm(forms.Form):
    decision = forms.ChoiceField(
        label="Quyết định",
        choices=(("approve", "Chấp thuận"), ("reject", "Từ chối")),
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        label="Lý do quyết định",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    requires_change_agreement = forms.BooleanField(
        label="Cần khách hàng duyệt thay đổi giá hoặc lịch",
        required=False,
    )
    unit_price = forms.DecimalField(
        label="Đơn giá mới (VNĐ)",
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=0,
        required=False,
    )
    delivery_fee = forms.DecimalField(
        label="Phí giao mới (VNĐ)",
        min_value=Decimal("0"),
        max_digits=12,
        decimal_places=0,
        required=False,
    )
    proposed_delivery_at = forms.DateTimeField(
        label="Thời gian giao mới",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        required=False,
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_approval_id = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, order=None, approval=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and order:
            self.initial["expected_version"] = order.version
        if not self.is_bound and approval:
            self.initial["expected_approval_id"] = approval.pk
            base_quote = order.active_change_agreement or order.current_quote
            if base_quote:
                self.initial["unit_price"] = base_quote.unit_price
                self.initial["delivery_fee"] = base_quote.delivery_fee
                self.initial["proposed_delivery_at"] = base_quote.proposed_delivery_at

    def clean(self):
        cleaned_data = super().clean()
        reason = cleaned_data.get("reason", "").strip()
        cleaned_data["reason"] = reason
        if not reason:
            self.add_error("reason", "Vui lòng ghi lý do quyết định.")
        if cleaned_data.get("decision") == "approve" and cleaned_data.get(
            "requires_change_agreement"
        ):
            for field in ("unit_price", "delivery_fee", "proposed_delivery_at"):
                if cleaned_data.get(field) is None:
                    self.add_error(
                        field,
                        "Nhập đầy đủ điều kiện thương mại của phụ lục.",
                    )
            delivery_at = cleaned_data.get("proposed_delivery_at")
            if delivery_at and delivery_at <= timezone.now():
                self.add_error(
                    "proposed_delivery_at",
                    "Lịch giao mới phải ở tương lai.",
                )
        return cleaned_data


class AgreementResponseForm(forms.Form):
    expected_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_agreement_id = forms.IntegerField(widget=forms.HiddenInput)
    expected_agreement_revision = forms.IntegerField(widget=forms.HiddenInput)
    message = forms.CharField(
        label="Ghi chú phản hồi",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean_message(self):
        return self.cleaned_data["message"].strip()


class ContinueDiscussionForm(forms.Form):
    message = forms.CharField(
        label="Nội dung trao đổi tiếp",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_version = forms.IntegerField(widget=forms.HiddenInput)

    def clean_message(self):
        message = self.cleaned_data["message"].strip()
        if not message:
            raise forms.ValidationError("Vui lòng nhập nội dung trao đổi.")
        return message


class DeliveryStartForm(forms.Form):
    expected_version = forms.IntegerField(widget=forms.HiddenInput)


class CustomerPrepaymentReportForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)


class DeliveryConfirmationForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_attempt_revision = forms.IntegerField(widget=forms.HiddenInput)
    note = forms.CharField(
        label="Ghi chú bàn giao",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    proof = forms.ImageField(
        label="Ảnh minh chứng bàn giao (không bắt buộc)",
        required=False,
        validators=[validate_catalog_image],
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/webp"}
        ),
    )

    def clean_note(self):
        return self.cleaned_data["note"].strip()


class DeliveryFailureForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    reason = forms.CharField(
        label="Lý do giao thất bại",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Vui lòng ghi lý do giao thất bại.")
        return reason


class DeliveryIncidentForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_attempt_revision = forms.IntegerField(widget=forms.HiddenInput)
    incident_type = forms.ChoiceField(
        label="Loại sự cố",
        choices=OrderIncident.Type.choices,
    )
    description = forms.CharField(
        label="Mô tả sự cố",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    proof = forms.ImageField(
        label="Ảnh minh chứng (không bắt buộc)",
        required=False,
        validators=[validate_catalog_image],
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/webp"}
        ),
        help_text="Ảnh lưu riêng tư; JPEG, PNG hoặc WebP, tối đa 5 MiB và 20 megapixel.",
    )

    def clean_description(self):
        value = self.cleaned_data["description"].strip()
        if not value:
            raise forms.ValidationError("Vui lòng mô tả sự cố.")
        return value


class RedeliveryForm(forms.Form):
    expected_version = forms.IntegerField(widget=forms.HiddenInput)
    scheduled_at = forms.DateTimeField(
        label="Lịch giao lại đã thống nhất",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    customer_agreement = forms.CharField(
        label="Ghi nhận thỏa thuận với khách",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    reason = forms.CharField(
        label="Lý do giao lại",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self):
        cleaned = super().clean()
        for field in ("customer_agreement", "reason"):
            if field in cleaned:
                cleaned[field] = cleaned[field].strip()
                if not cleaned[field]:
                    self.add_error(field, "Trường này không được để trống.")
        if cleaned.get("scheduled_at") and cleaned["scheduled_at"] <= timezone.now():
            self.add_error("scheduled_at", "Lịch giao lại phải ở tương lai.")
        return cleaned


class IncidentProposalForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_incident_revision = forms.IntegerField(widget=forms.HiddenInput)
    resolution = forms.ChoiceField(
        label="Phương án đề xuất cho chủ cửa hàng",
        choices=OrderIncident.Resolution.choices,
    )
    plan = forms.CharField(
        label="Chi tiết phương án",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "Phương án này không tự đổi giá, địa chỉ hoặc lịch đã chốt. "
            "Mọi thay đổi điều khoản cần phụ lục được OWNER duyệt và khách chấp nhận."
        ),
    )
    customer_agreement = forms.CharField(
        label="Thỏa thuận với khách (bắt buộc khi giao lại)",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    scheduled_at = forms.DateTimeField(
        label="Lịch giao lại",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        required=False,
    )

    def clean(self):
        cleaned = super().clean()
        for field in ("plan", "customer_agreement"):
            if field in cleaned:
                cleaned[field] = cleaned[field].strip()
        if not cleaned.get("plan"):
            self.add_error("plan", "Mô tả phương án cụ thể.")
        if cleaned.get("resolution") == OrderIncident.Resolution.REDELIVER:
            if not cleaned.get("customer_agreement"):
                self.add_error(
                    "customer_agreement",
                    "Ghi rõ khách đã đồng ý lịch và nội dung giao lại.",
                )
            if cleaned.get("scheduled_at") is None:
                self.add_error("scheduled_at", "Chọn lịch giao lại.")
            elif cleaned["scheduled_at"] <= timezone.now():
                self.add_error("scheduled_at", "Lịch giao lại phải ở tương lai.")
        return cleaned


class IncidentDecisionForm(forms.Form):
    decision = forms.ChoiceField(
        label="Quyết định",
        choices=(("approve", "Duyệt phương án"), ("reject", "Yêu cầu phương án khác")),
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        label="Lý do quyết định",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_incident_revision = forms.IntegerField(widget=forms.HiddenInput)

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Vui lòng ghi lý do quyết định.")
        return reason


class IncidentCustomerResponseForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_incident_revision = forms.IntegerField(widget=forms.HiddenInput)
    message = forms.CharField(
        label="Ghi chú xác nhận",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean_message(self):
        return self.cleaned_data["message"].strip()


class IncidentExecutionForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    expected_incident_revision = forms.IntegerField(widget=forms.HiddenInput)
    result = forms.CharField(
        label="Kết quả thực hiện phương án",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean_result(self):
        result = self.cleaned_data["result"].strip()
        if not result:
            raise forms.ValidationError("Ghi rõ kết quả thực hiện.")
        return result


class PaymentForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    idempotency_key = forms.UUIDField(
        widget=forms.HiddenInput,
        initial=uuid.uuid4,
    )
    method = forms.ChoiceField(label="Phương thức", choices=Payment.Method.choices)
    amount = forms.DecimalField(
        label="Số tiền thực nhận (VNĐ)",
        min_value=Decimal("1"),
        max_digits=15,
        decimal_places=0,
        widget=forms.NumberInput(attrs={"step": "1"}),
    )
    received_at = forms.DateTimeField(
        label="Thời điểm thực nhận",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        initial=timezone.now,
    )
    transaction_reference = forms.CharField(
        label="Mã tham chiếu chuyển khoản (nếu có)",
        max_length=120,
        required=False,
    )
    notes = forms.CharField(
        label="Ghi chú đối chiếu",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean_transaction_reference(self):
        return self.cleaned_data["transaction_reference"].strip()

    def clean_notes(self):
        return self.cleaned_data["notes"].strip()


class RefundForm(forms.Form):
    expected_order_version = forms.IntegerField(widget=forms.HiddenInput)
    idempotency_key = forms.UUIDField(
        widget=forms.HiddenInput,
        initial=uuid.uuid4,
    )
    payment_id = forms.ChoiceField(label="Khoản thu gốc")
    method = forms.ChoiceField(label="Phương thức hoàn", choices=Refund.Method.choices)
    amount = forms.DecimalField(
        label="Số tiền đã thực hoàn (VNĐ)",
        min_value=Decimal("1"),
        max_digits=15,
        decimal_places=0,
        widget=forms.NumberInput(attrs={"step": "1"}),
    )
    refunded_at = forms.DateTimeField(
        label="Thời điểm thực hoàn",
        input_formats=DATETIME_INPUT_FORMATS,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
        initial=timezone.now,
    )
    transaction_reference = forms.CharField(
        label="Mã tham chiếu hoàn tiền",
        max_length=120,
        required=False,
    )
    notes = forms.CharField(
        label="Ghi chú",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    proof = forms.ImageField(
        label="Minh chứng hoàn tiền (không bắt buộc)",
        required=False,
        validators=[validate_catalog_image],
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/jpeg,image/png,image/webp"}
        ),
    )

    def __init__(self, *args, payments=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_id"].choices = [
            (str(payment.pk), f"Khoản thu #{payment.pk} · {payment.amount:.0f} VNĐ")
            for payment in payments
        ]

    def clean_transaction_reference(self):
        return self.cleaned_data["transaction_reference"].strip()

    def clean_notes(self):
        return self.cleaned_data["notes"].strip()


class OrderReviewForm(forms.Form):
    rating = forms.TypedChoiceField(
        label="Điểm đánh giá",
        coerce=int,
        choices=(
            (1, "1 sao — Chưa hài lòng"),
            (2, "2 sao — Chưa tốt"),
            (3, "3 sao — Bình thường"),
            (4, "4 sao — Hài lòng"),
            (5, "5 sao — Rất hài lòng"),
        ),
        widget=forms.RadioSelect,
        error_messages={"invalid_choice": "Chọn mức đánh giá từ 1 đến 5 sao."},
    )
    comment = forms.CharField(
        label="Nhận xét (không bắt buộc)",
        max_length=2000,
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "maxlength": 2000,
                "placeholder": "Chia sẻ trải nghiệm của bạn với cửa hàng.",
            }
        ),
    )

    def clean_comment(self):
        return self.cleaned_data["comment"].strip()


class ReviewReplyForm(forms.Form):
    content = forms.CharField(
        label="Nội dung phản hồi",
        max_length=2000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "maxlength": 2000,
                "placeholder": "Nhập phản hồi của cửa hàng.",
            }
        ),
    )

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if not content:
            raise forms.ValidationError("Vui lòng nhập nội dung phản hồi.")
        return content


class OwnerReviewFilterForm(forms.Form):
    query = forms.CharField(
        label="Mã đơn",
        max_length=16,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "BBC-..."}),
    )
    rating = forms.ChoiceField(
        label="Số sao",
        required=False,
        choices=(
            ("", "Tất cả"),
            (1, "1 sao"),
            (2, "2 sao"),
            (3, "3 sao"),
            (4, "4 sao"),
            (5, "5 sao"),
        ),
    )
    replied = forms.ChoiceField(
        label="Phản hồi",
        required=False,
        choices=(
            ("", "Tất cả"),
            ("yes", "Đã phản hồi"),
            ("no", "Chưa phản hồi"),
        ),
    )
    date_from = forms.DateField(
        label="Từ ngày",
        required=False,
        input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="Đến ngày",
        required=False,
        input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("date_from")
        end = cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "Ngày kết thúc phải từ ngày bắt đầu trở đi.")
        cleaned["query"] = cleaned.get("query", "").strip()
        return cleaned


class ReviewPeriodFilterForm(forms.Form):
    date_from = forms.DateField(
        label="Từ ngày",
        input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="Đến ngày",
        input_formats=("%Y-%m-%d",),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("date_from")
        end = cleaned.get("date_to")
        if start and end and start > end:
            self.add_error("date_to", "Ngày kết thúc phải từ ngày bắt đầu trở đi.")
        return cleaned
