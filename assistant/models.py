import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class ChatSession(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Đang mở"
        CLOSED = "closed", "Đã đóng"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assistant_sessions",
    )
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.OPEN,
    )
    active_request_id = models.UUIDField(null=True, blank=True, editable=False)
    active_request_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-pk")
        indexes = [models.Index(fields=("customer", "status", "-updated_at"))]

    def __str__(self):
        return f"Tư vấn {self.pk}"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Khách hàng"
        ASSISTANT = "assistant", "BiBi Cake AI"

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=9, choices=Role.choices)
    content = models.TextField(max_length=4000)
    request_id = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "pk")
        indexes = [models.Index(fields=("session", "created_at"))]


class StorePolicy(models.Model):
    code = models.SlugField(max_length=60, unique=True)
    title = models.CharField(max_length=150)
    content = models.TextField(max_length=5000)
    is_published = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="managed_store_policies",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("title", "pk")
        verbose_name = "chính sách cửa hàng"
        verbose_name_plural = "chính sách cửa hàng"

    def __str__(self):
        return f"{self.code} · {self.title}"


class AIDraft(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="drafts",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assistant_drafts",
    )
    cake_template = models.ForeignKey(
        "catalog.CakeTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assistant_drafts",
    )
    quantity = models.PositiveSmallIntegerField(null=True, blank=True)
    size = models.CharField(max_length=80, blank=True)
    flavor = models.CharField(max_length=100, blank=True)
    inscription = models.CharField(max_length=250, blank=True)
    decoration_request = models.CharField(max_length=2000, blank=True)
    requested_delivery_date = models.DateField(null=True, blank=True)
    requested_delivery_time = models.TimeField(null=True, blank=True)
    budget_vnd = models.PositiveBigIntegerField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField()
    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assistant_draft",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-pk")
        indexes = [models.Index(fields=("customer", "expires_at"))]

    @property
    def is_available(self):
        return self.order_id is None and self.expires_at > timezone.now()


class AIRequestLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Đang xử lý"
        SUCCEEDED = "succeeded", "Thành công"
        FALLBACK = "fallback", "Trả lời dự phòng"
        FAILED = "failed", "Thất bại"

    request_id = models.UUIDField(unique=True, editable=False)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.PROTECT,
        related_name="ai_requests",
    )
    draft = models.ForeignKey(
        AIDraft,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_requests",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="assistant_ai_requests",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
    )
    provider = models.CharField(max_length=40)
    model = models.CharField(max_length=100, blank=True)
    response_ms = models.PositiveIntegerField(null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    suggested_cake_ids = models.JSONField(default=list)
    policy_source_codes = models.JSONField(default=list)
    policy_source_snapshots = models.JSONField(default=list)
    missing_fields = models.JSONField(default=list)
    error_category = models.CharField(max_length=40, blank=True)
    requires_staff = models.BooleanField(default=False)
    schema_failure = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("customer", "created_at"))]

    @property
    def suggested_cakes(self):
        from catalog.models import CakeTemplate

        return CakeTemplate.objects.public().select_related("category").filter(
            pk__in=self.suggested_cake_ids
        )

    @property
    def policy_sources(self):
        return StorePolicy.objects.filter(
            is_published=True,
            code__in=self.policy_source_codes,
        )
