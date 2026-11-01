from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from .models import (
    IntakeQuoteApproval,
    Order,
    OrderHistory,
    OwnerIntakeCheck,
    Quote,
    StaffAvailabilityCheck,
)
from .services import (
    InvalidOrderTransition,
    StaleOrderVersion,
    accept_quote,
    claim_legacy_intake_order,
    current_availability_check,
    current_owner_intake_check,
    record_owner_intake_check,
    record_staff_check,
    reject_order,
    request_customer_supplement,
    send_quote,
    submit_intake_quote_approval,
)
from .tests import OrderWorkflowTestCase


class IntakeClassificationTests(OrderWorkflowTestCase):
    def check_staff(
        self,
        order,
        *,
        classification=StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
        materials=True,
        schedule=True,
        reasons=None,
        details="",
        basis="Đối chiếu mẫu và quy định hiện hành của cửa hàng.",
        reason="",
        reason_details="",
    ):
        return record_staff_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            materials_available=materials,
            schedule_available=schedule,
            notes="Đã kiểm tra khả năng tiếp nhận.",
            reason=reason,
            reason_details=reason_details,
            authority_classification=classification,
            authority_reasons=reasons or [],
            authority_details=details,
            authority_basis=basis,
        )

    def quote_terms(self):
        return {
            "unit_price": Decimal("410000"),
            "delivery_fee": Decimal("30000"),
            "proposed_delivery_at": timezone.now() + timedelta(days=6),
            "expires_at": timezone.now() + timedelta(days=3),
            "notes": "Báo giá theo yêu cầu hiện hành.",
            "revision_reason": "",
        }

    def send_staff_quote(self, order):
        return send_quote(
            order_id=order.pk,
            actor=self.staff,
            expected_version=order.version,
            **self.quote_terms(),
        )

    def transfer_to_owner(self, order):
        return self.check_staff(
            order,
            classification=StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED,
            reasons=[StaffAvailabilityCheck.AuthorityReason.DESIGN],
            details="Kết cấu theo thiết kế riêng cần chủ xem xét.",
        )

    def owner_check(self, order, *, materials=True, schedule=True):
        return record_owner_intake_check(
            order_id=order.pk,
            owner=self.owner,
            expected_order_version=order.version,
            materials_available=materials,
            schedule_available=schedule,
            notes="Chủ đã kiểm tra nguyên liệu và lịch làm/giao.",
        )

    def test_normal_order_is_quoted_by_staff_without_owner_approval(self):
        order = self.check_staff(self.create_order())

        order, quote = self.send_staff_quote(order)

        self.assertEqual(order.status, Order.Status.CHO_KHACH_XAC_NHAN)
        self.assertEqual(quote.created_by, self.staff)
        self.assertEqual(quote.status, Quote.Status.PENDING)
        self.assertFalse(IntakeQuoteApproval.objects.filter(order=order).exists())
        self.assertFalse(OwnerIntakeCheck.objects.filter(order=order).exists())

        order = accept_quote(
            order_id=order.pk,
            customer=self.customer,
            quote_id=quote.pk,
            quote_revision=quote.revision,
            expected_order_version=order.version,
        )
        self.assertEqual(order.status, Order.Status.DA_XAC_NHAN)

    def test_unclassified_or_incomplete_classification_cannot_send_quote(self):
        unclassified = self.check_staff(
            self.create_order(),
            classification=StaffAvailabilityCheck.AuthorityClassification.UNCLASSIFIED,
            basis="",
        )
        with self.assertRaises(InvalidOrderTransition):
            self.send_staff_quote(unclassified)

        needs_info = self.check_staff(
            self.create_order(),
            classification=StaffAvailabilityCheck.AuthorityClassification.NEEDS_INFO,
            details="Cần khách xác nhận kiểu trang trí riêng.",
            basis="",
        )
        self.assertIsNone(current_availability_check(needs_info).materials_available)
        self.assertIsNone(current_availability_check(needs_info).schedule_available)
        with self.assertRaises(InvalidOrderTransition):
            self.send_staff_quote(needs_info)
        needs_info = request_customer_supplement(
            order_id=needs_info.pk,
            staff=self.staff,
            expected_version=needs_info.version,
            message="Vui lòng xác nhận kiểu trang trí riêng.",
        )
        self.assertEqual(needs_info.status, Order.Status.CAN_BO_SUNG)

    def test_owner_classification_requires_reason_and_specific_description(self):
        order = self.create_order()
        with self.assertRaises(ValidationError):
            record_staff_check(
                order_id=order.pk,
                staff=self.staff,
                expected_version=order.version,
                materials_available=True,
                schedule_available=True,
                notes="",
                authority_classification=StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED,
            )
        self.assertFalse(StaffAvailabilityCheck.objects.filter(order=order).exists())

        with self.assertRaises(ValidationError):
            self.check_staff(
                order,
                classification=StaffAvailabilityCheck.AuthorityClassification.OWNER_REQUIRED,
                reasons=[],
                details="",
            )
        self.assertFalse(StaffAvailabilityCheck.objects.filter(order=order).exists())

    def test_owner_handoff_is_not_a_staff_availability_check_or_quote_approval(self):
        order = self.transfer_to_owner(self.create_order())
        check = current_availability_check(order)

        self.assertEqual(order.status, Order.Status.CHO_CHU_DUYET_BAO_GIA)
        self.assertEqual(order.intake_handler_role, "OWNER")
        self.assertIsNone(check.materials_available)
        self.assertIsNone(check.schedule_available)
        self.assertEqual(check.authority_reasons, ["DESIGN"])
        self.assertFalse(IntakeQuoteApproval.objects.filter(order=order).exists())
        with self.assertRaises(InvalidOrderTransition):
            self.send_staff_quote(order)
        with self.assertRaises(InvalidOrderTransition):
            self.check_staff(order)

    def test_owner_checks_and_sends_quote_directly_then_customer_confirms(self):
        order = self.transfer_to_owner(self.create_order())
        order, owner_check = self.owner_check(order)

        self.assertTrue(owner_check.is_fully_available)
        self.assertEqual(current_owner_intake_check(order), owner_check)
        with self.assertRaises(InvalidOrderTransition):
            self.send_staff_quote(order)

        order, quote = send_quote(
            order_id=order.pk,
            actor=self.owner,
            expected_version=order.version,
            **self.quote_terms(),
        )
        self.assertEqual(quote.created_by, self.owner)
        self.assertEqual(order.status, Order.Status.CHO_KHACH_XAC_NHAN)
        self.assertFalse(IntakeQuoteApproval.objects.filter(order=order).exists())

        order = accept_quote(
            order_id=order.pk,
            customer=self.customer,
            quote_id=quote.pk,
            quote_revision=quote.revision,
            expected_order_version=order.version,
        )
        self.assertEqual(order.status, Order.Status.DA_XAC_NHAN)
        self.assertEqual(
            order.history.filter(action=OrderHistory.Action.QUOTE_ACCEPTED).count(),
            1,
        )

    def test_owner_rejects_after_check_and_customer_sees_public_reason(self):
        order = self.transfer_to_owner(self.create_order())
        order, owner_check = self.owner_check(order, materials=False)

        rejected = reject_order(
            order_id=order.pk,
            actor=self.owner,
            expected_version=order.version,
            reason="Cửa hàng chưa thể đáp ứng nguyên liệu theo lịch yêu cầu.",
            internal_note="Nhà cung cấp chưa giao đủ nguyên liệu.",
        )

        self.assertEqual(rejected.status, Order.Status.TU_CHOI)
        self.assertEqual(
            rejected.rejection_reason,
            "Cửa hàng chưa thể đáp ứng nguyên liệu theo lịch yêu cầu.",
        )
        self.assertEqual(
            rejected.history.filter(action=OrderHistory.Action.ORDER_REJECTED).count(),
            1,
        )
        with self.assertRaises(InvalidOrderTransition):
            self.owner_check(rejected)
        with self.assertRaises(InvalidOrderTransition):
            send_quote(
                order_id=rejected.pk,
                actor=self.owner,
                expected_version=rejected.version,
                **self.quote_terms(),
            )

        self.client.force_login(self.customer)
        response = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": rejected.pk})
        )
        self.assertContains(response, rejected.rejection_reason)
        self.assertNotContains(response, "Nhà cung cấp chưa giao đủ nguyên liệu")

    def test_owner_rejection_redirects_to_queue_after_order_leaves_it(self):
        order = self.transfer_to_owner(self.create_order())
        order, _check = self.owner_check(order, materials=False)
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("orders:owner_reject_intake_order", kwargs={"pk": order.pk}),
            {
                "expected_order_version": order.version,
                "reason": "Cửa hàng chưa thể đáp ứng theo thời gian yêu cầu.",
                "internal_note": "",
                "confirm_rejection": "on",
            },
        )

        self.assertRedirects(response, reverse("orders:owner_intake_order_list"))
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.TU_CHOI)

    def test_staff_shortage_does_not_transfer_order_to_owner(self):
        order = self.check_staff(
            self.create_order(),
            materials=False,
            reason=StaffAvailabilityCheck.Reason.INGREDIENT,
            reason_details="Nhà cung cấp chưa giao đủ dâu.",
        )

        self.assertEqual(order.intake_handler_role, "STAFF")
        self.assertEqual(order.status, Order.Status.CHO_KIEM_TRA)
        self.assertEqual(current_availability_check(order).reason, "INGREDIENT")
        self.assertFalse(IntakeQuoteApproval.objects.filter(order=order).exists())
        with self.assertRaises(InvalidOrderTransition):
            self.send_staff_quote(order)

    def test_owner_must_claim_legacy_waiting_order_before_processing(self):
        order = self.create_order()
        Order.objects.filter(pk=order.pk).update(
            status=Order.Status.CHO_CHU_DUYET_BAO_GIA
        )
        order.refresh_from_db()

        claimed = claim_legacy_intake_order(
            order_id=order.pk,
            owner=self.owner,
            expected_order_version=order.version,
        )

        self.assertEqual(claimed.intake_handler_role, "OWNER")
        self.assertEqual(claimed.status, Order.Status.CHO_CHU_DUYET_BAO_GIA)
        self.assertTrue(
            claimed.history.filter(
                action=OrderHistory.Action.INTAKE_TRANSFERRED_TO_OWNER
            ).exists()
        )
        with self.assertRaises(StaleOrderVersion):
            claim_legacy_intake_order(
                order_id=order.pk,
                owner=self.owner,
                expected_order_version=order.version - 1,
            )

    def test_owner_queue_and_detail_render_without_get_side_effects(self):
        order = self.transfer_to_owner(self.create_order())
        version_before = order.version
        history_before = order.history.count()
        checks_before = order.owner_intake_checks.count()

        self.client.force_login(self.owner)
        queue = self.client.get(reverse("orders:owner_intake_order_list"))
        detail = self.client.get(
            reverse("orders:owner_intake_order_detail", kwargs={"pk": order.pk})
        )

        self.assertEqual(queue.status_code, 200)
        self.assertTemplateUsed(queue, "orders/owner_intake_order_list.html")
        self.assertContains(queue, order.code)
        self.assertEqual(detail.status_code, 200)
        self.assertTemplateUsed(detail, "orders/owner_intake_order_detail.html")
        self.assertContains(detail, "Kiểm tra khả năng tiếp nhận")
        order.refresh_from_db()
        self.assertEqual(order.version, version_before)
        self.assertEqual(order.history.count(), history_before)
        self.assertEqual(order.owner_intake_checks.count(), checks_before)

        check_url = reverse("orders:owner_check_intake_order", kwargs={"pk": order.pk})
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(
            reverse("orders:owner_intake_order_detail", kwargs={"pk": order.pk})
        ).status_code, 403)
        self.assertEqual(self.client.post(check_url, {}).status_code, 403)

    def test_legacy_quote_approval_endpoints_cannot_create_or_decide_approvals(self):
        order = self.create_order()
        order = self.transfer_to_owner(order)
        with self.assertRaisesMessage(InvalidOrderTransition, "không gửi báo giá chờ duyệt"):
            submit_intake_quote_approval(
                order_id=order.pk,
                staff=self.staff,
                expected_version=order.version,
                **self.quote_terms(),
            )
        self.assertFalse(IntakeQuoteApproval.objects.filter(order=order).exists())

        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("orders:staff_submit_intake_quote_approval", kwargs={"pk": order.pk}),
            {},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(IntakeQuoteApproval.objects.filter(order=order).exists())

    def test_classification_requires_staff_role_and_post_csrf(self):
        order = self.create_order()
        url = reverse("orders:staff_check_order", kwargs={"pk": order.pk})
        payload = {
            "expected_version": order.version,
            "materials_available": "yes",
            "schedule_available": "yes",
            "notes": "Đã kiểm tra.",
            "authority_classification": "IN_SCOPE",
            "authority_basis": "Theo mẫu và chính sách hiện hành.",
            "owner_approved": "yes",
        }

        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(url, payload).status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        csrf_client.get(reverse("orders:staff_order_detail", kwargs={"pk": order.pk}))
        self.assertEqual(csrf_client.post(url, payload).status_code, 403)

        self.client.force_login(self.staff)
        detail = self.client.get(reverse("orders:staff_order_detail", kwargs={"pk": order.pk}))
        self.assertEqual(detail.status_code, 200)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.intake_handler_role, "STAFF")
        self.assertEqual(current_availability_check(order).authority_classification, "IN_SCOPE")
