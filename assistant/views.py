from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.models import User

from .forms import StorePolicyForm
from .models import StorePolicy


def published_policy(request, code):
    policy = get_object_or_404(
        StorePolicy.objects.filter(is_published=True),
        code=code,
    )
    return render(request, "assistant/policy_detail.html", {"policy": policy})


def published_policy_list(request):
    policies = StorePolicy.objects.filter(is_published=True)
    return render(
        request,
        "assistant/policy_list.html",
        {"policies": policies},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_policy_list(request):
    policies = StorePolicy.objects.select_related("created_by")
    return render(
        request,
        "assistant/owner_policy_list.html",
        {"policies": policies},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_policy_create(request):
    form = StorePolicyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        policy = form.save(commit=False)
        policy.created_by = request.user
        policy.save()
        messages.success(request, "Đã tạo nội dung chính sách.")
        return redirect("assistant:owner_policy_list")
    return render(
        request,
        "assistant/owner_policy_form.html",
        {"form": form, "page_title": "Thêm chính sách"},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_policy_edit(request, policy_id):
    policy = get_object_or_404(StorePolicy, pk=policy_id)
    form = StorePolicyForm(request.POST or None, instance=policy)
    if request.method == "POST" and form.is_valid():
        updated = form.save(commit=False)
        content_changed = bool(
            set(form.changed_data).intersection({"title", "content"})
        )
        if form.changed_data:
            updated.version += 1
        if policy.is_published and content_changed:
            updated.is_published = False
        updated.save()
        if policy.is_published and content_changed:
            messages.success(
                request,
                "Đã lưu phiên bản nháp mới. Hãy xem lại và công bố lại cho khách hàng.",
            )
        else:
            messages.success(request, "Đã cập nhật nội dung chính sách.")
        return redirect("assistant:owner_policy_list")
    return render(
        request,
        "assistant/owner_policy_form.html",
        {"form": form, "page_title": "Sửa chính sách", "policy": policy},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_policy_toggle(request, policy_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    with transaction.atomic():
        policy = get_object_or_404(StorePolicy.objects.select_for_update(), pk=policy_id)
        policy.is_published = not policy.is_published
        policy.version += 1
        policy.save(update_fields=("is_published", "version", "updated_at"))
    messages.success(
        request,
        "Đã công bố chính sách." if policy.is_published else "Đã chuyển chính sách về bản nháp.",
    )
    return redirect("assistant:owner_policy_list")
