import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import User

from .ai import (
    AssistantError,
    AssistantUnavailable,
    ai_is_available,
    run_assistant_turn,
)
from .forms import ChatMessageForm, StorePolicyForm
from .models import AIRequestLog, ChatSession, StorePolicy


def published_policy(request, code):
    policy = get_object_or_404(
        StorePolicy.objects.filter(is_published=True),
        code=code,
    )
    return render(request, "assistant/policy_detail.html", {"policy": policy})


def index(request):
    if request.user.is_authenticated and request.user.role != User.Role.CUSTOMER:
        raise PermissionDenied
    sessions = (
        ChatSession.objects.filter(customer=request.user).order_by("-updated_at")[:20]
        if request.user.is_authenticated
        else ()
    )
    provider_available = ai_is_available()
    return render(
        request,
        "assistant/index.html",
        {
            "sessions": sessions,
            "ai_available": provider_available,
        },
    )


@login_required
@role_required(User.Role.CUSTOMER)
def create_session(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    session = ChatSession.objects.create(customer=request.user)
    return redirect("assistant:chat_session", session_id=session.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def chat_session(request, session_id):
    session = get_object_or_404(
        ChatSession.objects.filter(customer=request.user),
        pk=session_id,
    )
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST":
        form = ChatMessageForm(request.POST)
        if not form.is_valid():
            return _chat_form_error(request, session, form)
        try:
            result = run_assistant_turn(
                session=session,
                customer=request.user,
                request_id=form.cleaned_data["request_id"],
                message=form.cleaned_data["content"],
            )
        except AssistantUnavailable:
            return _assistant_error(
                request,
                session,
                "Tư vấn AI chưa được cấu hình. Bạn có thể tiếp tục bằng biểu mẫu đặt bánh.",
                status=503,
            )
        except AssistantError as exc:
            error_messages = {
                "invalid_message": "Tin nhắn phải có nội dung và tối đa 2.000 ký tự.",
                "rate_limited": "Bạn gửi tin nhắn quá nhanh. Vui lòng thử lại sau ít phút.",
                "session_busy": "Phiên tư vấn đang xử lý yêu cầu khác. Vui lòng thử lại.",
                "session_closed": "Phiên tư vấn này đã đóng.",
                "request_conflict": "Mã yêu cầu đã được sử dụng cho phiên khác.",
            }
            return _assistant_error(
                request,
                session,
                error_messages.get(
                    exc.category,
                    "Chưa thể xử lý tin nhắn. Bạn có thể thử lại hoặc dùng biểu mẫu đặt bánh.",
                ),
                status=(
                    409
                    if exc.category in {"session_busy", "request_conflict"}
                    else 503
                    if exc.category
                    in {
                        "authentication",
                        "invalid_provider_response",
                        "invalid_json",
                        "provider_unavailable",
                        "provider_rejected",
                        "rate_limited",
                        "timeout_or_connection",
                    }
                    else 400
                ),
                retryable=exc.category
                in {
                    "invalid_provider_response",
                    "invalid_json",
                    "provider_unavailable",
                    "timeout_or_connection",
                },
            )
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {
                    "redirect_url": redirect(
                        "assistant:chat_session",
                        session_id=session.pk,
                    ).url,
                    "in_progress": result.get("in_progress", False),
                }
            )
        return redirect("assistant:chat_session", session_id=session.pk)

    latest_log = (
        AIRequestLog.objects.filter(session=session)
        .select_related("draft", "draft__cake_template")
        .first()
    )
    if latest_log and latest_log.status not in {
        AIRequestLog.Status.SUCCEEDED,
        AIRequestLog.Status.FALLBACK,
    }:
        latest_log = None
    return render(
        request,
        "assistant/chat_session.html",
        {
            "session": session,
            "chat_messages": session.messages.all(),
            "form": ChatMessageForm(),
            "ai_available": ai_is_available(),
            "latest_log": latest_log,
            "suggested_cakes": (
                latest_log.suggested_cakes
                if latest_log is not None
                else ()
            ),
            "policy_sources": (
                latest_log.policy_source_snapshots
                if latest_log is not None
                else ()
            ),
            "draft": latest_log.draft if latest_log is not None else None,
        },
    )


def _chat_form_error(request, session, form):
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    return render(
        request,
        "assistant/chat_session.html",
        {
            "session": session,
            "chat_messages": session.messages.all(),
            "form": form,
            "ai_available": ai_is_available(),
        },
        status=400,
    )


def _assistant_error(request, session, message, *, status, retryable=False):
    if request.headers.get("Accept") == "application/json":
        return JsonResponse(
            {"error": message, "retryable": retryable},
            status=status,
        )
    messages.error(request, message)
    return redirect("assistant:chat_session", session_id=session.pk)


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
                "Đã lưu phiên bản nháp mới. Hãy xem lại và công bố lại để chatbot sử dụng.",
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
