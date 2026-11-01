from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from .models import (
    Order,
    OrderAlternative,
    OrderHistory,
    IntakeQuoteApproval,
    Quote,
    StaffAvailabilityCheck,
    WorkflowEvent,
)
from .services import (
    InvalidOrderTransition,
    StaleOrderVersion,
    create_alternative_draft,
    current_availability_check,
    decide_order_alternative,
    record_staff_check,
    reject_order,
    request_customer_supplement,
    respond_order_alternative,
    send_order_alternative,
    update_customer_request,
)
from .tests import OrderWorkflowTestCase


class IntakeAlternativeWorkflowTests(OrderWorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.chocolate = type(self.cake).objects.create(
            category=self.category,
            name="Bánh kem chocolate",
            reference_price=Decimal("390000"),
        )

    def check_unavailable(self, order, *, reason=StaffAvailabilityCheck.Reason.INGREDIENT):
        return record_staff_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            materials_available=False,
            schedule_available=True,
            notes="Không đủ nguyên liệu dâu cho ngày khách yêu cầu.",
            reason=reason,
            reason_details="Nhà cung cấp chưa giao đủ dâu.",
            authority_classification=StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
            authority_basis="Yêu cầu theo mẫu chuẩn; thiếu nguyên liệu là vấn đề khả năng đáp ứng.",
        )

    def make_draft(self, order, *, requires_owner_approval=False, **overrides):
        values = {
            "proposed_cake_id": self.chocolate.pk,
            "size": "22 cm",
            "flavor": "Chocolate",
            "quantity": 2,
            "inscription": "Chúc mừng",
            "decoration_request": "Trang trí chocolate",
            "desired_delivery_at": timezone.now() + timedelta(days=5),
            "difference_summary": "Đổi vị dâu sang chocolate do thiếu dâu.",
            "estimated_unit_price": Decimal("390000"),
            "requires_owner_approval": requires_owner_approval,
        }
        values.update(overrides)
        return create_alternative_draft(
            order_id=order.pk,
            staff=self.staff,
            expected_order_version=order.version,
            **values,
        )

    def send_to_customer(self, order, alternative):
        return send_order_alternative(
            order_id=order.pk,
            alternative_id=alternative.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_alternative_revision=alternative.revision,
        )

    def respond(self, order, alternative, *, accept, response=""):
        return respond_order_alternative(
            order_id=order.pk,
            alternative_id=alternative.pk,
            customer=self.customer,
            expected_order_version=order.version,
            expected_alternative_revision=alternative.revision,
            accept=accept,
            response=response,
        )

    def test_staff_check_records_failure_reason_actor_time_and_request_snapshot(self):
        order = self.create_order()
        order = self.check_unavailable(order)
        check = current_availability_check(order)

        self.assertIsNotNone(check)
        self.assertFalse(check.is_fully_available)
        self.assertEqual(check.reason, StaffAvailabilityCheck.Reason.INGREDIENT)
        self.assertEqual(check.checked_by, self.staff)
        self.assertIsNotNone(check.checked_at)
        self.assertEqual(check.request_snapshot["flavor"], "Dâu tươi")
        self.assertEqual(check.request_revision, order.request_revision)
        history = order.history.get(action=OrderHistory.Action.STAFF_CHECKED)
        self.assertEqual(history.details["reason"], "INGREDIENT")

    def test_schedule_failure_and_other_reason_require_specific_details(self):
        order = self.create_order()
        order = record_staff_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            materials_available=True,
            schedule_available=False,
            notes="Lịch làm đã kín.",
            reason=StaffAvailabilityCheck.Reason.DELIVERY,
            authority_classification=StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
            authority_basis="Yêu cầu theo mẫu chuẩn; lịch kín là khả năng đáp ứng.",
        )
        self.assertFalse(current_availability_check(order).is_fully_available)
        with self.assertRaises(ValidationError):
            record_staff_check(
                order_id=order.pk,
                staff=self.staff,
                expected_version=order.version,
                materials_available=False,
                schedule_available=True,
                notes="Có vấn đề khác.",
                reason=StaffAvailabilityCheck.Reason.OTHER,
            )

    def test_quote_is_blocked_without_a_current_successful_check(self):
        order = self.create_order()
        with self.assertRaisesMessage(InvalidOrderTransition, "kiểm tra hiện hành"):
            self.quote(order)
        order = self.check_unavailable(order)
        with self.assertRaisesMessage(InvalidOrderTransition, "kiểm tra hiện hành"):
            self.quote(order)

    def test_staff_creates_and_sends_a_full_in_scope_alternative(self):
        order = self.check_unavailable(self.create_order())
        original_request = {
            "cake_name_snapshot": order.cake_name_snapshot,
            "flavor": order.flavor,
            "desired_delivery_at": order.desired_delivery_at.isoformat(),
        }
        order, alternative = self.make_draft(order)

        self.assertEqual(alternative.status, OrderAlternative.Status.DRAFT)
        self.assertEqual(alternative.request_before["flavor"], "Dâu tươi")
        self.assertEqual(alternative.request_after["flavor"], "Chocolate")
        self.assertEqual(alternative.request_before["recipient_phone"], order.recipient_phone)
        self.assertEqual(alternative.difference_summary, "Đổi vị dâu sang chocolate do thiếu dâu.")
        self.assertEqual(order.status, Order.Status.CHO_KIEM_TRA)
        self.assertEqual(order.flavor, "Dâu tươi")

        order, alternative = self.send_to_customer(order, alternative)
        self.assertEqual(order.status, Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN)
        self.assertEqual(alternative.status, OrderAlternative.Status.PENDING_CUSTOMER)
        self.assertEqual(IntakeQuoteApproval.objects.filter(order=order).count(), 0)
        self.assertEqual(alternative.request_before["cake_name_snapshot"], original_request["cake_name_snapshot"])
        self.assertEqual(order.flavor, "Dâu tươi")
        self.assertEqual(order.history.filter(action=OrderHistory.Action.ALTERNATIVE_SENT).count(), 1)

    def test_owner_approval_is_required_before_customer_send_and_owner_does_not_send(self):
        order = self.check_unavailable(self.create_order())
        order, alternative = self.make_draft(order, requires_owner_approval=True)
        order, alternative = self.send_to_customer(order, alternative)

        self.assertEqual(order.status, Order.Status.CHO_CHU_DUYET_PHUONG_AN)
        self.assertEqual(alternative.status, OrderAlternative.Status.PENDING_OWNER)
        with self.assertRaises(InvalidOrderTransition):
            self.respond(order, alternative, accept=True)

        order, alternative = decide_order_alternative(
            alternative_id=alternative.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_alternative_revision=alternative.revision,
            approve=True,
            reason="Phạm vi phương án được chấp thuận.",
        )
        self.assertEqual(order.status, Order.Status.CHO_CHU_DUYET_PHUONG_AN)
        self.assertEqual(alternative.status, OrderAlternative.Status.APPROVED)
        self.client.force_login(self.owner)
        owner_send = self.client.post(
            reverse(
                "orders:staff_send_alternative",
                kwargs={"pk": order.pk, "alternative_id": alternative.pk},
            ),
            {
                "expected_order_version": order.version,
                "expected_alternative_revision": alternative.revision,
            },
        )
        self.assertEqual(owner_send.status_code, 403)

        self.client.logout()
        order, alternative = self.send_to_customer(order, alternative)
        self.assertEqual(order.status, Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN)
        self.assertEqual(alternative.status, OrderAlternative.Status.PENDING_CUSTOMER)
        self.assertNotEqual(order.status, Order.Status.DA_XAC_NHAN)

    def test_customer_acceptance_applies_request_and_requires_a_new_check(self):
        order = self.check_unavailable(self.create_order())
        order, alternative = self.make_draft(order)
        order, alternative = self.send_to_customer(order, alternative)
        old_revision = order.request_revision
        order, alternative = self.respond(
            order,
            alternative,
            accept=True,
            response="Tôi đồng ý đổi sang chocolate.",
        )

        self.assertEqual(order.status, Order.Status.CHO_KIEM_TRA)
        self.assertEqual(order.request_revision, old_revision + 1)
        self.assertEqual(order.cake_template_id, self.chocolate.pk)
        self.assertEqual(order.cake_name_snapshot, self.chocolate.name)
        self.assertEqual(order.flavor, "Chocolate")
        self.assertEqual(order.size, "22 cm")
        self.assertIsNone(order.materials_available)
        self.assertIsNone(current_availability_check(order))
        self.assertEqual(alternative.status, OrderAlternative.Status.ACCEPTED)
        self.assertEqual(alternative.customer_response, "Tôi đồng ý đổi sang chocolate.")
        self.assertEqual(order.history.filter(action=OrderHistory.Action.ALTERNATIVE_CUSTOMER_ACCEPTED).count(), 1)
        self.assertFalse(
            WorkflowEvent.objects.filter(
                order=order,
                event_type=WorkflowEvent.EventType.ORDER_CONFIRMED,
            ).exists()
        )
        with self.assertRaisesMessage(InvalidOrderTransition, "kiểm tra hiện hành"):
            self.quote(order)
        order = record_staff_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            materials_available=True,
            schedule_available=True,
            notes="Đã xác nhận nguyên liệu và lịch làm có thể đáp ứng.",
            authority_classification=StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
            authority_basis="Đối chiếu mẫu và quy định hiện hành.",
        )
        order, quote = self.quote(order)
        from .services import accept_quote

        order = accept_quote(
            order_id=order.pk,
            customer=self.customer,
            quote_id=quote.pk,
            quote_revision=quote.revision,
            expected_order_version=order.version,
        )
        self.assertEqual(order.status, Order.Status.DA_XAC_NHAN)

    def test_customer_decline_marks_not_accepted_and_preserves_original_request(self):
        order = self.check_unavailable(self.create_order())
        original_flavor = order.flavor
        order, alternative = self.make_draft(order)
        order, alternative = self.send_to_customer(order, alternative)
        order, alternative = self.respond(
            order,
            alternative,
            accept=False,
            response="Tôi muốn giữ vị dâu.",
        )

        self.assertEqual(order.status, Order.Status.TU_CHOI)
        self.assertEqual(order.flavor, original_flavor)
        self.assertEqual(alternative.status, OrderAlternative.Status.DECLINED)
        self.assertEqual(alternative.customer_response, "Tôi muốn giữ vị dâu.")
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertEqual(order.history.filter(action=OrderHistory.Action.ALTERNATIVE_CUSTOMER_DECLINED).count(), 1)

    def test_customer_cannot_access_another_orders_alternative(self):
        order = self.check_unavailable(self.create_order())
        order, alternative = self.make_draft(order)
        order, alternative = self.send_to_customer(order, alternative)
        self.client.force_login(self.other_customer)

        response = self.client.post(
            reverse(
                "orders:customer_respond_alternative",
                kwargs={"pk": order.pk, "alternative_id": alternative.pk},
            ),
            {},
        )
        self.assertEqual(response.status_code, 404)

    def test_old_proposal_is_stale_after_staff_replaces_it(self):
        order = self.check_unavailable(self.create_order())
        order, first = self.make_draft(order)
        order, first = self.send_to_customer(order, first)
        order, second = self.make_draft(order, flavor="Vani")

        first.refresh_from_db()
        self.assertEqual(first.status, OrderAlternative.Status.SUPERSEDED)
        self.assertEqual(second.version_number, 2)
        self.assertEqual(
            OrderAlternative.objects.filter(
                order=order,
                status__in=OrderAlternative.ACTIVE_STATUSES,
            ).count(),
            1,
        )
        with self.assertRaises(InvalidOrderTransition):
            respond_order_alternative(
                order_id=order.pk,
                alternative_id=first.pk,
                customer=self.customer,
                expected_order_version=order.version,
                expected_alternative_revision=first.revision,
                accept=True,
            )

    def test_rechecking_invalidates_draft_and_quote_from_old_revision(self):
        order = self.check_unavailable(self.create_order())
        order, draft = self.make_draft(order)
        order = record_staff_check(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            materials_available=True,
            schedule_available=True,
            notes="Đã tìm được nguyên liệu thay thế phù hợp.",
            authority_classification=StaffAvailabilityCheck.AuthorityClassification.IN_SCOPE,
            authority_basis="Đối chiếu yêu cầu sau cập nhật với mẫu chuẩn.",
        )
        draft.refresh_from_db()
        self.assertEqual(draft.status, OrderAlternative.Status.SUPERSEDED)
        self.assertTrue(current_availability_check(order).is_fully_available)
        self.assertEqual(self.quote(order)[0].status, Order.Status.CHO_KHACH_XAC_NHAN)

    def test_changing_customer_request_invalidates_old_availability_check(self):
        order = self.check_unavailable(self.create_order())
        order = request_customer_supplement(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            message="Vui lòng xác nhận lại kích thước.",
        )
        order = update_customer_request(
            order_id=order.pk,
            customer=self.customer,
            expected_version=order.version,
            data=self.order_data(size="24 cm"),
        )
        self.assertEqual(order.request_revision, 2)
        self.assertIsNone(current_availability_check(order))
        with self.assertRaises(InvalidOrderTransition):
            self.quote(order)

    def test_starting_alternative_invalidates_pending_quote_and_stale_acceptance(self):
        order = self.check_order(self.create_order())
        order, quote = self.quote(order)
        order = self.check_unavailable(order)
        quote.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CHO_KIEM_TRA)
        self.assertIsNone(order.current_quote_id)
        self.assertEqual(quote.status, Quote.Status.REPLACED)
        order, _alternative = self.make_draft(order)

        self.assertEqual(quote.status, Quote.Status.REPLACED)
        self.assertIsNone(order.current_quote_id)
        with self.assertRaises(InvalidOrderTransition):
            from .services import accept_quote

            accept_quote(
                order_id=order.pk,
                customer=self.customer,
                quote_id=quote.pk,
                quote_revision=quote.revision,
                expected_order_version=order.version,
            )

    def test_owner_rejection_returns_to_staff_without_exposing_option_to_customer(self):
        order = self.check_unavailable(self.create_order())
        order, alternative = self.make_draft(order, requires_owner_approval=True)
        order, alternative = self.send_to_customer(order, alternative)
        order, alternative = decide_order_alternative(
            alternative_id=alternative.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_alternative_revision=alternative.revision,
            approve=False,
            reason="Phương án chưa phù hợp với khả năng cửa hàng.",
        )

        self.assertEqual(order.status, Order.Status.CHO_KIEM_TRA)
        self.assertEqual(alternative.status, OrderAlternative.Status.OWNER_REJECTED)
        self.client.force_login(self.customer)
        detail = self.client.get(reverse("orders:order_detail", kwargs={"pk": order.pk}))
        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Phương án thay thế v1 cần bạn phản hồi")

    def test_staff_can_end_intake_without_a_feasible_plan_and_keep_draft_history(self):
        order = self.check_unavailable(self.create_order())
        order, alternative = self.make_draft(order)
        order = reject_order(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            reason="Cửa hàng không có phương án khả thi.",
        )
        alternative.refresh_from_db()

        self.assertEqual(order.status, Order.Status.TU_CHOI)
        self.assertEqual(alternative.status, OrderAlternative.Status.SUPERSEDED)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())
        self.assertTrue(
            order.history.filter(
                action=OrderHistory.Action.ORDER_REJECTED,
                message="Cửa hàng không có phương án khả thi.",
            ).exists()
        )

    def test_confirmed_order_cannot_use_intake_alternatives(self):
        order = self.check_order(self.create_order())
        order, _quote = self.quote(order)
        from .services import accept_quote

        order = accept_quote(
            order_id=order.pk,
            customer=self.customer,
            quote_id=order.current_quote_id,
            quote_revision=order.current_quote.revision,
            expected_order_version=order.version,
        )
        with self.assertRaises(InvalidOrderTransition):
            self.make_draft(order)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.DA_XAC_NHAN)

    def test_alternative_content_is_immutable_after_creation(self):
        order = self.check_unavailable(self.create_order())
        _order, alternative = self.make_draft(order)
        alternative.flavor = "Đã sửa lén"
        with self.assertRaisesMessage(ValueError, "bất biến"):
            alternative.save()

    def test_staff_owner_customer_views_are_role_protected_and_get_is_read_only(self):
        order = self.check_unavailable(self.create_order())
        order, alternative = self.make_draft(order, requires_owner_approval=True)
        order, alternative = self.send_to_customer(order, alternative)
        queue_url = reverse("orders:owner_approval_list")
        detail_url = reverse(
            "orders:owner_intake_alternative_detail",
            kwargs={"alternative_id": alternative.pk},
        )
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(queue_url).status_code, 200)
        self.assertContains(self.client.get(queue_url), "Phương án thay thế tại giai đoạn tiếp nhận")
        self.assertEqual(self.client.get(detail_url).status_code, 200)
        self.assertEqual(alternative.revision, OrderAlternative.objects.get(pk=alternative.pk).revision)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        denied = csrf_client.post(
            reverse(
                "orders:owner_decide_intake_alternative",
                kwargs={"alternative_id": alternative.pk},
            ),
            {
                "expected_order_version": order.version,
                "expected_alternative_revision": alternative.revision,
                "decision": "approve",
                "reason": "Duyệt phương án.",
            },
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(detail_url).status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("orders:staff_order_detail", kwargs={"pk": order.pk})
            ).status_code,
            403,
        )

    def test_customer_response_requires_csrf_and_is_not_duplicated(self):
        order = self.check_unavailable(self.create_order())
        order, alternative = self.make_draft(order)
        order, alternative = self.send_to_customer(order, alternative)
        url = reverse(
            "orders:customer_respond_alternative",
            kwargs={"pk": order.pk, "alternative_id": alternative.pk},
        )
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.customer)
        denied = csrf_client.post(
            url,
            {
                "expected_order_version": order.version,
                "expected_alternative_revision": alternative.revision,
                "decision": "accept",
            },
        )
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.customer)
        accepted = self.client.post(
            url,
            {
                "expected_order_version": order.version,
                "expected_alternative_revision": alternative.revision,
                "decision": "accept",
            },
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.ALTERNATIVE_CUSTOMER_ACCEPTED,
            ).count(),
            1,
        )
        self.assertEqual(OrderAlternative.objects.get(pk=alternative.pk).status, OrderAlternative.Status.ACCEPTED)
        with self.assertRaises(StaleOrderVersion):
            respond_order_alternative(
                order_id=order.pk,
                alternative_id=alternative.pk,
                customer=self.customer,
                expected_order_version=order.version,
                expected_alternative_revision=alternative.revision,
                accept=True,
            )

    def test_staff_customer_http_happy_path_shows_preview_and_requires_customer_post(self):
        order = self.create_order()
        self.client.force_login(self.staff)
        check_url = reverse("orders:staff_check_order", kwargs={"pk": order.pk})
        checked = self.client.post(
            check_url,
            {
                "expected_version": order.version,
                "materials_available": "no",
                "schedule_available": "yes",
                "reason": StaffAvailabilityCheck.Reason.INGREDIENT,
                "reason_details": "Dâu chưa được giao.",
                "notes": "Không đủ nguyên liệu dâu.",
                "authority_classification": "IN_SCOPE",
                "authority_basis": "Yêu cầu theo mẫu chuẩn; thiếu nguyên liệu là vấn đề khả năng đáp ứng.",
            },
        )
        self.assertEqual(checked.status_code, 302)
        order.refresh_from_db()
        proposal_url = reverse("orders:staff_create_alternative", kwargs={"pk": order.pk})
        created = self.client.post(
            proposal_url,
            {
                "expected_order_version": order.version,
                "proposed_cake": self.chocolate.pk,
                "size": "22 cm",
                "flavor": "Chocolate",
                "quantity": "2",
                "inscription": "",
                "decoration_request": "Trang trí chocolate",
                "desired_delivery_at": (
                    timezone.now() + timedelta(days=5)
                ).strftime("%Y-%m-%dT%H:%M"),
                "difference_summary": "Đổi vị dâu sang chocolate.",
                "estimated_unit_price": "390000",
            },
        )
        self.assertEqual(created.status_code, 302)
        order.refresh_from_db()
        alternative = OrderAlternative.objects.get(order=order)
        preview = self.client.get(
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Đổi vị dâu sang chocolate.")
        sent = self.client.post(
            reverse(
                "orders:staff_send_alternative",
                kwargs={"pk": order.pk, "alternative_id": alternative.pk},
            ),
            {
                "expected_order_version": order.version,
                "expected_alternative_revision": alternative.revision,
            },
        )
        self.assertEqual(sent.status_code, 302)
        order.refresh_from_db()
        alternative.refresh_from_db()
        self.client.force_login(self.customer)
        customer_detail = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(customer_detail.status_code, 200)
        self.assertContains(customer_detail, "Đồng ý phương án")
        self.assertContains(customer_detail, "390.000")
        self.assertEqual(order.status, Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN)
        self.assertEqual(alternative.status, OrderAlternative.Status.PENDING_CUSTOMER)
        self.assertEqual(WorkflowEvent.objects.filter(order=order).count(), 0)

    def test_db_constraint_allows_only_one_active_alternative_per_order(self):
        order = self.check_unavailable(self.create_order())
        order, _alternative = self.make_draft(order)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrderAlternative.objects.create(
                    order=order,
                    version_number=2,
                    base_request_revision=order.request_revision,
                    availability_check=current_availability_check(order),
                    created_by=self.staff,
                    request_before={},
                    request_after={},
                    cake_template=self.chocolate,
                    cake_name_snapshot=self.chocolate.name,
                    reference_price_snapshot=self.chocolate.reference_price,
                    size="20 cm",
                    flavor="Chocolate",
                    quantity=1,
                    desired_delivery_at=timezone.now() + timedelta(days=5),
                    difference_summary="Phương án trùng.",
                )
