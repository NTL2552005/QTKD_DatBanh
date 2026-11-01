from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts.models import User
from catalog.validators import validate_catalog_image

from .models import (
    ApprovalRequest,
    CakeReviewVersion,
    ChangeAgreement,
    Order,
    OrderHistory,
    ProductionCheck,
    Quote,
    RevisionRequest,
)
from .services import InvalidOrderTransition, StaleOrderVersion


REVISION_ACTIVE_STATUSES = (
    RevisionRequest.Status.OPEN,
    RevisionRequest.Status.IN_PROGRESS,
    RevisionRequest.Status.WAITING_OWNER,
    RevisionRequest.Status.WAITING_CUSTOMER,
    RevisionRequest.Status.OWNER_REJECTED,
    RevisionRequest.Status.CUSTOMER_DECLINED,
)


def _load_production_order(order_id, *, customer=None):
    orders = Order.objects.select_related(
        "current_quote",
        "current_review",
        "active_change_agreement",
    )
    if customer is not None:
        orders = orders.filter(customer=customer)
    return orders.get(pk=order_id)


def _assert_expected_version(order, expected_version):
    if order.version != expected_version:
        raise StaleOrderVersion(
            "Đơn hàng đã thay đổi. Hãy tải lại trang trước khi tiếp tục."
        )


def _require_role(user, role):
    if user.role != role:
        raise InvalidOrderTransition("Tài khoản không có quyền thực hiện thao tác này.")


def _assert_contract(order):
    if (
        order.current_quote is None
        or order.current_quote.status != Quote.Status.ACCEPTED
    ):
        raise InvalidOrderTransition(
            "Đơn cần có báo giá được khách chấp nhận làm căn cứ sản xuất."
        )


def _change_order(order, *, expected_version, old_status, updates):
    changes = {
        **updates,
        "version": F("version") + 1,
        "updated_at": timezone.now(),
    }
    changed = Order.objects.filter(
        pk=order.pk,
        status=old_status,
        version=expected_version,
    ).update(**changes)
    if changed != 1:
        raise StaleOrderVersion(
            "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
        )
    order.refresh_from_db()
    return order


def _history(order, actor, action, old_status, message="", *, details=None, quote=None):
    return OrderHistory.objects.create(
        order=order,
        actor=actor,
        action=action,
        from_status=old_status,
        to_status=order.status,
        message=message,
        details=details or {},
        quote=quote,
    )


def _latest_check(order):
    return order.production_checks.filter(
        production_round=order.production_round
    ).order_by("-checked_at", "-pk").first()


def _active_revision(order):
    return order.revision_requests.filter(
        status__in=REVISION_ACTIVE_STATUSES
    ).order_by("-requested_at", "-pk").first()


def _latest_review(order):
    return order.review_versions.order_by("-version_number").first()


@transaction.atomic
def start_production(*, order_id, staff, expected_version):
    _require_role(staff, User.Role.STAFF)
    order = _load_production_order(order_id)
    _assert_expected_version(order, expected_version)
    if order.status != Order.Status.DA_XAC_NHAN:
        raise InvalidOrderTransition(
            "Chỉ có thể bắt đầu làm khi khách đã xác nhận đơn và báo giá."
        )
    _assert_contract(order)
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_version,
        old_status=old_status,
        updates={"status": Order.Status.DANG_LAM},
    )
    _history(
        order,
        staff,
        OrderHistory.Action.PRODUCTION_STARTED,
        old_status,
        "Nhân viên bắt đầu làm bánh theo báo giá khách đã chấp nhận.",
        details={
            "production_round": order.production_round,
            "accepted_quote_id": order.current_quote_id,
        },
        quote=order.current_quote,
    )
    return order


@transaction.atomic
def record_production_check(
    *,
    order_id,
    staff,
    expected_version,
    passed,
    notes,
):
    _require_role(staff, User.Role.STAFF)
    order = _load_production_order(order_id)
    _assert_expected_version(order, expected_version)
    if order.status != Order.Status.DANG_LAM:
        raise InvalidOrderTransition("Chỉ kiểm tra chất lượng khi đang làm bánh.")
    _assert_contract(order)
    notes = notes.strip()
    if not passed and not notes:
        raise ValidationError({"notes": "Khi chưa đạt, phải ghi rõ lý do."})

    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_version,
        old_status=old_status,
        updates={},
    )
    check = ProductionCheck.objects.create(
        order=order,
        production_round=order.production_round,
        checked_by=staff,
        passed=passed,
        notes=notes,
    )
    _history(
        order,
        staff,
        OrderHistory.Action.QUALITY_CHECKED,
        old_status,
        notes,
        details={
            "check_id": check.pk,
            "production_round": check.production_round,
            "passed": passed,
        },
        quote=order.current_quote,
    )
    return order, check


def send_review_photo(
    *,
    order_id,
    staff,
    expected_version,
    photo,
    description="",
    replacement_reason="",
):
    stored_photos = []
    try:
        with transaction.atomic():
            return _send_review_photo(
                order_id=order_id,
                staff=staff,
                expected_version=expected_version,
                photo=photo,
                description=description,
                replacement_reason=replacement_reason,
                stored_photos=stored_photos,
            )
    except Exception:
        for storage, name in stored_photos:
            storage.delete(name)
        raise


def _send_review_photo(
    *,
    order_id,
    staff,
    expected_version,
    photo,
    description,
    replacement_reason,
    stored_photos,
):
    _require_role(staff, User.Role.STAFF)
    validate_catalog_image(photo)
    order = _load_production_order(order_id)
    _assert_expected_version(order, expected_version)
    if order.status not in (
        Order.Status.DANG_LAM,
        Order.Status.CHO_DUYET_ANH,
    ):
        raise InvalidOrderTransition("Đơn hiện không thể gửi ảnh duyệt.")
    _assert_contract(order)
    check = _latest_check(order)
    if check is None or not check.passed:
        raise InvalidOrderTransition(
            "Cần có lần kiểm tra chất lượng đạt còn hiệu lực trước khi gửi ảnh."
        )
    current_review = order.current_review
    replacing = order.status == Order.Status.CHO_DUYET_ANH
    if replacing and (
        current_review is None
        or current_review.status != CakeReviewVersion.Status.PENDING
    ):
        raise InvalidOrderTransition("Không tìm thấy ảnh đang chờ duyệt để thay thế.")
    replacement_reason = replacement_reason.strip()
    if replacing and not replacement_reason:
        raise ValidationError(
            {"replacement_reason": "Vui lòng ghi lý do thay ảnh đang chờ duyệt."}
        )

    latest = _latest_review(order)
    version_number = (latest.version_number if latest else 0) + 1
    old_status = order.status
    if replacing:
        replaced = CakeReviewVersion.objects.filter(
            pk=current_review.pk,
            status=CakeReviewVersion.Status.PENDING,
            revision=current_review.revision,
        ).update(
            status=CakeReviewVersion.Status.REPLACED,
            revision=F("revision") + 1,
        )
        if replaced != 1:
            raise StaleOrderVersion("Ảnh vừa được phản hồi; hãy tải lại đơn hàng.")
    review = CakeReviewVersion(
        order=order,
        version_number=version_number,
        production_round=order.production_round,
        photo=photo,
        description=description.strip(),
        sent_by=staff,
        production_check=check,
        replacement_reason=replacement_reason,
    )
    review.photo.save(photo.name, photo, save=False)
    stored_photos.append((review.photo.storage, review.photo.name))
    review.save()
    order = _change_order(
        order,
        expected_version=expected_version,
        old_status=old_status,
        updates={
            "status": Order.Status.CHO_DUYET_ANH,
            "current_review": review,
        },
    )
    revision_request = _active_revision(order)
    if (
        revision_request
        and revision_request.status == RevisionRequest.Status.IN_PROGRESS
    ):
        revision_request.status = RevisionRequest.Status.COMPLETED
        revision_request.resolved_at = timezone.now()
        revision_request.replacement_review_version = review
        revision_request.save(
            update_fields=(
                "status",
                "resolved_at",
                "replacement_review_version",
            )
        )
    _history(
        order,
        staff,
        (
            OrderHistory.Action.REVIEW_PHOTO_REPLACED
            if replacing
            else OrderHistory.Action.REVIEW_PHOTO_SENT
        ),
        old_status,
        replacement_reason or description.strip() or "Gửi ảnh bánh để khách duyệt.",
        details={
            "review_version_id": review.pk,
            "review_version": review.version_number,
            "production_round": review.production_round,
            "production_check_id": check.pk,
            "replaced_version_id": current_review.pk if replacing else None,
        },
        quote=order.current_quote,
    )
    from .workflow_events import create_workflow_event

    create_workflow_event(
        order=order,
        event_type="cake.review_requested",
        business_key=f"cake-review-requested:{review.pk}",
        cake_review_version=review.version_number,
    )
    if revision_request and revision_request.status == RevisionRequest.Status.COMPLETED:
        _history(
            order,
            staff,
            OrderHistory.Action.REVISION_COMPLETED,
            old_status,
            "Ảnh phiên bản mới đã được gửi để xử lý yêu cầu chỉnh sửa.",
            details={
                "revision_request_id": revision_request.pk,
                "review_version_id": review.pk,
            },
            quote=order.current_quote,
        )
    return order, review


@transaction.atomic
def accept_review(
    *,
    order_id,
    customer,
    review_id,
    expected_order_version,
    expected_review_revision,
):
    _require_role(customer, User.Role.CUSTOMER)
    order = _load_production_order(order_id, customer=customer)
    _assert_expected_version(order, expected_order_version)
    review = CakeReviewVersion.objects.get(pk=review_id, order=order)
    latest = _latest_review(order)
    if (
        order.status != Order.Status.CHO_DUYET_ANH
        or order.current_review_id != review.pk
        or latest is None
        or latest.pk != review.pk
        or review.status != CakeReviewVersion.Status.PENDING
        or review.revision != expected_review_revision
        or review.production_round != order.production_round
    ):
        raise InvalidOrderTransition(
            "Ảnh không còn là phiên bản đang chờ duyệt. Hãy tải lại đơn."
        )
    now = timezone.now()
    updated = CakeReviewVersion.objects.filter(
        pk=review.pk,
        order=order,
        status=CakeReviewVersion.Status.PENDING,
        revision=expected_review_revision,
    ).update(
        status=CakeReviewVersion.Status.ACCEPTED,
        responded_at=now,
        revision=F("revision") + 1,
    )
    if updated != 1:
        raise StaleOrderVersion("Ảnh vừa được phản hồi; hãy tải lại đơn hàng.")
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        updates={"status": Order.Status.SAN_SANG_GIAO},
    )
    _history(
        order,
        customer,
        OrderHistory.Action.REVIEW_ACCEPTED,
        old_status,
        "Khách hàng đồng ý ảnh bánh hiện hành.",
        details={"review_version_id": review.pk, "review_version": review.version_number},
        quote=order.current_quote,
    )
    return order


@transaction.atomic
def request_review_revision(
    *,
    order_id,
    customer,
    review_id,
    expected_order_version,
    expected_review_revision,
    message,
):
    _require_role(customer, User.Role.CUSTOMER)
    order = _load_production_order(order_id, customer=customer)
    _assert_expected_version(order, expected_order_version)
    review = CakeReviewVersion.objects.get(pk=review_id, order=order)
    latest = _latest_review(order)
    message = message.strip()
    if not message:
        raise ValidationError({"message": "Vui lòng mô tả nội dung cần chỉnh sửa."})
    if (
        order.status != Order.Status.CHO_DUYET_ANH
        or order.current_review_id != review.pk
        or latest is None
        or latest.pk != review.pk
        or review.status != CakeReviewVersion.Status.PENDING
        or review.revision != expected_review_revision
        or review.production_round != order.production_round
    ):
        raise InvalidOrderTransition(
            "Chỉ có thể yêu cầu sửa ảnh hiện hành đang chờ duyệt."
        )
    now = timezone.now()
    updated = CakeReviewVersion.objects.filter(
        pk=review.pk,
        status=CakeReviewVersion.Status.PENDING,
        revision=expected_review_revision,
    ).update(
        status=CakeReviewVersion.Status.REVISION_REQUESTED,
        responded_at=now,
        customer_response=message,
        revision=F("revision") + 1,
    )
    if updated != 1:
        raise StaleOrderVersion("Ảnh vừa được phản hồi; hãy tải lại đơn hàng.")
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        updates={"status": Order.Status.CHO_XU_LY_CHINH_SUA},
    )
    revision_request = RevisionRequest.objects.create(
        order=order,
        review_version=review,
        customer=customer,
        message=message,
    )
    _history(
        order,
        customer,
        OrderHistory.Action.REVISION_REQUESTED,
        old_status,
        message,
        details={
            "revision_request_id": revision_request.pk,
            "review_version_id": review.pk,
            "review_version": review.version_number,
        },
        quote=order.current_quote,
    )
    return order, revision_request


@transaction.atomic
def classify_revision_request(
    *,
    order_id,
    staff,
    expected_order_version,
    scope,
    reason,
    approval_summary="",
    proposed_plan="",
):
    _require_role(staff, User.Role.STAFF)
    order = _load_production_order(order_id)
    _assert_expected_version(order, expected_order_version)
    if order.status not in (
        Order.Status.CHO_XU_LY_CHINH_SUA,
        Order.Status.CHO_TRAO_DOI_CHINH_SUA,
    ):
        raise InvalidOrderTransition("Đơn hiện không chờ phân loại chỉnh sửa.")
    revision_request = _active_revision(order)
    if revision_request is None or revision_request.status != RevisionRequest.Status.OPEN:
        raise InvalidOrderTransition("Không có yêu cầu chỉnh sửa đang chờ phân loại.")
    if order.current_review_id != revision_request.review_version_id:
        raise InvalidOrderTransition("Yêu cầu không còn gắn với ảnh hiện hành.")
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "Vui lòng ghi lý do phân loại."})
    if scope not in RevisionRequest.Scope.values:
        raise ValidationError({"scope": "Phạm vi chỉnh sửa không hợp lệ."})
    old_status = order.status
    revision_request.scope = scope
    revision_request.classified_by = staff
    revision_request.classification_reason = reason
    revision_request.taken_at = revision_request.taken_at or timezone.now()
    if scope == RevisionRequest.Scope.WITHIN:
        revision_request.status = RevisionRequest.Status.IN_PROGRESS
        revision_request.save(
            update_fields=(
                "scope",
                "classified_by",
                "classification_reason",
                "taken_at",
                "status",
            )
        )
        order = _change_order(
            order,
            expected_version=expected_order_version,
            old_status=old_status,
            updates={
                "status": Order.Status.DANG_LAM,
                "production_round": F("production_round") + 1,
            },
        )
        _history(
            order,
            staff,
            OrderHistory.Action.REVISION_CLASSIFIED,
            old_status,
            reason,
            details={
                "revision_request_id": revision_request.pk,
                "scope": scope,
                "production_round": order.production_round,
            },
            quote=order.current_quote,
        )
        return order, revision_request, None

    summary = approval_summary.strip()
    plan = proposed_plan.strip()
    if not summary or not plan:
        raise ValidationError(
            "Yêu cầu vượt phạm vi cần tóm tắt và phương án đề xuất."
        )
    revision_request.status = RevisionRequest.Status.WAITING_OWNER
    revision_request.save(
        update_fields=(
            "scope",
            "classified_by",
            "classification_reason",
            "taken_at",
            "status",
        )
    )
    approval = ApprovalRequest.objects.create(
        revision_request=revision_request,
        submitted_by=staff,
        summary=summary,
        reason=reason,
        proposed_plan=plan,
    )
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        updates={"status": Order.Status.CHO_CHU_DUYET},
    )
    _history(
        order,
        staff,
        OrderHistory.Action.OWNER_APPROVAL_REQUESTED,
        old_status,
        reason,
        details={
            "revision_request_id": revision_request.pk,
            "approval_request_id": approval.pk,
            "summary": summary,
            "proposed_plan": plan,
        },
        quote=order.current_quote,
    )
    return order, revision_request, approval


def _current_commercial_terms(order):
    if order.active_change_agreement_id:
        return order.active_change_agreement
    quote = order.current_quote
    if quote is None or quote.status != Quote.Status.ACCEPTED:
        raise InvalidOrderTransition("Không tìm thấy thỏa thuận giá đã được khách chấp nhận.")
    return quote


@transaction.atomic
def decide_revision_approval(
    *,
    approval_id,
    owner,
    expected_order_version,
    expected_approval_id,
    approve,
    reason,
    requires_change_agreement=False,
    unit_price=None,
    delivery_fee=None,
    proposed_delivery_at=None,
):
    _require_role(owner, User.Role.OWNER)
    if approval_id != expected_approval_id:
        raise StaleOrderVersion("Yêu cầu duyệt đã thay đổi. Hãy tải lại.")
    approval = ApprovalRequest.objects.select_related(
        "revision_request__order__current_quote",
        "revision_request__order__active_change_agreement",
    ).get(pk=approval_id)
    if approval.submitted_by_id == owner.pk:
        raise InvalidOrderTransition("Người gửi yêu cầu không được tự duyệt.")
    order = _load_production_order(approval.revision_request.order_id)
    _assert_expected_version(order, expected_order_version)
    revision_request = approval.revision_request
    if (
        approval.status != ApprovalRequest.Status.PENDING
        or revision_request.status != RevisionRequest.Status.WAITING_OWNER
        or order.status != Order.Status.CHO_CHU_DUYET
    ):
        raise InvalidOrderTransition("Yêu cầu này không còn chờ chủ duyệt.")
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "Vui lòng ghi lý do quyết định."})
    now = timezone.now()
    old_status = order.status
    if not approve:
        approval.status = ApprovalRequest.Status.REJECTED
        approval.decided_by = owner
        approval.decision_reason = reason
        approval.decided_at = now
        approval.save(
            update_fields=("status", "decided_by", "decision_reason", "decided_at")
        )
        revision_request.status = RevisionRequest.Status.OWNER_REJECTED
        revision_request.save(update_fields=("status",))
        order = _change_order(
            order,
            expected_version=expected_order_version,
            old_status=old_status,
            updates={"status": Order.Status.CHO_XU_LY_CHINH_SUA},
        )
        _history(
            order,
            owner,
            OrderHistory.Action.OWNER_APPROVAL_DECIDED,
            old_status,
            reason,
            details={
                "approval_request_id": approval.pk,
                "decision": "rejected",
                "revision_request_id": revision_request.pk,
            },
            quote=order.current_quote,
        )
        return order, None

    if requires_change_agreement:
        if unit_price is None or delivery_fee is None or proposed_delivery_at is None:
            raise ValidationError(
                "Cần nhập đơn giá, phí giao và lịch mới cho phụ lục."
            )
        if (
            unit_price < 0
            or delivery_fee < 0
            or unit_price != unit_price.to_integral_value()
            or delivery_fee != delivery_fee.to_integral_value()
        ):
            raise ValidationError("Giá phải là số nguyên VNĐ không âm.")
        if proposed_delivery_at <= now:
            raise ValidationError("Lịch giao mới phải ở tương lai.")
        base = _current_commercial_terms(order)
        total = unit_price * order.quantity + delivery_fee
        previous_total = base.total
        latest_version = (
            ChangeAgreement.objects.filter(order=order)
            .order_by("-version_number")
            .values_list("version_number", flat=True)
            .first()
            or 0
        )
        snapshot = {
            "cake_name": order.cake_name_snapshot,
            "size": order.size,
            "flavor": order.flavor,
            "quantity": order.quantity,
            "inscription": order.inscription,
            "decoration_request": order.decoration_request,
            "recipient_name": order.recipient_name,
            "recipient_phone": order.recipient_phone,
            "delivery_address": order.delivery_address,
            "desired_delivery_at": order.desired_delivery_at.isoformat(),
            "note": order.note,
        }
        agreement = ChangeAgreement.objects.create(
            order=order,
            revision_request=revision_request,
            approval_request=approval,
            version_number=latest_version + 1,
            based_on_quote=base if isinstance(base, Quote) else None,
            based_on_agreement=base if isinstance(base, ChangeAgreement) else None,
            request_snapshot=snapshot,
            requested_change_snapshot=revision_request.message,
            quantity_snapshot=order.quantity,
            unit_price=unit_price,
            delivery_fee=delivery_fee,
            previous_total=previous_total,
            price_delta=total - previous_total,
            total=total,
            proposed_delivery_at=proposed_delivery_at,
        )
        approval.requires_change_agreement = True
        approval.status = ApprovalRequest.Status.APPROVED
        approval.decided_by = owner
        approval.decision_reason = reason
        approval.decided_at = now
        approval.save(
            update_fields=(
                "requires_change_agreement",
                "status",
                "decided_by",
                "decision_reason",
                "decided_at",
            )
        )
        revision_request.status = RevisionRequest.Status.WAITING_CUSTOMER
        revision_request.save(update_fields=("status",))
        order = _change_order(
            order,
            expected_version=expected_order_version,
            old_status=old_status,
            updates={"status": Order.Status.CHO_KHACH_DUYET_THAY_DOI},
        )
        _history(
            order,
            owner,
            OrderHistory.Action.OWNER_APPROVAL_DECIDED,
            old_status,
            reason,
            details={
                "approval_request_id": approval.pk,
                "decision": "approved",
                "requires_change_agreement": True,
            },
            quote=order.current_quote,
        )
        _history(
            order,
            owner,
            OrderHistory.Action.AGREEMENT_SENT,
            old_status,
            "Chủ cửa hàng duyệt phương án; chờ khách đồng ý phụ lục trước khi sửa.",
            details={
                "change_agreement_id": agreement.pk,
                "version": agreement.version_number,
                "previous_total": str(previous_total),
                "price_delta": str(agreement.price_delta),
                "total": str(total),
                "proposed_delivery_at": proposed_delivery_at.isoformat(),
            },
            quote=order.current_quote,
        )
        return order, agreement

    approval.status = ApprovalRequest.Status.APPROVED
    approval.decided_by = owner
    approval.decision_reason = reason
    approval.decided_at = now
    approval.save(
        update_fields=("status", "decided_by", "decision_reason", "decided_at")
    )
    revision_request.status = RevisionRequest.Status.IN_PROGRESS
    revision_request.save(update_fields=("status",))
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        updates={
            "status": Order.Status.DANG_LAM,
            "production_round": F("production_round") + 1,
        },
    )
    _history(
        order,
        owner,
        OrderHistory.Action.OWNER_APPROVAL_DECIDED,
        old_status,
        reason,
        details={
            "approval_request_id": approval.pk,
            "decision": "approved",
            "requires_change_agreement": False,
            "production_round": order.production_round,
        },
        quote=order.current_quote,
    )
    return order, None


@transaction.atomic
def respond_change_agreement(
    *,
    agreement_id,
    customer,
    expected_order_version,
    expected_agreement_id,
    expected_agreement_revision,
    accept,
    message="",
):
    _require_role(customer, User.Role.CUSTOMER)
    if agreement_id != expected_agreement_id:
        raise StaleOrderVersion("Phụ lục đã thay đổi. Hãy tải lại đơn hàng.")
    agreement = ChangeAgreement.objects.select_related(
        "order__current_quote",
        "order__active_change_agreement",
        "revision_request",
        "approval_request",
    ).get(pk=agreement_id, order__customer=customer)
    order = _load_production_order(agreement.order_id, customer=customer)
    _assert_expected_version(order, expected_order_version)
    if (
        order.status != Order.Status.CHO_KHACH_DUYET_THAY_DOI
        or agreement.status != ChangeAgreement.Status.PENDING
        or agreement.revision != expected_agreement_revision
        or order.change_agreements.filter(
            status=ChangeAgreement.Status.PENDING
        ).count()
        != 1
        or agreement.approval_request.status != ApprovalRequest.Status.APPROVED
    ):
        raise InvalidOrderTransition("Phụ lục không còn chờ phản hồi hiện hành.")
    now = timezone.now()
    old_status = order.status
    new_agreement_status = (
        ChangeAgreement.Status.ACCEPTED
        if accept
        else ChangeAgreement.Status.REJECTED
    )
    changed = ChangeAgreement.objects.filter(
        pk=agreement.pk,
        order=order,
        status=ChangeAgreement.Status.PENDING,
        revision=expected_agreement_revision,
    ).update(
        status=new_agreement_status,
        responded_at=now,
        customer_response=message.strip(),
        revision=F("revision") + 1,
    )
    if changed != 1:
        raise StaleOrderVersion("Phụ lục vừa được phản hồi; hãy tải lại đơn.")
    if accept:
        revision_request = agreement.revision_request
        revision_request.status = RevisionRequest.Status.IN_PROGRESS
        revision_request.save(update_fields=("status",))
        order_updates = {
            "status": Order.Status.DANG_LAM,
            "production_round": F("production_round") + 1,
            "active_change_agreement": agreement,
        }
        action = OrderHistory.Action.AGREEMENT_ACCEPTED
        history_message = message.strip() or "Khách hàng đồng ý phụ lục thay đổi."
    else:
        agreement.revision_request.status = RevisionRequest.Status.CUSTOMER_DECLINED
        agreement.revision_request.resolved_at = now
        agreement.revision_request.save(update_fields=("status", "resolved_at"))
        order_updates = {"status": Order.Status.CHO_TRAO_DOI_CHINH_SUA}
        action = OrderHistory.Action.AGREEMENT_REJECTED
        history_message = message.strip() or "Khách hàng từ chối phụ lục; giữ nguyên thỏa thuận trước."
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        updates=order_updates,
    )
    _history(
        order,
        customer,
        action,
        old_status,
        history_message,
        details={
            "change_agreement_id": agreement.pk,
            "agreement_version": agreement.version_number,
            "accepted": accept,
            "old_terms_preserved": not accept,
            "production_round": order.production_round,
        },
        quote=order.current_quote,
    )
    return order, agreement


@transaction.atomic
def keep_current_review(*, order_id, customer, expected_order_version, review_id):
    _require_role(customer, User.Role.CUSTOMER)
    order = _load_production_order(order_id, customer=customer)
    _assert_expected_version(order, expected_order_version)
    revision_request = _active_revision(order)
    latest = _latest_review(order)
    rejected_approval = (
        revision_request.approval_requests.filter(
            status=ApprovalRequest.Status.REJECTED
        )
        .order_by("-decided_at", "-pk")
        .first()
        if revision_request
        else None
    )
    if (
        order.status != Order.Status.CHO_XU_LY_CHINH_SUA
        or revision_request is None
        or revision_request.status != RevisionRequest.Status.OWNER_REJECTED
        or rejected_approval is None
        or review_id != order.current_review_id
        or latest is None
        or latest.pk != review_id
        or latest.status != CakeReviewVersion.Status.REVISION_REQUESTED
        or order.change_agreements.filter(
            status=ChangeAgreement.Status.PENDING
        ).exists()
    ):
        raise InvalidOrderTransition(
            "Không thể giữ ảnh cũ khi còn thay đổi chưa được xử lý."
        )
    now = timezone.now()
    old_status = order.status
    changed = CakeReviewVersion.objects.filter(
        pk=latest.pk,
        order=order,
        status=CakeReviewVersion.Status.REVISION_REQUESTED,
        revision=latest.revision,
    ).update(
        status=CakeReviewVersion.Status.ACCEPTED,
        responded_at=now,
        revision=F("revision") + 1,
    )
    if changed != 1:
        raise StaleOrderVersion("Ảnh vừa thay đổi; hãy tải lại đơn.")
    revision_request.status = RevisionRequest.Status.CUSTOMER_KEPT
    revision_request.resolved_at = now
    revision_request.save(update_fields=("status", "resolved_at"))
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        updates={"status": Order.Status.SAN_SANG_GIAO},
    )
    _history(
        order,
        customer,
        OrderHistory.Action.CUSTOMER_KEPT_REVIEW,
        old_status,
        "Khách xác nhận rõ ràng giữ phương án ảnh hiện tại sau khi chủ từ chối chỉnh sửa.",
        details={
            "review_version_id": latest.pk,
            "revision_request_id": revision_request.pk,
            "approval_request_id": rejected_approval.pk,
        },
        quote=order.current_quote,
    )
    return order


@transaction.atomic
def continue_revision_discussion(
    *,
    order_id,
    customer,
    expected_order_version,
    message,
):
    _require_role(customer, User.Role.CUSTOMER)
    order = _load_production_order(order_id, customer=customer)
    _assert_expected_version(order, expected_order_version)
    revision_request = _active_revision(order)
    message = message.strip()
    if not message:
        raise ValidationError({"message": "Vui lòng nhập nội dung trao đổi."})
    if revision_request is None:
        raise InvalidOrderTransition("Không tìm thấy yêu cầu chỉnh sửa đang trao đổi.")
    can_continue_after_owner_reject = (
        order.status == Order.Status.CHO_XU_LY_CHINH_SUA
        and revision_request.status == RevisionRequest.Status.OWNER_REJECTED
        and revision_request.approval_requests.filter(
            status=ApprovalRequest.Status.REJECTED
        ).exists()
    )
    can_continue_after_agreement_decline = (
        order.status == Order.Status.CHO_TRAO_DOI_CHINH_SUA
        and revision_request.status == RevisionRequest.Status.CUSTOMER_DECLINED
    )
    if not (
        can_continue_after_owner_reject or can_continue_after_agreement_decline
    ):
        raise InvalidOrderTransition("Đơn hiện không chờ tiếp tục trao đổi chỉnh sửa.")
    if can_continue_after_owner_reject or can_continue_after_agreement_decline:
        revision_request.status = RevisionRequest.Status.OPEN
        revision_request.resolved_at = None
        revision_request.save(update_fields=("status", "resolved_at"))
    old_status = order.status
    order = _change_order(
        order,
        expected_version=expected_order_version,
        old_status=old_status,
        updates={"status": Order.Status.CHO_TRAO_DOI_CHINH_SUA},
    )
    _history(
        order,
        customer,
        OrderHistory.Action.CUSTOMER_CONTINUED_DISCUSSION,
        old_status,
        message,
        details={"revision_request_id": revision_request.pk},
        quote=order.current_quote,
    )
    return order, revision_request
