import json
import socket
import uuid
from datetime import timedelta, timezone as datetime_timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.core.validators import validate_email
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import WorkflowEvent, WorkflowRetryRequest

MAX_ATTEMPTS = 5
MAX_BATCH_SIZE = 100
MAX_RETRY_AFTER_SECONDS = 3600
LEASE_SECONDS = 60


def create_workflow_event(*, order, event_type, business_key, **event_data):
    event_id = uuid.uuid4()
    occurred_at = timezone.now()
    site_base = settings.SITE_BASE_URL.rstrip("/")
    payload = {
        "event_id": str(event_id),
        "event_type": event_type,
        "schema_version": 1,
        "occurred_at": occurred_at.isoformat(),
        "order_code": order.code,
        "action_url": f"{site_base}{reverse('orders:order_detail', kwargs={'pk': order.pk})}",
        **event_data,
    }
    recipient_email = (order.customer.email or "").strip()
    try:
        validate_email(recipient_email)
    except ValidationError:
        recipient_email = ""
    if recipient_email:
        payload["recipient_email"] = recipient_email
        display_name = order.customer.get_full_name().strip()
        if display_name:
            payload["recipient_display_name"] = display_name
    status = (
        WorkflowEvent.Status.PENDING
        if recipient_email
        else WorkflowEvent.Status.FAILED
    )
    defaults = {
        "event_id": event_id,
        "event_type": event_type,
        "order": order,
        "payload_version": 1,
        "payload": payload,
        "status": status,
        "last_error": (
            ""
            if recipient_email
            else "Khách hàng chưa cấu hình email hợp lệ."
        ),
    }
    try:
        with transaction.atomic():
            event, _created = WorkflowEvent.objects.get_or_create(
                business_key=business_key,
                defaults=defaults,
            )
    except IntegrityError:
        event = WorkflowEvent.objects.get(business_key=business_key)
    return event


def request_event_retry(*, event_id, owner):
    if owner.role != User.Role.OWNER:
        raise PermissionDenied("Chỉ OWNER được yêu cầu thử lại sự kiện workflow.")
    with transaction.atomic():
        event = WorkflowEvent.objects.filter(pk=event_id).first()
        if event is None:
            return False
        if (
            event.status != WorkflowEvent.Status.FAILED
            or not event.payload.get("recipient_email")
        ):
            return False
        attempts_before_retry = event.attempts
        updated = WorkflowEvent.objects.filter(
            pk=event.pk,
            status=WorkflowEvent.Status.FAILED,
        ).update(
            status=WorkflowEvent.Status.PENDING,
            attempts_since_retry=0,
            next_attempt_at=timezone.now(),
            lease_until=None,
            lease_token=None,
            last_error="",
        )
        if updated != 1:
            return False
        WorkflowRetryRequest.objects.create(
            event=event,
            requested_by=owner,
            attempts_before_retry=attempts_before_retry,
        )
        return True


def _validate_webhook_url(url):
    parsed = urlsplit(url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("WORKFLOW_WEBHOOK_URL must be an absolute URL without credentials.")
    if parsed.scheme == "https":
        return
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme == "http"
        and settings.DEBUG
        and parsed.hostname in local_hosts
    ):
        return
    raise ValueError(
        "Webhook must use HTTPS; HTTP is allowed only for a loopback host when DEBUG is enabled."
    )


def _retry_delay(attempts, retry_after=None):
    if retry_after:
        try:
            seconds = int(retry_after)
        except ValueError:
            try:
                retry_time = parsedate_to_datetime(retry_after)
                if retry_time.tzinfo is None:
                    retry_time = retry_time.replace(tzinfo=datetime_timezone.utc)
                seconds = int((retry_time - timezone.now()).total_seconds())
            except (TypeError, ValueError, OverflowError):
                seconds = 0
        if seconds > 0:
            return min(seconds, MAX_RETRY_AFTER_SECONDS)
    return min(30 * (2 ** max(attempts - 1, 0)), MAX_RETRY_AFTER_SECONDS)


def _finish_attempt(event, claim_token, *, status, response_status=None, error="", delay=None):
    now = timezone.now()
    updates = {
        "status": status,
        "last_attempt_at": now,
        "last_response_status": response_status,
        "last_error": error[:500],
        "lease_until": None,
        "lease_token": None,
    }
    if status == WorkflowEvent.Status.DELIVERED:
        updates["delivered_at"] = now
    elif status == WorkflowEvent.Status.PENDING:
        updates["next_attempt_at"] = now + timedelta(seconds=delay)
    WorkflowEvent.objects.filter(
        pk=event.pk,
        status=WorkflowEvent.Status.PROCESSING,
        lease_token=claim_token,
    ).update(**updates)


def _send_event(event, claim_token):
    if not event.payload.get("recipient_email"):
        _finish_attempt(
            event,
            claim_token,
            status=WorkflowEvent.Status.FAILED,
            error="Khách hàng chưa cấu hình địa chỉ email.",
        )
        return
    url = settings.WORKFLOW_WEBHOOK_URL.strip()
    _validate_webhook_url(url)
    if not settings.WORKFLOW_WEBHOOK_TOKEN:
        _finish_attempt(
            event,
            claim_token,
            status=WorkflowEvent.Status.FAILED,
            error="Chưa cấu hình token xác thực webhook.",
        )
        return
    timeout = settings.WORKFLOW_TIMEOUT_SECONDS
    if timeout <= 0 or timeout > 30:
        _finish_attempt(
            event,
            claim_token,
            status=WorkflowEvent.Status.FAILED,
            error="WORKFLOW_TIMEOUT_SECONDS phải lớn hơn 0 và không quá 30.",
        )
        return
    request = Request(
        url,
        data=json.dumps(event.payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.WORKFLOW_WEBHOOK_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
            "X-Event-ID": str(event.event_id),
            "Idempotency-Key": str(event.event_id),
        },
        method="POST",
    )
    try:
        opener = build_opener(_NoRedirectHandler)
        with opener.open(request, timeout=timeout) as response:
            response_status = response.status
    except HTTPError as exc:
        response_status = exc.code
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if exc.fp is not None:
            exc.close()
        if response_status == 429 or response_status >= 500:
            if event.attempts_since_retry >= MAX_ATTEMPTS:
                _finish_attempt(
                    event,
                    claim_token,
                    status=WorkflowEvent.Status.FAILED,
                    response_status=response_status,
                    error=f"Webhook trả lỗi HTTP {response_status}; đã hết lượt thử tự động.",
                )
            else:
                _finish_attempt(
                    event,
                    claim_token,
                    status=WorkflowEvent.Status.PENDING,
                    response_status=response_status,
                    error=f"Webhook trả lỗi HTTP {response_status}.",
                    delay=_retry_delay(event.attempts_since_retry, retry_after),
                )
        else:
            error = (
                "Webhook từ chối xác thực (HTTP 401/403)."
                if response_status in (401, 403)
                else f"Webhook từ chối yêu cầu (HTTP {response_status})."
            )
            _finish_attempt(
                event,
                claim_token,
                status=WorkflowEvent.Status.FAILED,
                response_status=response_status,
                error=error,
            )
        return
    except (URLError, TimeoutError, socket.timeout, OSError):
        if event.attempts_since_retry >= MAX_ATTEMPTS:
            _finish_attempt(
                event,
                claim_token,
                status=WorkflowEvent.Status.FAILED,
                error="Không kết nối được webhook; đã hết lượt thử tự động.",
            )
        else:
            _finish_attempt(
                event,
                claim_token,
                status=WorkflowEvent.Status.PENDING,
                error="Không kết nối được webhook.",
                delay=_retry_delay(event.attempts_since_retry),
            )
        return

    if 200 <= response_status < 300:
        _finish_attempt(
            event,
            claim_token,
            status=WorkflowEvent.Status.DELIVERED,
            response_status=response_status,
        )
        return
    _finish_attempt(
        event,
        claim_token,
        status=WorkflowEvent.Status.FAILED,
        response_status=response_status,
        error=f"Webhook trả trạng thái HTTP {response_status}.",
    )


def dispatch_workflow_events(*, limit=20):
    if not 1 <= limit <= MAX_BATCH_SIZE:
        raise ValueError(f"limit phải nằm trong khoảng 1 đến {MAX_BATCH_SIZE}.")
    if not settings.WORKFLOW_ENABLED:
        return {"disabled": True, "processed": 0}
    if not settings.WORKFLOW_WEBHOOK_URL:
        raise ValueError("WORKFLOW_ENABLED bật nhưng thiếu WORKFLOW_WEBHOOK_URL.")
    _validate_webhook_url(settings.WORKFLOW_WEBHOOK_URL.strip())

    now = timezone.now()
    WorkflowEvent.objects.filter(
        status=WorkflowEvent.Status.PROCESSING,
        lease_until__lte=now,
    ).update(
        status=WorkflowEvent.Status.PENDING,
        lease_until=None,
        lease_token=None,
        next_attempt_at=now,
    )
    event_ids = list(
        WorkflowEvent.objects.filter(
            status=WorkflowEvent.Status.PENDING,
            next_attempt_at__lte=now,
        )
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    processed = 0
    for event_id in event_ids:
        claim_token = uuid.uuid4()
        claimed_at = timezone.now()
        claimed = WorkflowEvent.objects.filter(
            pk=event_id,
            status=WorkflowEvent.Status.PENDING,
            next_attempt_at__lte=claimed_at,
        ).update(
            status=WorkflowEvent.Status.PROCESSING,
            attempts=F("attempts") + 1,
            attempts_since_retry=F("attempts_since_retry") + 1,
            lease_until=claimed_at + timedelta(seconds=LEASE_SECONDS),
            lease_token=claim_token,
            last_attempt_at=claimed_at,
        )
        if claimed != 1:
            continue
        event = WorkflowEvent.objects.get(pk=event_id)
        _send_event(event, claim_token)
        processed += 1
    return {"disabled": False, "processed": processed}


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
