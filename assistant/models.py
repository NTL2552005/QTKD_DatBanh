from django.conf import settings
from django.db import models


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
