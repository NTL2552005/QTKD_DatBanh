import json
import math
import secrets
import socket
import uuid
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User

from .models import (
    OrderReview,
    ReviewClassificationAudit,
    ReviewClassificationEvent,
)

MAX_ATTEMPTS = 5
MAX_BATCH_SIZE = 100
LEASE_SECONDS = 60
RETRY_BASE_SECONDS = 30
ALLOWED_LABELS = set(OrderReview.SentimentLabel.values)
AUTO_LABEL_THRESHOLD = 0.95


class CallbackPayloadError(ValueError):
    pass


class CallbackConflict(ValueError):
    pass


class StaleReviewClassification(ValueError):
    pass


def _validate_webhook_url(url):
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("N8N_REVIEW_WEBHOOK_URL must be an absolute URL without credentials.")
    if parsed.scheme == "https":
        return
    if (
        parsed.scheme == "http"
        and settings.DEBUG
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        return
    raise ValueError(
        "n8n webhook must use HTTPS except for a loopback host when DEBUG is enabled."
    )


def _finish_attempt(event, claim_token, *, status, error=""):
    now = timezone.now()
    updates = {
        "status": status,
        "last_error": error[:500],
        "last_attempt_at": now,
        "updated_at": now,
        "lease_until": None,
        "lease_token": None,
    }
    if status == ReviewClassificationEvent.Status.PENDING:
        updates["next_attempt_at"] = now + timedelta(
            seconds=min(RETRY_BASE_SECONDS * (2 ** max(event.attempts - 1, 0)), 3600)
        )
    ReviewClassificationEvent.objects.filter(
        pk=event.pk,
        status=ReviewClassificationEvent.Status.PROCESSING,
        lease_token=claim_token,
    ).update(**updates)


def _send_event(event, claim_token):
    request = Request(
        settings.N8N_REVIEW_WEBHOOK_URL.strip(),
        data=json.dumps(event.payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Workflow-Key": settings.N8N_REVIEW_WEBHOOK_KEY,
            "X-Event-ID": str(event.event_id),
            "Idempotency-Key": str(event.event_id),
        },
        method="POST",
    )
    try:
        with build_opener(_NoRedirectHandler).open(
            request,
            timeout=settings.N8N_REVIEW_TIMEOUT_SECONDS,
        ) as response:
            response_status = response.status
    except HTTPError as exc:
        response_status = exc.code
        if exc.fp is not None:
            exc.close()
        if response_status == 429 or response_status >= 500:
            status = (
                ReviewClassificationEvent.Status.FAILED
                if event.attempts >= MAX_ATTEMPTS
                else ReviewClassificationEvent.Status.PENDING
            )
            _finish_attempt(
                event,
                claim_token,
                status=status,
                error=f"Webhook trả lỗi HTTP {response_status}.",
            )
        else:
            _finish_attempt(
                event,
                claim_token,
                status=ReviewClassificationEvent.Status.FAILED,
                error=f"Webhook từ chối yêu cầu (HTTP {response_status}).",
            )
        return
    except (URLError, TimeoutError, socket.timeout, OSError):
        status = (
            ReviewClassificationEvent.Status.FAILED
            if event.attempts >= MAX_ATTEMPTS
            else ReviewClassificationEvent.Status.PENDING
        )
        _finish_attempt(
            event,
            claim_token,
            status=status,
            error="Không kết nối được webhook phân loại.",
        )
        return

    if 200 <= response_status < 300:
        _finish_attempt(
            event,
            claim_token,
            status=ReviewClassificationEvent.Status.SENT,
        )
    else:
        _finish_attempt(
            event,
            claim_token,
            status=ReviewClassificationEvent.Status.FAILED,
            error=f"Webhook trả trạng thái HTTP {response_status}.",
        )


def dispatch_review_classification_events(*, limit=20, event_id=None, retry_failed=False):
    if not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError(f"limit phải nằm trong khoảng 1 đến {MAX_BATCH_SIZE}.")
    if not settings.N8N_REVIEW_WEBHOOK_URL:
        raise ValueError("Chưa cấu hình N8N_REVIEW_WEBHOOK_URL.")
    if not settings.N8N_REVIEW_WEBHOOK_KEY:
        raise ValueError("Chưa cấu hình N8N_REVIEW_WEBHOOK_KEY.")
    if not 0 < settings.N8N_REVIEW_TIMEOUT_SECONDS <= 30:
        raise ValueError("N8N_REVIEW_TIMEOUT_SECONDS phải lớn hơn 0 và không quá 30.")
    _validate_webhook_url(settings.N8N_REVIEW_WEBHOOK_URL.strip())

    now = timezone.now()
    ReviewClassificationEvent.objects.filter(
        status=ReviewClassificationEvent.Status.PROCESSING,
        lease_until__lte=now,
    ).update(
        status=ReviewClassificationEvent.Status.PENDING,
        lease_until=None,
        lease_token=None,
        next_attempt_at=now,
        updated_at=now,
    )
    if retry_failed:
        failed = ReviewClassificationEvent.objects.filter(
            status=ReviewClassificationEvent.Status.FAILED
        )
        if event_id:
            failed = failed.filter(event_id=event_id)
        failed.update(
            status=ReviewClassificationEvent.Status.PENDING,
            attempts=0,
            next_attempt_at=now,
            last_error="",
            updated_at=now,
        )

    events = ReviewClassificationEvent.objects.filter(
        status=ReviewClassificationEvent.Status.PENDING,
        next_attempt_at__lte=now,
    )
    if event_id:
        events = events.filter(event_id=event_id)
    event_ids = list(events.order_by("created_at", "pk").values_list("pk", flat=True)[:limit])

    processed = 0
    claimed_ids = []
    for pk in event_ids:
        claim_token = uuid.uuid4()
        claimed_at = timezone.now()
        claimed = ReviewClassificationEvent.objects.filter(
            pk=pk,
            status=ReviewClassificationEvent.Status.PENDING,
            next_attempt_at__lte=claimed_at,
        ).update(
            status=ReviewClassificationEvent.Status.PROCESSING,
            attempts=F("attempts") + 1,
            lease_until=claimed_at + timedelta(seconds=LEASE_SECONDS),
            lease_token=claim_token,
            last_attempt_at=claimed_at,
            updated_at=claimed_at,
        )
        if claimed != 1:
            continue
        claimed_ids.append(pk)
        event = ReviewClassificationEvent.objects.get(pk=pk)
        _send_event(event, claim_token)
        processed += 1
    final_statuses = list(
        ReviewClassificationEvent.objects.filter(
            pk__in=claimed_ids
        ).values_list("status", flat=True)
    )
    return {
        "processed": processed,
        "sent": sum(status == ReviewClassificationEvent.Status.SENT for status in final_statuses),
        "pending": sum(status == ReviewClassificationEvent.Status.PENDING for status in final_statuses),
        "failed": sum(status == ReviewClassificationEvent.Status.FAILED for status in final_statuses),
    }


def validate_callback_payload(payload):
    if not isinstance(payload, dict):
        raise CallbackPayloadError("Body phải là một JSON object.")
    required = {
        "event_id",
        "review_id",
        "ai_label",
        "ai_confidence",
        "ai_reason",
        "ai_valid",
    }
    if not required.issubset(payload):
        raise CallbackPayloadError("Thiếu trường bắt buộc trong callback.")

    try:
        event_id = uuid.UUID(payload["event_id"])
    except (TypeError, ValueError, AttributeError):
        raise CallbackPayloadError("event_id không hợp lệ.") from None

    review_id = payload["review_id"]
    if not isinstance(review_id, str) or not review_id or len(review_id) > 64:
        raise CallbackPayloadError("review_id không hợp lệ.")

    ai_valid = payload["ai_valid"]
    if not isinstance(ai_valid, bool):
        raise CallbackPayloadError("ai_valid phải là boolean.")

    ai_label = payload["ai_label"]
    if ai_label is not None and (
        not isinstance(ai_label, str) or ai_label not in ALLOWED_LABELS
    ):
        raise CallbackPayloadError("ai_label không nằm trong tập nhãn cho phép.")

    confidence = payload["ai_confidence"]
    if confidence is not None:
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
        ):
            raise CallbackPayloadError("ai_confidence phải là số hữu hạn từ 0 đến 1.")
        try:
            confidence = float(confidence)
        except OverflowError:
            raise CallbackPayloadError(
                "ai_confidence phải là số hữu hạn từ 0 đến 1."
            ) from None
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise CallbackPayloadError("ai_confidence phải là số hữu hạn từ 0 đến 1.")

    if ai_valid and (ai_label is None or confidence is None):
        raise CallbackPayloadError("Kết quả hợp lệ phải có nhãn và độ tin cậy.")
    if not isinstance(payload["ai_reason"], str) or len(payload["ai_reason"]) > 2000:
        raise CallbackPayloadError("ai_reason phải là chuỗi không quá 2.000 ký tự.")

    return {
        "event_id": event_id,
        "review_id": review_id,
        "ai_label": ai_label,
        "ai_confidence": confidence,
        "ai_reason": payload["ai_reason"],
        "ai_valid": ai_valid,
    }


@transaction.atomic
def accept_review_classification(payload):
    result = validate_callback_payload(payload)
    event = (
        ReviewClassificationEvent.objects.select_related("review")
        .filter(event_id=result["event_id"])
        .first()
    )
    if event is None:
        raise LookupError("Không tìm thấy event_id.")
    review = event.review
    if result["review_id"] != str(review.pk):
        raise CallbackPayloadError("review_id không khớp với event_id.")

    callback_result = {
        "ai_label": result["ai_label"],
        "ai_confidence": result["ai_confidence"],
        "ai_reason": result["ai_reason"],
        "ai_valid": result["ai_valid"],
    }
    if event.callback_result is not None:
        if event.callback_result == callback_result:
            return "duplicate"
        raise CallbackConflict("Callback mâu thuẫn với kết quả đã nhận.")
    if review.classification_status == OrderReview.ClassificationStatus.REVIEWED:
        return "ignored"

    auto_labeled = (
        result["ai_valid"]
        and result["ai_confidence"] >= AUTO_LABEL_THRESHOLD
    )
    status = (
        OrderReview.ClassificationStatus.AUTO_LABELED
        if auto_labeled
        else OrderReview.ClassificationStatus.PENDING_REVIEW
    )
    updated = OrderReview.objects.filter(
        pk=review.pk,
        classification_version=review.classification_version,
        classification_status__in=(
            OrderReview.ClassificationStatus.PENDING,
            OrderReview.ClassificationStatus.NOT_REQUESTED,
        ),
    ).update(
        ai_label=result["ai_label"],
        ai_confidence=result["ai_confidence"],
        ai_reason=result["ai_reason"],
        classification_status=status,
        final_label=result["ai_label"] if auto_labeled else None,
        classification_version=F("classification_version") + 1,
    )
    if updated != 1:
        review.refresh_from_db()
        event.refresh_from_db()
        if event.callback_result == callback_result:
            return "duplicate"
        if review.classification_status == OrderReview.ClassificationStatus.REVIEWED:
            return "ignored"
        raise CallbackConflict("Đánh giá đã được cập nhật đồng thời.")

    ReviewClassificationEvent.objects.filter(pk=event.pk).update(
        callback_result=callback_result,
        callback_received_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return "accepted"


@transaction.atomic
def confirm_review_classification(*, review_id, reviewer, final_label, review_note, version):
    if reviewer.role not in (User.Role.STAFF, User.Role.OWNER):
        raise PermissionError("Chỉ STAFF hoặc OWNER được xác nhận phân loại.")
    if final_label not in ALLOWED_LABELS:
        raise ValueError("Nhãn cuối cùng không hợp lệ.")
    if not isinstance(review_note, str) or len(review_note) > 2000:
        raise ValueError("Lý do xác nhận không hợp lệ.")

    review = OrderReview.objects.select_related("order").filter(pk=review_id).first()
    if review is None:
        raise LookupError("Không tìm thấy đánh giá.")
    previous_label = review.final_label
    updated = OrderReview.objects.filter(
        pk=review.pk,
        classification_status=OrderReview.ClassificationStatus.PENDING_REVIEW,
        classification_version=version,
    ).update(
        final_label=final_label,
        reviewed_by=reviewer,
        reviewed_at=timezone.now(),
        review_note=review_note,
        classification_status=OrderReview.ClassificationStatus.REVIEWED,
        classification_version=F("classification_version") + 1,
    )
    if updated != 1:
        raise StaleReviewClassification(
            "Đánh giá đã được người khác xử lý hoặc có kết quả mới. Hãy tải lại."
        )
    ReviewClassificationAudit.objects.create(
        review=review,
        reviewed_by=reviewer,
        previous_label=previous_label,
        final_label=final_label,
        review_note=review_note,
    )
    return review


def callback_key_matches(candidate):
    configured = settings.N8N_REVIEW_CALLBACK_KEY
    return bool(
        configured
        and candidate
        and secrets.compare_digest(configured.encode("utf-8"), candidate.encode("utf-8"))
    )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
