import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from .forms import PaymentForm, RefundForm
from .delivery import (
    customer_accept_incident_plan,
    customer_confirm_delivery,
    decide_incident,
    execute_incident_resolution,
    fail_delivery,
    order_financials,
    propose_incident_resolution,
    record_payment,
    record_refund,
    report_prepayment,
    report_delivery_incident,
    schedule_redelivery,
    staff_confirm_delivery,
    start_delivery,
)
from .models import (
    DeliveryAttempt,
    Order,
    OrderHistory,
    OrderIncident,
    Payment,
    Refund,
    RevisionRequest,
)
from .services import InvalidOrderTransition, StaleOrderVersion
from .production import accept_review
from .test_production import ProductionWorkflowTestCase, image_upload


class DeliveryWorkflowTests(ProductionWorkflowTestCase):
    def test_payment_and_refund_amount_inputs_accept_integer_vnd(self):
        payment_form = PaymentForm()
        refund_form = RefundForm()
        for form in (payment_form, refund_form):
            html = form["amount"].as_widget()
            self.assertIn('min="1"', html)
            self.assertIn('step="1"', html)

        valid_payment = PaymentForm(
            data={
                "expected_order_version": 1,
                "idempotency_key": str(uuid.uuid4()),
                "method": Payment.Method.CASH,
                "amount": "15",
                "received_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "transaction_reference": "",
                "notes": "",
            }
        )
        self.assertTrue(valid_payment.is_valid(), valid_payment.errors)
        fractional_payment = PaymentForm(
            data={
                "expected_order_version": 1,
                "idempotency_key": str(uuid.uuid4()),
                "method": Payment.Method.CASH,
                "amount": "15.01",
                "received_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "transaction_reference": "",
                "notes": "",
            }
        )
        self.assertFalse(fractional_payment.is_valid())

    def test_customer_prepayment_report_is_visible_but_does_not_record_payment(self):
        order = self.accepted_order()
        self.client.force_login(self.customer)
        url = reverse("orders:customer_report_prepayment", kwargs={"pk": order.pk})
        detail_url = reverse("orders:order_detail", kwargs={"pk": order.pk})
        initial_detail = self.client.get(detail_url)
        self.assertEqual(initial_detail.status_code, 200)
        self.assertContains(initial_detail, "Tôi đã chuyển khoản trước")

        response = self.client.post(
            url,
            {"expected_order_version": order.version},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], detail_url)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DA_XAC_NHAN)
        self.assertEqual(order.payment_status, Order.PaymentStatus.UNPAID)
        self.assertEqual(order.payments.count(), 0)
        self.assertEqual(
            order.history.filter(
                action=OrderHistory.Action.CUSTOMER_PREPAYMENT_REPORTED
            ).count(),
            1,
        )

        customer_detail = self.client.get(detail_url)
        self.assertContains(customer_detail, "Bạn đã báo chuyển khoản trước")
        self.client.force_login(self.staff)
        staff_detail = self.client.get(
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk})
        )
        self.assertContains(staff_detail, "Khách đã báo chuyển khoản trước")

        self.client.force_login(self.customer)
        repeated = self.client.post(
            url,
            {"expected_order_version": order.version},
        )
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(repeated["Location"], detail_url)
        self.assertEqual(
            order.history.filter(
                action=OrderHistory.Action.CUSTOMER_PREPAYMENT_REPORTED
            ).count(),
            1,
        )

    def test_prepayment_report_requires_customer_ownership_post_and_csrf(self):
        order = self.accepted_order()
        url = reverse("orders:customer_report_prepayment", kwargs={"pk": order.pk})
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 405)

        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.customer)
        self.assertEqual(
            csrf_client.post(
                url,
                {"expected_order_version": order.version},
            ).status_code,
            403,
        )
        self.assertFalse(
            order.history.filter(
                action=OrderHistory.Action.CUSTOMER_PREPAYMENT_REPORTED
            ).exists()
        )

        self.client.force_login(self.other_customer)
        self.assertEqual(
            self.client.post(
                url,
                {"expected_order_version": order.version},
            ).status_code,
            404,
        )
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(
                url,
                {"expected_order_version": order.version},
            ).status_code,
            403,
        )

    def test_prepayment_report_needs_accepted_quote_and_does_not_replace_verified_payment(self):
        order = self.new_order()
        with self.assertRaises(InvalidOrderTransition):
            report_prepayment(
                order_id=order.pk,
                customer=self.customer,
                expected_order_version=order.version,
            )

        order = self.ready_order()
        order, created = report_prepayment(
            order_id=order.pk,
            customer=self.customer,
            expected_order_version=order.version,
        )
        self.assertTrue(created)
        self.assertEqual(order.payments.count(), 0)

        order, attempt = self.start_attempt(order)
        order, _attempt = staff_confirm_delivery(
            order_id=order.pk,
            staff=self.staff,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            expected_attempt_revision=attempt.revision,
            note="Đã giao bánh thành công.",
        )
        self.assertEqual(order.status, Order.Status.DA_GIAO_CHO_THANH_TOAN)

        order, payment, created = self.record_cash(order, "825000")
        self.assertTrue(created)
        self.assertEqual(payment.confirmed_by, self.staff)
        self.assertEqual(order.status, Order.Status.HOAN_TAT)

    def ready_order(self):
        order = self.start_order()
        order, _check = self.check_quality(order)
        order, review = self.make_review(order)
        order = accept_review(
            order_id=order.pk,
            customer=self.customer,
            review_id=review.pk,
            expected_order_version=order.version,
            expected_review_revision=review.revision,
        )
        self.assertEqual(order.status, Order.Status.SAN_SANG_GIAO)
        return order

    def start_attempt(self, order):
        return start_delivery(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
        )

    def confirm_by_customer(self, order, attempt):
        return customer_confirm_delivery(
            order_id=order.pk,
            customer=self.customer,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            expected_attempt_revision=attempt.revision,
        )

    def record_cash(self, order, amount, *, key=None):
        return record_payment(
            order_id=order.pk,
            staff=self.staff,
            expected_order_version=order.version,
            method=Payment.Method.CASH,
            amount=Decimal(amount),
            received_at=timezone.now(),
            transaction_reference="",
            notes="Đã nhận tiền mặt.",
            idempotency_key=key or uuid.uuid4(),
        )

    def report_customer_issue(self, order, attempt, *, photo=None):
        return report_delivery_incident(
            order_id=order.pk,
            reporter=self.customer,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            expected_attempt_revision=attempt.revision,
            incident_type=OrderIncident.Type.DAMAGED,
            description="Bánh bị hư hỏng khi nhận.",
            photo=photo,
        )

    def test_delivery_requires_ready_status_and_current_customer_accepted_review(self):
        order = self.accepted_order()
        with self.assertRaises(InvalidOrderTransition):
            self.start_attempt(order)

        order = self.start_order()
        order, _check = self.check_quality(order)
        order, _review = self.make_review(order)
        with self.assertRaises(InvalidOrderTransition):
            self.start_attempt(order)

        ready = self.ready_order()
        _, attempt = self.start_attempt(ready)
        self.assertEqual(attempt.result, DeliveryAttempt.Result.IN_PROGRESS)
        self.assertEqual(Order.objects.get(pk=ready.pk).status, Order.Status.DANG_GIAO)

    def test_delivery_blocks_open_revision_and_stale_order_or_attempt(self):
        order = self.ready_order()
        RevisionRequest.objects.create(
            order=order,
            review_version=order.current_review,
            customer=self.customer,
            message="Yêu cầu chỉnh sửa còn mở.",
        )
        with self.assertRaises(InvalidOrderTransition):
            self.start_attempt(order)

        order = self.ready_order()
        _, attempt = self.start_attempt(order)
        with self.assertRaises(StaleOrderVersion):
            customer_confirm_delivery(
                order_id=order.pk,
                customer=self.customer,
                attempt_id=attempt.pk,
                expected_order_version=order.version - 1,
                expected_attempt_revision=attempt.revision,
            )

    def test_only_one_active_delivery_and_failed_attempt_is_preserved_on_retry(self):
        order = self.ready_order()
        order, first = self.start_attempt(order)
        with self.assertRaises(InvalidOrderTransition):
            self.start_attempt(order)

        order, first = fail_delivery(
            order_id=order.pk,
            staff=self.staff,
            attempt_id=first.pk,
            expected_order_version=order.version,
            reason="Không liên hệ được người nhận.",
        )
        self.assertEqual(order.status, Order.Status.GIAO_THAT_BAI)
        self.assertEqual(first.result, DeliveryAttempt.Result.FAILED)
        order = schedule_redelivery(
            order_id=order.pk,
            staff=self.staff,
            expected_order_version=order.version,
            scheduled_at=timezone.now() + timedelta(days=1),
            customer_agreement="Khách đồng ý giao ngày mai.",
            reason="Đã thống nhất thời gian mới.",
        )
        order, second = self.start_attempt(order)
        self.assertEqual(second.attempt_number, 2)
        self.assertEqual(
            DeliveryAttempt.objects.get(pk=first.pk).result,
            DeliveryAttempt.Result.FAILED,
        )
        self.assertEqual(DeliveryAttempt.objects.filter(order=order).count(), 2)
        with self.assertRaises(StaleOrderVersion):
            report_delivery_incident(
                order_id=order.pk,
                reporter=self.staff,
                attempt_id=first.pk,
                expected_order_version=order.version,
                expected_attempt_revision=first.revision,
                incident_type=OrderIncident.Type.UNREACHABLE,
                description="Cố gắng báo sự cố cho lần giao cũ.",
            )

    def test_staff_handoff_records_staff_source_and_payment_is_independent(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        order, attempt = staff_confirm_delivery(
            order_id=order.pk,
            staff=self.staff,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            expected_attempt_revision=attempt.revision,
            note="Đã bàn giao trực tiếp cho người nhận.",
            proof=image_upload("delivery.png"),
        )
        self.assertEqual(attempt.confirmation_source, DeliveryAttempt.ConfirmationSource.STAFF)
        self.assertEqual(order.status, Order.Status.DA_GIAO_CHO_THANH_TOAN)
        evidence_url = reverse(
            "orders:private_order_evidence",
            kwargs={"evidence_type": "delivery", "evidence_id": attempt.pk},
        )
        self.client.force_login(self.other_customer)
        self.assertEqual(self.client.get(evidence_url).status_code, 403)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(evidence_url).status_code, 200)

        order, _payment, created = self.record_cash(order, "825000")
        self.assertTrue(created)
        self.assertEqual(order.status, Order.Status.HOAN_TAT)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)

    def test_full_payment_before_delivery_does_not_complete_until_delivery(self):
        order = self.ready_order()
        order, _payment, _created = self.record_cash(order, "825000")
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        self.assertEqual(order.status, Order.Status.SAN_SANG_GIAO)

        order, attempt = self.start_attempt(order)
        order, _attempt = self.confirm_by_customer(order, attempt)
        self.assertEqual(order.status, Order.Status.HOAN_TAT)

    def test_partial_or_invalid_payment_is_rejected_and_finances_use_decimal(self):
        order = self.ready_order()
        self.assertEqual(order_financials(order)["total"], Decimal("825000"))
        with self.assertRaises(ValidationError):
            self.record_cash(order, "0")
        with self.assertRaises(ValidationError):
            self.record_cash(order, "-1")
        with self.assertRaises(ValidationError):
            self.record_cash(order, "825001")
        with self.assertRaises(ValidationError):
            self.record_cash(order, "1.5")
        with self.assertRaises(ValidationError):
            record_payment(
                order_id=order.pk,
                staff=self.staff,
                expected_order_version=order.version,
                method=Payment.Method.BANK_TRANSFER,
                amount=Decimal("100"),
                received_at=timezone.now(),
                transaction_reference="",
                notes="Khách gửi ảnh chuyển khoản.",
                idempotency_key=uuid.uuid4(),
            )
        order, _payment, _created = self.record_cash(order, "125000")
        self.assertEqual(order.payment_status, Order.PaymentStatus.PARTIAL)
        self.assertEqual(order_financials(order)["received"], Decimal("125000"))
        self.assertEqual(order_financials(order)["remaining"], Decimal("700000"))
        self.assertEqual(order.status, Order.Status.SAN_SANG_GIAO)

    def test_payment_replay_is_idempotent_and_payload_timestamp_is_checked(self):
        order = self.ready_order()
        key = uuid.uuid4()
        received_at = timezone.now()
        args = {
            "order_id": order.pk,
            "staff": self.staff,
            "expected_order_version": order.version,
            "method": Payment.Method.CASH,
            "amount": Decimal("100000"),
            "received_at": received_at,
            "transaction_reference": "",
            "notes": "Đã nhận.",
            "idempotency_key": key,
        }
        _order, first, created = record_payment(**args)
        self.assertTrue(created)
        retry_order, second, created = record_payment(**args)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Payment.objects.filter(order=order).count(), 1)
        args["received_at"] = received_at + timedelta(seconds=1)
        from .services import IdempotencyConflict

        with self.assertRaises(IdempotencyConflict):
            record_payment(**args)
        self.assertEqual(retry_order.version, order.version + 1)

    def test_customer_can_only_confirm_owned_active_attempt_and_get_has_no_side_effect(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        other_order = self.ready_order_for_other_customer()
        _, other_attempt = self.start_attempt(other_order)

        with self.assertRaises(InvalidOrderTransition):
            customer_confirm_delivery(
                order_id=order.pk,
                customer=self.customer,
                attempt_id=other_attempt.pk,
                expected_order_version=order.version,
                expected_attempt_revision=other_attempt.revision,
            )

        self.client.force_login(self.customer)
        response = self.client.get(
            reverse("orders:customer_confirm_delivery", kwargs={
                "pk": order.pk,
                "attempt_id": attempt.pk,
            })
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.DANG_GIAO)

    def ready_order_for_other_customer(self):
        order = self.accepted_order()
        order.customer = self.other_customer
        order.save(update_fields=("customer",))
        order = self.start_order_for_existing(order)
        return order

    def start_order_for_existing(self, order):
        from .production import start_production

        order = start_production(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
        )
        order, _check = self.check_quality(order)
        order, review = self.make_review(order)
        return accept_review(
            order_id=order.pk,
            customer=self.other_customer,
            review_id=review.pk,
            expected_order_version=order.version,
            expected_review_revision=review.revision,
        )

    def test_delivery_failure_requires_reason_and_keeps_history(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        with self.assertRaises(ValidationError):
            fail_delivery(
                order_id=order.pk,
                staff=self.staff,
                attempt_id=attempt.pk,
                expected_order_version=order.version,
                reason=" ",
            )
        order, attempt = fail_delivery(
            order_id=order.pk,
            staff=self.staff,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            reason="Người nhận chưa có mặt.",
        )
        self.assertEqual(order.status, Order.Status.GIAO_THAT_BAI)
        self.assertTrue(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.DELIVERY_FAILED,
                message="Người nhận chưa có mặt.",
            ).exists()
        )

    def test_incident_blocks_completion_and_owner_is_only_decider(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        order, attempt = self.confirm_by_customer(order, attempt)
        order, incident = self.report_customer_issue(order, attempt)
        self.assertEqual(order.status, Order.Status.CHO_XU_LY_SU_CO)
        self.assertEqual(attempt.result, DeliveryAttempt.Result.DELIVERED)
        order, _payment, _created = self.record_cash(order, "825000")
        self.assertEqual(order.status, Order.Status.CHO_XU_LY_SU_CO)

        with self.assertRaises(InvalidOrderTransition):
            decide_incident(
                incident_id=incident.pk,
                owner=self.staff,
                expected_order_version=order.version,
                expected_incident_revision=incident.revision,
                approve=True,
                reason="Không được phép.",
            )
        order, incident = propose_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            resolution=OrderIncident.Resolution.KEEP_CURRENT,
            plan="Khách giữ bánh hiện tại sau khi được hỗ trợ.",
        )
        order, incident = decide_incident(
            incident_id=incident.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            approve=True,
            reason="Chấp thuận phương án.",
        )
        self.assertEqual(incident.status, OrderIncident.Status.WAITING_CUSTOMER)
        order, incident = customer_accept_incident_plan(
            incident_id=incident.pk,
            customer=self.customer,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            message="Tôi đồng ý giữ bánh.",
        )
        self.assertEqual(incident.status, OrderIncident.Status.RESOLVED)
        self.assertEqual(order.status, Order.Status.HOAN_TAT)

    def test_failed_delivery_can_be_escalated_for_owner_approval_then_redelivered(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        order, attempt = fail_delivery(
            order_id=order.pk,
            staff=self.staff,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            reason="Khách yêu cầu đổi lịch.",
        )
        order, incident = report_delivery_incident(
            order_id=order.pk,
            reporter=self.staff,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            expected_attempt_revision=attempt.revision,
            incident_type=OrderIncident.Type.UNREACHABLE,
            description="Khách đề nghị xếp lại lịch giao.",
        )
        order, incident = propose_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            resolution=OrderIncident.Resolution.REDELIVER,
            plan="Giao lại theo lịch khách đã chọn.",
            customer_agreement="Khách xác nhận lịch giao lại.",
            scheduled_at=timezone.now() + timedelta(days=2),
        )
        order, incident = decide_incident(
            incident_id=incident.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            approve=True,
            reason="Duyệt giao lại.",
        )
        from .delivery import record_incident_resolution

        order, incident = record_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            result="Đã thống nhất và ghi lịch giao lại.",
        )
        self.assertEqual(order.status, Order.Status.SAN_SANG_GIAO)
        self.assertEqual(incident.status, OrderIncident.Status.RESOLVED)
        self.assertEqual(order.delivery_rescheduled_at, incident.proposed_delivery_at)

    def test_owner_approved_repair_requires_new_qa_and_customer_accepted_photo(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        order, incident = self.report_customer_issue(order, attempt)
        with self.assertRaises(InvalidOrderTransition):
            propose_incident_resolution(
                incident_id=incident.pk,
                staff=self.staff,
                expected_order_version=order.version,
                expected_incident_revision=incident.revision,
                resolution=OrderIncident.Resolution.KEEP_CURRENT,
                plan="Giữ bánh khi khách chưa nhận.",
            )
        order, incident = propose_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            resolution=OrderIncident.Resolution.REPAIR,
            plan="Sửa bánh, thực hiện QA và gửi ảnh mới.",
        )
        order, incident = decide_incident(
            incident_id=incident.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            approve=True,
            reason="Duyệt phương án sửa bánh.",
        )
        self.assertEqual(order.status, Order.Status.DANG_LAM)
        self.assertEqual(order.production_round, 2)
        self.assertIsNone(order.current_review_id)
        with self.assertRaises(InvalidOrderTransition):
            execute_incident_resolution(
                incident_id=incident.pk,
                staff=self.staff,
                expected_order_version=order.version,
                expected_incident_revision=incident.revision,
                result="Đã sửa xong.",
            )

        order, check = self.check_quality(order)
        self.assertEqual(check.production_round, 2)
        order, review = self.make_review(order)
        order = accept_review(
            order_id=order.pk,
            customer=self.customer,
            review_id=review.pk,
            expected_order_version=order.version,
            expected_review_revision=review.revision,
        )
        order, incident = execute_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            result="Bánh đã sửa, QA đạt và khách duyệt ảnh mới.",
        )
        self.assertEqual(incident.status, OrderIncident.Status.RESOLVED)
        self.assertEqual(order.status, Order.Status.SAN_SANG_GIAO)
        order, second_attempt = self.start_attempt(order)
        self.assertEqual(second_attempt.attempt_number, 2)

    def test_owner_approved_refund_is_not_completed_until_actual_refund_recorded(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        order, attempt = staff_confirm_delivery(
            order_id=order.pk,
            staff=self.staff,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            expected_attempt_revision=attempt.revision,
            note="Đã bàn giao.",
        )
        order, payment, _created = self.record_cash(order, "825000")
        order, incident = self.report_customer_issue(order, attempt)
        order, incident = propose_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            resolution=OrderIncident.Resolution.CANCEL,
            plan="Hủy đơn và hoàn lại toàn bộ khoản thu.",
        )
        order, incident = decide_incident(
            incident_id=incident.pk,
            owner=self.owner,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            approve=True,
            reason="Duyệt hủy và hoàn.",
        )
        self.assertEqual(order.status, Order.Status.CHO_XU_LY_SU_CO)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PAID)
        with self.assertRaises(ValidationError):
            record_refund(
                order_id=order.pk,
                incident_id=incident.pk,
                payment_id=payment.pk,
                staff=self.staff,
                expected_order_version=order.version,
                method=Refund.Method.CASH,
                amount=Decimal("825001"),
                refunded_at=timezone.now(),
                transaction_reference="",
                notes="Hoàn quá số tiền.",
                idempotency_key=uuid.uuid4(),
            )
        order, refund, created = record_refund(
            order_id=order.pk,
            incident_id=incident.pk,
            payment_id=payment.pk,
            staff=self.staff,
            expected_order_version=order.version,
            method=Refund.Method.CASH,
            amount=Decimal("825000"),
            refunded_at=timezone.now(),
            transaction_reference="",
            notes="Đã hoàn tiền mặt.",
            idempotency_key=uuid.uuid4(),
        )
        self.assertTrue(created)
        self.assertEqual(order.status, Order.Status.DA_HUY)
        self.assertEqual(order.payment_status, Order.PaymentStatus.UNPAID)
        self.assertEqual(order_financials(order)["net_received"], Decimal("0"))
        self.assertEqual(refund.amount, Decimal("825000"))
        self.assertEqual(Refund.objects.count(), 1)
        refunded_at = refund.refunded_at
        replay_order, replay, created = record_refund(
            order_id=order.pk,
            incident_id=incident.pk,
            payment_id=payment.pk,
            staff=self.staff,
            expected_order_version=order.version - 1,
            method=Refund.Method.CASH,
            amount=Decimal("825000"),
            refunded_at=refunded_at,
            transaction_reference="",
            notes="Đã hoàn tiền mặt.",
            idempotency_key=refund.idempotency_key,
        )
        self.assertFalse(created)
        self.assertEqual(replay.pk, refund.pk)
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(replay_order.status, Order.Status.DA_HUY)

    def test_staff_start_delivery_is_post_only_csrf_protected_and_role_protected(self):
        order = self.ready_order()
        url = reverse("orders:staff_start_delivery", kwargs={"pk": order.pk})
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 405)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        denied = csrf_client.post(url, {"expected_version": order.version})
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.owner)
        self.assertEqual(
            self.client.post(url, {"expected_version": order.version}).status_code,
            403,
        )
        self.assertEqual(Order.objects.get(pk=order.pk).status, Order.Status.SAN_SANG_GIAO)

    def test_private_incident_evidence_is_only_available_to_related_parties(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        order, incident = self.report_customer_issue(
            order,
            attempt,
            photo=image_upload("incident.png"),
        )
        url = reverse(
            "orders:private_order_evidence",
            kwargs={"evidence_type": "incident", "evidence_id": incident.pk},
        )
        self.client.force_login(self.other_customer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.customer)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Cache-Control"].startswith("private"))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code, 200)

        order, incident = propose_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            resolution=OrderIncident.Resolution.REPAIR,
            plan="Kiểm tra, sửa bánh và gửi ảnh mới cho khách.",
        )
        self.client.force_login(self.owner)
        review_url = reverse(
            "orders:private_review_image",
            kwargs={"review_id": order.current_review_id},
        )
        self.assertEqual(self.client.get(review_url).status_code, 200)
        detail = self.client.get(
            reverse("orders:owner_incident_detail", kwargs={"incident_id": incident.pk})
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Thỏa thuận và ảnh bánh đã duyệt")

    def test_owner_decision_route_returns_to_incident_history_not_staff_area(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        order, attempt = self.confirm_by_customer(order, attempt)
        order, incident = self.report_customer_issue(order, attempt)
        order, incident = propose_incident_resolution(
            incident_id=incident.pk,
            staff=self.staff,
            expected_order_version=order.version,
            expected_incident_revision=incident.revision,
            resolution=OrderIncident.Resolution.KEEP_CURRENT,
            plan="Khách xem xét và xác nhận giữ phương án hiện tại.",
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("orders:owner_decide_incident", kwargs={"incident_id": incident.pk}),
            {
                "decision": "approve",
                "reason": "Duyệt phương án đã nêu.",
                "expected_order_version": order.version,
                "expected_incident_revision": incident.revision,
            },
        )
        self.assertRedirects(
            response,
            reverse("orders:owner_incident_detail", kwargs={"incident_id": incident.pk}),
        )
        detail = self.client.get(response.url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Chấp thuận")
        self.assertNotContains(detail, "Ghi nhận quyết định")

    def test_customer_and_staff_order_details_render_delivery_actions(self):
        order = self.ready_order()
        self.client.force_login(self.customer)
        customer_detail = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(customer_detail.status_code, 200)
        self.assertContains(customer_detail, "Giao hàng và thanh toán")
        self.assertContains(customer_detail, "Chưa có lần giao hàng")

        self.client.force_login(self.staff)
        staff_detail = self.client.get(
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(staff_detail.status_code, 200)
        self.assertContains(staff_detail, "Bắt đầu giao hàng")

        order, attempt = self.start_attempt(order)
        self.client.force_login(self.customer)
        customer_detail = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(customer_detail.status_code, 200)
        self.assertContains(customer_detail, "Đã nhận bánh, bánh đạt yêu cầu")
        self.assertContains(customer_detail, "Báo bánh có vấn đề")
        self.client.force_login(self.staff)
        staff_detail = self.client.get(
            reverse("orders:staff_order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(staff_detail.status_code, 200)
        self.assertContains(staff_detail, "Nhân viên xác nhận đã bàn giao")
        self.assertContains(staff_detail, "Ghi nhận tiền đã thực nhận")

    def test_staff_handoff_proof_is_removed_if_database_transition_fails(self):
        order = self.ready_order()
        order, attempt = self.start_attempt(order)
        photo = image_upload("failed-handoff.png")
        with patch(
            "orders.delivery._change_order",
            side_effect=StaleOrderVersion("Đơn vừa thay đổi."),
        ):
            with self.assertRaises(StaleOrderVersion):
                staff_confirm_delivery(
                    order_id=order.pk,
                    staff=self.staff,
                    attempt_id=attempt.pk,
                    expected_order_version=order.version,
                    expected_attempt_revision=attempt.revision,
                    note="Đã bàn giao.",
                    proof=photo,
                )
        attempt.refresh_from_db()
        self.assertFalse(attempt.proof)
        self.assertFalse(
            any(
                path.is_file()
                for path in Path(self.private_media.name).rglob("*")
                if "order-evidence" in path.parts
            )
        )
