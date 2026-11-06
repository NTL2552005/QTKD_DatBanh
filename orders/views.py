import json
import logging
import uuid
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.http import (
    FileResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.decorators import role_required
from accounts.models import User
from catalog.models import CakeTemplate

from .forms import (
    AlternativeCustomerResponseForm,
    AlternativeOwnerDecisionForm,
    AlternativeActionForm,
    OrderCreateForm,
    OrderAlternativeForm,
    OrderSupplementForm,
    AgreementResponseForm,
    CakeReviewForm,
    ContinueDiscussionForm,
    CustomerPrepaymentReportForm,
    DeliveryConfirmationForm,
    DeliveryFailureForm,
    DeliveryIncidentForm,
    DeliveryStartForm,
    IncidentCustomerResponseForm,
    IncidentDecisionForm,
    IncidentExecutionForm,
    IncidentProposalForm,
    IntakeQuoteApprovalDecisionForm,
    IntakeRejectionNoticeForm,
    OrderReviewForm,
    OwnerApprovalForm,
    OwnerClaimIntakeOrderForm,
    OwnerIntakeCheckForm,
    OwnerIntakeRejectionForm,
    OwnerReviewFilterForm,
    PaymentForm,
    ProductionCheckForm,
    QuoteChangeRequestForm,
    QuoteForm,
    QuoteResponseForm,
    RedeliveryForm,
    RefundForm,
    ReviewPeriodFilterForm,
    ReviewClassificationReviewForm,
    ReviewReplyForm,
    RevisionClassificationForm,
    RevisionRequestForm,
    StaffCheckForm,
    StaffMessageForm,
)
from .models import (
    ApprovalRequest,
    CakeReviewVersion,
    ChangeAgreement,
    DeliveryAttempt,
    IntakeQuoteApproval,
    Order,
    OrderIncident,
    OrderHistory,
    OrderAlternative,
    OwnerIntakeCheck,
    OrderReview,
    Payment,
    ProductionCheck,
    Quote,
    Refund,
    RevisionRequest,
    ReviewReply,
    ReviewClassificationEvent,
    StaffAvailabilityCheck,
    WorkflowEvent,
)
from .delivery import (
    ACTIVE_INCIDENT_STATUSES,
    customer_accept_incident_plan,
    customer_confirm_delivery,
    decide_incident,
    execute_incident_resolution,
    fail_delivery,
    order_financials,
    propose_incident_resolution,
    PREPAYMENT_REPORT_STATUSES,
    record_incident_resolution,
    record_payment,
    record_refund,
    report_prepayment,
    report_delivery_incident,
    schedule_redelivery,
    staff_confirm_delivery,
    start_delivery,
)
from .reviews import (
    REVIEW_AVAILABLE,
    REVIEW_EXPIRED,
    REVIEW_NOT_COMPLETED,
    REVIEW_TIME_UNKNOWN,
    reply_to_order_review,
    review_deadline,
    review_state,
    submit_order_review,
)
from .review_classification import (
    CallbackConflict,
    CallbackPayloadError,
    StaleReviewClassification,
    accept_review_classification,
    callback_key_matches,
    confirm_review_classification,
    dispatch_review_classification_events,
)
from .production import (
    accept_review,
    classify_revision_request,
    continue_revision_discussion,
    decide_revision_approval,
    keep_current_review,
    record_production_check,
    request_review_revision,
    respond_change_agreement,
    send_review_photo,
    start_production,
)
from .services import (
    IdempotencyConflict,
    InvalidOrderTransition,
    OrderActionError,
    StaleOrderVersion,
    accept_quote,
    claim_legacy_intake_order,
    create_order,
    create_alternative_draft,
    current_availability_check,
    current_owner_intake_check,
    decide_order_alternative,
    decide_intake_quote_approval,
    record_staff_check,
    record_owner_intake_check,
    reject_order,
    respond_order_alternative,
    request_customer_supplement,
    request_quote_change,
    send_quote,
    send_order_alternative,
    submit_intake_quote_approval,
    notify_customer_intake_rejection,
    update_customer_request,
)

logger = logging.getLogger(__name__)
from .workflow_events import request_event_retry

PAGE_SIZE = 10
STAFF_STATUSES = {
    value: label for value, label in Order.Status.choices
}
EDITABLE_STATUSES = (
    Order.Status.CHO_KIEM_TRA,
    Order.Status.CAN_BO_SUNG,
    Order.Status.CHO_TRAO_DOI,
    Order.Status.CHO_KHACH_XAC_NHAN,
)
PRODUCTION_QUEUE_STATUSES = (
    Order.Status.DANG_LAM,
    Order.Status.CHO_DUYET_ANH,
    Order.Status.CHO_XU_LY_CHINH_SUA,
    Order.Status.CHO_CHU_DUYET,
    Order.Status.CHO_KHACH_DUYET_THAY_DOI,
    Order.Status.CHO_TRAO_DOI_CHINH_SUA,
    Order.Status.SAN_SANG_GIAO,
    Order.Status.DANG_GIAO,
    Order.Status.GIAO_THAT_BAI,
    Order.Status.CHO_XU_LY_SU_CO,
    Order.Status.DA_GIAO_CHO_THANH_TOAN,
)


def _action_error(request, order, error, *, staff=False):
    messages.error(request, str(error))
    route = "orders:staff_order_detail" if staff else "orders:order_detail"
    return redirect(route, pk=order.pk)


def _display_quote_status(quote):
    if quote.status == Quote.Status.PENDING and quote.expires_at <= timezone.now():
        return "Hết hạn"
    return quote.get_status_display()


def create_order_view(request, slug):
    if not request.user.is_authenticated:
        login_url = reverse("accounts:login")
        next_url = reverse("orders:create_order", kwargs={"slug": slug})
        return redirect(f"{login_url}?next={next_url}")
    if request.user.role != User.Role.CUSTOMER:
        raise PermissionDenied
    cake = get_object_or_404(
        CakeTemplate.objects.public().select_related("category"),
        slug=slug,
    )
    initial = {}
    draft = None
    if request.method == "GET" and request.GET.get("draft"):
        from assistant.models import AIDraft

        draft = get_object_or_404(
            AIDraft.objects.filter(customer=request.user),
            pk=request.GET["draft"],
        )
        if draft.order_id:
            messages.info(request, "Bản nháp này đã được dùng để gửi yêu cầu đặt bánh.")
            return redirect("orders:order_detail", pk=draft.order_id)
        if draft.expires_at <= timezone.now():
            messages.warning(request, "Bản nháp đã hết hạn. Vui lòng nhập thông tin mới.")
        elif draft.cake_template_id != cake.pk:
            raise PermissionDenied
        else:
            initial = {
                "draft_id": str(draft.pk),
                "size": draft.size,
                "flavor": draft.flavor,
                "quantity": draft.quantity,
                "inscription": draft.inscription,
                "decoration_request": draft.decoration_request,
            }
            if draft.requested_delivery_date and draft.requested_delivery_time:
                desired_at = datetime.combine(
                    draft.requested_delivery_date,
                    draft.requested_delivery_time,
                )
                initial["desired_delivery_at"] = timezone.make_aware(
                    desired_at,
                    timezone.get_current_timezone(),
                )
    form = OrderCreateForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                draft_id = form.cleaned_data.get("draft_id")
                if draft_id:
                    from assistant.models import AIDraft

                    draft = get_object_or_404(
                        AIDraft.objects.filter(customer=request.user),
                        pk=draft_id,
                    )
                    if draft.cake_template_id != cake.pk:
                        raise ValidationError("Mẫu bánh không khớp với bản nháp.")
                    if not draft.order_id and draft.expires_at <= timezone.now():
                        raise ValidationError("Bản nháp đã hết hạn; vui lòng kiểm tra lại.")
                order, created = create_order(
                    customer=request.user,
                    cake_id=cake.pk,
                    request_key=form.cleaned_data["request_key"],
                    data=form.cleaned_data,
                )
                if draft_id:
                    if draft.order_id and draft.order_id != order.pk:
                        raise ValidationError(
                            "Bản nháp đã được sử dụng cho một yêu cầu đặt bánh khác."
                        )
                    if not draft.order_id:
                        claimed = AIDraft.objects.filter(
                            pk=draft.pk,
                            customer=request.user,
                            order__isnull=True,
                            expires_at__gt=timezone.now(),
                        ).update(order=order, updated_at=timezone.now())
                        if not claimed:
                            draft.refresh_from_db()
                            if draft.order_id != order.pk:
                                raise ValidationError(
                                    "Bản nháp vừa được sử dụng. Vui lòng kiểm tra đơn của bạn."
                                )
        except (OrderActionError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                message = "; ".join(exc.messages)
            else:
                message = str(exc)
            form.add_error(None, message)
        else:
            messages.success(
                request,
                "Đã gửi yêu cầu đặt bánh."
                if created
                else "Yêu cầu này đã được nhận trước đó; không tạo đơn trùng.",
            )
            return redirect("orders:order_detail", pk=order.pk)
    return render(
        request,
        "orders/order_form.html",
        {
            "form": form,
            "cake": cake,
            "page_title": "Gửi yêu cầu đặt bánh",
            "draft": draft,
        },
    )


@login_required
@role_required(User.Role.CUSTOMER)
def customer_order_list(request):
    orders = Order.objects.filter(customer=request.user).select_related(
        "cake_template",
        "current_quote",
    )
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        orders = orders.filter(code__icontains=query)
    if status in STAFF_STATUSES:
        orders = orders.filter(status=status)
    page_obj = Paginator(orders, PAGE_SIZE).get_page(request.GET.get("page"))
    review_by_order = {
        review.order_id: review
        for review in OrderReview.objects.filter(
            order_id__in=[order.pk for order in page_obj.object_list]
        ).select_related("reply")
    }
    for order in page_obj.object_list:
        if order.current_quote:
            order.current_quote.display_status = _display_quote_status(
                order.current_quote
            )
        order.customer_review = review_by_order.get(order.pk)
        order.customer_review_state, order.customer_review_deadline = review_state(
            order,
            review=order.customer_review,
        )
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "orders/customer_order_list.html",
        {
            "page_obj": page_obj,
            "search_query": query,
            "selected_status": status,
            "statuses": Order.Status.choices,
            "pagination_query": query_params.urlencode(),
            "now": timezone.now(),
        },
    )


def _request_snapshot_differences(before, after):
    labels = {
        "cake_name_snapshot": "Mẫu bánh",
        "size": "Kích thước",
        "flavor": "Hương vị",
        "quantity": "Số lượng",
        "inscription": "Nội dung trên bánh",
        "decoration_request": "Trang trí",
        "desired_delivery_at": "Thời gian giao",
    }
    changes = []
    for field, label in labels.items():
        before_value = before.get(field, "")
        after_value = after.get(field, "")
        if before_value == after_value:
            continue
        if field == "desired_delivery_at":
            try:
                before_value = timezone.localtime(
                    datetime.fromisoformat(before_value)
                ).strftime("%d/%m/%Y %H:%M")
                after_value = timezone.localtime(
                    datetime.fromisoformat(after_value)
                ).strftime("%d/%m/%Y %H:%M")
            except (TypeError, ValueError):
                pass
        changes.append(
            {"label": label, "before": before_value, "after": after_value}
        )
    return changes


def _alternative_differences(alternative):
    return _request_snapshot_differences(
        alternative.request_before,
        alternative.request_after,
    )


def _order_detail_context(order, *, staff=False):
    quotes = list(order.quotes.select_related("created_by").order_by("-version_number"))
    for quote in quotes:
        quote.display_status = _display_quote_status(quote)
    status_labels = dict(Order.Status.choices)
    field_labels = {
        "size": "Kích thước",
        "flavor": "Hương vị",
        "quantity": "Số lượng",
        "inscription": "Nội dung trên bánh",
        "decoration_request": "Yêu cầu trang trí",
        "recipient_name": "Tên người nhận",
        "recipient_phone": "Số điện thoại",
        "delivery_address": "Địa chỉ giao",
        "desired_delivery_at": "Thời gian giao",
        "note": "Ghi chú",
    }
    history = list(order.history.select_related("actor", "quote").all())
    internal_history_actions = {
        OrderHistory.Action.STAFF_CHECKED,
        OrderHistory.Action.INTAKE_TRANSFERRED_TO_OWNER,
        OrderHistory.Action.OWNER_INTAKE_CHECKED,
        OrderHistory.Action.INTAKE_QUOTE_APPROVAL_SUBMITTED,
        OrderHistory.Action.INTAKE_QUOTE_APPROVAL_DECIDED,
        OrderHistory.Action.INTAKE_LEGACY_APPROVAL_CLOSED,
    }
    if not staff:
        history = [
            entry for entry in history if entry.action not in internal_history_actions
        ]
    intake_approval = (
        order.intake_quote_approvals.select_related(
            "submitted_by",
            "decided_by",
            "customer_notified_by",
            "quote",
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    current_check = current_availability_check(order)
    if current_check:
        authority_reason_labels = dict(StaffAvailabilityCheck.AuthorityReason.choices)
        current_check.authority_reason_labels = [
            authority_reason_labels.get(reason, reason)
            for reason in current_check.authority_reasons
        ]
    alternatives = list(
        order.alternatives.select_related(
            "created_by",
            "cake_template",
            "availability_check__checked_by",
            "owner_decided_by",
        ).all()
    )
    for alternative in alternatives:
        alternative.changed_fields = _alternative_differences(alternative)
    for entry in history:
        entry.from_status_label = status_labels.get(entry.from_status, entry.from_status)
        entry.to_status_label = status_labels.get(entry.to_status, entry.to_status)
        entry.changed_fields = [
            {
                "label": field_labels.get(field, field),
                "before": change.get("before", ""),
                "after": change.get("after", ""),
            }
            for field, change in entry.details.get("changes", {}).items()
        ]
        if entry.details.get("request_before") and entry.details.get("request_after"):
            entry.changed_fields.extend(
                _request_snapshot_differences(
                    entry.details["request_before"],
                    entry.details["request_after"],
                )
            )
    return {
        "order": order,
        "order_review": (
            OrderReview.objects.filter(order=order)
            .select_related("customer", "reply__owner")
            .first()
        ),
        "quotes": quotes,
        "history": history,
        "availability_check": current_check,
        "availability_checks": list(
            order.availability_checks.select_related("checked_by").all()
        ),
        "owner_intake_check": current_owner_intake_check(order),
        "intake_quote_approval": intake_approval,
        "intake_quote_approval_current": bool(
            intake_approval
            and current_check
            and intake_approval.request_revision == order.request_revision
            and intake_approval.availability_check_id == current_check.pk
        ),
        "can_reclassify_intake": not (
            intake_approval
            and intake_approval.status == IntakeQuoteApproval.Status.APPROVED
            and current_check
            and intake_approval.request_revision == order.request_revision
            and intake_approval.availability_check_id == current_check.pk
        ),
        "customer_intake_notice": (
            intake_approval.customer_notice
            if not staff
            and intake_approval
            and intake_approval.status == IntakeQuoteApproval.Status.REJECTED
            and intake_approval.customer_notice
            else ""
        ),
        "rejection_reason": order.rejection_reason,
        "alternatives": alternatives,
        "latest_alternative": alternatives[0] if alternatives else None,
        "staff_view": staff,
        "editable_statuses": EDITABLE_STATUSES,
        "check_statuses": (
            Order.Status.CHO_KIEM_TRA,
            Order.Status.CHO_TRAO_DOI,
            Order.Status.CHO_KHACH_XAC_NHAN,
        ),
        "now": timezone.now(),
    }


def _production_context(order):
    review_versions = list(
        order.review_versions.select_related("sent_by", "production_check").all()
    )
    for review in review_versions:
        review.private_image_url = reverse(
            "orders:private_review_image",
            kwargs={"review_id": review.pk},
        )
    revision_requests = list(
        order.revision_requests.select_related(
            "customer",
            "classified_by",
            "review_version",
            "replacement_review_version",
        )
        .prefetch_related("approval_requests", "change_agreements")
        .all()
    )
    for revision in revision_requests:
        revision.latest_approval = revision.approval_requests.order_by(
            "-created_at", "-pk"
        ).first()
        revision.latest_agreement = revision.change_agreements.order_by(
            "-version_number"
        ).first()
    production_checks = list(
        order.production_checks.select_related("checked_by").all()
    )
    latest_revision = revision_requests[0] if revision_requests else None
    pending_owner_revision = order.revision_requests.filter(
        status=RevisionRequest.Status.WAITING_OWNER
    ).order_by("-requested_at").first()
    pending_approval = None
    if pending_owner_revision:
        pending_approval = pending_owner_revision.approval_requests.filter(
            status=ApprovalRequest.Status.PENDING
        ).first()
    return {
        "review_versions": review_versions,
        "revision_requests": revision_requests,
        "production_checks": production_checks,
        "latest_production_check": production_checks[0] if production_checks else None,
        "current_revision_request": latest_revision,
        "pending_agreement": order.change_agreements.filter(
            status=ChangeAgreement.Status.PENDING
        ).select_related("approval_request").first(),
        "pending_approval": pending_approval,
        "has_rejected_approval": (
            latest_revision is not None
            and latest_revision.status == RevisionRequest.Status.OWNER_REJECTED
            and latest_revision.approval_requests.filter(
                status=ApprovalRequest.Status.REJECTED
            ).exists()
        ),
    }


def _delivery_context(order, *, staff=False):
    attempts = list(
        order.delivery_attempts.select_related("handled_by", "confirmed_by").all()
    )
    incidents = list(
        order.incidents.select_related(
            "reported_by",
            "decided_by",
            "executed_by",
            "delivery_attempt",
        )
        .prefetch_related("refunds")
        .all()
    )
    payments = list(order.payments.select_related("confirmed_by").all())
    refunds = list(order.refunds.select_related("payment", "recorded_by", "incident").all())
    prepayment_report = order.history.filter(
        action=OrderHistory.Action.CUSTOMER_PREPAYMENT_REPORTED
    ).first()
    financials = None
    has_accepted_terms = (
        order.current_quote is not None
        and order.current_quote.status == Quote.Status.ACCEPTED
    )
    if order.active_change_agreement or has_accepted_terms:
        financials = order_financials(order)
    active_attempt = next(
        (
            attempt
            for attempt in reversed(attempts)
            if attempt.result == DeliveryAttempt.Result.IN_PROGRESS
        ),
        None,
    )
    for attempt in attempts:
        attempt.evidence_url = (
            reverse(
                "orders:private_order_evidence",
                kwargs={
                    "evidence_type": "delivery",
                    "evidence_id": attempt.pk,
                },
            )
            if attempt.proof
            else ""
        )
    for incident in incidents:
        incident.evidence_url = (
            reverse(
                "orders:private_order_evidence",
                kwargs={"evidence_type": "incident", "evidence_id": incident.pk},
            )
            if incident.proof
            else ""
        )
    for refund in refunds:
        refund.evidence_url = (
            reverse(
                "orders:private_order_evidence",
                kwargs={"evidence_type": "refund", "evidence_id": refund.pk},
            )
            if refund.proof
            else ""
        )
    return {
        "delivery_attempts": attempts,
        "active_delivery_attempt": active_attempt,
        "incidents": incidents,
        "payments": payments,
        "refunds": refunds,
        "financials": financials,
        "prepayment_report": prepayment_report,
        "can_report_prepayment": bool(
            financials
            and financials["remaining"] > 0
            and order.status in PREPAYMENT_REPORT_STATUSES
        ),
        "staff_view": staff,
    }


def _customer_order_detail_context(order, *, review_form=None):
    context = _order_detail_context(order)
    context.update(_production_context(order))
    context.update(_delivery_context(order))
    if context["can_report_prepayment"] and not context["prepayment_report"]:
        context["prepayment_report_form"] = CustomerPrepaymentReportForm(
            initial={"expected_order_version": order.version}
        )
    alternative = context["latest_alternative"]
    if (
        alternative
        and alternative.status == OrderAlternative.Status.PENDING_CUSTOMER
        and order.status == Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN
    ):
        context["alternative_response_form"] = AlternativeCustomerResponseForm(
            initial={
                "expected_order_version": order.version,
                "expected_alternative_revision": alternative.revision,
            }
        )
    if context["active_delivery_attempt"]:
        attempt = context["active_delivery_attempt"]
        context["delivery_confirmation_form"] = DeliveryConfirmationForm(
            initial={
                "expected_order_version": order.version,
                "expected_attempt_revision": attempt.revision,
            }
        )
        context["delivery_incident_form"] = DeliveryIncidentForm(
            initial={
                "expected_order_version": order.version,
                "expected_attempt_revision": attempt.revision,
            }
        )
    elif (
        order.status in (Order.Status.DA_GIAO_CHO_THANH_TOAN, Order.Status.HOAN_TAT)
        and not any(
            incident.status in ACTIVE_INCIDENT_STATUSES
            for incident in context["incidents"]
        )
    ):
        latest_delivered_attempt = next(
            (
                attempt
                for attempt in reversed(context["delivery_attempts"])
                if attempt.result == DeliveryAttempt.Result.DELIVERED
            ),
            None,
        )
        if latest_delivered_attempt:
            context["incident_eligible_delivery"] = latest_delivered_attempt
            context["delivery_incident_form"] = DeliveryIncidentForm(
                initial={
                    "expected_order_version": order.version,
                    "expected_attempt_revision": latest_delivered_attempt.revision,
                }
            )
    for incident in context["incidents"]:
        if incident.status == OrderIncident.Status.WAITING_CUSTOMER:
            incident.customer_response_form = IncidentCustomerResponseForm(
                initial={
                    "expected_order_version": order.version,
                    "expected_incident_revision": incident.revision,
                }
            )
    if order.current_quote and order.status == Order.Status.CHO_KHACH_XAC_NHAN:
        initial = {
            "expected_order_version": order.version,
            "expected_quote_id": order.current_quote_id,
            "expected_quote_revision": order.current_quote.revision,
        }
        context["accept_form"] = QuoteResponseForm(initial=initial)
        context["change_form"] = QuoteChangeRequestForm(initial=initial)
    if (
        order.current_review
        and order.status == Order.Status.CHO_DUYET_ANH
        and order.current_review.status == CakeReviewVersion.Status.PENDING
    ):
        review_initial = {
            "expected_order_version": order.version,
            "expected_review_id": order.current_review_id,
            "expected_review_revision": order.current_review.revision,
        }
        context["revision_form"] = RevisionRequestForm(initial=review_initial)
    agreement = context["pending_agreement"]
    if agreement and order.status == Order.Status.CHO_KHACH_DUYET_THAY_DOI:
        context["agreement_form"] = AgreementResponseForm(
            initial={
                "expected_version": order.version,
                "expected_agreement_id": agreement.pk,
                "expected_agreement_revision": agreement.revision,
            }
        )
    if context["has_rejected_approval"]:
        context["keep_review_version_id"] = order.current_review_id
    if order.status in (
        Order.Status.CHO_XU_LY_CHINH_SUA,
        Order.Status.CHO_TRAO_DOI_CHINH_SUA,
    ):
        context["continue_discussion_form"] = ContinueDiscussionForm(
            initial={"expected_version": order.version}
        )
    state, deadline = review_state(order, review=context["order_review"])
    context["review_state"] = state
    context["review_deadline"] = deadline
    if review_form is not None:
        context["order_review_form"] = review_form
    elif state == REVIEW_AVAILABLE:
        context["order_review_form"] = OrderReviewForm()
    return context


@login_required
@role_required(User.Role.CUSTOMER)
def customer_order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("cake_template", "current_quote"),
        pk=pk,
        customer=request.user,
    )
    return render(
        request,
        "orders/order_detail.html",
        _customer_order_detail_context(order),
    )


@login_required
@role_required(User.Role.CUSTOMER)
def customer_report_prepayment_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    form = CustomerPrepaymentReportForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("Biểu mẫu báo chuyển khoản không hợp lệ."),
            staff=False,
        )
    try:
        updated, created = report_prepayment(
            order_id=order.pk,
            customer=request.user,
            **form.cleaned_data,
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=False)
    messages.success(
        request,
        "Đã báo cửa hàng bạn chuyển khoản trước. Khoản thanh toán chỉ được ghi "
        "nhận sau khi nhân viên đối chiếu tiền thực nhận."
        if created
        else "Cửa hàng đã nhận được thông báo chuyển khoản trước của bạn.",
    )
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_submit_order_review(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    form = OrderReviewForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "orders/order_detail.html",
            _customer_order_detail_context(order, review_form=form),
        )
    try:
        _review, created = submit_order_review(
            order_id=order.pk,
            customer=request.user,
            rating=form.cleaned_data["rating"],
            comment=form.cleaned_data["comment"],
        )
    except (OrderActionError, ValidationError) as exc:
        error = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        messages.error(request, error)
    else:
        if created:
            messages.success(request, "Đã gửi đánh giá đơn hàng.")
            if _review.comment.strip():
                try:
                    dispatch_review_classification_events(
                        limit=1,
                        event_id=_review.classification_event.event_id,
                    )
                except ValueError:
                    logger.warning(
                        "Không gửi được sự kiện phân loại đánh giá; sự kiện được giữ trong outbox."
                    )
                    messages.warning(
                        request,
                        "Đánh giá đã được lưu; chưa gửi được sang n8n. Sự kiện vẫn được giữ để thử lại.",
                    )
                else:
                    _review.classification_event.refresh_from_db()
                    if _review.classification_event.status in (
                        ReviewClassificationEvent.Status.PENDING,
                        ReviewClassificationEvent.Status.FAILED,
                    ):
                        messages.warning(
                            request,
                            "Đánh giá đã được lưu nhưng n8n chưa tiếp nhận; sự kiện sẽ được thử lại.",
                        )
            else:
                messages.info(
                    request,
                    "Đánh giá không có nhận xét nên không cần phân loại văn bản.",
                )
        else:
            messages.info(
                request,
                "Đánh giá đã được gửi trước đó và không thể sửa hoặc gửi lại.",
            )
    return redirect("orders:order_detail", pk=order.pk)


@csrf_exempt
def n8n_review_classification_callback(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not callback_key_matches(request.headers.get("X-Callback-Key", "")):
        return JsonResponse({"error": "invalid_callback_key"}, status=403)
    if len(request.body) > 65536:
        return JsonResponse({"error": "payload_too_large"}, status=413)
    if request.content_type != "application/json":
        return JsonResponse({"error": "content_type_must_be_json"}, status=415)
    try:
        payload = json.loads(request.body.decode("utf-8"))
        result = accept_review_classification(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, CallbackPayloadError) as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except LookupError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except CallbackConflict as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"status": result})


@login_required
@role_required((User.Role.STAFF, User.Role.OWNER))
def staff_pending_review_classifications(request):
    pending = (
        OrderReview.objects.filter(
            classification_status=OrderReview.ClassificationStatus.PENDING_REVIEW
        )
        .select_related("order", "customer")
        .order_by("created_at", "pk")
    )
    page_obj = Paginator(pending, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request,
        "orders/staff_review_classification_list.html",
        {"page_obj": page_obj},
    )


@login_required
@role_required((User.Role.STAFF, User.Role.OWNER))
def staff_review_classification_detail(request, review_id):
    review = get_object_or_404(
        OrderReview.objects.select_related("order", "customer", "reviewed_by"),
        pk=review_id,
    )
    if review.classification_status != OrderReview.ClassificationStatus.PENDING_REVIEW:
        if request.method == "POST":
            messages.error(request, "Đánh giá này đã được xử lý; không thể ghi đè.")
            return redirect("orders:staff_pending_review_classifications")
    form = ReviewClassificationReviewForm(
        request.POST or None,
        initial={"version": review.classification_version},
    )
    if request.method == "POST":
        if not form.is_valid():
            return render(
                request,
                "orders/staff_review_classification_detail.html",
                {"review": review, "form": form},
            )
        try:
            confirm_review_classification(
                review_id=review.pk,
                reviewer=request.user,
                final_label=form.cleaned_data["final_label"],
                review_note=form.cleaned_data["review_note"],
                version=form.cleaned_data["version"],
            )
        except StaleReviewClassification as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Đã xác nhận nhãn đánh giá.")
        return redirect("orders:staff_pending_review_classifications")
    return render(
        request,
        "orders/staff_review_classification_detail.html",
        {"review": review, "form": form},
    )


@login_required
@role_required(User.Role.CUSTOMER)
def customer_supplement(request, pk):
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    if order.status != Order.Status.CAN_BO_SUNG or not order.needs_supplement:
        messages.error(request, "Đơn hàng hiện không chờ khách bổ sung.")
        return redirect("orders:order_detail", pk=order.pk)
    form = OrderSupplementForm(request.POST or None, instance=order)
    if request.method == "POST" and form.is_valid():
        try:
            updated = update_customer_request(
                order_id=order.pk,
                customer=request.user,
                expected_version=form.cleaned_data["expected_version"],
                data=form.cleaned_data,
            )
        except OrderActionError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Đã cập nhật và gửi lại yêu cầu.")
            return redirect("orders:order_detail", pk=updated.pk)
    return render(
        request,
        "orders/order_supplement.html",
        {"order": order, "form": form},
    )


@login_required
@role_required(User.Role.CUSTOMER)
def customer_accept_quote(request, pk, quote_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    quote = get_object_or_404(Quote, pk=quote_id, order=order)
    form = QuoteResponseForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Biểu mẫu xác nhận không hợp lệ. Hãy tải lại đơn hàng.")
        return redirect("orders:order_detail", pk=order.pk)
    if form.cleaned_data["expected_quote_id"] != quote.pk:
        messages.error(request, "Báo giá đã thay đổi. Hãy tải lại đơn hàng.")
        return redirect("orders:order_detail", pk=order.pk)
    try:
        updated = accept_quote(
            order_id=order.pk,
            customer=request.user,
            quote_id=quote.pk,
            quote_revision=form.cleaned_data["expected_quote_revision"],
            expected_order_version=form.cleaned_data["expected_order_version"],
        )
    except OrderActionError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "Bạn đã đồng ý báo giá. Đơn hàng đã được xác nhận.")
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_request_quote_change(request, pk, quote_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    quote = get_object_or_404(Quote, pk=quote_id, order=order)
    form = QuoteChangeRequestForm(request.POST)
    if not form.is_valid():
        for error in form.errors.get("message", []):
            messages.error(request, error)
        for error in form.non_field_errors():
            messages.error(request, error)
        return redirect("orders:order_detail", pk=order.pk)
    if form.cleaned_data["expected_quote_id"] != quote.pk:
        messages.error(request, "Báo giá đã thay đổi. Hãy tải lại đơn hàng.")
        return redirect("orders:order_detail", pk=order.pk)
    try:
        updated = request_quote_change(
            order_id=order.pk,
            customer=request.user,
            quote_id=quote.pk,
            quote_revision=form.cleaned_data["expected_quote_revision"],
            expected_order_version=form.cleaned_data["expected_order_version"],
            message=form.cleaned_data["message"],
        )
    except OrderActionError as exc:
        return _action_error(request, order, exc)
    messages.success(request, "Đã gửi yêu cầu trao đổi lại.")
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_order_list(request):
    orders = Order.objects.select_related("customer", "cake_template", "current_quote")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        orders = orders.filter(Q(code__icontains=query))
    if status in STAFF_STATUSES:
        orders = orders.filter(status=status)
    page_obj = Paginator(orders, PAGE_SIZE).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "orders/staff_order_list.html",
        {
            "page_obj": page_obj,
            "search_query": query,
            "selected_status": status,
            "statuses": Order.Status.choices,
            "pagination_query": query_params.urlencode(),
            "now": timezone.now(),
        },
    )


@login_required
@role_required(User.Role.STAFF)
def staff_order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related("customer", "cake_template", "current_quote"),
        pk=pk,
    )
    context = _order_detail_context(order, staff=True)
    context.update(_production_context(order))
    context.update(_delivery_context(order, staff=True))
    context["owner_handled_intake"] = (
        order.status == Order.Status.CHO_CHU_DUYET_BAO_GIA
        or (
            order.intake_handler_role == "OWNER"
            and order.status
            in (
                Order.Status.CHO_TRAO_DOI,
                Order.Status.CHO_KHACH_XAC_NHAN,
            )
        )
    )
    context["check_form"] = StaffCheckForm(
        initial={
            "materials_available": (
                "yes" if order.materials_available is True else
                "no" if order.materials_available is False else ""
            ),
            "schedule_available": (
                "yes" if order.schedule_available is True else
                "no" if order.schedule_available is False else ""
            ),
            "notes": order.staff_check_notes,
            "reason": (
                context["availability_check"].reason
                if context["availability_check"]
                else ""
            ),
            "reason_details": (
                context["availability_check"].reason_details
                if context["availability_check"]
                else ""
            ),
            "authority_classification": (
                context["availability_check"].authority_classification
                if context["availability_check"]
                and context["availability_check"].authority_classification
                != StaffAvailabilityCheck.AuthorityClassification.UNCLASSIFIED
                else ""
            ),
            "authority_reasons": (
                context["availability_check"].authority_reasons
                if context["availability_check"]
                else []
            ),
            "authority_details": (
                context["availability_check"].authority_details
                if context["availability_check"]
                else ""
            ),
            "authority_basis": (
                context["availability_check"].authority_basis
                if context["availability_check"]
                else ""
            ),
            "expected_version": order.version,
        }
    )
    if (
        context["availability_check"]
        and context["availability_check"].has_availability_failure
        and order.status
        in (
            Order.Status.CHO_KIEM_TRA,
            Order.Status.CHO_TRAO_DOI,
            Order.Status.CHO_KHACH_XAC_NHAN,
            Order.Status.CHO_CHU_DUYET_PHUONG_AN,
            Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN,
        )
    ):
        context["alternative_form"] = OrderAlternativeForm(order=order)
    latest_alternative = context["latest_alternative"]
    if latest_alternative and latest_alternative.status in (
        OrderAlternative.Status.DRAFT,
        OrderAlternative.Status.APPROVED,
    ):
        context["alternative_send_form"] = AlternativeActionForm(
            initial={
                "expected_order_version": order.version,
                "expected_alternative_revision": latest_alternative.revision,
            }
        )
    context["message_form"] = StaffMessageForm(
        initial={"expected_version": order.version}
    )
    quote_initial = {}
    if (
        context["intake_quote_approval_current"]
        and context["intake_quote_approval"].status
        == IntakeQuoteApproval.Status.APPROVED
    ):
        approved_terms = context["intake_quote_approval"].quote_snapshot
        quote_initial = {
            "unit_price": approved_terms["unit_price"],
            "delivery_fee": approved_terms["delivery_fee"],
            "proposed_delivery_at": datetime.fromisoformat(
                approved_terms["proposed_delivery_at"]
            ),
            "expires_at": datetime.fromisoformat(approved_terms["expires_at"]),
            "notes": approved_terms["notes"],
            "revision_reason": approved_terms["revision_reason"],
        }
    context["quote_form"] = QuoteForm(order=order, initial=quote_initial)
    if (
        context["intake_quote_approval"]
        and context["intake_quote_approval"].status
        == IntakeQuoteApproval.Status.REJECTED
        and context["intake_quote_approval"].customer_notified_at is None
    ):
        context["intake_rejection_notice_form"] = IntakeRejectionNoticeForm(
            initial={
                "expected_order_version": order.version,
                "expected_approval_revision": context[
                    "intake_quote_approval"
                ].revision,
            }
        )
    context["delivery_start_form"] = DeliveryStartForm(
        initial={"expected_version": order.version}
    )
    if context["active_delivery_attempt"]:
        attempt = context["active_delivery_attempt"]
        context["delivery_confirmation_form"] = DeliveryConfirmationForm(
            initial={
                "expected_order_version": order.version,
                "expected_attempt_revision": attempt.revision,
            }
        )
        context["delivery_failure_form"] = DeliveryFailureForm(
            initial={"expected_order_version": order.version}
        )
        context["delivery_incident_form"] = DeliveryIncidentForm(
            initial={
                "expected_order_version": order.version,
                "expected_attempt_revision": attempt.revision,
            }
        )
    if order.status == Order.Status.GIAO_THAT_BAI and context["delivery_attempts"]:
        context["failed_delivery_attempt"] = context["delivery_attempts"][-1]
        context["redelivery_form"] = RedeliveryForm(
            initial={"expected_version": order.version}
        )
        context["delivery_incident_form"] = DeliveryIncidentForm(
            initial={
                "expected_order_version": order.version,
                "expected_attempt_revision": context[
                    "failed_delivery_attempt"
                ].revision,
            }
        )
    if (
        context["financials"]
        and context["financials"]["remaining"] > 0
        and order.status not in (Order.Status.DA_HUY, Order.Status.TU_CHOI)
    ):
        context["payment_form"] = PaymentForm(
            initial={"expected_order_version": order.version}
        )
    for incident in context["incidents"]:
        if incident.status in (OrderIncident.Status.OPEN, OrderIncident.Status.REJECTED):
            incident.proposal_form = IncidentProposalForm(
                initial={
                    "expected_order_version": order.version,
                    "expected_incident_revision": incident.revision,
                }
            )
        if (
            incident.status == OrderIncident.Status.IN_PROGRESS
            and incident.proposed_resolution
            in (OrderIncident.Resolution.REPAIR, OrderIncident.Resolution.REPLACE)
        ):
            incident.execution_form = IncidentExecutionForm(
                initial={
                    "expected_order_version": order.version,
                    "expected_incident_revision": incident.revision,
                }
            )
        if (
            incident.status == OrderIncident.Status.IN_PROGRESS
            and incident.proposed_resolution == OrderIncident.Resolution.REDELIVER
        ):
            incident.execution_form = IncidentExecutionForm(
                initial={
                    "expected_order_version": order.version,
                    "expected_incident_revision": incident.revision,
                }
            )
        if (
            incident.status == OrderIncident.Status.IN_PROGRESS
            and incident.proposed_resolution == OrderIncident.Resolution.CANCEL
            and context["payments"]
        ):
            incident.refund_form = RefundForm(
                payments=context["payments"],
                initial={"expected_order_version": order.version},
            )
    context["start_production_version"] = order.version
    if order.status == Order.Status.DANG_LAM:
        context["production_check_form"] = ProductionCheckForm(
            initial={"expected_version": order.version}
        )
    if order.status in (
        Order.Status.DANG_LAM,
        Order.Status.CHO_DUYET_ANH,
    ):
        context["review_form"] = CakeReviewForm(order=order)
    if (
        order.status
        in (
            Order.Status.CHO_XU_LY_CHINH_SUA,
            Order.Status.CHO_TRAO_DOI_CHINH_SUA,
        )
        and context["current_revision_request"]
        and context["current_revision_request"].status
        == RevisionRequest.Status.OPEN
    ):
        context["revision_classification_form"] = RevisionClassificationForm(
            initial={"expected_version": order.version}
        )
    return render(request, "orders/order_detail.html", context)


@login_required
@role_required(User.Role.STAFF)
def staff_check_order(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = StaffCheckForm(request.POST)
    if not form.is_valid():
        for error in form.errors.values():
            for message in error:
                messages.error(request, message)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated = record_staff_check(
            order_id=order.pk,
            staff=request.user,
            expected_version=form.cleaned_data["expected_version"],
            materials_available=(
                {"yes": True, "no": False}.get(
                    form.cleaned_data["materials_available"]
                )
            ),
            schedule_available=(
                {"yes": True, "no": False}.get(
                    form.cleaned_data["schedule_available"]
                )
            ),
            notes=form.cleaned_data["notes"],
            reason=form.cleaned_data.get("reason", ""),
            reason_details=form.cleaned_data.get("reason_details", ""),
            authority_classification=form.cleaned_data["authority_classification"],
            authority_reasons=form.cleaned_data["authority_reasons"],
            authority_details=form.cleaned_data["authority_details"],
            authority_basis=form.cleaned_data["authority_basis"],
        )
    except OrderActionError as exc:
        return _action_error(request, order, exc, staff=True)
    if updated.intake_handler_role == "OWNER":
        messages.success(
            request,
            "Đã chuyển nguyên yêu cầu cho chủ cửa hàng xử lý.",
        )
    else:
        messages.success(request, "Đã lưu kết quả phân loại và kiểm tra.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_create_alternative(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = OrderAlternativeForm(request.POST, order=order)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, alternative = create_alternative_draft(
            order_id=order.pk,
            staff=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            proposed_cake_id=form.cleaned_data["proposed_cake"].pk,
            size=form.cleaned_data["size"],
            flavor=form.cleaned_data["flavor"],
            quantity=form.cleaned_data["quantity"],
            inscription=form.cleaned_data["inscription"],
            decoration_request=form.cleaned_data["decoration_request"],
            desired_delivery_at=form.cleaned_data["desired_delivery_at"],
            difference_summary=form.cleaned_data["difference_summary"],
            estimated_unit_price=form.cleaned_data["estimated_unit_price"],
            requires_owner_approval=form.cleaned_data["requires_owner_approval"],
        )
    except (OrderActionError, ValidationError) as exc:
        error_messages = exc.messages if isinstance(exc, ValidationError) else [str(exc)]
        for error in error_messages:
            messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(
        request,
        f"Đã lưu phương án nháp v{alternative.version_number} để xem lại trước khi gửi.",
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_send_alternative(request, pk, alternative_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    alternative = get_object_or_404(
        OrderAlternative,
        pk=alternative_id,
        order=order,
    )
    form = AlternativeActionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Biểu mẫu gửi phương án không hợp lệ. Hãy tải lại trang.")
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, sent_alternative = send_order_alternative(
            order_id=order.pk,
            alternative_id=alternative.pk,
            staff=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_alternative_revision=form.cleaned_data[
                "expected_alternative_revision"
            ],
        )
    except OrderActionError as exc:
        return _action_error(request, order, exc, staff=True)
    if sent_alternative.status == OrderAlternative.Status.PENDING_OWNER:
        messages.success(
            request,
            "Đã chuyển phương án cho OWNER duyệt; chưa gửi nội dung cho khách.",
        )
    else:
        messages.success(request, "Đã gửi phương án hiện hành cho khách phản hồi.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_respond_alternative(request, pk, alternative_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    alternative = get_object_or_404(
        OrderAlternative,
        pk=alternative_id,
        order=order,
    )
    form = AlternativeCustomerResponseForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Phản hồi không hợp lệ. Hãy tải lại chi tiết đơn.")
        return redirect("orders:order_detail", pk=pk)
    try:
        updated, _alternative = respond_order_alternative(
            order_id=order.pk,
            alternative_id=alternative.pk,
            customer=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_alternative_revision=form.cleaned_data[
                "expected_alternative_revision"
            ],
            accept=form.cleaned_data["decision"] == "accept",
            response=form.cleaned_data["response"],
        )
    except OrderActionError as exc:
        return _action_error(request, order, exc)
    if updated.status == Order.Status.CHO_KIEM_TRA:
        messages.success(
            request,
            "Đã ghi nhận bạn đồng ý phương án. Nhân viên sẽ kiểm tra lại; đây chưa phải xác nhận đơn hoặc giá.",
        )
    else:
        messages.info(
            request,
            "Đã ghi nhận bạn từ chối phương án. Cửa hàng không tiếp nhận đơn này; bạn có thể tạo yêu cầu mới.",
        )
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_request_supplement(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = StaffMessageForm(request.POST)
    if not form.is_valid():
        for error in form.errors.get("message", []):
            messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated = request_customer_supplement(
            order_id=order.pk,
            staff=request.user,
            expected_version=form.cleaned_data["expected_version"],
            message=form.cleaned_data["message"],
        )
    except OrderActionError as exc:
        return _action_error(request, order, exc, staff=True)
    messages.success(request, "Đã gửi yêu cầu bổ sung cho khách hàng.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_reject_order(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = StaffMessageForm(request.POST)
    if not form.is_valid():
        for error in form.errors.get("message", []):
            messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated = reject_order(
            order_id=order.pk,
            staff=request.user,
            expected_version=form.cleaned_data["expected_version"],
            reason=form.cleaned_data["message"],
        )
    except OrderActionError as exc:
        return _action_error(request, order, exc, staff=True)
    messages.success(request, "Đã từ chối tiếp nhận đơn.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_send_quote(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = QuoteForm(request.POST, order=order)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, quote = send_quote(
            order_id=order.pk,
            staff=request.user,
            **form.cleaned_data,
        )
    except (OrderActionError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            for error in exc.messages:
                messages.error(request, error)
        else:
            messages.error(request, str(exc))
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(
        request,
        f"Đã gửi báo giá phiên bản {quote.version_number} cho khách hàng.",
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_submit_intake_quote_approval(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    messages.error(
        request,
        "Quy trình này đã dừng: STAFF gửi báo giá trong quyền; đơn vượt quyền được chuyển cho OWNER trực tiếp xử lý.",
    )
    return redirect("orders:staff_order_detail", pk=pk)
    order = get_object_or_404(Order, pk=pk)
    form = QuoteForm(request.POST, order=order)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, approval = submit_intake_quote_approval(
            order_id=order.pk,
            staff=request.user,
            **form.cleaned_data,
        )
    except (OrderActionError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            for error in exc.messages:
                messages.error(request, error)
        else:
            messages.error(request, str(exc))
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(
        request,
        f"Đã gửi OWNER duyệt nội dung báo giá (yêu cầu duyệt #{approval.pk}); chưa gửi báo giá cho khách.",
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_notify_intake_quote_rejection(request, pk, approval_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    messages.error(
        request,
        "Quy trình cũ không còn dùng. OWNER ghi lý do từ chối trực tiếp để khách xem trong chi tiết đơn.",
    )
    return redirect("orders:staff_order_detail", pk=pk)
    order = get_object_or_404(Order, pk=pk)
    form = IntakeRejectionNoticeForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, _approval = notify_customer_intake_rejection(
            order_id=order.pk,
            approval_id=approval_id,
            staff=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_approval_revision=form.cleaned_data[
                "expected_approval_revision"
            ],
            message=form.cleaned_data["message"],
        )
    except (OrderActionError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.error(request, str(exc))
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(request, "Đã ghi nhận thông báo trong chi tiết đơn của khách.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
def private_review_image(request, review_id):
    review = get_object_or_404(
        CakeReviewVersion.objects.select_related("order"),
        pk=review_id,
    )
    if request.user.role == User.Role.CUSTOMER:
        allowed = review.order.customer_id == request.user.pk
    elif request.user.role == User.Role.STAFF:
        allowed = True
    elif request.user.role == User.Role.OWNER:
        allowed = ApprovalRequest.objects.filter(
            revision_request__order=review.order
        ).exists() or OrderIncident.objects.filter(
            order=review.order,
        ).exists()
    else:
        allowed = False
    if not allowed:
        raise PermissionDenied
    response = FileResponse(
        review.photo.open("rb"),
        content_type=(
            "image/png"
            if review.photo.name.lower().endswith(".png")
            else "image/webp"
            if review.photo.name.lower().endswith(".webp")
            else "image/jpeg"
        ),
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _production_error_redirect(order, *, staff=False, owner=False):
    if owner:
        return redirect("orders:owner_approval_detail", approval_id=order.pk)
    if staff:
        return redirect("orders:staff_order_detail", pk=order.pk)
    return redirect("orders:order_detail", pk=order.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_start_production(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    try:
        updated = start_production(
            order_id=order.pk,
            staff=request.user,
            expected_version=int(request.POST.get("expected_version", "-1")),
        )
    except (OrderActionError, ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(request, "Đã bắt đầu làm bánh theo thỏa thuận đã xác nhận.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_record_production_check(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = ProductionCheckForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, check = record_production_check(
            order_id=order.pk,
            staff=request.user,
            expected_version=form.cleaned_data["expected_version"],
            passed=form.cleaned_data["passed"] == "yes",
            notes=form.cleaned_data["notes"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(
        request,
        "Đã ghi nhận kiểm tra chất lượng "
        + ("đạt." if check.passed else "chưa đạt; cần xử lý trước khi gửi ảnh."),
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_send_review_photo(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = CakeReviewForm(request.POST, request.FILES, order=order)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, review = send_review_photo(
            order_id=order.pk,
            staff=request.user,
            expected_version=form.cleaned_data["expected_version"],
            photo=form.cleaned_data["photo"],
            description=form.cleaned_data["description"],
            replacement_reason=form.cleaned_data["replacement_reason"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(
        request,
        f"Đã gửi ảnh phiên bản {review.version_number} để khách hàng duyệt.",
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_accept_review(request, pk, review_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    try:
        updated = accept_review(
            order_id=order.pk,
            customer=request.user,
            review_id=review_id,
            expected_order_version=int(request.POST.get("expected_order_version", "-1")),
            expected_review_revision=int(
                request.POST.get("expected_review_revision", "-1")
            ),
        )
    except (OrderActionError, ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(request, "Bạn đã duyệt ảnh bánh. Đơn hàng sẵn sàng giao.")
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_request_review_revision(request, pk, review_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    form = RevisionRequestForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:order_detail", pk=order.pk)
    if form.cleaned_data["expected_review_id"] != review_id:
        messages.error(request, "Ảnh bánh đã thay đổi. Hãy tải lại đơn hàng.")
        return redirect("orders:order_detail", pk=order.pk)
    try:
        updated, _revision_request = request_review_revision(
            order_id=order.pk,
            customer=request.user,
            review_id=review_id,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_review_revision=form.cleaned_data["expected_review_revision"],
            message=form.cleaned_data["message"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(request, "Đã gửi yêu cầu chỉnh sửa cho cửa hàng.")
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_classify_revision(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = RevisionClassificationForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:staff_order_detail", pk=pk)
    try:
        updated, _revision_request, approval = classify_revision_request(
            order_id=order.pk,
            staff=request.user,
            expected_order_version=form.cleaned_data["expected_version"],
            scope=form.cleaned_data["scope"],
            reason=form.cleaned_data["reason"],
            approval_summary=form.cleaned_data["approval_summary"],
            proposed_plan=form.cleaned_data["proposed_plan"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:staff_order_detail", pk=pk)
    messages.success(
        request,
        (
            "Đã chuyển yêu cầu vượt phạm vi sang chủ cửa hàng duyệt."
            if approval
            else "Đã nhận chỉnh sửa trong phạm vi; cần làm lại và kiểm tra chất lượng."
        ),
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.OWNER)
def owner_intake_order_list(request):
    queue_statuses = (
        Order.Status.CHO_CHU_DUYET_BAO_GIA,
        Order.Status.CHO_TRAO_DOI,
        Order.Status.CHO_KHACH_XAC_NHAN,
    )
    orders = Order.objects.filter(
        Q(status=Order.Status.CHO_CHU_DUYET_BAO_GIA)
        | Q(intake_handler_role="OWNER", status__in=queue_statuses)
        | Q(
            status=Order.Status.CHO_KIEM_TRA,
            intake_handler_role="STAFF",
            intake_quote_approvals__status=IntakeQuoteApproval.Status.APPROVED,
        )
    ).select_related(
        "customer",
        "cake_template",
        "current_quote",
    ).distinct()
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        orders = orders.filter(
            Q(code__icontains=query)
            | Q(cake_name_snapshot__icontains=query)
            | Q(customer__username__icontains=query)
        )
    if status in {value for value, _label in Order.Status.choices}:
        orders = orders.filter(status=status)
    page_obj = Paginator(orders.order_by("-updated_at", "-pk"), PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    for order in page_obj.object_list:
        order.transfer_history = order.history.filter(
            action=OrderHistory.Action.INTAKE_TRANSFERRED_TO_OWNER
        ).select_related("actor").first()
        order.current_staff_check = current_availability_check(order)
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "orders/owner_intake_order_list.html",
        {
            "page_obj": page_obj,
            "search_query": query,
            "selected_status": status,
            "statuses": [
                (value, label)
                for value, label in Order.Status.choices
                if value in (*queue_statuses, Order.Status.CHO_KIEM_TRA)
            ],
            "pagination_query": query_params.urlencode(),
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_intake_order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.select_related(
            "customer",
            "cake_template",
            "current_quote",
        )
        .filter(
            Q(pk=pk)
            & (
                Q(status=Order.Status.CHO_CHU_DUYET_BAO_GIA)
                | Q(
                    intake_handler_role="OWNER",
                    status__in=(
                        Order.Status.CHO_TRAO_DOI,
                        Order.Status.CHO_KHACH_XAC_NHAN,
                    ),
                )
                | Q(
                    status=Order.Status.CHO_KIEM_TRA,
                    intake_handler_role="STAFF",
                    intake_quote_approvals__status=IntakeQuoteApproval.Status.APPROVED,
                )
            )
        )
        .distinct()
    )
    legacy_approval = order.intake_quote_approvals.order_by(
        "-created_at", "-pk"
    ).first()
    transfer_history = order.history.filter(
        action=OrderHistory.Action.INTAKE_TRANSFERRED_TO_OWNER
    ).select_related("actor").first()
    owner_checks = list(
        order.owner_intake_checks.select_related("checked_by").all()
    )
    current_check = current_owner_intake_check(order)
    return render(
        request,
        "orders/owner_intake_order_detail.html",
        {
            "order": order,
            "transfer_history": transfer_history,
            "staff_check": current_availability_check(order),
            "owner_checks": owner_checks,
            "owner_intake_check": current_check,
            "history": order.history.select_related("actor", "quote").all(),
            "quotes": order.quotes.select_related("created_by").all(),
            "legacy_approval": legacy_approval,
            "claim_form": OwnerClaimIntakeOrderForm(
                initial={"expected_order_version": order.version}
            ),
            "owner_check_form": OwnerIntakeCheckForm(
                initial={"expected_order_version": order.version}
            ),
            "quote_form": QuoteForm(order=order),
            "rejection_form": OwnerIntakeRejectionForm(
                initial={"expected_order_version": order.version}
            ),
            "can_claim_legacy": (
                (
                    order.status == Order.Status.CHO_CHU_DUYET_BAO_GIA
                    and order.intake_handler_role != "OWNER"
                )
                or (
                    order.status == Order.Status.CHO_KIEM_TRA
                    and order.intake_handler_role == "STAFF"
                    and legacy_approval is not None
                    and legacy_approval.status == IntakeQuoteApproval.Status.APPROVED
                )
            ),
            "can_owner_process": (
                order.intake_handler_role == "OWNER"
                and order.status
                in (
                    Order.Status.CHO_CHU_DUYET_BAO_GIA,
                    Order.Status.CHO_TRAO_DOI,
                )
            ),
            "quote_can_send": (
                order.current_quote is None
                or order.current_quote.status == Quote.Status.CHANGE_REQUESTED
                or (
                    order.current_quote.status == Quote.Status.PENDING
                    and order.current_quote.expires_at <= timezone.now()
                )
            ),
            "now": timezone.now(),
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_claim_intake_order(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = OwnerClaimIntakeOrderForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Yêu cầu nhận xử lý không hợp lệ.")
        return redirect("orders:owner_intake_order_detail", pk=pk)
    try:
        order = claim_legacy_intake_order(
            order_id=pk,
            owner=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
        )
    except OrderActionError as exc:
        messages.error(request, str(exc))
        return redirect("orders:owner_intake_order_detail", pk=pk)
    messages.success(request, "Đã nhận xử lý đơn theo quy trình mới.")
    return redirect("orders:owner_intake_order_detail", pk=order.pk)


@login_required
@role_required(User.Role.OWNER)
def owner_check_intake_order(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = OwnerIntakeCheckForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:owner_intake_order_detail", pk=order.pk)
    try:
        updated, check = record_owner_intake_check(
            order_id=order.pk,
            owner=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            materials_available=form.cleaned_data["materials_available"] == "yes",
            schedule_available=form.cleaned_data["schedule_available"] == "yes",
            notes=form.cleaned_data["notes"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:owner_intake_order_detail", pk=order.pk)
    messages.success(request, "Đã lưu kết quả kiểm tra khả năng tiếp nhận.")
    return redirect("orders:owner_intake_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.OWNER)
def owner_send_intake_quote(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = QuoteForm(request.POST, order=order)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:owner_intake_order_detail", pk=order.pk)
    try:
        updated, quote = send_quote(
            order_id=order.pk,
            actor=request.user,
            **form.cleaned_data,
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:owner_intake_order_detail", pk=order.pk)
    messages.success(
        request,
        f"Đã gửi trực tiếp báo giá phiên bản {quote.version_number} cho khách.",
    )
    return redirect("orders:owner_intake_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.OWNER)
def owner_reject_intake_order(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = OwnerIntakeRejectionForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:owner_intake_order_detail", pk=order.pk)
    try:
        updated = reject_order(
            order_id=order.pk,
            actor=request.user,
            expected_version=form.cleaned_data["expected_order_version"],
            reason=form.cleaned_data["reason"],
            internal_note=form.cleaned_data["internal_note"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:owner_intake_order_detail", pk=order.pk)
    messages.success(request, "Đã từ chối tiếp nhận; lý do đã hiển thị cho khách.")
    return redirect("orders:owner_intake_order_list")


@login_required
@role_required(User.Role.OWNER)
def owner_approval_list(request):
    approvals = ApprovalRequest.objects.filter(
        status=ApprovalRequest.Status.PENDING
    ).select_related(
        "revision_request__order",
        "revision_request__customer",
        "revision_request__review_version",
        "submitted_by",
    )
    page_obj = Paginator(approvals, PAGE_SIZE).get_page(request.GET.get("page"))
    intake_alternatives = OrderAlternative.objects.filter(
        status=OrderAlternative.Status.PENDING_OWNER,
    ).select_related(
        "order",
        "created_by",
        "cake_template",
        "availability_check",
    )
    return render(
        request,
        "orders/owner_approval_list.html",
        {
            "page_obj": page_obj,
            "intake_alternatives": intake_alternatives,
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_intake_alternative_detail(request, alternative_id):
    alternative = get_object_or_404(
        OrderAlternative.objects.select_related(
            "order__customer",
            "order__cake_template",
            "cake_template",
            "created_by",
            "availability_check__checked_by",
        ),
        pk=alternative_id,
        status=OrderAlternative.Status.PENDING_OWNER,
        order__status=Order.Status.CHO_CHU_DUYET_PHUONG_AN,
    )
    order = alternative.order
    alternative.changed_fields = _alternative_differences(alternative)
    return render(
        request,
        "orders/owner_intake_alternative_detail.html",
        {
            "order": order,
            "alternative": alternative,
            "form": AlternativeOwnerDecisionForm(
                initial={
                    "expected_order_version": order.version,
                    "expected_alternative_revision": alternative.revision,
                }
            ),
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_decide_intake_alternative(request, alternative_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    alternative = get_object_or_404(
        OrderAlternative.objects.select_related("order"),
        pk=alternative_id,
    )
    form = AlternativeOwnerDecisionForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(
            "orders:owner_intake_alternative_detail",
            alternative_id=alternative.pk,
        )
    try:
        order, _alternative = decide_order_alternative(
            alternative_id=alternative.pk,
            owner=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_alternative_revision=form.cleaned_data[
                "expected_alternative_revision"
            ],
            approve=form.cleaned_data["decision"] == "approve",
            reason=form.cleaned_data["reason"],
        )
    except OrderActionError as exc:
        messages.error(request, str(exc))
        return redirect(
            "orders:owner_intake_alternative_detail",
            alternative_id=alternative.pk,
        )
    if order.status == Order.Status.CHO_CHU_DUYET_PHUONG_AN:
        messages.success(
            request,
            "Đã duyệt phương án. Nhân viên vẫn phải gửi đúng phiên bản cho khách.",
        )
    else:
        messages.info(
            request,
            "Đã từ chối phương án; đơn trở lại hàng đợi để nhân viên xử lý tiếp.",
        )
    return redirect("orders:staff_order_detail", pk=order.pk)


@login_required
@role_required(User.Role.OWNER)
def owner_intake_quote_approval_detail(request, approval_id):
    messages.info(
        request,
        "Yêu cầu duyệt báo giá theo quy trình cũ không còn hiệu lực; OWNER xử lý đơn trực tiếp tại hàng đợi tiếp nhận.",
    )
    return redirect("orders:owner_intake_order_list")
    approval = get_object_or_404(
        IntakeQuoteApproval.objects.select_related(
            "order__cake_template",
            "order__customer",
            "availability_check",
            "submitted_by",
        ),
        pk=approval_id,
        status=IntakeQuoteApproval.Status.PENDING,
        order__status=Order.Status.CHO_CHU_DUYET_BAO_GIA,
    )
    authority_reason_labels = dict(StaffAvailabilityCheck.AuthorityReason.choices)
    return render(
        request,
        "orders/owner_intake_quote_approval_detail.html",
        {
            "approval": approval,
            "order": approval.order,
            "authority_reason_labels": [
                authority_reason_labels.get(reason, reason)
                for reason in approval.authority_reasons
            ],
            "form": IntakeQuoteApprovalDecisionForm(
                initial={
                    "expected_order_version": approval.order.version,
                    "expected_approval_revision": approval.revision,
                }
            ),
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_decide_intake_quote_approval(request, approval_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    messages.error(
        request,
        "Không thể duyệt báo giá theo quy trình cũ. Hãy mở đơn trong hàng đợi để OWNER kiểm tra và xử lý trực tiếp.",
    )
    return redirect("orders:owner_intake_order_list")
    approval = get_object_or_404(
        IntakeQuoteApproval.objects.select_related("order"),
        pk=approval_id,
    )
    form = IntakeQuoteApprovalDecisionForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(
            "orders:owner_intake_quote_approval_detail",
            approval_id=approval.pk,
        )
    try:
        order, _approval = decide_intake_quote_approval(
            approval_id=approval.pk,
            owner=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_approval_revision=form.cleaned_data[
                "expected_approval_revision"
            ],
            approve=form.cleaned_data["decision"] == "approve",
            reason=form.cleaned_data["reason"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect(
            "orders:owner_intake_quote_approval_detail",
            approval_id=approval.pk,
        )
    if order.status == Order.Status.CHO_KIEM_TRA:
        messages.success(
            request,
            "Đã duyệt báo giá. STAFF phải gửi đúng giá và lịch đã được duyệt; đơn chưa được xác nhận.",
        )
        return redirect("orders:staff_order_detail", pk=order.pk)
    messages.info(
        request,
        "Đã từ chối tiếp nhận. Đơn đã kết thúc; STAFF cần thông báo khách.",
    )
    return redirect("orders:owner_approval_list")


@login_required
@role_required(User.Role.OWNER)
def owner_approval_detail(request, approval_id):
    approval = get_object_or_404(
        ApprovalRequest.objects.select_related(
            "revision_request__order__current_quote",
            "revision_request__order__current_review",
            "revision_request__order__active_change_agreement",
            "revision_request__review_version",
            "revision_request__customer",
            "submitted_by",
        ),
        pk=approval_id,
        status=ApprovalRequest.Status.PENDING,
        revision_request__status=RevisionRequest.Status.WAITING_OWNER,
    )
    order = approval.revision_request.order
    review = approval.revision_request.review_version
    review.private_image_url = reverse(
        "orders:private_review_image",
        kwargs={"review_id": review.pk},
    )
    context = {
        "approval": approval,
        "revision_request": approval.revision_request,
        "order": order,
        "review": review,
        "history": order.history.select_related("actor", "quote").all(),
        "form": OwnerApprovalForm(order=order, approval=approval),
        "now": timezone.now(),
    }
    return render(request, "orders/owner_approval_detail.html", context)


@login_required
@role_required(User.Role.OWNER)
def owner_decide_approval(request, approval_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    approval = get_object_or_404(
        ApprovalRequest.objects.select_related("revision_request__order"),
        pk=approval_id,
    )
    order = approval.revision_request.order
    form = OwnerApprovalForm(request.POST, order=order, approval=approval)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(
            "orders:owner_approval_detail",
            approval_id=approval.pk,
        )
    try:
        updated, agreement = decide_revision_approval(
            approval_id=approval.pk,
            owner=request.user,
            expected_order_version=form.cleaned_data["expected_version"],
            expected_approval_id=form.cleaned_data["expected_approval_id"],
            approve=form.cleaned_data["decision"] == "approve",
            reason=form.cleaned_data["reason"],
            requires_change_agreement=form.cleaned_data[
                "requires_change_agreement"
            ],
            unit_price=form.cleaned_data.get("unit_price"),
            delivery_fee=form.cleaned_data.get("delivery_fee"),
            proposed_delivery_at=form.cleaned_data.get("proposed_delivery_at"),
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect(
            "orders:owner_approval_detail",
            approval_id=approval.pk,
        )
    if agreement:
        messages.success(
            request,
            "Đã duyệt phương án. Phụ lục đã gửi khách và phải được khách đồng ý trước khi sửa.",
        )
    elif updated.status == Order.Status.DANG_LAM:
        messages.success(request, "Đã duyệt; nhân viên được phép tiếp tục chỉnh sửa.")
    else:
        messages.success(
            request,
            "Đã từ chối yêu cầu; khách và nhân viên có thể tiếp tục trao đổi.",
        )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_respond_change_agreement(request, pk, agreement_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    form = AgreementResponseForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:order_detail", pk=order.pk)
    if form.cleaned_data["expected_agreement_id"] != agreement_id:
        messages.error(request, "Phụ lục đã thay đổi. Hãy tải lại đơn.")
        return redirect("orders:order_detail", pk=order.pk)
    decision = request.POST.get("decision")
    if decision not in ("accept", "reject"):
        messages.error(request, "Lựa chọn phản hồi phụ lục không hợp lệ.")
        return redirect("orders:order_detail", pk=order.pk)
    try:
        updated, agreement = respond_change_agreement(
            agreement_id=agreement_id,
            customer=request.user,
            expected_order_version=form.cleaned_data["expected_version"],
            expected_agreement_id=form.cleaned_data["expected_agreement_id"],
            expected_agreement_revision=form.cleaned_data[
                "expected_agreement_revision"
            ],
            accept=decision == "accept",
            message=form.cleaned_data["message"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(
        request,
        (
            f"Bạn đã đồng ý phụ lục phiên bản {agreement.version_number}; nhân viên có thể tiếp tục sửa."
            if agreement.status == ChangeAgreement.Status.ACCEPTED
            else "Bạn đã từ chối phụ lục. Thỏa thuận giá và lịch cũ được giữ nguyên."
        ),
    )
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_keep_current_review(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    try:
        updated = keep_current_review(
            order_id=order.pk,
            customer=request.user,
            expected_order_version=int(request.POST.get("expected_version", "-1")),
            review_id=int(request.POST.get("review_id", "-1")),
        )
    except (OrderActionError, ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(
        request,
        "Bạn đã xác nhận giữ phương án bánh hiện tại. Đơn sẵn sàng giao.",
    )
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_continue_revision_discussion(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    form = ContinueDiscussionForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("orders:order_detail", pk=order.pk)
    try:
        updated, _revision = continue_revision_discussion(
            order_id=order.pk,
            customer=request.user,
            expected_order_version=form.cleaned_data["expected_version"],
            message=form.cleaned_data["message"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:order_detail", pk=order.pk)
    messages.success(request, "Đã gửi nội dung trao đổi tiếp cho cửa hàng.")
    return redirect("orders:order_detail", pk=updated.pk)


def _delivery_action_error(request, order, error, *, staff):
    message = (
        "; ".join(error.messages)
        if isinstance(error, ValidationError)
        else str(error)
    )
    messages.error(request, message)
    route = "orders:staff_order_detail" if staff else "orders:order_detail"
    return redirect(route, pk=order.pk)


@login_required
def private_order_evidence(request, evidence_type, evidence_id):
    evidence_models = {
        "delivery": DeliveryAttempt,
        "incident": OrderIncident,
        "refund": Refund,
    }
    evidence_model = evidence_models.get(evidence_type)
    if evidence_model is None:
        raise PermissionDenied
    evidence = get_object_or_404(evidence_model, pk=evidence_id)
    order = evidence.order
    photo = evidence.proof
    if not photo:
        raise PermissionDenied
    if request.user.role == User.Role.CUSTOMER:
        allowed = order.customer_id == request.user.pk
    elif request.user.role == User.Role.STAFF:
        allowed = True
    elif request.user.role == User.Role.OWNER:
        allowed = (
            evidence_type == "incident"
            or (
                evidence_type == "delivery"
                and evidence.incidents.exists()
            )
            or (
                evidence_type == "refund"
                and evidence.incident.owner_decision
                == OrderIncident.OwnerDecision.APPROVED
            )
        )
    else:
        allowed = False
    if not allowed:
        raise PermissionDenied
    suffix = photo.name.lower()
    content_type = (
        "image/png"
        if suffix.endswith(".png")
        else "image/webp"
        if suffix.endswith(".webp")
        else "image/jpeg"
    )
    response = FileResponse(photo.open("rb"), content_type=content_type)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@role_required(User.Role.STAFF)
def staff_start_delivery(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = DeliveryStartForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("Biểu mẫu bắt đầu giao không hợp lệ."),
            staff=True,
        )
    try:
        updated, attempt = start_delivery(
            order_id=order.pk,
            staff=request.user,
            expected_version=form.cleaned_data["expected_version"],
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(request, f"Đã bắt đầu giao lần {attempt.attempt_number}.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_confirm_delivery_view(request, pk, attempt_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    form = DeliveryConfirmationForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("Biểu mẫu xác nhận giao không hợp lệ."),
            staff=False,
        )
    try:
        updated, _attempt = customer_confirm_delivery(
            order_id=order.pk,
            customer=request.user,
            attempt_id=attempt_id,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_attempt_revision=form.cleaned_data["expected_attempt_revision"],
            note=form.cleaned_data["note"],
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=False)
    if updated.status == Order.Status.HOAN_TAT:
        messages.success(request, "Đã xác nhận nhận bánh; đơn hàng đã hoàn tất.")
    else:
        messages.success(
            request,
            "Đã xác nhận nhận bánh. Nhân viên sẽ cập nhật thanh toán khi thực nhận.",
        )
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_report_delivery_incident(request, pk, attempt_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    form = DeliveryIncidentForm(request.POST, request.FILES)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=False,
        )
    try:
        updated, incident = report_delivery_incident(
            order_id=order.pk,
            reporter=request.user,
            attempt_id=attempt_id,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_attempt_revision=form.cleaned_data["expected_attempt_revision"],
            incident_type=form.cleaned_data["incident_type"],
            description=form.cleaned_data["description"],
            photo=form.cleaned_data.get("proof"),
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=False)
    messages.success(request, f"Đã tiếp nhận phản ánh sự cố #{incident.pk}.")
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_report_delivery_incident(request, pk, attempt_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = DeliveryIncidentForm(request.POST, request.FILES)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=True,
        )
    try:
        updated, incident = report_delivery_incident(
            order_id=order.pk,
            reporter=request.user,
            attempt_id=attempt_id,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_attempt_revision=form.cleaned_data["expected_attempt_revision"],
            incident_type=form.cleaned_data["incident_type"],
            description=form.cleaned_data["description"],
            photo=form.cleaned_data.get("proof"),
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(request, f"Đã tiếp nhận sự cố #{incident.pk}; đơn chờ chủ duyệt.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_confirm_delivery_view(request, pk, attempt_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = DeliveryConfirmationForm(request.POST, request.FILES)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("Biểu mẫu bàn giao không hợp lệ."),
            staff=True,
        )
    try:
        updated, _attempt = staff_confirm_delivery(
            order_id=order.pk,
            staff=request.user,
            attempt_id=attempt_id,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_attempt_revision=form.cleaned_data["expected_attempt_revision"],
            note=form.cleaned_data["note"],
            proof=form.cleaned_data.get("proof"),
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(
        request,
        "Đã ghi nhận nhân viên xác nhận bàn giao; đây không phải xác nhận của khách.",
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_fail_delivery_view(request, pk, attempt_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = DeliveryFailureForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=True,
        )
    try:
        updated, _attempt = fail_delivery(
            order_id=order.pk,
            staff=request.user,
            attempt_id=attempt_id,
            expected_order_version=form.cleaned_data["expected_order_version"],
            reason=form.cleaned_data["reason"],
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(request, "Đã ghi nhận giao thất bại; đơn chưa bị tự động hủy.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_schedule_redelivery_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = RedeliveryForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=True,
        )
    try:
        updated = schedule_redelivery(
            order_id=order.pk,
            staff=request.user,
            expected_order_version=form.cleaned_data["expected_version"],
            scheduled_at=form.cleaned_data["scheduled_at"],
            customer_agreement=form.cleaned_data["customer_agreement"],
            reason=form.cleaned_data["reason"],
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(request, "Đã xếp lịch giao lại theo thỏa thuận; lần giao mới sẽ có bản ghi riêng.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_propose_incident_view(request, pk, incident_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = IncidentProposalForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=True,
        )
    get_object_or_404(OrderIncident, pk=incident_id, order=order)
    try:
        updated, _incident = propose_incident_resolution(
            incident_id=incident_id,
            staff=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_incident_revision=form.cleaned_data["expected_incident_revision"],
            resolution=form.cleaned_data["resolution"],
            plan=form.cleaned_data["plan"],
            customer_agreement=form.cleaned_data["customer_agreement"],
            scheduled_at=form.cleaned_data["scheduled_at"],
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(request, "Đã chuyển phương án sự cố cho chủ cửa hàng quyết định.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.OWNER)
def owner_incident_list(request):
    incidents = OrderIncident.objects.filter(
        status=OrderIncident.Status.PENDING_OWNER
    ).select_related(
        "order",
        "order__customer",
        "delivery_attempt",
        "reported_by",
    )
    page_obj = Paginator(incidents, PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "orders/owner_incident_list.html", {"page_obj": page_obj})


@login_required
@role_required(User.Role.OWNER)
def owner_incident_detail(request, incident_id):
    incident = get_object_or_404(
        OrderIncident.objects.select_related(
            "order__customer",
            "order__current_quote",
            "order__current_review",
            "delivery_attempt",
            "reported_by",
        ),
        pk=incident_id,
    )
    order = incident.order
    review = order.current_review
    context = {
        "incident": incident,
        "order": order,
        "delivery_attempts": order.delivery_attempts.select_related(
            "handled_by", "confirmed_by"
        ),
        "payments": order.payments.select_related("confirmed_by"),
        "refunds": order.refunds.select_related("payment", "recorded_by"),
        "financials": order_financials(order),
        "history": order.history.select_related("actor").all(),
        "decision_form": IncidentDecisionForm(
            initial={
                "expected_order_version": order.version,
                "expected_incident_revision": incident.revision,
            }
        ),
        "review": review,
    }
    if review and review.photo:
        context["review_image_url"] = reverse(
            "orders:private_review_image",
            kwargs={"review_id": review.pk},
        )
    if incident.proof:
        context["incident_evidence_url"] = reverse(
            "orders:private_order_evidence",
            kwargs={"evidence_type": "incident", "evidence_id": incident.pk},
        )
    return render(request, "orders/owner_incident_detail.html", context)


@login_required
@role_required(User.Role.OWNER)
def owner_decide_incident_view(request, incident_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    incident = get_object_or_404(
        OrderIncident.objects.select_related("order"),
        pk=incident_id,
    )
    order = incident.order
    form = IncidentDecisionForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=False,
        )
    try:
        _updated, _incident = decide_incident(
            incident_id=incident.pk,
            owner=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_incident_revision=form.cleaned_data["expected_incident_revision"],
            approve=form.cleaned_data["decision"] == "approve",
            reason=form.cleaned_data["reason"],
        )
    except (OrderActionError, ValidationError) as exc:
        messages.error(
            request,
            "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc),
        )
        return redirect("orders:owner_incident_detail", incident_id=incident.pk)
    messages.success(request, "Đã lưu quyết định của chủ cửa hàng.")
    return redirect("orders:owner_incident_detail", incident_id=incident.pk)


@login_required
@role_required(User.Role.CUSTOMER)
def customer_accept_incident_plan_view(request, pk, incident_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk, customer=request.user)
    get_object_or_404(OrderIncident, pk=incident_id, order=order)
    form = IncidentCustomerResponseForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("Biểu mẫu xác nhận phương án không hợp lệ."),
            staff=False,
        )
    try:
        updated, _incident = customer_accept_incident_plan(
            incident_id=incident_id,
            customer=request.user,
            expected_order_version=form.cleaned_data["expected_order_version"],
            expected_incident_revision=form.cleaned_data["expected_incident_revision"],
            message=form.cleaned_data["message"],
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=False)
    messages.success(request, "Đã ghi nhận bạn đồng ý giữ phương án hiện tại.")
    return redirect("orders:order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_execute_incident_view(request, pk, incident_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = IncidentExecutionForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=True,
        )
    incident = get_object_or_404(OrderIncident, pk=incident_id, order=order)
    arguments = {
        "incident_id": incident.pk,
        "staff": request.user,
        "expected_order_version": form.cleaned_data["expected_order_version"],
        "expected_incident_revision": form.cleaned_data["expected_incident_revision"],
        "result": form.cleaned_data["result"],
    }
    try:
        if incident.proposed_resolution == OrderIncident.Resolution.REDELIVER:
            updated, _incident = record_incident_resolution(**arguments)
        else:
            updated, _incident = execute_incident_resolution(**arguments)
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(request, "Đã ghi nhận thực hiện phương án sự cố.")
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_record_payment_view(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = PaymentForm(request.POST)
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=True,
        )
    try:
        updated, _payment, created = record_payment(
            order_id=order.pk,
            staff=request.user,
            **form.cleaned_data,
        )
    except (OrderActionError, ValidationError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(
        request,
        "Đã ghi nhận khoản tiền nhân viên đối chiếu thực nhận."
        if created
        else "Khoản thu đã được ghi nhận trước đó; không tạo bản ghi trùng.",
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


@login_required
@role_required(User.Role.STAFF)
def staff_record_refund_view(request, pk, incident_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    order = get_object_or_404(Order, pk=pk)
    form = RefundForm(
        request.POST,
        request.FILES,
        payments=order.payments.all(),
    )
    if not form.is_valid():
        return _delivery_action_error(
            request,
            order,
            ValidationError("; ".join(
                error for errors in form.errors.values() for error in errors
            )),
            staff=True,
        )
    data = form.cleaned_data
    try:
        updated, _refund, created = record_refund(
            order_id=order.pk,
            incident_id=incident_id,
            payment_id=int(data["payment_id"]),
            staff=request.user,
            expected_order_version=data["expected_order_version"],
            method=data["method"],
            amount=data["amount"],
            refunded_at=data["refunded_at"],
            transaction_reference=data["transaction_reference"],
            notes=data["notes"],
            idempotency_key=data["idempotency_key"],
            proof=data.get("proof"),
        )
    except (OrderActionError, ValidationError, ValueError) as exc:
        return _delivery_action_error(request, order, exc, staff=True)
    messages.success(
        request,
        "Đã ghi nhận khoản hoàn đã thực hiện bên ngoài hệ thống."
        if created
        else "Khoản hoàn đã được ghi nhận trước đó; không tạo bản ghi trùng.",
    )
    return redirect("orders:staff_order_detail", pk=updated.pk)


def _review_filter_bounds(date_from, date_to):
    start = timezone.make_aware(datetime.combine(date_from, time.min))
    end = timezone.make_aware(
        datetime.combine(date_to + timedelta(days=1), time.min)
    )
    return start, end


@login_required
@role_required(User.Role.OWNER)
def owner_review_list(request):
    form = OwnerReviewFilterForm(request.GET or None)
    reviews = OrderReview.objects.select_related(
        "order",
        "customer",
        "reply__owner",
    )
    if form.is_valid():
        filters = form.cleaned_data
        if filters["query"]:
            reviews = reviews.filter(order__code__icontains=filters["query"])
        if filters["rating"]:
            reviews = reviews.filter(rating=int(filters["rating"]))
        if filters["replied"] == "yes":
            reviews = reviews.filter(reply__isnull=False)
        elif filters["replied"] == "no":
            reviews = reviews.filter(reply__isnull=True)
        if filters["date_from"] and filters["date_to"]:
            start, end = _review_filter_bounds(
                filters["date_from"],
                filters["date_to"],
            )
            reviews = reviews.filter(created_at__gte=start, created_at__lt=end)
        elif filters["date_from"]:
            start = timezone.make_aware(
                datetime.combine(filters["date_from"], time.min)
            )
            reviews = reviews.filter(created_at__gte=start)
        elif filters["date_to"]:
            end = timezone.make_aware(
                datetime.combine(filters["date_to"] + timedelta(days=1), time.min)
            )
            reviews = reviews.filter(created_at__lt=end)
    page_obj = Paginator(reviews, PAGE_SIZE).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "orders/owner_review_list.html",
        {
            "filter_form": form,
            "page_obj": page_obj,
            "pagination_query": query_params.urlencode(),
        },
    )


def _owner_review_detail_context(review, *, reply_form=None):
    context = {
        "review": review,
        "order": review.order,
        "order_history": review.order.history.select_related("actor").all(),
    }
    if reply_form is not None:
        context["reply_form"] = reply_form
    elif not hasattr(review, "reply"):
        context["reply_form"] = ReviewReplyForm()
    return context


@login_required
@role_required(User.Role.OWNER)
def owner_review_detail(request, review_id):
    review = get_object_or_404(
        OrderReview.objects.select_related(
            "order",
            "order__customer",
            "customer",
            "reply__owner",
        ),
        pk=review_id,
    )
    return render(
        request,
        "orders/owner_review_detail.html",
        _owner_review_detail_context(review),
    )


@login_required
@role_required(User.Role.OWNER)
def owner_reply_to_review(request, review_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    review = get_object_or_404(
        OrderReview.objects.select_related(
            "order",
            "order__customer",
            "customer",
            "reply__owner",
        ),
        pk=review_id,
    )
    if hasattr(review, "reply"):
        messages.info(request, "Đánh giá này đã được chủ cửa hàng phản hồi.")
        return redirect("orders:owner_review_detail", review_id=review.pk)
    form = ReviewReplyForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "orders/owner_review_detail.html",
            _owner_review_detail_context(review, reply_form=form),
        )
    try:
        _reply, created = reply_to_order_review(
            review_id=review.pk,
            owner=request.user,
            content=form.cleaned_data["content"],
        )
    except (OrderActionError, ValidationError) as exc:
        error = (
            "; ".join(exc.messages)
            if isinstance(exc, ValidationError)
            else str(exc)
        )
        messages.error(request, error)
    else:
        messages.success(
            request,
            "Đã gửi phản hồi."
            if created
            else "Phản hồi đã được ghi trước đó và không thể sửa.",
        )
    return redirect("orders:owner_review_detail", review_id=review.pk)


@login_required
@role_required(User.Role.OWNER)
def owner_workflow_event_list(request):
    events = WorkflowEvent.objects.select_related("order").prefetch_related(
        "retry_requests__requested_by"
    )
    status = request.GET.get("status", "")
    event_type = request.GET.get("event_type", "")
    query = request.GET.get("q", "").strip()
    if status in WorkflowEvent.Status.values:
        events = events.filter(status=status)
    if event_type in WorkflowEvent.EventType.values:
        events = events.filter(event_type=event_type)
    if query:
        events = events.filter(order__code__icontains=query)
    page_obj = Paginator(events, PAGE_SIZE).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    event_counts = {
        row["status"]: row["count"]
        for row in WorkflowEvent.objects.values("status").annotate(count=Count("pk"))
    }
    for event in page_obj.object_list:
        event.can_retry = (
            event.status == WorkflowEvent.Status.FAILED
            and bool(event.payload.get("recipient_email"))
        )
    return render(
        request,
        "orders/owner_workflow_events.html",
        {
            "page_obj": page_obj,
            "selected_status": status,
            "selected_event_type": event_type,
            "search_query": query,
            "event_statuses": WorkflowEvent.Status.choices,
            "event_types": WorkflowEvent.EventType.choices,
            "event_counts": event_counts,
            "pagination_query": query_params.urlencode(),
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_retry_workflow_event(request, event_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    event = get_object_or_404(WorkflowEvent, pk=event_id)
    if event.status != WorkflowEvent.Status.FAILED:
        messages.error(request, "Chỉ có thể xếp thử lại sự kiện đang thất bại.")
    elif not event.payload.get("recipient_email"):
        messages.error(
            request,
            "Không thể thử lại: sự kiện không có email người nhận hợp lệ.",
        )
    elif request_event_retry(event_id=event.pk, owner=request.user):
        messages.success(
            request,
            "Đã xếp sự kiện để thử lại; không gửi webhook ngay trong yêu cầu này.",
        )
    else:
        messages.info(request, "Sự kiện vừa được cập nhật; hãy tải lại danh sách.")
    return redirect("orders:owner_workflow_event_list")
