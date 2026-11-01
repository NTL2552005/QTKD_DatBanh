import hashlib
import json
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from catalog.models import CakeTemplate

from .models import (
    IntakeQuoteApproval,
    Order,
    OrderAlternative,
    OrderHistory,
    OwnerIntakeCheck,
    Quote,
    StaffAvailabilityCheck,
)


class OrderActionError(Exception):
    pass


class InvalidOrderTransition(OrderActionError):
    pass


class StaleOrderVersion(OrderActionError):
    pass


class IdempotencyConflict(OrderActionError):
    pass


REQUEST_FIELDS = (
    "size",
    "flavor",
    "quantity",
    "inscription",
    "decoration_request",
    "recipient_name",
    "recipient_phone",
    "delivery_address",
    "desired_delivery_at",
    "note",
)


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value


def _request_snapshot(data):
    return {field: _json_value(data[field]) for field in REQUEST_FIELDS}


def _order_request_snapshot(order):
    return {
        "cake_template_id": order.cake_template_id,
        "cake_name_snapshot": order.cake_name_snapshot,
        "reference_price_snapshot": _json_value(order.reference_price_snapshot),
        **{
            field: _json_value(getattr(order, field))
            for field in REQUEST_FIELDS
        },
    }


def _commercial_request_snapshot(order):
    return {
        "cake_template_id": order.cake_template_id,
        "cake_name_snapshot": order.cake_name_snapshot,
        **{
            field: _json_value(getattr(order, field))
            for field in (
                "size",
                "flavor",
                "quantity",
                "inscription",
                "decoration_request",
                "desired_delivery_at",
            )
        },
    }


def _quote_terms_snapshot(
    *,
    unit_price,
    delivery_fee,
    quantity,
    proposed_delivery_at,
    expires_at,
    notes="",
    revision_reason="",
):
    return {
        "unit_price": int(unit_price),
        "quantity": quantity,
        "cake_total": int(unit_price * quantity),
        "delivery_fee": int(delivery_fee),
        "total": int(unit_price * quantity + delivery_fee),
        "proposed_delivery_at": proposed_delivery_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "notes": notes.strip(),
        "revision_reason": revision_reason.strip(),
    }


def _normalize_quote_datetime(value):
    return value.replace(second=0, microsecond=0)


def current_availability_check(order):
    return (
        StaffAvailabilityCheck.objects.filter(
            order=order,
            request_revision=order.request_revision,
        )
        .select_related("checked_by")
        .order_by("-checked_at", "-pk")
        .first()
    )


def current_owner_intake_check(order):
    return (
        OwnerIntakeCheck.objects.filter(
            order=order,
            request_revision=order.request_revision,
        )
        .select_related("checked_by")
        .order_by("-checked_at", "-pk")
        .first()
    )


def claim_legacy_intake_order(*, order_id, owner, expected_order_version):
    if getattr(owner, "role", None) != "OWNER":
        raise InvalidOrderTransition("Chỉ chủ cửa hàng được nhận xử lý đơn tiếp nhận.")
    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_order_version)
        has_legacy_approval = order.intake_quote_approvals.filter(
            status=IntakeQuoteApproval.Status.APPROVED
        ).exists()
        can_claim_waiting_order = (
            order.status == Order.Status.CHO_CHU_DUYET_BAO_GIA
            and order.intake_handler_role == "STAFF"
        )
        can_claim_approved_legacy = (
            order.status == Order.Status.CHO_KIEM_TRA
            and order.intake_handler_role == "STAFF"
            and has_legacy_approval
        )
        if not (can_claim_waiting_order or can_claim_approved_legacy):
            raise InvalidOrderTransition("Đơn không còn chờ được chủ nhận xử lý.")
        changed = Order.objects.filter(
            pk=order.pk,
            version=expected_order_version,
            status=order.status,
            intake_handler_role="STAFF",
        ).update(
            status=Order.Status.CHO_CHU_DUYET_BAO_GIA,
            intake_handler_role="OWNER",
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise StaleOrderVersion("Đơn vừa được nhận xử lý; hãy tải lại.")
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=owner,
            action=OrderHistory.Action.INTAKE_TRANSFERRED_TO_OWNER,
            from_status=order.status,
            to_status=order.status,
            message="Chủ cửa hàng nhận tiếp quản yêu cầu đang chờ theo quy trình cũ.",
            details={"legacy_process_claim": True},
        )
        _close_legacy_intake_approvals(
            order,
            owner=owner,
            reason="Chủ nhận tiếp quản và rà soát lại theo quy trình mới.",
        )
        return order


def _fingerprint(customer_id, cake, data):
    payload = {
        "customer_id": customer_id,
        "cake_id": cake.pk,
        "cake_name": cake.name,
        "reference_price": format(cake.reference_price, "f"),
        "request": _request_snapshot(data),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def create_order(*, customer, cake_id, request_key, data):
    try:
        with transaction.atomic():
            cake = (
                CakeTemplate.objects.public()
                .select_related("category")
                .get(pk=cake_id)
            )
            fingerprint = _fingerprint(customer.pk, cake, data)
            order = Order.objects.create(
                customer=customer,
                cake_template=cake,
                request_key=request_key,
                payload_fingerprint=fingerprint,
                cake_name_snapshot=cake.name,
                reference_price_snapshot=cake.reference_price,
                **{field: data[field] for field in REQUEST_FIELDS},
            )
            OrderHistory.objects.create(
                order=order,
                actor=customer,
                action=OrderHistory.Action.ORDER_CREATED,
                to_status=order.status,
                message="Khách hàng gửi yêu cầu đặt bánh.",
                details={"request": _request_snapshot(data)},
            )
            return order, True
    except CakeTemplate.DoesNotExist as exc:
        raise InvalidOrderTransition(
            "Mẫu bánh hiện không nhận đặt hoặc danh mục đã ngừng hoạt động."
        ) from exc
    except IntegrityError:
        existing = Order.objects.filter(request_key=request_key).first()
        if existing is None:
            raise
        if existing.customer_id != customer.pk or existing.cake_template_id != cake_id:
            raise IdempotencyConflict(
                "Mã yêu cầu này đã được dùng cho thông tin khác. Hãy tải lại biểu mẫu."
            )
        cake = CakeTemplate.objects.get(pk=existing.cake_template_id)
        if existing.payload_fingerprint != _fingerprint(customer.pk, cake, data):
            raise IdempotencyConflict(
                "Mã yêu cầu này đã được dùng cho thông tin khác. Hãy tải lại biểu mẫu."
            )
        return existing, False


def _load_order(order_id):
    return Order.objects.select_related(
        "customer",
        "cake_template",
        "current_quote",
    ).get(pk=order_id)


def _assert_version(order, expected_version):
    if order.version != expected_version:
        raise StaleOrderVersion(
            "Đơn hàng đã thay đổi. Hãy tải lại trang trước khi tiếp tục."
        )


def _transition_order(
    *,
    order_id,
    actor,
    expected_version,
    allowed_statuses,
    new_status,
    action,
    message="",
    details=None,
    updates=None,
    quote=None,
    allow_same_status=False,
):
    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_version)
        if order.status not in allowed_statuses:
            raise InvalidOrderTransition(
                "Trạng thái đơn hàng đã thay đổi; hãy tải lại trang."
            )
        if not allow_same_status and order.status == new_status:
            raise InvalidOrderTransition("Đơn hàng đã ở trạng thái này.")

        old_status = order.status
        changes = dict(updates or {})
        changes.update(
            status=new_status,
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
        changed = Order.objects.filter(
            pk=order.pk,
            version=expected_version,
            status=old_status,
        ).update(**changes)
        if changed != 1:
            raise StaleOrderVersion(
                "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
            )
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=actor,
            action=action,
            from_status=old_status,
            to_status=new_status,
            message=message,
            details=details or {},
            quote=quote,
        )
        return order


def update_customer_request(*, order_id, customer, expected_version, data):
    order = Order.objects.get(pk=order_id, customer=customer)
    if order.status != Order.Status.CAN_BO_SUNG or not order.needs_supplement:
        raise InvalidOrderTransition("Đơn hàng hiện không chờ khách bổ sung.")
    before = {field: _json_value(getattr(order, field)) for field in REQUEST_FIELDS}
    after = _request_snapshot(data)
    changes = {
        field: data[field]
        for field in REQUEST_FIELDS
    }
    changed_fields = {
        field: {"before": before[field], "after": after[field]}
        for field in REQUEST_FIELDS
        if before[field] != after[field]
    }
    with transaction.atomic():
        updated = _transition_order(
            order_id=order.pk,
            actor=customer,
            expected_version=expected_version,
            allowed_statuses=(Order.Status.CAN_BO_SUNG,),
            new_status=Order.Status.CHO_KIEM_TRA,
            action=OrderHistory.Action.CUSTOMER_UPDATED,
            message="Khách hàng đã cập nhật và gửi lại yêu cầu.",
            details={"changes": changed_fields},
            updates={
                **changes,
                "needs_supplement": False,
                "request_revision": F("request_revision") + 1,
                "materials_available": None,
                "schedule_available": None,
                "staff_check_notes": "",
            },
        )
        IntakeQuoteApproval.objects.filter(
            order=updated,
            request_revision__lt=updated.request_revision,
            status__in=(
                IntakeQuoteApproval.Status.PENDING,
                IntakeQuoteApproval.Status.APPROVED,
            ),
        ).update(
            status=IntakeQuoteApproval.Status.STALE,
            revision=F("revision") + 1,
        )
        return updated


def record_staff_check(
    *,
    order_id,
    staff,
    expected_version,
    materials_available,
    schedule_available,
    notes,
    reason="",
    reason_details="",
    authority_classification=StaffAvailabilityCheck.AuthorityClassification.UNCLASSIFIED,
    authority_reasons=None,
    authority_details="",
    authority_basis="",
):
    order = Order.objects.get(pk=order_id)
    if getattr(staff, "role", None) != "STAFF":
        raise InvalidOrderTransition("Chỉ nhân viên được kiểm tra và phân loại đơn.")
    notes = (notes or "").strip()
    owner_handoff = (
        authority_classification
        == StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED
    )
    needs_info = (
        authority_classification
        == StaffAvailabilityCheck.AuthorityClassification.NEEDS_INFO
    )
    if not owner_handoff and not notes:
        raise ValidationError({"notes": "Vui lòng ghi nội dung kiểm tra."})
    authority_reasons = list(authority_reasons or [])
    valid_classifications = {
        StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
        StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED,
        StaffAvailabilityCheck.AuthorityClassification.NEEDS_INFO,
        StaffAvailabilityCheck.AuthorityClassification.UNCLASSIFIED,
    }
    if authority_classification not in valid_classifications:
        raise ValidationError({"authority_classification": "Phân loại không hợp lệ."})
    valid_authority_reasons = set(StaffAvailabilityCheck.AuthorityReason.values)
    if any(item not in valid_authority_reasons for item in authority_reasons):
        raise ValidationError({"authority_reasons": "Lý do vượt quyền không hợp lệ."})
    authority_details = authority_details.strip()
    authority_basis = authority_basis.strip()
    if owner_handoff or needs_info:
        materials_available = None
        schedule_available = None
        reason = ""
        reason_details = ""
    elif materials_available is None or schedule_available is None:
        raise ValidationError(
            "Hãy kiểm tra cả nguyên liệu và thời gian trước khi phân loại trong quyền."
        )
    unavailable = materials_available is False or schedule_available is False
    if (
        authority_classification == StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE
        and (not authority_basis or authority_reasons)
    ):
        raise ValidationError(
            "Phân loại trong quyền cần ghi căn cứ và không chọn lý do vượt quyền."
        )
    if (
        authority_classification == StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED
        and (not authority_reasons or not authority_details)
    ):
        raise ValidationError(
            "Đơn vượt quyền cần ít nhất một lý do và mô tả cụ thể."
        )
    if (
        authority_classification == StaffAvailabilityCheck.AuthorityClassification.NEEDS_INFO
        and (not authority_details or authority_reasons or authority_basis)
    ):
        raise ValidationError(
            "Hãy ghi rõ thông tin còn thiếu; chưa chọn lý do vượt quyền hoặc căn cứ trong quyền."
        )
    if (
        authority_classification == StaffAvailabilityCheck.AuthorityClassification.UNCLASSIFIED
        and (authority_reasons or authority_details or authority_basis)
    ):
        raise ValidationError("Kết quả chưa phân loại không được chứa căn cứ phân loại.")
    if order.status not in (
        Order.Status.CHO_KIEM_TRA,
        Order.Status.CHO_TRAO_DOI,
        Order.Status.CHO_KHACH_XAC_NHAN,
    ):
        raise InvalidOrderTransition("Không thể cập nhật kiểm tra ở trạng thái này.")
    if order.intake_handler_role == "OWNER":
        raise InvalidOrderTransition(
            "Đơn đã chuyển chủ xử lý; nhân viên không thể nhận lại hoặc đổi phân loại."
        )
    if unavailable and not owner_handoff:
        if reason not in StaffAvailabilityCheck.Reason.values or not reason:
            raise ValidationError(
                {"reason": "Chọn nguyên nhân cụ thể khi yêu cầu chưa đáp ứng."}
            )
        if reason == StaffAvailabilityCheck.Reason.OTHER and not reason_details.strip():
            raise ValidationError(
                {"reason_details": "Mô tả nguyên nhân khác là bắt buộc."}
            )
    elif not unavailable:
        reason = ""
        reason_details = ""

    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_version)
        if order.status not in (
            Order.Status.CHO_KIEM_TRA,
            Order.Status.CHO_TRAO_DOI,
            Order.Status.CHO_KHACH_XAC_NHAN,
        ):
            raise InvalidOrderTransition("Không thể cập nhật kiểm tra ở trạng thái này.")
        if order.intake_handler_role == "OWNER":
            raise InvalidOrderTransition(
                "Đơn đã chuyển chủ xử lý; nhân viên không thể nhận lại hoặc đổi phân loại."
            )
        old_status = order.status
        if IntakeQuoteApproval.objects.filter(
            order=order,
            request_revision=order.request_revision,
        ).exclude(status=IntakeQuoteApproval.Status.STALE).exists():
            raise InvalidOrderTransition(
                "Đã gửi OWNER duyệt yêu cầu này; không thể đổi phân loại để bỏ qua quyết định."
            )
        check_snapshot = _order_request_snapshot(order)
        quote_to_invalidate = None
        new_status = old_status
        if (
            materials_available is False
            or schedule_available is False
            or authority_classification
            != StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE
        ):
            if order.current_quote and order.current_quote.status in (
                Quote.Status.PENDING,
                Quote.Status.CHANGE_REQUESTED,
            ):
                quote_to_invalidate = order.current_quote
                new_status = Order.Status.CHO_KIEM_TRA
                Quote.objects.filter(
                    pk=quote_to_invalidate.pk,
                    status=quote_to_invalidate.status,
                ).update(
                    status=Quote.Status.REPLACED,
                    revision=F("revision") + 1,
                )
        invalidated_drafts = list(
            OrderAlternative.objects.filter(
                order=order,
                status=OrderAlternative.Status.DRAFT,
            )
        )
        for draft in invalidated_drafts:
            OrderAlternative.objects.filter(
                pk=draft.pk,
                status=OrderAlternative.Status.DRAFT,
                revision=draft.revision,
            ).update(
                status=OrderAlternative.Status.SUPERSEDED,
                revision=F("revision") + 1,
            )
        if owner_handoff:
            new_status = Order.Status.CHO_CHU_DUYET_BAO_GIA
        updated_count = Order.objects.filter(
            pk=order.pk,
            version=expected_version,
            status=old_status,
        ).update(
            status=new_status,
            materials_available=materials_available,
            schedule_available=schedule_available,
            current_quote=None if quote_to_invalidate else order.current_quote,
            staff_check_notes=notes.strip(),
            intake_handler_role="OWNER" if owner_handoff else "STAFF",
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
        if updated_count != 1:
            raise StaleOrderVersion(
                "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
            )
        check = StaffAvailabilityCheck.objects.create(
            order=order,
            request_revision=order.request_revision,
            materials_available=materials_available,
            schedule_available=schedule_available,
            reason=reason,
            reason_details=reason_details.strip(),
            notes=notes.strip(),
            request_snapshot=check_snapshot,
            checked_by=staff,
            authority_classification=authority_classification,
            authority_reasons=authority_reasons,
            authority_details=authority_details,
            authority_basis=authority_basis,
        )
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=staff,
            action=OrderHistory.Action.STAFF_CHECKED,
            from_status=old_status,
            to_status=new_status,
            message=notes.strip(),
            details={
                "availability_check_id": check.pk,
                "request_revision": check.request_revision,
                "materials_available": materials_available,
                "schedule_available": schedule_available,
                "reason": reason,
                "reason_details": reason_details.strip(),
                "request_snapshot": check_snapshot,
                "authority_classification": authority_classification,
                "authority_reasons": authority_reasons,
                "authority_details": authority_details,
                "authority_basis": authority_basis,
                "invalidated_quote_id": (
                    quote_to_invalidate.pk if quote_to_invalidate else None
                ),
            },
            quote=quote_to_invalidate,
        )
        for draft in invalidated_drafts:
            OrderHistory.objects.create(
                order=order,
                actor=staff,
                action=OrderHistory.Action.ALTERNATIVE_SUPERSEDED,
                from_status=old_status,
                to_status=old_status,
                message=(
                    f"Phương án nháp v{draft.version_number} hết hiệu lực "
                    "sau khi kiểm tra lại."
                ),
                details={
                    "alternative_id": draft.pk,
                    "version": draft.version_number,
                    "availability_check_id": check.pk,
                },
            )
        if owner_handoff:
            OrderHistory.objects.create(
                order=order,
                actor=staff,
                action=OrderHistory.Action.INTAKE_TRANSFERRED_TO_OWNER,
                from_status=old_status,
                to_status=order.status,
                message=authority_details,
                details={
                    "request_revision": order.request_revision,
                    "availability_check_id": check.pk,
                    "authority_reasons": authority_reasons,
                    "authority_details": authority_details,
                    "internal_note": notes,
                    "request_snapshot": check_snapshot,
                },
            )
        return order


def request_customer_supplement(*, order_id, staff, expected_version, message):
    if getattr(staff, "role", None) != "STAFF":
        raise InvalidOrderTransition("Chỉ nhân viên được yêu cầu khách bổ sung.")
    order = Order.objects.get(pk=order_id)
    if order.intake_handler_role == "OWNER":
        raise InvalidOrderTransition("Đơn đã chuyển chủ xử lý; không thể yêu cầu bổ sung thay chủ.")
    return _transition_order(
        order_id=order_id,
        actor=staff,
        expected_version=expected_version,
        allowed_statuses=(Order.Status.CHO_KIEM_TRA,),
        new_status=Order.Status.CAN_BO_SUNG,
        action=OrderHistory.Action.SUPPLEMENT_REQUESTED,
        message=message,
        updates={"needs_supplement": True},
    )


def record_owner_intake_check(
    *,
    order_id,
    owner,
    expected_order_version,
    materials_available,
    schedule_available,
    notes,
):
    if getattr(owner, "role", None) != "OWNER":
        raise InvalidOrderTransition("Chỉ chủ cửa hàng được kiểm tra đơn đã chuyển chủ.")
    if not isinstance(materials_available, bool) or not isinstance(
        schedule_available, bool
    ):
        raise ValidationError("Kết quả nguyên liệu và thời gian không hợp lệ.")
    notes = notes.strip()
    if not notes:
        raise ValidationError({"notes": "Ghi nội dung kiểm tra khả năng tiếp nhận."})
    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_order_version)
        if (
            order.intake_handler_role != "OWNER"
            or order.status
            not in (
                Order.Status.CHO_CHU_DUYET_BAO_GIA,
                Order.Status.CHO_TRAO_DOI,
            )
            or order.needs_supplement
        ):
            raise InvalidOrderTransition("Đơn hiện không chờ chủ xử lý tiếp nhận.")
        check = OwnerIntakeCheck.objects.create(
            order=order,
            request_revision=order.request_revision,
            materials_available=materials_available,
            schedule_available=schedule_available,
            notes=notes,
            checked_by=owner,
        )
        old_status = order.status
        changed = Order.objects.filter(
            pk=order.pk,
            version=expected_order_version,
            status=old_status,
            intake_handler_role="OWNER",
        ).update(version=F("version") + 1, updated_at=timezone.now())
        if changed != 1:
            raise StaleOrderVersion("Đơn vừa thay đổi; hãy tải lại trang.")
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=owner,
            action=OrderHistory.Action.OWNER_INTAKE_CHECKED,
            from_status=old_status,
            to_status=order.status,
            message=notes,
            details={
                "owner_check_id": check.pk,
                "request_revision": check.request_revision,
                "materials_available": materials_available,
                "schedule_available": schedule_available,
            },
        )
        return order, check


def _close_legacy_intake_approvals(order, *, owner, reason):
    approvals = list(
        IntakeQuoteApproval.objects.filter(
            order=order,
            status__in=(
                IntakeQuoteApproval.Status.PENDING,
                IntakeQuoteApproval.Status.APPROVED,
            ),
        )
    )
    now = timezone.now()
    for approval in approvals:
        updated = IntakeQuoteApproval.objects.filter(
            pk=approval.pk,
            status=approval.status,
            revision=approval.revision,
        ).update(
            status=IntakeQuoteApproval.Status.STALE,
            decided_by=owner,
            decision_reason=reason,
            decided_at=now,
            revision=F("revision") + 1,
        )
        if updated != 1:
            raise StaleOrderVersion(
                "Yêu cầu theo quy trình cũ vừa thay đổi; hãy tải lại đơn."
            )
        OrderHistory.objects.create(
            order=order,
            actor=owner,
            action=OrderHistory.Action.INTAKE_LEGACY_APPROVAL_CLOSED,
            from_status=order.status,
            to_status=order.status,
            message=reason,
            details={"approval_id": approval.pk, "previous_status": approval.status},
        )
    return [approval.pk for approval in approvals]


def reject_order(
    *,
    order_id,
    expected_version,
    reason,
    actor=None,
    staff=None,
    internal_note="",
):
    actor = actor or staff
    if actor is None or getattr(actor, "role", None) not in ("STAFF", "OWNER"):
        raise InvalidOrderTransition(
            "Chỉ nhân viên hoặc chủ cửa hàng được từ chối tiếp nhận."
        )
    reason = reason.strip()
    if not reason:
        raise ValidationError({"reason": "Vui lòng ghi lý do không tiếp nhận."})
    internal_note = internal_note.strip()
    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_version)
        owner_handled = order.intake_handler_role == "OWNER"
        if actor.role == "OWNER":
            owner_check = current_owner_intake_check(order)
            if (
                not owner_handled
                or order.status
                not in (Order.Status.CHO_CHU_DUYET_BAO_GIA, Order.Status.CHO_TRAO_DOI)
                or owner_check is None
            ):
                raise InvalidOrderTransition(
                    "Chủ chỉ được từ chối đơn đã chuyển đến mình và đã ghi nhận kiểm tra."
                )
            _close_legacy_intake_approvals(
                order,
                owner=actor,
                reason="Chủ cửa hàng kết thúc tiếp nhận theo quy trình mới.",
            )
        elif owner_handled or order.status not in (
            Order.Status.CHO_KIEM_TRA,
            Order.Status.CAN_BO_SUNG,
            Order.Status.CHO_TRAO_DOI,
            Order.Status.CHO_KHACH_XAC_NHAN,
        ):
            raise InvalidOrderTransition(
                "Nhân viên không thể từ chối đơn đã chuyển chủ, được xác nhận hoặc đang chờ phản hồi phương án."
            )
        old_status = order.status
        previous_quote = order.current_quote
        now = timezone.now()
        if previous_quote and previous_quote.status in (
            Quote.Status.PENDING,
            Quote.Status.CHANGE_REQUESTED,
        ):
            Quote.objects.filter(
                pk=previous_quote.pk,
                status=previous_quote.status,
            ).update(
                status=Quote.Status.REPLACED,
                revision=F("revision") + 1,
            )
        active_alternatives = list(
            OrderAlternative.objects.filter(
                order=order,
                status__in=OrderAlternative.ACTIVE_STATUSES,
            )
        )
        for alternative in active_alternatives:
            OrderAlternative.objects.filter(
                pk=alternative.pk,
                status=alternative.status,
                revision=alternative.revision,
            ).update(
                status=OrderAlternative.Status.SUPERSEDED,
                revision=F("revision") + 1,
            )
        changed = Order.objects.filter(
            pk=order.pk,
            version=expected_version,
            status=old_status,
        ).update(
            status=Order.Status.TU_CHOI,
            current_quote=None,
            needs_supplement=False,
            rejection_reason=reason,
            version=F("version") + 1,
            updated_at=now,
        )
        if changed != 1:
            raise StaleOrderVersion(
                "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
            )
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=actor,
            action=OrderHistory.Action.ORDER_REJECTED,
            from_status=old_status,
            to_status=order.status,
            message=reason,
            details={"internal_note": internal_note},
            quote=previous_quote,
        )
        for alternative in active_alternatives:
            OrderHistory.objects.create(
                order=order,
                actor=actor,
                action=OrderHistory.Action.ALTERNATIVE_SUPERSEDED,
                from_status=old_status,
                to_status=order.status,
                message=f"Phương án v{alternative.version_number} đóng cùng yêu cầu tiếp nhận.",
                details={
                    "alternative_id": alternative.pk,
                    "version": alternative.version_number,
                    "rejection_reason": reason,
                },
            )
        return order


def create_alternative_draft(
    *,
    order_id,
    staff,
    expected_order_version,
    proposed_cake_id,
    size,
    flavor,
    quantity,
    inscription="",
    decoration_request="",
    desired_delivery_at,
    difference_summary,
    estimated_unit_price=None,
    requires_owner_approval=False,
):
    allowed_statuses = (
        Order.Status.CHO_KIEM_TRA,
        Order.Status.CHO_TRAO_DOI,
        Order.Status.CHO_KHACH_XAC_NHAN,
        Order.Status.CHO_CHU_DUYET_PHUONG_AN,
        Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN,
    )
    try:
        with transaction.atomic():
            order = _load_order(order_id)
            _assert_version(order, expected_order_version)
            if order.status not in allowed_statuses or order.needs_supplement:
                raise InvalidOrderTransition(
                    "Chỉ có thể lập phương án cho đơn đang được tiếp nhận."
                )
            availability_check = current_availability_check(order)
            if (
                availability_check is None
                or not availability_check.has_availability_failure
            ):
                raise InvalidOrderTransition(
                    "Cần có kết quả kiểm tra hiện hành chưa đáp ứng trước khi lập phương án."
                )
            cake = CakeTemplate.objects.public().get(pk=proposed_cake_id)
            if not difference_summary.strip():
                raise ValidationError(
                    {"difference_summary": "Vui lòng giải thích nội dung thay đổi."}
                )
            if quantity < 1:
                raise ValidationError({"quantity": "Số lượng phải lớn hơn 0."})
            if desired_delivery_at <= timezone.now():
                raise ValidationError(
                    {"desired_delivery_at": "Thời gian giao đề xuất phải ở tương lai."}
                )
            if estimated_unit_price is not None and (
                estimated_unit_price < 0
                or estimated_unit_price != estimated_unit_price.to_integral_value()
            ):
                raise ValidationError(
                    {"estimated_unit_price": "Giá dự kiến phải là số nguyên không âm."}
                )

            old_status = order.status
            previous_active = list(
                OrderAlternative.objects.filter(
                    order=order,
                    status__in=OrderAlternative.ACTIVE_STATUSES,
                )
            )
            for alternative in previous_active:
                OrderAlternative.objects.filter(
                    pk=alternative.pk,
                    status=alternative.status,
                    revision=alternative.revision,
                ).update(
                    status=OrderAlternative.Status.SUPERSEDED,
                    revision=F("revision") + 1,
                )
            if order.current_quote and order.current_quote.status in (
                Quote.Status.PENDING,
                Quote.Status.CHANGE_REQUESTED,
            ):
                Quote.objects.filter(
                    pk=order.current_quote.pk,
                    status=order.current_quote.status,
                ).update(
                    status=Quote.Status.REPLACED,
                    revision=F("revision") + 1,
                )

            before = _order_request_snapshot(order)
            after = {
                **before,
                "cake_template_id": cake.pk,
                "cake_name_snapshot": cake.name,
                "reference_price_snapshot": _json_value(cake.reference_price),
                "size": size.strip(),
                "flavor": flavor.strip(),
                "quantity": quantity,
                "inscription": inscription.strip(),
                "decoration_request": decoration_request.strip(),
                "desired_delivery_at": _json_value(desired_delivery_at),
            }
            latest_version = (
                OrderAlternative.objects.filter(order=order)
                .order_by("-version_number")
                .values_list("version_number", flat=True)
                .first()
                or 0
            )
            changed = Order.objects.filter(
                pk=order.pk,
                version=expected_order_version,
                status=old_status,
            ).update(
                status=Order.Status.CHO_KIEM_TRA,
                current_quote=None,
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            if changed != 1:
                raise StaleOrderVersion(
                    "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
                )
            alternative = OrderAlternative.objects.create(
                order=order,
                version_number=latest_version + 1,
                base_request_revision=order.request_revision,
                availability_check=availability_check,
                created_by=staff,
                request_before=before,
                request_after=after,
                cake_template=cake,
                cake_name_snapshot=cake.name,
                reference_price_snapshot=cake.reference_price,
                size=size.strip(),
                flavor=flavor.strip(),
                quantity=quantity,
                inscription=inscription.strip(),
                decoration_request=decoration_request.strip(),
                desired_delivery_at=desired_delivery_at,
                difference_summary=difference_summary.strip(),
                estimated_unit_price=estimated_unit_price,
                requires_owner_approval=requires_owner_approval,
            )
            order.refresh_from_db()
            for previous in previous_active:
                OrderHistory.objects.create(
                    order=order,
                    actor=staff,
                    action=OrderHistory.Action.ALTERNATIVE_SUPERSEDED,
                    from_status=old_status,
                    to_status=order.status,
                    message=f"Phương án v{previous.version_number} bị thay thế.",
                    details={
                        "alternative_id": previous.pk,
                        "version": previous.version_number,
                    },
                )
            OrderHistory.objects.create(
                order=order,
                actor=staff,
                action=OrderHistory.Action.ALTERNATIVE_DRAFTED,
                from_status=old_status,
                to_status=order.status,
                message=alternative.difference_summary,
                details={
                    "alternative_id": alternative.pk,
                    "version": alternative.version_number,
                    "requires_owner_approval": requires_owner_approval,
                    "request_before": before,
                    "request_after": after,
                },
            )
            alternative.refresh_from_db()
            return order, alternative
    except CakeTemplate.DoesNotExist as exc:
        raise InvalidOrderTransition(
            "Mẫu bánh đề xuất không còn nhận đặt."
        ) from exc


def send_order_alternative(
    *,
    order_id,
    alternative_id,
    staff,
    expected_order_version,
    expected_alternative_revision,
):
    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_order_version)
        old_order_status = order.status
        alternative = OrderAlternative.objects.get(
            pk=alternative_id,
            order=order,
        )
        current_check = current_availability_check(order)
        if (
            alternative.base_request_revision != order.request_revision
            or current_check is None
            or current_check.pk != alternative.availability_check_id
            or not current_check.has_availability_failure
        ):
            raise InvalidOrderTransition(
                "Yêu cầu hoặc kết quả kiểm tra đã thay đổi; hãy lập phương án mới."
            )
        is_owner_approved = (
            alternative.status == OrderAlternative.Status.APPROVED
            and alternative.requires_owner_approval
            and order.status == Order.Status.CHO_CHU_DUYET_PHUONG_AN
        )
        if not is_owner_approved and order.status != Order.Status.CHO_KIEM_TRA:
            raise InvalidOrderTransition(
                "Đơn hàng không còn ở trạng thái có thể gửi phương án."
            )
        if alternative.status != OrderAlternative.Status.DRAFT and not is_owner_approved:
            raise InvalidOrderTransition(
                "Phương án không còn ở trạng thái có thể gửi."
            )
        if alternative.revision != expected_alternative_revision:
            raise StaleOrderVersion(
                "Phương án đã thay đổi. Hãy tải lại trang."
            )
        if alternative.requires_owner_approval and not is_owner_approved:
            next_order_status = Order.Status.CHO_CHU_DUYET_PHUONG_AN
            next_alternative_status = OrderAlternative.Status.PENDING_OWNER
            action = OrderHistory.Action.ALTERNATIVE_OWNER_SUBMITTED
            message = "Nhân viên gửi phương án vượt quyền để chủ cửa hàng duyệt."
        else:
            next_order_status = Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN
            next_alternative_status = OrderAlternative.Status.PENDING_CUSTOMER
            action = OrderHistory.Action.ALTERNATIVE_SENT
            message = "Nhân viên gửi phương án thay thế cho khách."

        updated_alternative = OrderAlternative.objects.filter(
            pk=alternative.pk,
            status=alternative.status,
            revision=expected_alternative_revision,
            base_request_revision=order.request_revision,
        ).update(
            status=next_alternative_status,
            sent_at=timezone.now()
            if next_alternative_status == OrderAlternative.Status.PENDING_CUSTOMER
            else None,
            revision=F("revision") + 1,
        )
        if updated_alternative != 1:
            raise StaleOrderVersion("Phương án vừa được cập nhật. Hãy tải lại trang.")
        changed = Order.objects.filter(
            pk=order.pk,
            version=expected_order_version,
            status=order.status,
        ).update(
            status=next_order_status,
            version=F("version") + 1,
            updated_at=timezone.now(),
        )
        if changed != 1:
            raise StaleOrderVersion(
                "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
            )
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=staff,
            action=action,
            from_status=old_order_status,
            to_status=order.status,
            message=message,
            details={
                "alternative_id": alternative.pk,
                "version": alternative.version_number,
                "request_before": alternative.request_before,
                "request_after": alternative.request_after,
                "requires_owner_approval": alternative.requires_owner_approval,
            },
        )
        alternative.refresh_from_db()
        return order, alternative


def decide_order_alternative(
    *,
    alternative_id,
    owner,
    expected_order_version,
    expected_alternative_revision,
    approve,
    reason,
):
    with transaction.atomic():
        alternative = OrderAlternative.objects.select_related("order").get(
            pk=alternative_id
        )
        order = _load_order(alternative.order_id)
        _assert_version(order, expected_order_version)
        if (
            order.status != Order.Status.CHO_CHU_DUYET_PHUONG_AN
            or alternative.status != OrderAlternative.Status.PENDING_OWNER
            or alternative.revision != expected_alternative_revision
            or not alternative.requires_owner_approval
            or alternative.base_request_revision != order.request_revision
        ):
            raise InvalidOrderTransition(
                "Phương án không còn chờ chủ duyệt hoặc đã thay đổi."
            )
        now = timezone.now()
        new_status = (
            OrderAlternative.Status.APPROVED
            if approve
            else OrderAlternative.Status.OWNER_REJECTED
        )
        changed_alternative = OrderAlternative.objects.filter(
            pk=alternative.pk,
            status=OrderAlternative.Status.PENDING_OWNER,
            revision=expected_alternative_revision,
        ).update(
            status=new_status,
            owner_decided_by=owner,
            owner_decision_reason=reason.strip(),
            owner_decided_at=now,
            revision=F("revision") + 1,
        )
        if changed_alternative != 1:
            raise StaleOrderVersion(
                "Phương án vừa được quyết định ở nơi khác."
            )
        new_order_status = (
            Order.Status.CHO_CHU_DUYET_PHUONG_AN
            if approve
            else Order.Status.CHO_KIEM_TRA
        )
        changed_order = Order.objects.filter(
            pk=order.pk,
            version=expected_order_version,
            status=Order.Status.CHO_CHU_DUYET_PHUONG_AN,
        ).update(
            status=new_order_status,
            version=F("version") + 1,
            updated_at=now,
        )
        if changed_order != 1:
            raise StaleOrderVersion(
                "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
            )
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=owner,
            action=OrderHistory.Action.ALTERNATIVE_OWNER_DECIDED,
            from_status=Order.Status.CHO_CHU_DUYET_PHUONG_AN,
            to_status=order.status,
            message=reason.strip(),
            details={
                "alternative_id": alternative.pk,
                "version": alternative.version_number,
                "decision": "approved" if approve else "rejected",
                "request_before": alternative.request_before,
                "request_after": alternative.request_after,
            },
        )
        alternative.refresh_from_db()
        return order, alternative


def respond_order_alternative(
    *,
    order_id,
    alternative_id,
    customer,
    expected_order_version,
    expected_alternative_revision,
    accept,
    response="",
):
    with transaction.atomic():
        order = Order.objects.get(pk=order_id, customer=customer)
        _assert_version(order, expected_order_version)
        alternative = OrderAlternative.objects.get(
            pk=alternative_id,
            order=order,
        )
        latest = order.alternatives.order_by("-version_number").first()
        if (
            order.status != Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN
            or alternative.status != OrderAlternative.Status.PENDING_CUSTOMER
            or alternative.revision != expected_alternative_revision
            or alternative.base_request_revision != order.request_revision
            or latest is None
            or latest.pk != alternative.pk
        ):
            raise InvalidOrderTransition(
                "Phương án không còn hiện hành. Hãy tải lại đơn hàng."
            )
        try:
            cake = CakeTemplate.objects.public().get(pk=alternative.cake_template_id)
        except CakeTemplate.DoesNotExist as exc:
            raise InvalidOrderTransition(
                "Mẫu bánh trong phương án hiện không còn nhận đặt."
            ) from exc

        now = timezone.now()
        status = (
            OrderAlternative.Status.ACCEPTED
            if accept
            else OrderAlternative.Status.DECLINED
        )
        new_order_status = (
            Order.Status.CHO_KIEM_TRA if accept else Order.Status.TU_CHOI
        )
        changed_alternative = OrderAlternative.objects.filter(
            pk=alternative.pk,
            order=order,
            status=OrderAlternative.Status.PENDING_CUSTOMER,
            revision=expected_alternative_revision,
            base_request_revision=order.request_revision,
        ).update(
            status=status,
            customer_response=response.strip(),
            customer_responded_at=now,
            revision=F("revision") + 1,
        )
        if changed_alternative != 1:
            raise StaleOrderVersion(
                "Phương án vừa được phản hồi ở nơi khác."
            )

        old_status = order.status
        order_updates = {
            "status": new_order_status,
            "version": F("version") + 1,
            "updated_at": now,
        }
        history_action = OrderHistory.Action.ALTERNATIVE_CUSTOMER_DECLINED
        history_message = response.strip() or "Khách từ chối phương án thay thế."
        if accept:
            history_action = OrderHistory.Action.ALTERNATIVE_CUSTOMER_ACCEPTED
            history_message = response.strip() or "Khách đồng ý phương án thay thế."
            after = alternative.request_after
            try:
                proposed_delivery_at = datetime.fromisoformat(
                    after["desired_delivery_at"]
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidOrderTransition(
                    "Snapshot phương án không hợp lệ; cần nhân viên kiểm tra."
                ) from exc
            order_updates.update(
                cake_template=cake,
                cake_name_snapshot=cake.name,
                reference_price_snapshot=cake.reference_price,
                size=alternative.size,
                flavor=alternative.flavor,
                quantity=alternative.quantity,
                inscription=alternative.inscription,
                decoration_request=alternative.decoration_request,
                desired_delivery_at=proposed_delivery_at,
                request_revision=F("request_revision") + 1,
                materials_available=None,
                schedule_available=None,
                staff_check_notes="",
                needs_supplement=False,
                current_quote=None,
            )
        changed_order = Order.objects.filter(
            pk=order.pk,
            customer=customer,
            status=old_status,
            version=expected_order_version,
        ).update(**order_updates)
        if changed_order != 1:
            raise StaleOrderVersion(
                "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
            )
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=customer,
            action=history_action,
            from_status=old_status,
            to_status=order.status,
            message=history_message,
            details={
                "alternative_id": alternative.pk,
                "version": alternative.version_number,
                "request_before": alternative.request_before,
                "request_after": alternative.request_after,
                "accepted_request_revision": (
                    order.request_revision if accept else None
                ),
                "order_confirmed_event_created": False,
            },
        )
        alternative.refresh_from_db()
        return order, alternative


def _validate_quote_terms(
    *,
    unit_price,
    delivery_fee,
    proposed_delivery_at,
    expires_at,
):
    now = timezone.now()
    if unit_price < 0 or delivery_fee < 0:
        raise ValidationError("Đơn giá và phí giao không được âm.")
    if (
        unit_price != unit_price.to_integral_value()
        or delivery_fee != delivery_fee.to_integral_value()
    ):
        raise ValidationError("Giá phải là số nguyên VNĐ.")
    if proposed_delivery_at <= now:
        raise ValidationError("Thời gian giao đề xuất phải ở tương lai.")
    if expires_at <= now or expires_at >= proposed_delivery_at:
        raise ValidationError("Hạn chấp nhận phải ở tương lai và trước giờ giao.")


def submit_intake_quote_approval(
    *,
    order_id,
    staff,
    expected_version,
    unit_price,
    delivery_fee,
    proposed_delivery_at,
    expires_at,
    notes="",
    revision_reason="",
):
    raise InvalidOrderTransition(
        "Quy trình mới chuyển nguyên đơn cho OWNER; không gửi báo giá chờ duyệt."
    )
    try:
        with transaction.atomic():
            order = _load_order(order_id)
            _assert_version(order, expected_version)
            if order.status not in (
                Order.Status.CHO_KIEM_TRA,
                Order.Status.CHO_TRAO_DOI,
                Order.Status.CHO_KHACH_XAC_NHAN,
            ):
                raise InvalidOrderTransition(
                    "Đơn hàng không còn ở giai đoạn gửi chủ duyệt báo giá."
                )
            if order.needs_supplement:
                raise InvalidOrderTransition(
                    "Không thể gửi chủ duyệt khi đơn còn chờ khách bổ sung."
                )
            check = current_availability_check(order)
            if (
                check is None
                or not check.is_fully_available
                or order.materials_available is not True
                or order.schedule_available is not True
            ):
                raise InvalidOrderTransition(
                    "Trước khi xin duyệt báo giá cần kiểm tra xác nhận nguyên liệu và lịch đáp ứng."
                )
            if (
                check.authority_classification
                != StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED
            ):
                raise InvalidOrderTransition(
                    "Chỉ đơn được phân loại vượt quyền mới gửi OWNER duyệt."
                )
            if not check.authority_reasons or not check.authority_details.strip():
                raise InvalidOrderTransition(
                    "Phân loại vượt quyền phải có lý do và mô tả cụ thể."
                )
            if check.request_revision != order.request_revision:
                raise StaleOrderVersion(
                    "Kết quả phân loại không còn khớp yêu cầu hiện hành."
                )
            active_approval = IntakeQuoteApproval.objects.filter(
                order=order,
                status__in=(
                    IntakeQuoteApproval.Status.PENDING,
                    IntakeQuoteApproval.Status.APPROVED,
                ),
            ).order_by("-created_at", "-pk").first()
            if (
                active_approval
                and active_approval.status == IntakeQuoteApproval.Status.PENDING
            ):
                raise InvalidOrderTransition(
                    "Đơn đã có yêu cầu duyệt đang chờ OWNER."
                )
            old_quote = order.current_quote
            if old_quote and old_quote.status == Quote.Status.PENDING:
                if old_quote.expires_at > timezone.now():
                    raise InvalidOrderTransition(
                        "Báo giá hiện hành còn hạn; không thể thay bằng yêu cầu duyệt mới."
                    )
            if old_quote and not revision_reason.strip():
                raise ValidationError(
                    {"revision_reason": "Vui lòng ghi lý do tạo báo giá phiên bản mới."}
                )
            terms = _quote_terms_snapshot(
                unit_price=unit_price,
                delivery_fee=delivery_fee,
                quantity=order.quantity,
                proposed_delivery_at=proposed_delivery_at,
                expires_at=expires_at,
                notes=notes,
                revision_reason=revision_reason,
            )
            request_snapshot = _commercial_request_snapshot(order)
            if active_approval and (
                active_approval.request_revision == order.request_revision
                and active_approval.availability_check_id == check.pk
                and active_approval.request_snapshot == request_snapshot
                and active_approval.quote_snapshot == terms
            ):
                raise InvalidOrderTransition(
                    "Điều khoản này đã được OWNER duyệt; hãy gửi báo giá trực tiếp."
                )
            if active_approval:
                updated_approval = IntakeQuoteApproval.objects.filter(
                    pk=active_approval.pk,
                    status=IntakeQuoteApproval.Status.APPROVED,
                    revision=active_approval.revision,
                ).update(
                    status=IntakeQuoteApproval.Status.STALE,
                    revision=F("revision") + 1,
                )
                if updated_approval != 1:
                    raise StaleOrderVersion(
                        "Phê duyệt trước vừa thay đổi; hãy tải lại."
                    )
            old_status = order.status
            if old_quote and old_quote.status in (
                Quote.Status.PENDING,
                Quote.Status.CHANGE_REQUESTED,
            ):
                changed_quote = Quote.objects.filter(
                    pk=old_quote.pk,
                    status=old_quote.status,
                    revision=old_quote.revision,
                ).update(
                    status=Quote.Status.REPLACED,
                    revision=F("revision") + 1,
                )
                if changed_quote != 1:
                    raise StaleOrderVersion("Báo giá vừa thay đổi; hãy tải lại.")
            approval = IntakeQuoteApproval.objects.create(
                order=order,
                availability_check=check,
                request_revision=order.request_revision,
                request_snapshot=request_snapshot,
                authority_reasons=check.authority_reasons,
                authority_details=check.authority_details,
                quote_snapshot=terms,
                submitted_by=staff,
            )
            changed_order = Order.objects.filter(
                pk=order.pk,
                version=expected_version,
                status=old_status,
            ).update(
                status=Order.Status.CHO_CHU_DUYET_BAO_GIA,
                current_quote=None,
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
            if changed_order != 1:
                raise StaleOrderVersion(
                    "Đơn hàng vừa thay đổi ở nơi khác. Hãy tải lại."
                )
            order.refresh_from_db()
            OrderHistory.objects.create(
                order=order,
                actor=staff,
                action=OrderHistory.Action.INTAKE_QUOTE_APPROVAL_SUBMITTED,
                from_status=old_status,
                to_status=order.status,
                details={
                    "approval_id": approval.pk,
                    "request_revision": approval.request_revision,
                    "availability_check_id": check.pk,
                    "authority_reasons": approval.authority_reasons,
                    "authority_details": approval.authority_details,
                    "request_snapshot": approval.request_snapshot,
                    "quote_snapshot": terms,
                },
            )
            return order, approval
    except IntegrityError as exc:
        raise StaleOrderVersion(
            "Một yêu cầu duyệt khác vừa được tạo cho đơn này. Hãy tải lại."
        ) from exc


def decide_intake_quote_approval(
    *,
    approval_id,
    owner,
    expected_order_version,
    expected_approval_revision,
    approve,
    reason,
):
    raise InvalidOrderTransition(
        "Yêu cầu theo quy trình cũ không được duyệt để STAFF gửi báo giá; xử lý trực tiếp tại đơn."
    )
    with transaction.atomic():
        approval = IntakeQuoteApproval.objects.select_related("order").get(
            pk=approval_id
        )
        order = _load_order(approval.order_id)
        _assert_version(order, expected_order_version)
        current_check = current_availability_check(order)
        if (
            approval.status != IntakeQuoteApproval.Status.PENDING
            or approval.revision != expected_approval_revision
            or order.status != Order.Status.CHO_CHU_DUYET_BAO_GIA
            or approval.request_revision != order.request_revision
            or current_check is None
            or current_check.pk != approval.availability_check_id
            or current_check.authority_classification
            != StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED
        ):
            raise InvalidOrderTransition(
                "Yêu cầu duyệt đã được xử lý hoặc không còn khớp đơn hiện hành."
            )
        now = timezone.now()
        new_approval_status = (
            IntakeQuoteApproval.Status.APPROVED
            if approve
            else IntakeQuoteApproval.Status.REJECTED
        )
        changed_approval = IntakeQuoteApproval.objects.filter(
            pk=approval.pk,
            status=IntakeQuoteApproval.Status.PENDING,
            revision=expected_approval_revision,
        ).update(
            status=new_approval_status,
            decided_by=owner,
            decision_reason=reason,
            decided_at=now,
            revision=F("revision") + 1,
        )
        if changed_approval != 1:
            raise StaleOrderVersion("Yêu cầu duyệt vừa được quyết định.")
        new_order_status = (
            Order.Status.CHO_KIEM_TRA if approve else Order.Status.TU_CHOI
        )
        changed_order = Order.objects.filter(
            pk=order.pk,
            version=expected_order_version,
            status=Order.Status.CHO_CHU_DUYET_BAO_GIA,
        ).update(
            status=new_order_status,
            version=F("version") + 1,
            updated_at=now,
        )
        if changed_order != 1:
            raise StaleOrderVersion("Đơn hàng vừa thay đổi ở nơi khác.")
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=owner,
            action=OrderHistory.Action.INTAKE_QUOTE_APPROVAL_DECIDED,
            from_status=Order.Status.CHO_CHU_DUYET_BAO_GIA,
            to_status=order.status,
            details={
                "approval_id": approval.pk,
                "decision": "approved" if approve else "rejected",
                "decision_reason": reason,
                "request_revision": approval.request_revision,
                "quote_snapshot": approval.quote_snapshot,
                "order_rejected": not approve,
            },
        )
        approval.refresh_from_db()
        return order, approval


def notify_customer_intake_rejection(
    *,
    order_id,
    approval_id,
    staff,
    expected_order_version,
    expected_approval_revision,
    message,
):
    raise InvalidOrderTransition(
        "OWNER từ chối sẽ ghi lý do công khai trực tiếp; không cần STAFF thông báo lại."
    )
    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_order_version)
        approval = IntakeQuoteApproval.objects.get(
            pk=approval_id,
            order=order,
        )
        if (
            order.status != Order.Status.TU_CHOI
            or approval.status != IntakeQuoteApproval.Status.REJECTED
            or approval.revision != expected_approval_revision
        ):
            raise InvalidOrderTransition(
                "Chỉ thông báo được yêu cầu chủ đã từ chối và chưa xử lý."
            )
        if approval.customer_notified_at is not None:
            raise InvalidOrderTransition("Khách đã được thông báo; không gửi lặp.")
        now = timezone.now()
        changed_approval = IntakeQuoteApproval.objects.filter(
            pk=approval.pk,
            status=IntakeQuoteApproval.Status.REJECTED,
            revision=expected_approval_revision,
            customer_notified_at__isnull=True,
        ).update(
            customer_notice=message,
            customer_notified_by=staff,
            customer_notified_at=now,
            revision=F("revision") + 1,
        )
        changed_order = Order.objects.filter(
            pk=order.pk,
            version=expected_order_version,
            status=Order.Status.TU_CHOI,
        ).update(
            version=F("version") + 1,
            updated_at=now,
        )
        if changed_approval != 1 or changed_order != 1:
            raise StaleOrderVersion("Thông báo vừa được ghi nhận ở nơi khác.")
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=staff,
            action=OrderHistory.Action.INTAKE_OWNER_REJECTION_NOTIFIED,
            from_status=Order.Status.TU_CHOI,
            to_status=Order.Status.TU_CHOI,
            message=message,
            details={"approval_id": approval.pk},
        )
        approval.refresh_from_db()
        return order, approval


def send_quote(
    *,
    order_id,
    expected_version,
    unit_price,
    delivery_fee,
    proposed_delivery_at,
    expires_at,
    notes="",
    revision_reason="",
    actor=None,
    staff=None,
):
    actor = actor or staff
    actor_role = getattr(actor, "role", None)
    if actor_role not in ("STAFF", "OWNER"):
        raise InvalidOrderTransition("Chỉ nhân viên hoặc chủ cửa hàng được gửi báo giá.")
    proposed_delivery_at = _normalize_quote_datetime(proposed_delivery_at)
    expires_at = _normalize_quote_datetime(expires_at)
    _validate_quote_terms(
        unit_price=unit_price,
        delivery_fee=delivery_fee,
        proposed_delivery_at=proposed_delivery_at,
        expires_at=expires_at,
    )
    now = timezone.now()

    with transaction.atomic():
        order = _load_order(order_id)
        _assert_version(order, expected_version)
        if order.needs_supplement:
            raise InvalidOrderTransition(
                "Không thể gửi báo giá khi còn yêu cầu bổ sung chưa giải quyết."
            )
        if actor_role == "STAFF":
            if (
                order.intake_handler_role != "STAFF"
                or order.status
                not in (
                    Order.Status.CHO_KIEM_TRA,
                    Order.Status.CHO_TRAO_DOI,
                    Order.Status.CHO_KHACH_XAC_NHAN,
                )
            ):
                raise InvalidOrderTransition(
                    "Nhân viên không được gửi báo giá cho đơn đã chuyển chủ hoặc ngoài giai đoạn tiếp nhận."
                )
            availability_check = current_availability_check(order)
            if (
                availability_check is None
                or availability_check.request_revision != order.request_revision
                or not availability_check.is_fully_available
                or order.materials_available is not True
                or order.schedule_available is not True
            ):
                raise InvalidOrderTransition(
                    "Cần kết quả kiểm tra hiện hành xác nhận nguyên liệu và lịch làm đáp ứng."
                )
            if (
                availability_check.authority_classification
                != StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE
                or not availability_check.authority_basis.strip()
                or availability_check.authority_reasons
            ):
                raise InvalidOrderTransition(
                    "Chỉ STAFF được gửi báo giá khi đơn hiện hành đã được phân loại trong quyền."
                )
            if IntakeQuoteApproval.objects.filter(
                order=order,
                request_revision=order.request_revision,
                status__in=(
                    IntakeQuoteApproval.Status.PENDING,
                    IntakeQuoteApproval.Status.APPROVED,
                ),
            ).exists():
                raise InvalidOrderTransition(
                    "Đơn đã chuyển OWNER xử lý; nhân viên không thể gửi báo giá."
                )
        else:
            owner_check = current_owner_intake_check(order)
            owner_statuses = (
                Order.Status.CHO_CHU_DUYET_BAO_GIA,
                Order.Status.CHO_TRAO_DOI,
                Order.Status.CHO_KHACH_XAC_NHAN,
            )
            if (
                order.intake_handler_role != "OWNER"
                or order.status not in owner_statuses
                or owner_check is None
                or not owner_check.is_fully_available
            ):
                raise InvalidOrderTransition(
                    "Chủ chỉ được báo giá đơn đã chuyển đến mình sau khi kiểm tra đủ nguyên liệu và thời gian."
                )
            if (
                owner_check.request_revision != order.request_revision
                or owner_check.materials_available is not True
                or owner_check.schedule_available is not True
            ):
                raise InvalidOrderTransition(
                    "Kết quả kiểm tra của chủ không còn khớp yêu cầu hiện hành."
                )

        old_quote = order.current_quote
        if order.status == Order.Status.CHO_KHACH_XAC_NHAN:
            if (
                old_quote is None
                or old_quote.status != Quote.Status.PENDING
                or old_quote.expires_at > now
            ):
                raise InvalidOrderTransition(
                    "Báo giá hiện hành vẫn chờ phản hồi; chưa thể gửi bản mới."
                )
        if old_quote and old_quote.status == Quote.Status.PENDING:
            if old_quote.expires_at > now:
                raise InvalidOrderTransition(
                    "Báo giá hiện hành vẫn chờ phản hồi; chưa thể gửi bản mới."
                )
        if old_quote and not revision_reason.strip():
            raise ValidationError(
                {"revision_reason": "Vui lòng ghi lý do tạo báo giá phiên bản mới."}
            )

        latest_version = (
            Quote.objects.filter(order=order).order_by("-version_number")
            .values_list("version_number", flat=True)
            .first()
            or 0
        )
        if old_quote and old_quote.status in (
            Quote.Status.PENDING,
            Quote.Status.CHANGE_REQUESTED,
        ):
            updated_quote = Quote.objects.filter(
                pk=old_quote.pk,
                status=old_quote.status,
                revision=old_quote.revision,
            ).update(
                status=Quote.Status.REPLACED,
                revision=F("revision") + 1,
            )
            if updated_quote != 1:
                raise StaleOrderVersion(
                    "Báo giá vừa thay đổi. Hãy tải lại đơn hàng."
                )
        cake_total = unit_price * order.quantity
        total = cake_total + delivery_fee
        quote = Quote.objects.create(
            order=order,
            version_number=latest_version + 1,
            created_by=actor,
            cake_name_snapshot=order.cake_name_snapshot,
            size_snapshot=order.size,
            flavor_snapshot=order.flavor,
            quantity_snapshot=order.quantity,
            inscription_snapshot=order.inscription,
            decoration_snapshot=order.decoration_request,
            recipient_name_snapshot=order.recipient_name,
            recipient_phone_snapshot=order.recipient_phone,
            delivery_address_snapshot=order.delivery_address,
            unit_price=unit_price,
            cake_total=cake_total,
            delivery_fee=delivery_fee,
            total=total,
            proposed_delivery_at=proposed_delivery_at,
            expires_at=expires_at,
            notes=notes.strip(),
            revision_reason=revision_reason.strip(),
        )
        old_status = order.status
        if actor_role == "OWNER":
            _close_legacy_intake_approvals(
                order,
                owner=actor,
                reason="Đã chuyển sang quy trình chủ cửa hàng trực tiếp xử lý đơn.",
            )
        changed = Order.objects.filter(
            pk=order.pk,
            version=expected_version,
            status=old_status,
        ).update(
            status=Order.Status.CHO_KHACH_XAC_NHAN,
            current_quote=quote,
            version=F("version") + 1,
            updated_at=now,
        )
        if changed != 1:
            raise StaleOrderVersion(
                "Đơn hàng vừa được cập nhật ở nơi khác. Hãy tải lại trang."
            )
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=actor,
            action=(
                OrderHistory.Action.QUOTE_REVISED
                if latest_version
                else OrderHistory.Action.QUOTE_SENT
            ),
            from_status=old_status,
            to_status=order.status,
            message=revision_reason.strip()
            or ("Chủ cửa hàng gửi báo giá." if actor_role == "OWNER" else "Nhân viên gửi báo giá."),
            details={
                "version": quote.version_number,
                "unit_price": str(unit_price),
                "cake_total": str(cake_total),
                "delivery_fee": str(delivery_fee),
                "total": str(total),
                "proposed_delivery_at": proposed_delivery_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
            quote=quote,
        )
        return order, quote


def _verify_current_quote(order, quote_id, quote_revision, expected_order_version):
    _assert_version(order, expected_order_version)
    quote = Quote.objects.get(pk=quote_id, order=order)
    latest = order.quotes.order_by("-version_number").first()
    if (
        order.status != Order.Status.CHO_KHACH_XAC_NHAN
        or order.current_quote_id != quote.pk
        or quote.status != Quote.Status.PENDING
        or quote.revision != quote_revision
        or latest is None
        or latest.pk != quote.pk
    ):
        raise InvalidOrderTransition(
            "Báo giá không còn hiện hành. Hãy tải lại đơn hàng để xem phiên bản mới nhất."
        )
    if quote.expires_at <= timezone.now():
        raise InvalidOrderTransition(
            "Báo giá đã hết hạn; vui lòng liên hệ cửa hàng để được cập nhật."
        )
    return quote


def accept_quote(
    *,
    order_id,
    customer,
    quote_id,
    quote_revision,
    expected_order_version,
):
    with transaction.atomic():
        order = Order.objects.get(pk=order_id, customer=customer)
        quote = _verify_current_quote(
            order,
            quote_id,
            quote_revision,
            expected_order_version,
        )
        now = timezone.now()
        quote_changed = Quote.objects.filter(
            pk=quote.pk,
            order=order,
            status=Quote.Status.PENDING,
            revision=quote_revision,
            expires_at__gt=now,
        ).update(
            status=Quote.Status.ACCEPTED,
            responded_at=now,
            revision=F("revision") + 1,
        )
        if quote_changed != 1:
            raise StaleOrderVersion("Báo giá vừa thay đổi; hãy tải lại đơn hàng.")
        order_changed = Order.objects.filter(
            pk=order.pk,
            customer=customer,
            status=Order.Status.CHO_KHACH_XAC_NHAN,
            current_quote=quote,
            version=expected_order_version,
        ).update(
            status=Order.Status.DA_XAC_NHAN,
            version=F("version") + 1,
            updated_at=now,
        )
        if order_changed != 1:
            raise StaleOrderVersion("Đơn hàng vừa thay đổi; hãy tải lại trước khi xác nhận.")
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=customer,
            action=OrderHistory.Action.QUOTE_ACCEPTED,
            from_status=Order.Status.CHO_KHACH_XAC_NHAN,
            to_status=Order.Status.DA_XAC_NHAN,
            message="Khách hàng đồng ý báo giá hiện hành.",
            quote=quote,
        )
        from .workflow_events import create_workflow_event

        create_workflow_event(
            order=order,
            event_type="order.confirmed",
            business_key=f"quote-accepted:{quote.pk}",
            quote_version=quote.version_number,
        )
        return order


def request_quote_change(
    *,
    order_id,
    customer,
    quote_id,
    quote_revision,
    expected_order_version,
    message,
):
    with transaction.atomic():
        order = Order.objects.get(pk=order_id, customer=customer)
        quote = _verify_current_quote(
            order,
            quote_id,
            quote_revision,
            expected_order_version,
        )
        now = timezone.now()
        changed_quote = Quote.objects.filter(
            pk=quote.pk,
            status=Quote.Status.PENDING,
            revision=quote_revision,
            expires_at__gt=now,
        ).update(
            status=Quote.Status.CHANGE_REQUESTED,
            responded_at=now,
            customer_response=message,
            revision=F("revision") + 1,
        )
        if changed_quote != 1:
            raise StaleOrderVersion("Báo giá vừa thay đổi; hãy tải lại đơn hàng.")
        changed_order = Order.objects.filter(
            pk=order.pk,
            status=Order.Status.CHO_KHACH_XAC_NHAN,
            current_quote=quote,
            version=expected_order_version,
        ).update(
            status=Order.Status.CHO_TRAO_DOI,
            intake_handler_role=quote.created_by.role,
            version=F("version") + 1,
            updated_at=now,
        )
        if changed_order != 1:
            raise StaleOrderVersion("Đơn hàng vừa thay đổi; hãy tải lại trang.")
        order.refresh_from_db()
        OrderHistory.objects.create(
            order=order,
            actor=customer,
            action=OrderHistory.Action.CHANGE_REQUESTED,
            from_status=Order.Status.CHO_KHACH_XAC_NHAN,
            to_status=Order.Status.CHO_TRAO_DOI,
            message=message,
            quote=quote,
        )
        return order
