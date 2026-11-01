from django.contrib import admin

from .models import CakeTemplate, Category


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("slug", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CakeTemplate)
class CakeTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "category",
        "reference_price",
        "is_available",
        "advisory_data_verified",
        "updated_at",
    )
    list_filter = ("category", "is_available", "advisory_data_verified")
    search_fields = ("name",)
    readonly_fields = ("slug", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False
