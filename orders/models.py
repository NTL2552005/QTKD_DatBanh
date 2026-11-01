import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from catalog.validators import validate_catalog_image

from .storage import private_order_photo_storage


def new_order_code():
    return f"BBC-{uuid.uuid4().hex[:12].upper()}"


def order_review_upload_to(instance, filename):
    extension = getattr(instance.photo, "catalog_image_extension", ".jpg")
    return f"order-reviews/{instance.order_id}/{uuid.uuid4().hex}{extension}"


def private_evidence_upload_to(instance, filename):
    extension = getattr(instance.proof, "catalog_image_extension", ".jpg")
    return f"order-evidence/{instance.order_id}/{uuid.uuid4().hex}{extension}"


class Order(models.Model):
    class Status(models.TextChoices):
        CHO_KIEM_TRA = "CHO_KIEM_TRA", "Chờ kiểm tra"
        CAN_BO_SUNG = "CAN_BO_SUNG", "Cần bổ sung"
        CHO_CHU_DUYET_PHUONG_AN = (
            "CHO_CHU_DUYET_PHUONG_AN",
            "Chờ chủ duyệt phương án",
        )
        CHO_CHU_DUYET_BAO_GIA = (
            "CHO_CHU_DUYET_BAO_GIA",
            "Chờ chủ xử lý tiếp nhận",
        )
        CHO_KHACH_PHAN_HOI_PHUONG_AN = (
            "CHO_KHACH_PHAN_HOI_PHUONG_AN",
            "Chờ khách phản hồi phương án",
        )
        CHO_KHACH_XAC_NHAN = "CHO_KHACH_XAC_NHAN", "Chờ khách xác nhận"
        CHO_TRAO_DOI = "CHO_TRAO_DOI", "Chờ trao đổi"
        DA_XAC_NHAN = "DA_XAC_NHAN", "Đã xác nhận"
        TU_CHOI = "TU_CHOI", "Từ chối"
        DANG_LAM = "DANG_LAM", "Đang làm bánh"
        CHO_DUYET_ANH = "CHO_DUYET_ANH", "Chờ duyệt ảnh"
        CHO_XU_LY_CHINH_SUA = "CHO_XU_LY_CHINH_SUA", "Chờ xử lý chỉnh sửa"
        CHO_CHU_DUYET = "CHO_CHU_DUYET", "Chờ chủ cửa hàng duyệt"
        SAN_SANG_GIAO = "SAN_SANG_GIAO", "Sẵn sàng giao"
        CHO_KHACH_DUYET_THAY_DOI = (
            "CHO_KHACH_DUYET_THAY_DOI",
            "Chờ khách duyệt phụ lục",
        )
        CHO_TRAO_DOI_CHINH_SUA = (
            "CHO_TRAO_DOI_CHINH_SUA",
            "Đang trao đổi chỉnh sửa",
        )
        DANG_GIAO = "DANG_GIAO", "Đang giao"
        GIAO_THAT_BAI = "GIAO_THAT_BAI", "Giao thất bại"
        CHO_XU_LY_SU_CO = "CHO_XU_LY_SU_CO", "Chờ xử lý sự cố"
        DA_GIAO_CHO_THANH_TOAN = (
            "DA_GIAO_CHO_THANH_TOAN",
            "Đã giao, chờ thanh toán",
        )
        HOAN_TAT = "HOAN_TAT", "Hoàn tất"
        DA_HUY = "DA_HUY", "Đã hủy"

    class PaymentStatus(models.TextChoices):
        UNPAID = "UNPAID", "Chưa thu"
        PARTIAL = "PARTIAL", "Thu một phần"
        PAID = "PAID", "Đã thu đủ"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="khách hàng",
    )
    cake_template = models.ForeignKey(
        "catalog.CakeTemplate",
        on_delete=models.PROTECT,
        related_name="orders",
        verbose_name="mẫu bánh",
    )
    code = models.CharField("mã đơn", max_length=16, unique=True, default=new_order_code)
    request_key = models.UUIDField("mã yêu cầu gửi", unique=True)
    payload_fingerprint = models.CharField(max_length=64)
    cake_name_snapshot = models.CharField("tên mẫu tại thời điểm đặt", max_length=150)
    reference_price_snapshot = models.DecimalField(
        "giá tham khảo tại thời điểm đặt",
        max_digits=12,
        decimal_places=0,
    )
    size = models.CharField("kích thước", max_length=80)
    flavor = models.CharField("hương vị", max_length=100)
    quantity = models.PositiveIntegerField(
        "số lượng",
        validators=[MinValueValidator(1)],
    )
    inscription = models.CharField("nội dung viết trên bánh", max_length=250, blank=True)
    decoration_request = models.TextField("yêu cầu trang trí", max_length=2000, blank=True)
    recipient_name = models.CharField("tên người nhận", max_length=150)
    recipient_phone = models.CharField("số điện thoại người nhận", max_length=30)
    delivery_address = models.CharField("địa chỉ giao", max_length=500)
    desired_delivery_at = models.DateTimeField("thời gian giao mong muốn")
    note = models.TextField("ghi chú", max_length=2000, blank=True)
    status = models.CharField(
        "trạng thái",
        max_length=32,
        choices=Status.choices,
        default=Status.CHO_KIEM_TRA,
    )
    payment_status = models.CharField(
        "tình trạng thanh toán",
        max_length=12,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    intake_handler_role = models.CharField(
        "vai trò đang xử lý tiếp nhận",
        max_length=8,
        choices=(("STAFF", "Nhân viên"), ("OWNER", "Chủ cửa hàng")),
        default="STAFF",
    )
    rejection_reason = models.TextField(
        "lý do từ chối hiển thị cho khách",
        blank=True,
    )
    delivery_rescheduled_at = models.DateTimeField(
        "lịch giao lại đã thống nhất",
        null=True,
        blank=True,
    )
    delivery_reschedule_note = models.TextField(
        "ghi chú thống nhất giao lại",
        blank=True,
    )
    needs_supplement = models.BooleanField("đang chờ khách bổ sung", default=False)
    request_revision = models.PositiveIntegerField(
        "phiên bản yêu cầu hiện hành",
        default=1,
    )
    materials_available = models.BooleanField(
        "nguyên liệu đáp ứng",
        null=True,
        blank=True,
    )
    schedule_available = models.BooleanField(
        "lịch làm đáp ứng",
        null=True,
        blank=True,
    )
    staff_check_notes = models.TextField("ghi chú kiểm tra của nhân viên", blank=True)
    current_quote = models.ForeignKey(
        "orders.Quote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="báo giá hiện hành",
    )
    current_review = models.ForeignKey(
        "orders.CakeReviewVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="phiên bản ảnh hiện hành",
    )
    active_change_agreement = models.ForeignKey(
        "orders.ChangeAgreement",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="phụ lục thay đổi đang áp dụng",
    )
    production_round = models.PositiveIntegerField(
        "lần sản xuất",
        default=1,
        validators=[MinValueValidator(1)],
    )
    version = models.PositiveIntegerField(default=1)
    completed_at = models.DateTimeField(
        "thời điểm hoàn tất",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("ngày tạo", auto_now_add=True)
    updated_at = models.DateTimeField("cập nhật lần cuối", auto_now=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "đơn hàng"
        verbose_name_plural = "đơn hàng"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=1),
                name="orders_order_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(reference_price_snapshot__gte=0),
                name="orders_order_reference_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(production_round__gte=1),
                name="orders_order_production_round_positive",
            ),
            models.CheckConstraint(
                condition=Q(request_revision__gte=1),
                name="orders_order_request_revision_positive",
            ),
        ]

    def __str__(self):
        return self.code


class StaffAvailabilityCheck(models.Model):
    class AuthorityClassification(models.TextChoices):
        UNCLASSIFIED = "UNCLASSIFIED", "Chưa phân loại"
        IN_SCOPE = "IN_SCOPE", "Trong quyền nhân viên"
        OWNER_REQUIRED = "OWNER_REQUIRED", "Cần chủ phê duyệt"
        NEEDS_INFO = "NEEDS_INFO", "Chưa đủ thông tin phân loại"

    class Reason(models.TextChoices):
        NONE = "", "Không có trở ngại"
        INGREDIENT = "INGREDIENT", "Thiếu nguyên liệu"
        DECORATION = "DECORATION", "Thiếu phụ kiện trang trí"
        CAPACITY = "CAPACITY", "Thiếu thời gian hoặc năng lực sản xuất"
        DELIVERY = "DELIVERY", "Không đáp ứng lịch giao"
        OTHER = "OTHER", "Lý do khác"

    class AuthorityReason(models.TextChoices):
        DESIGN = "DESIGN", "Thiết kế/kết cấu ngoài phạm vi"
        RUSH = "RUSH", "Đơn gấp cần ngoại lệ thời gian"
        DISCOUNT = "DISCOUNT", "Giảm giá/điều kiện giá ngoài chính sách"
        COMMITMENT = "COMMITMENT", "Cam kết đặc biệt về sản phẩm/giao hàng"
        RESOURCES = "RESOURCES", "Chi phí/nguồn lực đặc biệt"
        OTHER = "OTHER", "Trường hợp khác"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="availability_checks",
        verbose_name="đơn hàng",
    )
    request_revision = models.PositiveIntegerField("phiên bản yêu cầu đã kiểm tra")
    materials_available = models.BooleanField(
        "nguyên liệu đáp ứng",
        null=True,
        blank=True,
    )
    schedule_available = models.BooleanField(
        "lịch làm đáp ứng",
        null=True,
        blank=True,
    )
    reason = models.CharField(
        "nguyên nhân chưa đáp ứng",
        max_length=16,
        choices=Reason.choices,
        blank=True,
    )
    reason_details = models.TextField("mô tả nguyên nhân", max_length=2000, blank=True)
    notes = models.TextField("nội dung kiểm tra", max_length=2000)
    authority_classification = models.CharField(
        "phân loại phạm vi quyền",
        max_length=20,
        choices=AuthorityClassification.choices,
        default=AuthorityClassification.UNCLASSIFIED,
    )
    authority_reasons = models.JSONField("lý do cần chủ duyệt", default=list, blank=True)
    authority_details = models.TextField("mô tả phân loại", max_length=2000, blank=True)
    authority_basis = models.TextField("căn cứ trong quyền nhân viên", max_length=1000, blank=True)
    request_snapshot = models.JSONField("snapshot yêu cầu", default=dict)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="order_availability_checks",
        verbose_name="nhân viên kiểm tra",
    )
    checked_at = models.DateTimeField("thời điểm kiểm tra", auto_now_add=True)

    class Meta:
        ordering = ("-checked_at", "-pk")
        indexes = [
            models.Index(fields=("order", "request_revision", "-checked_at")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(request_revision__gte=1),
                name="orders_availability_request_revision_positive",
            ),
        ]
        verbose_name = "kết quả kiểm tra khả năng đáp ứng"
        verbose_name_plural = "kết quả kiểm tra khả năng đáp ứng"

    _IMMUTABLE_FIELDS = (
        "order_id",
        "request_revision",
        "materials_available",
        "schedule_available",
        "reason",
        "reason_details",
        "notes",
        "authority_classification",
        "authority_reasons",
        "authority_details",
        "authority_basis",
        "request_snapshot",
        "checked_by_id",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                *self._IMMUTABLE_FIELDS
            ).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("Kết quả kiểm tra và phân loại đã ghi là bất biến.")
        super().save(*args, **kwargs)

    @property
    def is_fully_available(self):
        return (
            self.materials_available is True
            and self.schedule_available is True
        )

    @property
    def has_availability_failure(self):
        return (
            self.materials_available is False
            or self.schedule_available is False
        )


class OwnerIntakeCheck(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="owner_intake_checks",
        verbose_name="đơn hàng",
    )
    request_revision = models.PositiveIntegerField("phiên bản yêu cầu đã kiểm tra")
    materials_available = models.BooleanField("nguyên liệu đáp ứng")
    schedule_available = models.BooleanField("lịch làm đáp ứng")
    notes = models.TextField("nội dung kiểm tra", max_length=2000)
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owner_intake_checks",
        verbose_name="chủ cửa hàng kiểm tra",
    )
    checked_at = models.DateTimeField("thời điểm kiểm tra", auto_now_add=True)

    class Meta:
        ordering = ("-checked_at", "-pk")
        indexes = [
            models.Index(fields=("order", "request_revision", "-checked_at")),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(request_revision__gte=1),
                name="orders_owner_check_revision_positive",
            ),
        ]
        verbose_name = "kết quả kiểm tra tiếp nhận của chủ cửa hàng"
        verbose_name_plural = "kết quả kiểm tra tiếp nhận của chủ cửa hàng"

    _IMMUTABLE_FIELDS = (
        "order_id",
        "request_revision",
        "materials_available",
        "schedule_available",
        "notes",
        "checked_by_id",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                *self._IMMUTABLE_FIELDS
            ).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("Kết quả kiểm tra của chủ đã ghi là bất biến.")
        super().save(*args, **kwargs)

    @property
    def is_fully_available(self):
        return self.materials_available and self.schedule_available


class OrderAlternative(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Bản nháp"
        PENDING_OWNER = "PENDING_OWNER", "Chờ chủ duyệt"
        APPROVED = "APPROVED", "Đã được chủ duyệt"
        PENDING_CUSTOMER = "PENDING_CUSTOMER", "Chờ khách phản hồi"
        ACCEPTED = "ACCEPTED", "Khách đã đồng ý"
        DECLINED = "DECLINED", "Khách từ chối"
        OWNER_REJECTED = "OWNER_REJECTED", "Chủ cửa hàng từ chối"
        SUPERSEDED = "SUPERSEDED", "Bị thay thế"

    ACTIVE_STATUSES = (
        Status.DRAFT,
        Status.PENDING_OWNER,
        Status.APPROVED,
        Status.PENDING_CUSTOMER,
    )

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="alternatives",
        verbose_name="đơn hàng",
    )
    version_number = models.PositiveIntegerField("phiên bản")
    base_request_revision = models.PositiveIntegerField("phiên bản yêu cầu làm căn cứ")
    availability_check = models.ForeignKey(
        StaffAvailabilityCheck,
        on_delete=models.PROTECT,
        related_name="alternatives",
        verbose_name="kết quả kiểm tra không đáp ứng",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_order_alternatives",
        verbose_name="nhân viên đề xuất",
    )
    request_before = models.JSONField("snapshot yêu cầu trước phương án", default=dict)
    request_after = models.JSONField("snapshot yêu cầu đề xuất", default=dict)
    cake_template = models.ForeignKey(
        "catalog.CakeTemplate",
        on_delete=models.PROTECT,
        related_name="order_alternatives",
        verbose_name="mẫu bánh đề xuất",
    )
    cake_name_snapshot = models.CharField("tên mẫu đề xuất", max_length=150)
    reference_price_snapshot = models.DecimalField(
        "giá tham khảo tại thời điểm đề xuất",
        max_digits=12,
        decimal_places=0,
    )
    size = models.CharField("kích thước đề xuất", max_length=80)
    flavor = models.CharField("hương vị đề xuất", max_length=100)
    quantity = models.PositiveIntegerField(
        "số lượng đề xuất",
        validators=[MinValueValidator(1)],
    )
    inscription = models.CharField("nội dung trên bánh đề xuất", max_length=250, blank=True)
    decoration_request = models.TextField(
        "trang trí đề xuất",
        max_length=2000,
        blank=True,
    )
    desired_delivery_at = models.DateTimeField("thời gian giao đề xuất")
    difference_summary = models.TextField("giải thích khác biệt", max_length=2000)
    estimated_unit_price = models.DecimalField(
        "đơn giá dự kiến, chưa chốt",
        max_digits=12,
        decimal_places=0,
        null=True,
        blank=True,
    )
    requires_owner_approval = models.BooleanField("cần chủ duyệt", default=False)
    status = models.CharField(
        "trạng thái phương án",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    owner_decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_order_alternatives",
        null=True,
        blank=True,
        verbose_name="chủ cửa hàng quyết định",
    )
    owner_decision_reason = models.TextField("lý do quyết định của chủ", blank=True)
    owner_decided_at = models.DateTimeField("thời điểm chủ quyết định", null=True, blank=True)
    customer_response = models.TextField("phản hồi khách", blank=True)
    customer_responded_at = models.DateTimeField(
        "thời điểm khách phản hồi",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("thời điểm tạo", auto_now_add=True)
    sent_at = models.DateTimeField("thời điểm gửi khách", null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-version_number",)
        constraints = [
            models.UniqueConstraint(
                fields=("order", "version_number"),
                name="orders_alternative_order_version_unique",
            ),
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(
                    status__in=(
                        "DRAFT",
                        "PENDING_OWNER",
                        "APPROVED",
                        "PENDING_CUSTOMER",
                    )
                ),
                name="orders_one_active_alternative_per_order",
            ),
            models.CheckConstraint(
                condition=(
                    Q(version_number__gte=1)
                    & Q(base_request_revision__gte=1)
                    & Q(quantity__gte=1)
                    & Q(reference_price_snapshot__gte=0)
                ),
                name="orders_alternative_values_positive",
            ),
            models.CheckConstraint(
                condition=Q(estimated_unit_price__isnull=True)
                | Q(estimated_unit_price__gte=0),
                name="orders_alternative_estimated_price_nonnegative",
            ),
        ]
        verbose_name = "phương án thay thế"
        verbose_name_plural = "phương án thay thế"

    _IMMUTABLE_FIELDS = (
        "order_id",
        "version_number",
        "base_request_revision",
        "availability_check_id",
        "created_by_id",
        "request_before",
        "request_after",
        "cake_template_id",
        "cake_name_snapshot",
        "reference_price_snapshot",
        "size",
        "flavor",
        "quantity",
        "inscription",
        "decoration_request",
        "desired_delivery_at",
        "difference_summary",
        "estimated_unit_price",
        "requires_owner_approval",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                *self._IMMUTABLE_FIELDS
            ).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("Nội dung phương án đã tạo là bất biến.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} · phương án v{self.version_number}"


class Quote(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Chờ khách phản hồi"
        ACCEPTED = "ACCEPTED", "Đã chấp nhận"
        CHANGE_REQUESTED = "CHANGE_REQUESTED", "Yêu cầu trao đổi"
        REPLACED = "REPLACED", "Bị thay thế"
        EXPIRED = "EXPIRED", "Hết hạn"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="quotes",
        verbose_name="đơn hàng",
    )
    version_number = models.PositiveIntegerField("phiên bản")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_quotes",
        verbose_name="nhân viên lập",
    )
    cake_name_snapshot = models.CharField(max_length=150)
    size_snapshot = models.CharField(max_length=80)
    flavor_snapshot = models.CharField(max_length=100)
    quantity_snapshot = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    inscription_snapshot = models.CharField(max_length=250, blank=True)
    decoration_snapshot = models.TextField(blank=True)
    recipient_name_snapshot = models.CharField(max_length=150)
    recipient_phone_snapshot = models.CharField(max_length=30)
    delivery_address_snapshot = models.CharField(max_length=500)
    unit_price = models.DecimalField("đơn giá bánh (VNĐ)", max_digits=12, decimal_places=0)
    cake_total = models.DecimalField("thành tiền bánh (VNĐ)", max_digits=14, decimal_places=0)
    delivery_fee = models.DecimalField("phí giao hàng (VNĐ)", max_digits=12, decimal_places=0)
    total = models.DecimalField("tổng tiền (VNĐ)", max_digits=15, decimal_places=0)
    proposed_delivery_at = models.DateTimeField("thời gian giao đề xuất")
    notes = models.TextField("ghi chú báo giá", blank=True)
    revision_reason = models.TextField("lý do tạo báo giá mới", blank=True)
    expires_at = models.DateTimeField("hạn chấp nhận")
    status = models.CharField(
        "trạng thái báo giá",
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )
    sent_at = models.DateTimeField("thời điểm gửi", auto_now_add=True)
    responded_at = models.DateTimeField("thời điểm phản hồi", null=True, blank=True)
    customer_response = models.TextField("phản hồi khách hàng", blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-version_number",)
        verbose_name = "báo giá"
        verbose_name_plural = "báo giá"
        constraints = [
            models.UniqueConstraint(
                fields=("order", "version_number"),
                name="orders_quote_order_version_unique",
            ),
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(status="PENDING"),
                name="orders_one_pending_quote_per_order",
            ),
            models.CheckConstraint(
                condition=Q(version_number__gte=1) & Q(quantity_snapshot__gte=1),
                name="orders_quote_version_quantity_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(unit_price__gte=0)
                    & Q(cake_total__gte=0)
                    & Q(delivery_fee__gte=0)
                    & Q(total__gte=0)
                ),
                name="orders_quote_money_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(cake_total=F("unit_price") * F("quantity_snapshot")),
                name="orders_quote_cake_total_matches",
            ),
            models.CheckConstraint(
                condition=Q(total=F("cake_total") + F("delivery_fee")),
                name="orders_quote_total_matches",
            ),
        ]

    _COMMERCIAL_FIELDS = (
        "order_id",
        "version_number",
        "cake_name_snapshot",
        "size_snapshot",
        "flavor_snapshot",
        "quantity_snapshot",
        "inscription_snapshot",
        "decoration_snapshot",
        "recipient_name_snapshot",
        "recipient_phone_snapshot",
        "delivery_address_snapshot",
        "unit_price",
        "cake_total",
        "delivery_fee",
        "total",
        "proposed_delivery_at",
        "notes",
        "revision_reason",
        "expires_at",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(*self._COMMERCIAL_FIELDS).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in self._COMMERCIAL_FIELDS
            ):
                raise ValueError("Nội dung thương mại của báo giá đã gửi là bất biến.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} - v{self.version_number}"


class IntakeQuoteApproval(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Chờ chủ duyệt"
        APPROVED = "APPROVED", "Đã duyệt"
        REJECTED = "REJECTED", "Chủ từ chối"
        CONSUMED = "CONSUMED", "Đã dùng gửi báo giá"
        STALE = "STALE", "Hết hiệu lực do yêu cầu thay đổi"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="intake_quote_approvals",
        verbose_name="đơn hàng",
    )
    availability_check = models.ForeignKey(
        StaffAvailabilityCheck,
        on_delete=models.PROTECT,
        related_name="quote_approvals",
        verbose_name="kết quả kiểm tra và phân loại",
    )
    request_revision = models.PositiveIntegerField("phiên bản yêu cầu")
    request_snapshot = models.JSONField("snapshot yêu cầu", default=dict)
    authority_reasons = models.JSONField("lý do vượt quyền", default=list)
    authority_details = models.TextField("mô tả vượt quyền", max_length=2000)
    quote_snapshot = models.JSONField("điều khoản báo giá chờ duyệt", default=dict)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_intake_quote_approvals",
        verbose_name="nhân viên gửi duyệt",
    )
    status = models.CharField(
        "trạng thái phê duyệt",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_intake_quote_approvals",
        null=True,
        blank=True,
        verbose_name="chủ cửa hàng quyết định",
    )
    decision_reason = models.TextField("lý do quyết định", blank=True)
    decided_at = models.DateTimeField("thời điểm quyết định", null=True, blank=True)
    customer_notice = models.TextField("thông báo khách sau khi chủ từ chối", blank=True)
    customer_notified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="notified_rejected_intake_quotes",
        null=True,
        blank=True,
        verbose_name="nhân viên đã thông báo khách",
    )
    customer_notified_at = models.DateTimeField(
        "thời điểm nhân viên thông báo khách",
        null=True,
        blank=True,
    )
    quote = models.OneToOneField(
        Quote,
        on_delete=models.PROTECT,
        related_name="intake_approval",
        null=True,
        blank=True,
        verbose_name="báo giá đã gửi theo phê duyệt",
    )
    revision = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField("thời điểm gửi duyệt", auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(status__in=("PENDING", "APPROVED")),
                name="orders_one_active_intake_quote_approval",
            ),
            models.CheckConstraint(
                condition=Q(request_revision__gte=1) & Q(revision__gte=1),
                name="orders_intake_quote_approval_versions_positive",
            ),
        ]
        verbose_name = "phê duyệt báo giá tiếp nhận"
        verbose_name_plural = "phê duyệt báo giá tiếp nhận"

    _IMMUTABLE_FIELDS = (
        "order_id",
        "availability_check_id",
        "request_revision",
        "request_snapshot",
        "authority_reasons",
        "authority_details",
        "quote_snapshot",
        "submitted_by_id",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                *self._IMMUTABLE_FIELDS
            ).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("Nội dung gửi OWNER duyệt là bất biến.")
        super().save(*args, **kwargs)


class OrderHistory(models.Model):
    class Action(models.TextChoices):
        ORDER_CREATED = "ORDER_CREATED", "Khách gửi đơn"
        CUSTOMER_UPDATED = "CUSTOMER_UPDATED", "Khách bổ sung yêu cầu"
        STAFF_CHECKED = "STAFF_CHECKED", "Nhân viên kiểm tra và phân loại tiếp nhận"
        INTAKE_TRANSFERRED_TO_OWNER = (
            "INTAKE_TRANSFERRED_TO_OWNER",
            "Nhân viên chuyển đơn cho chủ xử lý",
        )
        INTAKE_LEGACY_APPROVAL_CLOSED = (
            "INTAKE_LEGACY_APPROVAL_CLOSED",
            "Đóng yêu cầu duyệt báo giá theo quy trình cũ",
        )
        OWNER_INTAKE_CHECKED = (
            "OWNER_INTAKE_CHECKED",
            "Chủ cửa hàng kiểm tra khả năng tiếp nhận",
        )
        INTAKE_QUOTE_APPROVAL_SUBMITTED = (
            "INTAKE_QUOTE_APPROVAL_SUBMITTED",
            "Gửi chủ duyệt báo giá",
        )
        INTAKE_QUOTE_APPROVAL_DECIDED = (
            "INTAKE_QUOTE_APPROVAL_DECIDED",
            "Chủ quyết định báo giá",
        )
        INTAKE_OWNER_REJECTION_NOTIFIED = (
            "INTAKE_OWNER_REJECTION_NOTIFIED",
            "Nhân viên thông báo khách về việc không tiếp nhận",
        )
        ALTERNATIVE_DRAFTED = "ALTERNATIVE_DRAFTED", "Lập phương án thay thế"
        ALTERNATIVE_SENT = "ALTERNATIVE_SENT", "Gửi phương án thay thế"
        ALTERNATIVE_OWNER_SUBMITTED = (
            "ALTERNATIVE_OWNER_SUBMITTED",
            "Gửi chủ duyệt phương án",
        )
        ALTERNATIVE_OWNER_DECIDED = (
            "ALTERNATIVE_OWNER_DECIDED",
            "Chủ cửa hàng duyệt phương án",
        )
        ALTERNATIVE_CUSTOMER_ACCEPTED = (
            "ALTERNATIVE_CUSTOMER_ACCEPTED",
            "Khách đồng ý phương án",
        )
        ALTERNATIVE_CUSTOMER_DECLINED = (
            "ALTERNATIVE_CUSTOMER_DECLINED",
            "Khách từ chối phương án",
        )
        ALTERNATIVE_SUPERSEDED = (
            "ALTERNATIVE_SUPERSEDED",
            "Thay thế phương án",
        )
        SUPPLEMENT_REQUESTED = "SUPPLEMENT_REQUESTED", "Yêu cầu bổ sung"
        QUOTE_SENT = "QUOTE_SENT", "Gửi báo giá"
        QUOTE_REVISED = "QUOTE_REVISED", "Gửi báo giá phiên bản mới"
        QUOTE_ACCEPTED = "QUOTE_ACCEPTED", "Khách đồng ý báo giá"
        CHANGE_REQUESTED = "CHANGE_REQUESTED", "Khách yêu cầu trao đổi"
        ORDER_REJECTED = "ORDER_REJECTED", "Từ chối tiếp nhận"
        PRODUCTION_STARTED = "PRODUCTION_STARTED", "Bắt đầu làm bánh"
        QUALITY_CHECKED = "QUALITY_CHECKED", "Kiểm tra chất lượng"
        REVIEW_PHOTO_SENT = "REVIEW_PHOTO_SENT", "Gửi ảnh bánh"
        REVIEW_PHOTO_REPLACED = "REVIEW_PHOTO_REPLACED", "Thay ảnh phiên bản"
        REVIEW_ACCEPTED = "REVIEW_ACCEPTED", "Khách duyệt ảnh"
        REVISION_REQUESTED = "REVISION_REQUESTED", "Khách yêu cầu chỉnh sửa"
        REVISION_CLASSIFIED = "REVISION_CLASSIFIED", "Phân loại chỉnh sửa"
        OWNER_APPROVAL_REQUESTED = "OWNER_APPROVAL_REQUESTED", "Gửi chủ duyệt"
        OWNER_APPROVAL_DECIDED = "OWNER_APPROVAL_DECIDED", "Chủ cửa hàng quyết định"
        AGREEMENT_SENT = "AGREEMENT_SENT", "Gửi phụ lục thay đổi"
        AGREEMENT_ACCEPTED = "AGREEMENT_ACCEPTED", "Khách đồng ý phụ lục"
        AGREEMENT_REJECTED = "AGREEMENT_REJECTED", "Khách từ chối phụ lục"
        CUSTOMER_KEPT_REVIEW = "CUSTOMER_KEPT_REVIEW", "Khách xác nhận giữ phương án"
        CUSTOMER_CONTINUED_DISCUSSION = (
            "CUSTOMER_CONTINUED_DISCUSSION",
            "Khách tiếp tục trao đổi",
        )
        REVISION_COMPLETED = "REVISION_COMPLETED", "Hoàn tất chỉnh sửa"
        DELIVERY_STARTED = "DELIVERY_STARTED", "Bắt đầu lần giao"
        DELIVERY_SUCCEEDED = "DELIVERY_SUCCEEDED", "Ghi nhận giao thành công"
        DELIVERY_FAILED = "DELIVERY_FAILED", "Ghi nhận giao thất bại"
        DELIVERY_RESCHEDULED = "DELIVERY_RESCHEDULED", "Thống nhất giao lại"
        CUSTOMER_PREPAYMENT_REPORTED = (
            "CUSTOMER_PREPAYMENT_REPORTED",
            "Khách báo đã chuyển khoản trước",
        )
        INCIDENT_REPORTED = "INCIDENT_REPORTED", "Báo sự cố giao/bánh"
        INCIDENT_PROPOSED = "INCIDENT_PROPOSED", "Đề xuất phương án sự cố"
        INCIDENT_DECIDED = "INCIDENT_DECIDED", "Chủ cửa hàng duyệt sự cố"
        INCIDENT_EXECUTED = "INCIDENT_EXECUTED", "Thực hiện phương án sự cố"
        INCIDENT_CUSTOMER_ACCEPTED = (
            "INCIDENT_CUSTOMER_ACCEPTED",
            "Khách chấp nhận phương án",
        )
        PAYMENT_RECORDED = "PAYMENT_RECORDED", "Ghi nhận khoản thu thực nhận"
        REFUND_RECORDED = "REFUND_RECORDED", "Ghi nhận hoàn tiền thực hiện"
        ORDER_COMPLETED = "ORDER_COMPLETED", "Hoàn tất đơn hàng"
        ORDER_REVIEW_SUBMITTED = "ORDER_REVIEW_SUBMITTED", "Khách đánh giá đơn hàng"
        ORDER_REVIEW_REPLIED = "ORDER_REVIEW_REPLIED", "Chủ cửa hàng phản hồi đánh giá"
        ORDER_CANCELLED = "ORDER_CANCELLED", "Hủy đơn sau xử lý"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="history",
        verbose_name="đơn hàng",
    )
    action = models.CharField("hành động", max_length=32, choices=Action.choices)
    from_status = models.CharField("trạng thái trước", max_length=32, blank=True)
    to_status = models.CharField("trạng thái sau", max_length=32, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="order_history",
        verbose_name="người thực hiện",
    )
    occurred_at = models.DateTimeField("thời điểm", auto_now_add=True)
    message = models.TextField("nội dung/lý do", blank=True)
    details = models.JSONField(default=dict, blank=True)
    quote = models.ForeignKey(
        Quote,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="history",
        verbose_name="báo giá liên quan",
    )

    class Meta:
        ordering = ("occurred_at", "pk")
        verbose_name = "lịch sử đơn hàng"
        verbose_name_plural = "lịch sử đơn hàng"

    def __str__(self):
        return f"{self.order.code} - {self.get_action_display()}"


class ProductionCheck(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="production_checks",
        verbose_name="đơn hàng",
    )
    production_round = models.PositiveIntegerField("lần sản xuất")
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="production_checks",
        verbose_name="nhân viên kiểm tra",
    )
    passed = models.BooleanField("đạt chất lượng")
    notes = models.TextField("ghi chú/lý do", max_length=2000)
    checked_at = models.DateTimeField("thời điểm kiểm tra", auto_now_add=True)

    class Meta:
        ordering = ("-checked_at", "-pk")
        verbose_name = "kiểm tra chất lượng"
        verbose_name_plural = "kiểm tra chất lượng"
        constraints = [
            models.CheckConstraint(
                condition=Q(production_round__gte=1),
                name="orders_check_round_positive",
            ),
            models.CheckConstraint(
                condition=Q(passed=True) | ~Q(notes=""),
                name="orders_failed_check_requires_reason",
            ),
        ]
        indexes = [
            models.Index(
                fields=("order", "production_round", "-checked_at"),
                name="orders_check_round_idx",
            ),
        ]

    def clean(self):
        if not self.passed and not self.notes.strip():
            raise ValidationError({"notes": "Khi chưa đạt, phải ghi rõ lý do."})

    def __str__(self):
        return f"{self.order.code} · lần {self.production_round}"


class CakeReviewVersion(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Chờ khách duyệt"
        ACCEPTED = "ACCEPTED", "Khách đã duyệt"
        REVISION_REQUESTED = "REVISION_REQUESTED", "Khách yêu cầu chỉnh sửa"
        REPLACED = "REPLACED", "Bị thay thế"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="review_versions",
        verbose_name="đơn hàng",
    )
    version_number = models.PositiveIntegerField("phiên bản")
    production_round = models.PositiveIntegerField("lần sản xuất")
    photo = models.ImageField(
        "ảnh bánh",
        storage=private_order_photo_storage,
        upload_to=order_review_upload_to,
        validators=[validate_catalog_image],
    )
    description = models.TextField("mô tả", max_length=2000, blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sent_cake_reviews",
        verbose_name="nhân viên gửi",
    )
    production_check = models.ForeignKey(
        ProductionCheck,
        on_delete=models.PROTECT,
        related_name="review_versions",
        verbose_name="kiểm tra chất lượng căn cứ",
    )
    status = models.CharField(
        "trạng thái",
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING,
    )
    sent_at = models.DateTimeField("thời điểm gửi", auto_now_add=True)
    responded_at = models.DateTimeField("thời điểm phản hồi", null=True, blank=True)
    customer_response = models.TextField("phản hồi khách", blank=True)
    revision = models.PositiveIntegerField(default=1)
    replacement_reason = models.TextField("lý do thay ảnh", blank=True)

    class Meta:
        ordering = ("-version_number",)
        verbose_name = "phiên bản ảnh bánh"
        verbose_name_plural = "phiên bản ảnh bánh"
        constraints = [
            models.UniqueConstraint(
                fields=("order", "version_number"),
                name="orders_review_order_version_unique",
            ),
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(status="PENDING"),
                name="orders_one_pending_review",
            ),
            models.CheckConstraint(
                condition=Q(version_number__gte=1) & Q(production_round__gte=1),
                name="orders_review_version_round_positive",
            ),
        ]

    _IMMUTABLE_FIELDS = (
        "order_id",
        "version_number",
        "production_round",
        "photo",
        "description",
        "sent_by_id",
        "production_check_id",
        "sent_at",
        "replacement_reason",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                *self._IMMUTABLE_FIELDS
            ).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("Nội dung phiên bản ảnh đã gửi là bất biến.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} · ảnh v{self.version_number}"


class RevisionRequest(models.Model):
    class Scope(models.TextChoices):
        WITHIN = "WITHIN", "Trong phạm vi đã thống nhất"
        OUTSIDE = "OUTSIDE", "Vượt phạm vi đã thống nhất"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Chờ nhân viên phân loại"
        IN_PROGRESS = "IN_PROGRESS", "Đã cho phép, đang chỉnh sửa"
        WAITING_OWNER = "WAITING_OWNER", "Chờ chủ cửa hàng duyệt"
        WAITING_CUSTOMER = "WAITING_CUSTOMER", "Chờ khách duyệt phụ lục"
        COMPLETED = "COMPLETED", "Đã xử lý"
        OWNER_REJECTED = "OWNER_REJECTED", "Chủ cửa hàng từ chối"
        CUSTOMER_KEPT = "CUSTOMER_KEPT", "Khách giữ phương án hiện tại"
        CUSTOMER_DECLINED = "CUSTOMER_DECLINED", "Khách từ chối phụ lục"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="revision_requests",
        verbose_name="đơn hàng",
    )
    review_version = models.ForeignKey(
        CakeReviewVersion,
        on_delete=models.PROTECT,
        related_name="revision_requests",
        verbose_name="phiên bản ảnh được phản hồi",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cake_revision_requests",
        verbose_name="khách yêu cầu",
    )
    message = models.TextField("nội dung yêu cầu", max_length=2000)
    status = models.CharField(
        "trạng thái xử lý",
        max_length=24,
        choices=Status.choices,
        default=Status.OPEN,
    )
    scope = models.CharField(
        "phạm vi",
        max_length=12,
        choices=Scope.choices,
        blank=True,
    )
    classified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="classified_cake_revisions",
        null=True,
        blank=True,
        verbose_name="nhân viên phân loại",
    )
    classification_reason = models.TextField("lý do phân loại", blank=True)
    requested_at = models.DateTimeField("thời điểm yêu cầu", auto_now_add=True)
    taken_at = models.DateTimeField("thời điểm tiếp nhận", null=True, blank=True)
    resolved_at = models.DateTimeField("thời điểm xử lý xong", null=True, blank=True)
    replacement_review_version = models.ForeignKey(
        CakeReviewVersion,
        on_delete=models.PROTECT,
        related_name="resolved_revision_requests",
        null=True,
        blank=True,
        verbose_name="ảnh gửi lại để xử lý",
    )

    class Meta:
        ordering = ("-requested_at", "-pk")
        verbose_name = "yêu cầu chỉnh sửa bánh"
        verbose_name_plural = "yêu cầu chỉnh sửa bánh"
        constraints = [
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(
                    status__in=(
                        "OPEN",
                        "IN_PROGRESS",
                        "WAITING_OWNER",
                        "WAITING_CUSTOMER",
                        "OWNER_REJECTED",
                        "CUSTOMER_DECLINED",
                    )
                ),
                name="orders_one_open_revision_request",
            ),
        ]

    def __str__(self):
        return f"{self.order.code} · chỉnh sửa"


class ApprovalRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Chờ chủ duyệt"
        APPROVED = "APPROVED", "Đã duyệt"
        REJECTED = "REJECTED", "Từ chối"

    revision_request = models.ForeignKey(
        RevisionRequest,
        on_delete=models.PROTECT,
        related_name="approval_requests",
        verbose_name="yêu cầu chỉnh sửa",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submitted_revision_approvals",
        verbose_name="nhân viên gửi",
    )
    summary = models.CharField("tóm tắt", max_length=500)
    reason = models.TextField("lý do vượt phạm vi", max_length=2000)
    proposed_plan = models.TextField("phương án đề xuất", max_length=2000)
    status = models.CharField(
        "kết quả",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requires_change_agreement = models.BooleanField(
        "cần khách duyệt phụ lục",
        default=False,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_revision_approvals",
        null=True,
        blank=True,
        verbose_name="chủ cửa hàng quyết định",
    )
    decision_reason = models.TextField("lý do quyết định", blank=True)
    decided_at = models.DateTimeField("thời điểm quyết định", null=True, blank=True)
    created_at = models.DateTimeField("thời điểm gửi duyệt", auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "yêu cầu chủ duyệt chỉnh sửa"
        verbose_name_plural = "yêu cầu chủ duyệt chỉnh sửa"

    def __str__(self):
        return f"{self.revision_request.order.code} · duyệt"


class ChangeAgreement(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Chờ khách xác nhận"
        ACCEPTED = "ACCEPTED", "Khách đã đồng ý"
        REJECTED = "REJECTED", "Khách từ chối"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="change_agreements",
        verbose_name="đơn hàng",
    )
    revision_request = models.ForeignKey(
        RevisionRequest,
        on_delete=models.PROTECT,
        related_name="change_agreements",
        verbose_name="yêu cầu chỉnh sửa",
    )
    approval_request = models.ForeignKey(
        ApprovalRequest,
        on_delete=models.PROTECT,
        related_name="change_agreements",
        verbose_name="quyết định chủ cửa hàng",
    )
    version_number = models.PositiveIntegerField("phiên bản phụ lục")
    based_on_quote = models.ForeignKey(
        Quote,
        on_delete=models.PROTECT,
        related_name="change_agreements",
        null=True,
        blank=True,
        verbose_name="báo giá gốc",
    )
    based_on_agreement = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="next_agreements",
        null=True,
        blank=True,
        verbose_name="phụ lục trước",
    )
    request_snapshot = models.JSONField("snapshot yêu cầu", default=dict)
    requested_change_snapshot = models.TextField("nội dung chỉnh sửa", max_length=2000)
    quantity_snapshot = models.PositiveIntegerField(
        "số lượng theo phụ lục",
        validators=[MinValueValidator(1)],
    )
    unit_price = models.DecimalField("đơn giá mới (VNĐ)", max_digits=12, decimal_places=0)
    delivery_fee = models.DecimalField("phí giao (VNĐ)", max_digits=12, decimal_places=0)
    previous_total = models.DecimalField(
        "tổng giá trước thay đổi (VNĐ)",
        max_digits=15,
        decimal_places=0,
    )
    price_delta = models.DecimalField(
        "chênh lệch tổng giá (VNĐ)",
        max_digits=15,
        decimal_places=0,
    )
    total = models.DecimalField("tổng giá sau thay đổi (VNĐ)", max_digits=15, decimal_places=0)
    proposed_delivery_at = models.DateTimeField("lịch giao mới")
    status = models.CharField(
        "trạng thái",
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    sent_at = models.DateTimeField("thời điểm gửi", auto_now_add=True)
    responded_at = models.DateTimeField("thời điểm phản hồi", null=True, blank=True)
    customer_response = models.TextField("phản hồi khách", blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-version_number",)
        verbose_name = "phụ lục thay đổi"
        verbose_name_plural = "phụ lục thay đổi"
        constraints = [
            models.UniqueConstraint(
                fields=("order", "version_number"),
                name="orders_change_order_version_unique",
            ),
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(status="PENDING"),
                name="orders_one_pending_change_agreement",
            ),
            models.CheckConstraint(
                condition=Q(version_number__gte=1)
                & Q(quantity_snapshot__gte=1)
                & Q(unit_price__gte=Decimal("0"))
                & Q(delivery_fee__gte=Decimal("0"))
                & Q(previous_total__gte=Decimal("0"))
                & Q(total__gte=Decimal("0"))
                & Q(price_delta=F("total") - F("previous_total"))
                & Q(
                    total=(
                        F("unit_price") * F("quantity_snapshot")
                        + F("delivery_fee")
                    )
                ),
                name="orders_change_agreement_money_consistent",
            ),
        ]

    def __str__(self):
        return f"{self.order.code} · phụ lục v{self.version_number}"


class DeliveryAttempt(models.Model):
    class Result(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "Đang giao"
        DELIVERED = "DELIVERED", "Đã giao"
        FAILED = "FAILED", "Giao thất bại"
        INCIDENT = "INCIDENT", "Phát sinh sự cố"

    class ConfirmationSource(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Khách xác nhận trên website"
        STAFF = "STAFF", "Nhân viên xác nhận khi bàn giao"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="delivery_attempts",
        verbose_name="đơn hàng",
    )
    attempt_number = models.PositiveIntegerField("lần giao")
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="handled_delivery_attempts",
        verbose_name="nhân viên giao",
    )
    recipient_name_snapshot = models.CharField(max_length=150)
    recipient_phone_snapshot = models.CharField(max_length=30)
    delivery_address_snapshot = models.CharField(max_length=500)
    scheduled_at = models.DateTimeField("thời gian giao đã thống nhất")
    started_at = models.DateTimeField("bắt đầu giao")
    ended_at = models.DateTimeField("kết thúc lần giao", null=True, blank=True)
    result = models.CharField(
        "kết quả",
        max_length=12,
        choices=Result.choices,
        default=Result.IN_PROGRESS,
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_delivery_attempts",
        null=True,
        blank=True,
        verbose_name="người xác nhận kết quả",
    )
    confirmation_source = models.CharField(
        "nguồn xác nhận",
        max_length=8,
        choices=ConfirmationSource.choices,
        blank=True,
    )
    confirmation_note = models.TextField("ghi chú bàn giao", max_length=2000, blank=True)
    failure_reason = models.TextField("lý do giao thất bại", max_length=2000, blank=True)
    proof = models.ImageField(
        "minh chứng giao",
        storage=private_order_photo_storage,
        upload_to=private_evidence_upload_to,
        validators=[validate_catalog_image],
        blank=True,
    )
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("attempt_number",)
        verbose_name = "lần giao hàng"
        verbose_name_plural = "lần giao hàng"
        constraints = [
            models.UniqueConstraint(
                fields=("order", "attempt_number"),
                name="orders_delivery_order_attempt_unique",
            ),
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(result="IN_PROGRESS"),
                name="orders_one_active_delivery",
            ),
            models.CheckConstraint(
                condition=Q(attempt_number__gte=1),
                name="orders_delivery_attempt_positive",
            ),
        ]

    _IMMUTABLE_FIELDS = (
        "order_id",
        "attempt_number",
        "handled_by_id",
        "recipient_name_snapshot",
        "recipient_phone_snapshot",
        "delivery_address_snapshot",
        "scheduled_at",
        "started_at",
    )

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                *self._IMMUTABLE_FIELDS
            ).first()
            if original and any(
                original[field] != getattr(self, field)
                for field in self._IMMUTABLE_FIELDS
            ):
                raise ValueError("Thông tin snapshot của lần giao là bất biến.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} · giao lần {self.attempt_number}"


class Payment(models.Model):
    class Method(models.TextChoices):
        CASH = "CASH", "Tiền mặt"
        BANK_TRANSFER = "BANK_TRANSFER", "Chuyển khoản (nhân viên đối chiếu)"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="đơn hàng",
    )
    method = models.CharField("phương thức", max_length=16, choices=Method.choices)
    amount = models.DecimalField("số tiền thực nhận (VNĐ)", max_digits=15, decimal_places=0)
    received_at = models.DateTimeField("thời điểm thực nhận")
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="confirmed_order_payments",
        verbose_name="nhân viên xác nhận",
    )
    transaction_reference = models.CharField(
        "mã tham chiếu",
        max_length=120,
        blank=True,
    )
    notes = models.TextField("ghi chú", max_length=2000, blank=True)
    idempotency_key = models.UUIDField("mã yêu cầu", unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("received_at", "pk")
        verbose_name = "khoản thu"
        verbose_name_plural = "khoản thu"
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="orders_payment_amount_positive",
            ),
            models.UniqueConstraint(
                fields=("transaction_reference",),
                condition=~Q(transaction_reference=""),
                name="orders_payment_reference_unique",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                "order_id",
                "method",
                "amount",
                "received_at",
                "confirmed_by_id",
                "transaction_reference",
                "notes",
                "idempotency_key",
            ).first()
            if original and any(
                original[field] != getattr(self, field) for field in original
            ):
                raise ValueError("Khoản thu đã xác nhận là bất biến.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} · thu {self.amount} VNĐ"


class OrderIncident(models.Model):
    class Type(models.TextChoices):
        UNREACHABLE = "UNREACHABLE", "Không liên hệ được"
        NOT_RECEIVED = "NOT_RECEIVED", "Khách chưa nhận bánh"
        DAMAGED = "DAMAGED", "Bánh hư hỏng"
        WRONG_REQUEST = "WRONG_REQUEST", "Bánh sai yêu cầu"
        OTHER = "OTHER", "Khác"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Chờ nhân viên đề xuất"
        PENDING_OWNER = "PENDING_OWNER", "Chờ chủ cửa hàng duyệt"
        REJECTED = "REJECTED", "Chủ cửa hàng yêu cầu phương án khác"
        APPROVED = "APPROVED", "Đã duyệt phương án"
        IN_PROGRESS = "IN_PROGRESS", "Đang thực hiện phương án"
        WAITING_CUSTOMER = "WAITING_CUSTOMER", "Chờ khách xác nhận"
        RESOLVED = "RESOLVED", "Đã xử lý"

    class Resolution(models.TextChoices):
        REDELIVER = "REDELIVER", "Thống nhất giao lại"
        REPAIR = "REPAIR", "Sửa bánh và duyệt lại"
        REPLACE = "REPLACE", "Làm bánh thay thế"
        CANCEL = "CANCEL", "Hủy đơn và hoàn tiền nếu đã thu"
        KEEP_CURRENT = "KEEP_CURRENT", "Giữ phương án hiện tại"

    class OwnerDecision(models.TextChoices):
        PENDING = "PENDING", "Chưa quyết định"
        APPROVED = "APPROVED", "Chấp thuận"
        REJECTED = "REJECTED", "Từ chối"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="incidents",
        verbose_name="đơn hàng",
    )
    delivery_attempt = models.ForeignKey(
        DeliveryAttempt,
        on_delete=models.PROTECT,
        related_name="incidents",
        null=True,
        blank=True,
        verbose_name="lần giao liên quan",
    )
    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reported_order_incidents",
        verbose_name="người báo",
    )
    incident_type = models.CharField("loại sự cố", max_length=20, choices=Type.choices)
    description = models.TextField("mô tả", max_length=2000)
    proof = models.ImageField(
        "minh chứng sự cố",
        storage=private_order_photo_storage,
        upload_to=private_evidence_upload_to,
        validators=[validate_catalog_image],
        blank=True,
    )
    status = models.CharField(
        "trạng thái",
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
    )
    proposed_resolution = models.CharField(
        "phương án đề xuất",
        max_length=12,
        choices=Resolution.choices,
        blank=True,
    )
    proposed_plan = models.TextField("chi tiết phương án", max_length=2000, blank=True)
    customer_agreement_note = models.TextField(
        "thỏa thuận với khách",
        max_length=2000,
        blank=True,
    )
    proposed_delivery_at = models.DateTimeField(
        "lịch giao lại đề xuất",
        null=True,
        blank=True,
    )
    owner_decision = models.CharField(
        "quyết định của chủ cửa hàng",
        max_length=10,
        choices=OwnerDecision.choices,
        default=OwnerDecision.PENDING,
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_order_incidents",
        null=True,
        blank=True,
        verbose_name="chủ cửa hàng quyết định",
    )
    decision_reason = models.TextField("lý do quyết định", blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    approved_refund_amount = models.DecimalField(
        "số tiền hoàn được duyệt (VNĐ)",
        max_digits=15,
        decimal_places=0,
        default=Decimal("0"),
    )
    executed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="executed_order_incidents",
        null=True,
        blank=True,
        verbose_name="nhân viên thực hiện",
    )
    execution_result = models.TextField("kết quả thực hiện", max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "sự cố đơn hàng"
        verbose_name_plural = "sự cố đơn hàng"
        constraints = [
            models.UniqueConstraint(
                fields=("order",),
                condition=Q(
                    status__in=(
                        "OPEN",
                        "PENDING_OWNER",
                        "REJECTED",
                        "APPROVED",
                        "IN_PROGRESS",
                        "WAITING_CUSTOMER",
                    )
                ),
                name="orders_one_active_incident",
            ),
            models.CheckConstraint(
                condition=Q(approved_refund_amount__gte=0),
                name="orders_incident_refund_nonnegative",
            ),
        ]

    def __str__(self):
        return f"{self.order.code} · sự cố #{self.pk}"


class Refund(models.Model):
    class Method(models.TextChoices):
        CASH = "CASH", "Tiền mặt"
        BANK_TRANSFER = "BANK_TRANSFER", "Chuyển khoản (thực hiện bên ngoài)"

    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name="đơn hàng",
    )
    payment = models.ForeignKey(
        Payment,
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name="khoản thu gốc",
    )
    incident = models.ForeignKey(
        OrderIncident,
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name="quyết định chủ cửa hàng",
    )
    amount = models.DecimalField("số tiền thực hoàn (VNĐ)", max_digits=15, decimal_places=0)
    method = models.CharField("phương thức hoàn", max_length=16, choices=Method.choices)
    transaction_reference = models.CharField("mã tham chiếu", max_length=120, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_order_refunds",
        verbose_name="nhân viên ghi nhận",
    )
    refunded_at = models.DateTimeField("thời điểm thực hoàn")
    notes = models.TextField("ghi chú", max_length=2000, blank=True)
    proof = models.ImageField(
        "minh chứng hoàn tiền",
        storage=private_order_photo_storage,
        upload_to=private_evidence_upload_to,
        validators=[validate_catalog_image],
        blank=True,
    )
    idempotency_key = models.UUIDField("mã yêu cầu", unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("refunded_at", "pk")
        verbose_name = "khoản hoàn tiền"
        verbose_name_plural = "khoản hoàn tiền"
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="orders_refund_amount_positive",
            ),
            models.UniqueConstraint(
                fields=("transaction_reference",),
                condition=~Q(transaction_reference=""),
                name="orders_refund_reference_unique",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values(
                "order_id",
                "payment_id",
                "incident_id",
                "amount",
                "method",
                "transaction_reference",
                "recorded_by_id",
                "refunded_at",
                "notes",
                "idempotency_key",
            ).first()
            if original and any(
                original[field] != getattr(self, field) for field in original
            ):
                raise ValueError("Khoản hoàn tiền đã ghi nhận là bất biến.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} · hoàn {self.amount} VNĐ"


class OrderReview(models.Model):
    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="review",
        verbose_name="đơn hàng",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="order_reviews",
        verbose_name="khách hàng đánh giá",
    )
    rating = models.PositiveSmallIntegerField("điểm đánh giá")
    comment = models.TextField("nhận xét", max_length=2000, blank=True)
    created_at = models.DateTimeField("thời điểm đánh giá", auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "đánh giá đơn hàng"
        verbose_name_plural = "đánh giá đơn hàng"
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__gte=1) & Q(rating__lte=5),
                name="orders_review_rating_1_to_5",
            ),
        ]

    def __str__(self):
        return f"{self.order.code} · {self.rating}/5 sao"


class ReviewReply(models.Model):
    review = models.OneToOneField(
        OrderReview,
        on_delete=models.PROTECT,
        related_name="reply",
        verbose_name="đánh giá",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="order_review_replies",
        verbose_name="chủ cửa hàng phản hồi",
    )
    content = models.TextField("nội dung phản hồi", max_length=2000)
    created_at = models.DateTimeField("thời điểm phản hồi", auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")
        verbose_name = "phản hồi đánh giá"
        verbose_name_plural = "phản hồi đánh giá"

    def __str__(self):
        return f"Phản hồi đánh giá đơn {self.review.order.code}"


class WorkflowEvent(models.Model):
    class EventType(models.TextChoices):
        ORDER_CONFIRMED = "order.confirmed", "Khách xác nhận báo giá"
        CAKE_REVIEW_REQUESTED = "cake.review_requested", "Yêu cầu khách duyệt ảnh"
        ORDER_COMPLETED = "order.completed", "Đơn hàng hoàn tất"

    class Status(models.TextChoices):
        PENDING = "pending", "Chờ gửi"
        PROCESSING = "processing", "Đang xử lý"
        DELIVERED = "delivered", "Workflow đã tiếp nhận"
        FAILED = "failed", "Gửi thất bại"

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=64, choices=EventType.choices)
    order = models.ForeignKey(
        Order,
        on_delete=models.PROTECT,
        related_name="workflow_events",
        verbose_name="đơn hàng",
    )
    business_key = models.CharField(max_length=160, unique=True)
    payload_version = models.PositiveSmallIntegerField(default=1)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    attempts_since_retry = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ("created_at", "pk")
        indexes = [
            models.Index(
                fields=("status", "next_attempt_at"),
                name="orders_wf_status_next_idx",
            ),
        ]
        verbose_name = "sự kiện workflow"
        verbose_name_plural = "sự kiện workflow"

    def __str__(self):
        return f"{self.event_type} · {self.order.code} · {self.status}"


class WorkflowRetryRequest(models.Model):
    event = models.ForeignKey(
        WorkflowEvent,
        on_delete=models.PROTECT,
        related_name="retry_requests",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="workflow_retry_requests",
    )
    attempts_before_retry = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        verbose_name = "yêu cầu thử lại workflow"
        verbose_name_plural = "yêu cầu thử lại workflow"
