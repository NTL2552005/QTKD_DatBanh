import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User

from .models import (
    ApprovalRequest,
    CakeReviewVersion,
    ChangeAgreement,
    DeliveryAttempt,
    Order,
    OrderHistory,
    OrderIncident,
    Payment,
    Quote,
    Refund,
    RevisionRequest,
)
from .services import IdempotencyConflict, InvalidOrderTransition, StaleOrderVersion

ACTIVE_REVISION_STATUSES = (
    RevisionRequest.Status.OPEN,
    RevisionRequest.Status.IN_PROGRESS,
    RevisionRequest.Status.WAITING_OWNER,
    RevisionRequest.Status.WAITING_CUSTOMER,
    RevisionRequest.Status.OWNER_REJECTED,
    RevisionRequest.Status.CUSTOMER_DECLINED,
)

ACTIVE_INCIDENT_STATUSES = (
    OrderIncident.Status.OPEN,
    OrderIncident.Status.PENDING_OWNER,
    OrderIncident.Status.REJECTED,
    OrderIncident.Status.APPROVED,
    OrderIncident.Status.IN_PROGRESS,
    OrderIncident.Status.WAITING_CUSTOMER,
)

PREPAYMENT_REPORT_STATUSES = (
    Order.Status.DA_XAC_NHAN,
    Order.Status.DANG_LAM,
    Order.Status.CHO_DUYET_ANH,
    Order.Status.CHO_XU_LY_CHINH_SUA,
    Order.Status.CHO_CHU_DUYET,
    Order.Status.SAN_SANG_GIAO,
    Order.Status.CHO_KHACH_DUYET_THAY_DOI,
    Order.Status.CHO_TRAO_DOI_CHINH_SUA,
    Order.Status.DANG_GIAO,
    Order.Status.GIAO_THAT_BAI,
    Order.Status.CHO_XU_LY_SU_CO,
    Order.Status.DA_GIAO_CHO_THANH_TOAN,
)


def _require_role(user, role):
    if user.role != role:
        raise InvalidOrderTransition("Tài khoản không có quyền thực hiện thao tác này.")


def _load_order(order_id, *, customer=None):
    orders = Order.objects.select_related(
        "customer",
        "current_quote",
        "current_review",
        "active_change_agreement",
    )
    if customer is not None:
        orders = orders.filter(customer=customer)
    return orders.get(pk=order_id)


def _check_version(order, expected_version):
    if order.version != expected_version:
        raise StaleOrderVersion("Đơn hàng đã thay đổi. Hãy tải lại trang.")


def _change_order(order, *, expected_version, old_status, new_status, updates=None):
    changes = dict(updates or {})
    if new_status == Order.Status.HOAN_TAT and order.completed_at is None:
        changes["completed_at"] = timezone.now()
    changes.update(
        status=new_status,
        version=F("version") + 1,
        updated_at=timezone.now(),
    )
    updated = Order.objects.filter(
        pk=order.pk,
        version=expected_version,
        status=old_status,
    ).update(**changes)
    if updated != 1:
        raise StaleOrderVersion("Đơn hàng vừa thay đổi. Hãy tải lại trang.")
    order.refresh_from_db()
    return order


def _history(order, actor, action, old_status, message, *, details=None, quote=None):
    entry = OrderHistory.objects.create(
        order=order,
        actor=actor,
        action=action,
        from_status=old_status,
        to_status=order.status,
        message=message,
        details=details or {},
        quote=quote,
    )
    if action == OrderHistory.Action.ORDER_COMPLETED:
        from .workflow_events import create_workflow_event

        create_workflow_event(
            order=order,
            event_type="order.completed",
            business_key=f"order-completed:{order.pk}",
            completed_at=order.completed_at.isoformat() if order.completed_at else None,
            review_deadline=(
                (
                    order.completed_at
                    + timedelta(hours=settings.ORDER_REVIEW_WINDOW_HOURS)
                ).isoformat()
                if order.completed_at
                else None
            ),
        )
    return entry


def _commercial_terms(order):
    agreement = order.active_change_agreement
    if agreement:
        if (
            agreement.status != ChangeAgreement.Status.ACCEPTED
            or agreement.approval_request.status != ApprovalRequest.Status.APPROVED
            or agreement.approval_request.decided_by_id is None
            or agreement.responded_at is None
        ):
            raise InvalidOrderTransition(
                "Phụ lục hiện hành chưa được chủ và khách xác nhận đầy đủ."
            )
        snapshot = agreement.request_snapshot
        return {
            "total": agreement.total,
            "recipient_name": snapshot["recipient_name"],
            "recipient_phone": snapshot["recipient_phone"],
            "delivery_address": snapshot["delivery_address"],
            "scheduled_at": agreement.proposed_delivery_at,
        }
    quote = order.current_quote
    if quote is None or quote.status != Quote.Status.ACCEPTED or quote.responded_at is None:
        raise InvalidOrderTransition(
            "Đơn cần có báo giá được khách xác nhận làm căn cứ giao hàng."
        )
    return {
        "total": quote.total,
        "recipient_name": quote.recipient_name_snapshot,
        "recipient_phone": quote.recipient_phone_snapshot,
        "delivery_address": quote.delivery_address_snapshot,
        "scheduled_at": quote.proposed_delivery_at,
    }


def order_financials(order):
    terms = _commercial_terms(order)
    received = sum(
        order.payments.values_list("amount", flat=True),
        Decimal("0"),
    )
    refunded = sum(
        order.refunds.values_list("amount", flat=True),
        Decimal("0"),
    )
    return {
        "total": terms["total"],
        "received": received,
        "refunded": refunded,
        "net_received": received - refunded,
        "remaining": max(terms["total"] - received + refunded, Decimal("0")),
    }


def _payment_status(net_received, total):
    if net_received >= total:
        return Order.PaymentStatus.PAID
    if net_received > 0:
        return Order.PaymentStatus.PARTIAL
    return Order.PaymentStatus.UNPAID


@transaction.atomic
def report_prepayment(*, order_id, customer, expected_order_version):
    _require_role(customer, User.Role.CUSTOMER)
    order = _load_order(order_id, customer=customer)
    if order.status not in PREPAYMENT_REPORT_STATUSES:
        raise InvalidOrderTransition(
            "Chỉ có thể báo đã chuyển tiền sau khi khách xác nhận báo giá "
            "và trước khi đơn kết thúc."
        )
    finances = order_financials(order)
    if finances["remaining"] <= 0:
        raise InvalidOrderTransition("Đơn đã được thanh toán đủ.")
    if order.history.filter(
        action=OrderHistory.Action.CUSTOMER_PREPAYMENT_REPORTED
    ).exists():
        return order, False
    _check_version(order, expected_order_version)
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=old_status,
    )
    _history(
        order,
        customer,
        OrderHistory.Action.CUSTOMER_PREPAYMENT_REPORTED,
        old_status,
        "Khách hàng báo đã chuyển khoản trước; chờ nhân viên đối chiếu tiền thực nhận.",
        details={"remaining_at_report": str(finances["remaining"])},
        quote=order.current_quote,
    )
    return order, True


def _has_open_work(order):
    return (
        order.revision_requests.filter(status__in=ACTIVE_REVISION_STATUSES).exists()
        or ApprovalRequest.objects.filter(
            revision_request__order=order,
            status=ApprovalRequest.Status.PENDING,
        ).exists()
        or order.incidents.filter(status__in=ACTIVE_INCIDENT_STATUSES).exists()
    )


def _assert_ready_for_delivery(order, *, ignore_incident_id=None):
    _commercial_terms(order)
    if order.current_review is None or (
        order.current_review.status != CakeReviewVersion.Status.ACCEPTED
        or order.current_review.production_round != order.production_round
    ):
        raise InvalidOrderTransition(
            "Cần có phiên bản bánh của lần sản xuất hiện tại được khách chấp nhận."
        )
    if (
        order.revision_requests.filter(status__in=ACTIVE_REVISION_STATUSES).exists()
        or ApprovalRequest.objects.filter(
            revision_request__order=order,
            status=ApprovalRequest.Status.PENDING,
        ).exists()
        or order.incidents.exclude(pk=ignore_incident_id)
        .filter(status__in=ACTIVE_INCIDENT_STATUSES)
        .exists()
    ):
        raise InvalidOrderTransition(
            "Đơn còn yêu cầu chỉnh sửa, phê duyệt hoặc sự cố đang mở."
        )


@transaction.atomic
def start_delivery(*, order_id, staff, expected_version):
    _require_role(staff, User.Role.STAFF)
    order = _load_order(order_id)
    _check_version(order, expected_version)
    if order.status != Order.Status.SAN_SANG_GIAO:
        raise InvalidOrderTransition("Chỉ bắt đầu giao đơn đang sẵn sàng giao.")
    _assert_ready_for_delivery(order)
    terms = _commercial_terms(order)
    attempt_number = (
        order.delivery_attempts.order_by("-attempt_number")
        .values_list("attempt_number", flat=True)
        .first()
        or 0
    ) + 1
    now = timezone.now()
    try:
        attempt = DeliveryAttempt.objects.create(
            order=order,
            attempt_number=attempt_number,
            handled_by=staff,
            recipient_name_snapshot=terms["recipient_name"],
            recipient_phone_snapshot=terms["recipient_phone"],
            delivery_address_snapshot=terms["delivery_address"],
            scheduled_at=order.delivery_rescheduled_at or terms["scheduled_at"],
            started_at=now,
        )
    except IntegrityError as exc:
        raise StaleOrderVersion(
            "Một lần giao vừa được tạo. Hãy tải lại đơn hàng."
        ) from exc
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_version,
        old_status=old_status,
        new_status=Order.Status.DANG_GIAO,
        updates={
            "delivery_rescheduled_at": None,
            "delivery_reschedule_note": "",
        },
    )
    _history(
        order,
        staff,
        OrderHistory.Action.DELIVERY_STARTED,
        old_status,
        f"Bắt đầu lần giao thứ {attempt_number}.",
        details={"delivery_attempt_id": attempt.pk, "attempt_number": attempt_number},
        quote=order.current_quote,
    )
    return order, attempt


def _active_delivery(order, attempt_id):
    attempt = DeliveryAttempt.objects.filter(
        pk=attempt_id,
        order=order,
        result=DeliveryAttempt.Result.IN_PROGRESS,
    ).first()
    if attempt is None:
        raise InvalidOrderTransition("Lần giao không còn hoạt động hoặc không thuộc đơn.")
    return attempt


def _successfully_deliver(order, attempt, *, actor, expected_version, source, note):
    if order.status != Order.Status.DANG_GIAO:
        raise InvalidOrderTransition("Đơn không ở trạng thái đang giao.")
    attempt.result = DeliveryAttempt.Result.DELIVERED
    attempt.ended_at = timezone.now()
    attempt.confirmed_by = actor
    attempt.confirmation_source = source
    attempt.confirmation_note = note
    attempt.revision += 1
    attempt.save(
        update_fields=(
            "result",
            "ended_at",
            "confirmed_by",
            "confirmation_source",
            "confirmation_note",
            "revision",
        )
    )
    finances = order_financials(order)
    new_status = (
        Order.Status.HOAN_TAT
        if finances["net_received"] >= finances["total"] and not _has_open_work(order)
        else Order.Status.DA_GIAO_CHO_THANH_TOAN
    )
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_version,
        old_status=old_status,
        new_status=new_status,
        updates={
            "payment_status": _payment_status(
                finances["net_received"],
                finances["total"],
            )
        },
    )
    _history(
        order,
        actor,
        OrderHistory.Action.DELIVERY_SUCCEEDED,
        old_status,
        note,
        details={
            "delivery_attempt_id": attempt.pk,
            "confirmation_source": source,
            "financials": {
                key: str(finances[key])
                for key in ("total", "received", "refunded", "net_received", "remaining")
            },
        },
        quote=order.current_quote,
    )
    if new_status == Order.Status.HOAN_TAT:
        _history(
            order,
            actor,
            OrderHistory.Action.ORDER_COMPLETED,
            old_status,
            "Đơn đã giao thành công, thu đủ tiền và không còn xử lý tồn đọng.",
            quote=order.current_quote,
        )
    return order, attempt


@transaction.atomic
def customer_confirm_delivery(
    *,
    order_id,
    customer,
    attempt_id,
    expected_order_version,
    expected_attempt_revision,
    note="",
):
    _require_role(customer, User.Role.CUSTOMER)
    order = _load_order(order_id, customer=customer)
    _check_version(order, expected_order_version)
    attempt = _active_delivery(order, attempt_id)
    if attempt.revision != expected_attempt_revision:
        raise StaleOrderVersion("Lần giao đã thay đổi. Hãy tải lại trang.")
    return _successfully_deliver(
        order,
        attempt,
        actor=customer,
        expected_version=expected_order_version,
        source=DeliveryAttempt.ConfirmationSource.CUSTOMER,
        note=note.strip() or "Khách xác nhận đã nhận bánh đạt yêu cầu.",
    )


def staff_confirm_delivery(
    *,
    order_id,
    staff,
    attempt_id,
    expected_order_version,
    expected_attempt_revision,
    note,
    proof=None,
):
    stored_files = []
    try:
        with transaction.atomic():
            _require_role(staff, User.Role.STAFF)
            if not note.strip():
                raise ValidationError(
                    {"note": "Cần ghi chú khi nhân viên xác nhận bàn giao."}
                )
            order = _load_order(order_id)
            _check_version(order, expected_order_version)
            attempt = _active_delivery(order, attempt_id)
            if attempt.revision != expected_attempt_revision:
                raise StaleOrderVersion("Lần giao đã thay đổi. Hãy tải lại trang.")
            if proof:
                attempt.proof.save(proof.name, proof, save=False)
                stored_files.append((attempt.proof.storage, attempt.proof.name))
                attempt.save(update_fields=("proof",))
            return _successfully_deliver(
                order,
                attempt,
                actor=staff,
                expected_version=expected_order_version,
                source=DeliveryAttempt.ConfirmationSource.STAFF,
                note=note.strip(),
            )
    except Exception:
        for storage, name in stored_files:
            storage.delete(name)
        raise


@transaction.atomic
def fail_delivery(*, order_id, staff, attempt_id, expected_order_version, reason):
    _require_role(staff, User.Role.STAFF)
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "Vui lòng ghi lý do giao thất bại."})
    order = _load_order(order_id)
    _check_version(order, expected_order_version)
    attempt = _active_delivery(order, attempt_id)
    old_status = order.status
    attempt.result = DeliveryAttempt.Result.FAILED
    attempt.ended_at = timezone.now()
    attempt.confirmed_by = staff
    attempt.failure_reason = reason
    attempt.revision += 1
    attempt.save(
        update_fields=(
            "result",
            "ended_at",
            "confirmed_by",
            "failure_reason",
            "revision",
        )
    )
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=Order.Status.GIAO_THAT_BAI,
    )
    _history(
        order,
        staff,
        OrderHistory.Action.DELIVERY_FAILED,
        old_status,
        reason,
        details={"delivery_attempt_id": attempt.pk},
        quote=order.current_quote,
    )
    return order, attempt


@transaction.atomic
def schedule_redelivery(
    *,
    order_id,
    staff,
    expected_order_version,
    scheduled_at,
    customer_agreement,
    reason,
):
    _require_role(staff, User.Role.STAFF)
    customer_agreement = customer_agreement.strip()
    reason = reason.strip()
    if not customer_agreement:
        raise ValidationError(
            {"customer_agreement": "Ghi nhận thỏa thuận giao lại với khách."}
        )
    if not reason:
        raise ValidationError({"reason": "Vui lòng ghi lý do giao lại."})
    if scheduled_at <= timezone.now():
        raise ValidationError({"scheduled_at": "Lịch giao lại phải ở tương lai."})
    order = _load_order(order_id)
    _check_version(order, expected_order_version)
    if order.status != Order.Status.GIAO_THAT_BAI:
        raise InvalidOrderTransition("Chỉ xếp lịch giao lại sau lần giao thất bại.")
    if _has_open_work(order):
        raise InvalidOrderTransition("Đơn còn sự cố hoặc yêu cầu xử lý đang mở.")
    latest = order.delivery_attempts.order_by("-attempt_number").first()
    if latest is None or latest.result != DeliveryAttempt.Result.FAILED:
        raise InvalidOrderTransition("Không tìm thấy lần giao thất bại cần xếp lại.")
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=Order.Status.SAN_SANG_GIAO,
        updates={
            "delivery_rescheduled_at": scheduled_at,
            "delivery_reschedule_note": customer_agreement,
        },
    )
    _history(
        order,
        staff,
        OrderHistory.Action.DELIVERY_RESCHEDULED,
        old_status,
        reason,
        details={
            "previous_attempt_id": latest.pk,
            "scheduled_at": scheduled_at.isoformat(),
            "customer_agreement": customer_agreement,
            "unchanged_terms": True,
        },
        quote=order.current_quote,
    )
    return order


def _save_incident_proof(incident, photo, stored_files):
    if photo:
        incident.proof.save(photo.name, photo, save=False)
        stored_files.append((incident.proof.storage, incident.proof.name))


def report_delivery_incident(
    *,
    order_id,
    reporter,
    attempt_id,
    expected_order_version,
    expected_attempt_revision,
    incident_type,
    description,
    photo=None,
):
    if reporter.role not in (User.Role.CUSTOMER, User.Role.STAFF):
        raise InvalidOrderTransition("Tài khoản không có quyền báo sự cố giao hàng.")
    if incident_type not in OrderIncident.Type.values:
        raise ValidationError({"incident_type": "Loại sự cố không hợp lệ."})
    description = description.strip()
    if not description:
        raise ValidationError({"description": "Vui lòng mô tả sự cố."})
    stored_files = []
    try:
        with transaction.atomic():
            order = _load_order(
                order_id,
                customer=reporter if reporter.role == User.Role.CUSTOMER else None,
            )
            _check_version(order, expected_order_version)
            attempt = DeliveryAttempt.objects.filter(
                pk=attempt_id,
                order=order,
            ).first()
            if attempt is None:
                raise InvalidOrderTransition("Không tìm thấy lần giao của đơn.")
            latest_attempt = order.delivery_attempts.order_by(
                "-attempt_number"
            ).first()
            if latest_attempt is None or latest_attempt.pk != attempt.pk:
                raise StaleOrderVersion(
                    "Đây không phải lần giao hiện hành của đơn."
                )
            if attempt.revision != expected_attempt_revision:
                raise StaleOrderVersion("Lần giao đã thay đổi. Hãy tải lại trang.")
            if attempt.result == DeliveryAttempt.Result.IN_PROGRESS:
                if order.status != Order.Status.DANG_GIAO:
                    raise InvalidOrderTransition("Đơn không ở trạng thái đang giao.")
                attempt.result = DeliveryAttempt.Result.INCIDENT
                attempt.ended_at = timezone.now()
                attempt.confirmed_by = reporter
                attempt.confirmation_source = (
                    DeliveryAttempt.ConfirmationSource.CUSTOMER
                    if reporter.role == User.Role.CUSTOMER
                    else DeliveryAttempt.ConfirmationSource.STAFF
                )
                attempt.confirmation_note = "Lần giao kết thúc do phát sinh sự cố."
                attempt.revision += 1
                attempt.save(
                    update_fields=(
                        "result",
                        "ended_at",
                        "confirmed_by",
                        "confirmation_source",
                        "confirmation_note",
                        "revision",
                    )
                )
            elif (
                attempt.result == DeliveryAttempt.Result.DELIVERED
                and order.status
                in (
                    Order.Status.DA_GIAO_CHO_THANH_TOAN,
                    Order.Status.HOAN_TAT,
                )
            ):
                pass
            elif (
                reporter.role == User.Role.STAFF
                and attempt.result == DeliveryAttempt.Result.FAILED
                and order.status == Order.Status.GIAO_THAT_BAI
            ):
                pass
            else:
                raise InvalidOrderTransition(
                    "Chỉ báo sự cố cho lần giao đang hoạt động, thất bại gần nhất hoặc bánh vừa giao."
                )
            incident = OrderIncident(
                order=order,
                delivery_attempt=attempt,
                reported_by=reporter,
                incident_type=incident_type,
                description=description,
            )
            _save_incident_proof(incident, photo, stored_files)
            incident.save()
            old_status = order.status
            order = _change_order(
                order,
                expected_version=expected_order_version,
                old_status=old_status,
                new_status=Order.Status.CHO_XU_LY_SU_CO,
            )
            _history(
                order,
                reporter,
                OrderHistory.Action.INCIDENT_REPORTED,
                old_status,
                description,
                details={
                    "incident_id": incident.pk,
                    "incident_type": incident.incident_type,
                    "delivery_attempt_id": attempt.pk,
                    "reporter_role": reporter.role,
                },
                quote=order.current_quote,
            )
            return order, incident
    except Exception:
        for storage, name in stored_files:
            storage.delete(name)
        raise


@transaction.atomic
def propose_incident_resolution(
    *,
    incident_id,
    staff,
    expected_order_version,
    expected_incident_revision,
    resolution,
    plan,
    customer_agreement="",
    scheduled_at=None,
):
    _require_role(staff, User.Role.STAFF)
    incident = OrderIncident.objects.select_related("order").get(pk=incident_id)
    order = _load_order(incident.order_id)
    _check_version(order, expected_order_version)
    if incident.revision != expected_incident_revision:
        raise StaleOrderVersion("Sự cố vừa được cập nhật. Hãy tải lại trang.")
    if incident.status not in (OrderIncident.Status.OPEN, OrderIncident.Status.REJECTED):
        raise InvalidOrderTransition("Sự cố hiện không chờ đề xuất hoặc sửa phương án.")
    if resolution not in OrderIncident.Resolution.values:
        raise ValidationError({"resolution": "Phương án xử lý không hợp lệ."})
    plan = plan.strip()
    if not plan:
        raise ValidationError({"plan": "Vui lòng mô tả phương án cụ thể."})
    if resolution == OrderIncident.Resolution.REDELIVER:
        if not customer_agreement.strip():
            raise ValidationError(
                {"customer_agreement": "Cần ghi nhận khách đã thống nhất giao lại."}
            )
        if scheduled_at is None or scheduled_at <= timezone.now():
            raise ValidationError({"scheduled_at": "Chọn lịch giao lại trong tương lai."})
        _assert_ready_for_delivery(order, ignore_incident_id=incident.pk)
    if (
        resolution == OrderIncident.Resolution.KEEP_CURRENT
        and (
            incident.delivery_attempt_id is None
            or incident.delivery_attempt.result != DeliveryAttempt.Result.DELIVERED
        )
    ):
        raise InvalidOrderTransition(
            "Chỉ đề xuất giữ phương án hiện tại sau khi khách đã nhận bánh."
        )
    if resolution == OrderIncident.Resolution.CANCEL:
        finances = order_financials(order)
        incident.approved_refund_amount = finances["net_received"]
    incident.proposed_resolution = resolution
    incident.proposed_plan = plan
    incident.customer_agreement_note = customer_agreement.strip()
    incident.proposed_delivery_at = scheduled_at
    incident.status = OrderIncident.Status.PENDING_OWNER
    incident.owner_decision = OrderIncident.OwnerDecision.PENDING
    incident.decided_by = None
    incident.decision_reason = ""
    incident.decided_at = None
    incident.revision += 1
    incident.save()
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=old_status,
    )
    _history(
        order,
        staff,
        OrderHistory.Action.INCIDENT_PROPOSED,
        old_status,
        plan,
        details={
            "incident_id": incident.pk,
            "resolution": resolution,
            "customer_agreement": customer_agreement.strip(),
            "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
        },
        quote=order.current_quote,
    )
    return order, incident


@transaction.atomic
def decide_incident(
    *,
    incident_id,
    owner,
    expected_order_version,
    expected_incident_revision,
    approve,
    reason,
):
    _require_role(owner, User.Role.OWNER)
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "Vui lòng ghi lý do quyết định."})
    incident = OrderIncident.objects.select_related("order").get(pk=incident_id)
    order = _load_order(incident.order_id)
    _check_version(order, expected_order_version)
    if incident.revision != expected_incident_revision:
        raise StaleOrderVersion("Sự cố vừa được cập nhật. Hãy tải lại trang.")
    if incident.status != OrderIncident.Status.PENDING_OWNER:
        raise InvalidOrderTransition("Phương án sự cố không còn chờ chủ duyệt.")
    resolution = incident.proposed_resolution
    old_status = order.status
    incident.decided_by = owner
    incident.decision_reason = reason
    incident.decided_at = timezone.now()
    incident.revision += 1
    if not approve:
        incident.owner_decision = OrderIncident.OwnerDecision.REJECTED
        incident.status = OrderIncident.Status.REJECTED
        incident.save()
        order = _change_order(
            order,
            expected_version=expected_order_version,
            old_status=old_status,
            new_status=old_status,
        )
    else:
        incident.owner_decision = OrderIncident.OwnerDecision.APPROVED
        if resolution == OrderIncident.Resolution.REDELIVER:
            incident.status = OrderIncident.Status.IN_PROGRESS
            new_status = Order.Status.CHO_XU_LY_SU_CO
        elif resolution in (
            OrderIncident.Resolution.REPAIR,
            OrderIncident.Resolution.REPLACE,
        ):
            incident.status = OrderIncident.Status.IN_PROGRESS
            new_status = Order.Status.DANG_LAM
            order.production_round += 1
        elif resolution == OrderIncident.Resolution.KEEP_CURRENT:
            incident.status = OrderIncident.Status.WAITING_CUSTOMER
            new_status = Order.Status.CHO_XU_LY_SU_CO
        elif resolution == OrderIncident.Resolution.CANCEL:
            finances = order_financials(order)
            if incident.approved_refund_amount != finances["net_received"]:
                raise InvalidOrderTransition(
                    "Số tiền hoàn cần được tính lại theo tổng thu ròng hiện tại."
                )
            if finances["net_received"] == 0:
                incident.status = OrderIncident.Status.RESOLVED
                incident.execution_result = "Hủy đơn; không phát sinh khoản tiền cần hoàn."
                incident.resolved_at = timezone.now()
                new_status = Order.Status.DA_HUY
            else:
                incident.status = OrderIncident.Status.IN_PROGRESS
                new_status = Order.Status.CHO_XU_LY_SU_CO
        else:
            raise InvalidOrderTransition("Phương án sự cố không hợp lệ.")
        incident.save()
        updates = {}
        if resolution in (
            OrderIncident.Resolution.REPAIR,
            OrderIncident.Resolution.REPLACE,
        ):
            updates["current_review"] = None
            updates["production_round"] = order.production_round
        if resolution == OrderIncident.Resolution.CANCEL and new_status == Order.Status.DA_HUY:
            updates["payment_status"] = Order.PaymentStatus.UNPAID
        order = _change_order(
            order,
            expected_version=expected_order_version,
            old_status=old_status,
            new_status=new_status,
            updates=updates,
        )
    _history(
        order,
        owner,
        OrderHistory.Action.INCIDENT_DECIDED,
        old_status,
        reason,
        details={
            "incident_id": incident.pk,
            "decision": incident.owner_decision,
            "resolution": resolution,
            "approved_refund_amount": str(incident.approved_refund_amount),
        },
        quote=order.current_quote,
    )
    if approve and resolution == OrderIncident.Resolution.CANCEL and order.status == Order.Status.DA_HUY:
        _history(
            order,
            owner,
            OrderHistory.Action.ORDER_CANCELLED,
            old_status,
            reason,
            details={"incident_id": incident.pk, "refund_required": False},
            quote=order.current_quote,
        )
    return order, incident


@transaction.atomic
def customer_accept_incident_plan(
    *, incident_id, customer, expected_order_version, expected_incident_revision, message=""
):
    _require_role(customer, User.Role.CUSTOMER)
    incident = OrderIncident.objects.select_related("order").get(
        pk=incident_id,
        order__customer=customer,
    )
    order = _load_order(incident.order_id, customer=customer)
    _check_version(order, expected_order_version)
    if incident.revision != expected_incident_revision:
        raise StaleOrderVersion("Phương án vừa thay đổi. Hãy tải lại trang.")
    if (
        incident.status != OrderIncident.Status.WAITING_CUSTOMER
        or incident.proposed_resolution != OrderIncident.Resolution.KEEP_CURRENT
    ):
        raise InvalidOrderTransition("Sự cố hiện không chờ khách xác nhận phương án.")
    if (
        incident.delivery_attempt_id is None
        or incident.delivery_attempt.result != DeliveryAttempt.Result.DELIVERED
    ):
        raise InvalidOrderTransition(
            "Chỉ có thể giữ phương án hiện tại sau khi khách đã nhận bánh."
        )
    incident.status = OrderIncident.Status.RESOLVED
    incident.execution_result = message.strip() or "Khách xác nhận giữ phương án hiện tại."
    incident.resolved_at = timezone.now()
    incident.revision += 1
    incident.save()
    finances = order_financials(order)
    new_status = (
        Order.Status.DA_GIAO_CHO_THANH_TOAN
        if incident.delivery_attempt_id
        else Order.Status.SAN_SANG_GIAO
    )
    if (
        incident.delivery_attempt_id
        and finances["net_received"] >= finances["total"]
        and not _has_open_work(order)
    ):
        new_status = Order.Status.HOAN_TAT
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=new_status,
        updates={
            "payment_status": _payment_status(finances["net_received"], finances["total"])
        },
    )
    if new_status == Order.Status.HOAN_TAT:
        _history(
            order,
            customer,
            OrderHistory.Action.ORDER_COMPLETED,
            old_status,
            "Khách xác nhận giữ phương án hiện tại; đơn đã giao và thu đủ tiền.",
            quote=order.current_quote,
        )
    _history(
        order,
        customer,
        OrderHistory.Action.INCIDENT_CUSTOMER_ACCEPTED,
        old_status,
        incident.execution_result,
        details={"incident_id": incident.pk},
        quote=order.current_quote,
    )
    return order, incident


@transaction.atomic
def execute_incident_resolution(
    *,
    incident_id,
    staff,
    expected_order_version,
    expected_incident_revision,
    result,
):
    _require_role(staff, User.Role.STAFF)
    result = result.strip()
    if not result:
        raise ValidationError({"result": "Ghi kết quả thực hiện phương án."})
    incident = OrderIncident.objects.select_related("order").get(pk=incident_id)
    order = _load_order(incident.order_id)
    _check_version(order, expected_order_version)
    if incident.revision != expected_incident_revision:
        raise StaleOrderVersion("Sự cố vừa thay đổi. Hãy tải lại trang.")
    if (
        incident.status != OrderIncident.Status.IN_PROGRESS
        or incident.proposed_resolution
        not in (OrderIncident.Resolution.REPAIR, OrderIncident.Resolution.REPLACE)
    ):
        raise InvalidOrderTransition("Phương án này chưa sẵn sàng để ghi nhận thực hiện.")
    if (
        order.status != Order.Status.SAN_SANG_GIAO
        or order.current_review is None
        or order.current_review.status != CakeReviewVersion.Status.ACCEPTED
        or order.current_review.production_round != order.production_round
    ):
        raise InvalidOrderTransition(
            "Cần hoàn tất sửa bánh, QA và được khách duyệt ảnh mới trước khi kết thúc sự cố."
        )
    incident.status = OrderIncident.Status.RESOLVED
    incident.executed_by = staff
    incident.execution_result = result
    incident.resolved_at = timezone.now()
    incident.revision += 1
    incident.save()
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=old_status,
    )
    _history(
        order,
        staff,
        OrderHistory.Action.INCIDENT_EXECUTED,
        old_status,
        result,
        details={"incident_id": incident.pk, "resolution": incident.proposed_resolution},
        quote=order.current_quote,
    )
    return order, incident


def _payment_replay(*, model, key, order, payload):
    existing = model.objects.filter(idempotency_key=key).first()
    if existing is None:
        return None
    if existing.order_id != order.pk or any(
        getattr(existing, field) != value for field, value in payload.items()
    ):
        raise IdempotencyConflict(
            "Mã yêu cầu đã được sử dụng cho thông tin khác; không ghi trùng."
        )
    return existing


@transaction.atomic
def record_payment(
    *,
    order_id,
    staff,
    expected_order_version,
    method,
    amount,
    received_at,
    transaction_reference,
    notes,
    idempotency_key,
):
    _require_role(staff, User.Role.STAFF)
    if method not in Payment.Method.values:
        raise ValidationError({"method": "Phương thức thu tiền không hợp lệ."})
    transaction_reference = transaction_reference.strip()
    notes = notes.strip()
    order = _load_order(order_id)
    replay = _payment_replay(
        model=Payment,
        key=idempotency_key,
        order=order,
        payload={
            "method": method,
            "amount": amount,
            "received_at": received_at,
            "confirmed_by_id": staff.pk,
            "transaction_reference": transaction_reference.strip(),
            "notes": notes,
        },
    )
    if replay:
        return order, replay, False
    _check_version(order, expected_order_version)
    if order.status in (Order.Status.DA_HUY, Order.Status.TU_CHOI):
        raise InvalidOrderTransition("Không thể ghi thu cho đơn đã hủy hoặc từ chối.")
    if order.incidents.filter(
        status=OrderIncident.Status.IN_PROGRESS,
        proposed_resolution=OrderIncident.Resolution.CANCEL,
    ).exists():
        raise InvalidOrderTransition(
            "Đang thực hiện hoàn tiền cho phương án hủy; không thể ghi thêm khoản thu."
        )
    terms = _commercial_terms(order)
    if amount <= 0 or amount != amount.to_integral_value():
        raise ValidationError({"amount": "Số tiền thực nhận phải là VNĐ nguyên dương."})
    if method == Payment.Method.BANK_TRANSFER and not transaction_reference.strip():
        raise ValidationError(
            {"transaction_reference": "Cần mã giao dịch đã được nhân viên đối chiếu."}
        )
    if received_at > timezone.now():
        raise ValidationError({"received_at": "Thời điểm thực nhận không thể ở tương lai."})
    finances = order_financials(order)
    if amount > finances["remaining"]:
        raise ValidationError(
            {"amount": f"Số tiền vượt khoản còn phải thu {finances['remaining']:.0f} VNĐ."}
        )
    try:
        payment = Payment.objects.create(
            order=order,
            method=method,
            amount=amount,
            received_at=received_at,
            confirmed_by=staff,
            transaction_reference=transaction_reference.strip(),
            notes=notes.strip(),
            idempotency_key=idempotency_key,
        )
    except IntegrityError as exc:
        raise ValidationError(
            {"transaction_reference": "Mã tham chiếu đã được ghi nhận trước đó."}
        ) from exc
    old_status = order.status
    finances = order_financials(order)
    status_updates = {
        "payment_status": _payment_status(finances["net_received"], terms["total"])
    }
    new_status = old_status
    if (
        order.status == Order.Status.DA_GIAO_CHO_THANH_TOAN
        and finances["net_received"] >= terms["total"]
        and not _has_open_work(order)
    ):
        new_status = Order.Status.HOAN_TAT
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=new_status,
        updates=status_updates,
    )
    _history(
        order,
        staff,
        OrderHistory.Action.PAYMENT_RECORDED,
        old_status,
        notes or "Nhân viên xác nhận khoản tiền thực nhận.",
        details={
            "payment_id": payment.pk,
            "method": payment.method,
            "amount": str(payment.amount),
            "transaction_reference": payment.transaction_reference,
            "bank_api_verified": False,
            "financials": {
                key: str(finances[key])
                for key in ("total", "received", "refunded", "net_received", "remaining")
            },
        },
        quote=order.current_quote,
    )
    if new_status == Order.Status.HOAN_TAT:
        _history(
            order,
            staff,
            OrderHistory.Action.ORDER_COMPLETED,
            old_status,
            "Đơn đã giao thành công, thu đủ tiền và không còn xử lý tồn đọng.",
            quote=order.current_quote,
        )
    return order, payment, True


def record_refund(
    *,
    order_id,
    incident_id,
    payment_id,
    staff,
    expected_order_version,
    method,
    amount,
    refunded_at,
    transaction_reference,
    notes,
    idempotency_key,
    proof=None,
):
    stored_files = []
    try:
        with transaction.atomic():
            return _record_refund(
                order_id=order_id,
                incident_id=incident_id,
                payment_id=payment_id,
                staff=staff,
                expected_order_version=expected_order_version,
                method=method,
                amount=amount,
                refunded_at=refunded_at,
                transaction_reference=transaction_reference,
                notes=notes,
                idempotency_key=idempotency_key,
                proof=proof,
                stored_files=stored_files,
            )
    except Exception:
        for storage, name in stored_files:
            storage.delete(name)
        raise


def _record_refund(
    *,
    order_id,
    incident_id,
    payment_id,
    staff,
    expected_order_version,
    method,
    amount,
    refunded_at,
    transaction_reference,
    notes,
    idempotency_key,
    proof,
    stored_files,
):
    _require_role(staff, User.Role.STAFF)
    if method not in Refund.Method.values:
        raise ValidationError({"method": "Phương thức hoàn tiền không hợp lệ."})
    transaction_reference = transaction_reference.strip()
    notes = notes.strip()
    order = _load_order(order_id)
    replay = _payment_replay(
        model=Refund,
        key=idempotency_key,
        order=order,
        payload={
            "incident_id": incident_id,
            "payment_id": payment_id,
            "method": method,
            "amount": amount,
            "recorded_by_id": staff.pk,
            "refunded_at": refunded_at,
            "transaction_reference": transaction_reference.strip(),
            "notes": notes,
        },
    )
    if replay:
        return order, replay, False
    _check_version(order, expected_order_version)
    incident = OrderIncident.objects.filter(
        pk=incident_id,
        order=order,
        proposed_resolution=OrderIncident.Resolution.CANCEL,
        owner_decision=OrderIncident.OwnerDecision.APPROVED,
        status=OrderIncident.Status.IN_PROGRESS,
    ).first()
    if incident is None:
        raise InvalidOrderTransition("Không tìm thấy phương án hủy đang chờ hoàn tiền.")
    payment = Payment.objects.filter(pk=payment_id, order=order).first()
    if payment is None:
        raise InvalidOrderTransition("Khoản thu không thuộc đơn hàng này.")
    if amount <= 0 or amount != amount.to_integral_value():
        raise ValidationError({"amount": "Số tiền hoàn phải là VNĐ nguyên dương."})
    if method == Refund.Method.BANK_TRANSFER and not transaction_reference.strip():
        raise ValidationError({"transaction_reference": "Nhập mã giao dịch hoàn thực tế."})
    if refunded_at > timezone.now():
        raise ValidationError({"refunded_at": "Thời điểm hoàn không thể ở tương lai."})
    already_refunded = sum(
        payment.refunds.values_list("amount", flat=True),
        Decimal("0"),
    )
    if amount > payment.amount - already_refunded:
        raise ValidationError({"amount": "Không thể hoàn vượt khoản thu còn có thể hoàn."})
    total_approved_recorded = sum(
        incident.refunds.values_list("amount", flat=True),
        Decimal("0"),
    )
    if amount > incident.approved_refund_amount - total_approved_recorded:
        raise ValidationError({"amount": "Vượt số tiền hoàn được chủ cửa hàng duyệt."})
    try:
        refund = Refund.objects.create(
            order=order,
            payment=payment,
            incident=incident,
            amount=amount,
            method=method,
            transaction_reference=transaction_reference.strip(),
            recorded_by=staff,
            refunded_at=refunded_at,
            notes=notes.strip(),
            idempotency_key=idempotency_key,
        )
    except IntegrityError as exc:
        raise ValidationError({"transaction_reference": "Mã tham chiếu hoàn bị trùng."}) from exc
    if proof:
        refund.proof.save(proof.name, proof, save=False)
        stored_files.append((refund.proof.storage, refund.proof.name))
        refund.save(update_fields=("proof",))
    old_status = order.status
    finances = order_financials(order)
    canceled = (
        total_approved_recorded + amount == incident.approved_refund_amount
        and finances["refunded"] == finances["received"]
    )
    updates = {
        "payment_status": _payment_status(finances["net_received"], finances["total"])
    }
    new_status = old_status
    if canceled:
        incident.status = OrderIncident.Status.RESOLVED
        incident.executed_by = staff
        incident.execution_result = (
            f"Đã ghi nhận hoàn đủ {incident.approved_refund_amount:.0f} VNĐ."
        )
        incident.resolved_at = timezone.now()
        incident.revision += 1
        incident.save()
        new_status = Order.Status.DA_HUY
        updates["payment_status"] = Order.PaymentStatus.UNPAID
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=new_status,
        updates=updates,
    )
    _history(
        order,
        staff,
        OrderHistory.Action.REFUND_RECORDED,
        old_status,
        notes or "Nhân viên xác nhận đã thực hiện khoản hoàn bên ngoài hệ thống.",
        details={
            "refund_id": refund.pk,
            "payment_id": payment.pk,
            "incident_id": incident.pk,
            "amount": str(refund.amount),
            "method": refund.method,
            "financials": {
                key: str(finances[key])
                for key in ("total", "received", "refunded", "net_received", "remaining")
            },
            "transfer_service_called": False,
        },
        quote=order.current_quote,
    )
    if canceled:
        _history(
            order,
            staff,
            OrderHistory.Action.ORDER_CANCELLED,
            old_status,
            "Đơn được hủy sau khi ghi nhận hoàn đủ tiền đã thu.",
            details={"incident_id": incident.pk},
            quote=order.current_quote,
        )
    return order, refund, True


def record_incident_resolution(
    *,
    incident_id,
    staff,
    expected_order_version,
    expected_incident_revision,
    result,
):
    _require_role(staff, User.Role.STAFF)
    incident = OrderIncident.objects.select_related("order").get(pk=incident_id)
    order = _load_order(incident.order_id)
    _check_version(order, expected_order_version)
    if incident.revision != expected_incident_revision:
        raise StaleOrderVersion("Sự cố vừa thay đổi. Hãy tải lại trang.")
    if (
        incident.status != OrderIncident.Status.IN_PROGRESS
        or incident.proposed_resolution != OrderIncident.Resolution.REDELIVER
    ):
        raise InvalidOrderTransition("Sự cố không chờ ghi nhận thực hiện giao lại.")
    result = result.strip()
    if not result:
        raise ValidationError({"result": "Ghi rõ kết quả thống nhất với khách."})
    if order.status != Order.Status.CHO_XU_LY_SU_CO:
        raise InvalidOrderTransition("Đơn không còn chờ xử lý sự cố giao lại.")
    incident.status = OrderIncident.Status.RESOLVED
    incident.executed_by = staff
    incident.execution_result = result
    incident.resolved_at = timezone.now()
    incident.revision += 1
    incident.save()
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        new_status=Order.Status.SAN_SANG_GIAO,
        updates={
            "delivery_rescheduled_at": incident.proposed_delivery_at,
            "delivery_reschedule_note": incident.customer_agreement_note,
        },
    )
    _history(
        order,
        staff,
        OrderHistory.Action.INCIDENT_EXECUTED,
        old_status,
        result,
        details={"incident_id": incident.pk, "resolution": incident.proposed_resolution},
        quote=order.current_quote,
    )
    _history(
        order,
        staff,
        OrderHistory.Action.DELIVERY_RESCHEDULED,
        old_status,
        result,
        details={
            "incident_id": incident.pk,
            "scheduled_at": incident.proposed_delivery_at.isoformat(),
            "customer_agreement": incident.customer_agreement_note,
            "unchanged_terms": True,
        },
        quote=order.current_quote,
    )
    return order, incident
