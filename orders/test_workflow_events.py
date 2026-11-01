import io
import json
import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .delivery import (
    customer_confirm_delivery,
    record_payment,
    start_delivery,
)
from .models import (
    Order,
    OrderHistory,
    Payment,
    Quote,
    WorkflowEvent,
    WorkflowRetryRequest,
)
from .production import (
    accept_review,
    record_production_check,
    send_review_photo,
    start_production,
)
from .services import StaleOrderVersion, accept_quote
from .tests import OrderWorkflowTestCase
from .workflow_events import (
    MAX_ATTEMPTS,
    create_workflow_event,
    dispatch_workflow_events,
)


def image_upload(name="cake.png"):
    image = Image.new("RGB", (32, 24), (230, 150, 170))
    data = io.BytesIO()
    image.save(data, format="PNG")
    return SimpleUploadedFile(name, data.getvalue(), content_type="image/png")


class BusinessEventTests(OrderWorkflowTestCase):
    def setUp(self):
        super().setUp()
        from .storage import private_order_photo_storage

        self.private_media = tempfile.TemporaryDirectory()
        self.addCleanup(self.private_media.cleanup)
        self.settings(PRIVATE_MEDIA_ROOT=self.private_media.name)
        private_order_photo_storage.__dict__.pop("base_location", None)
        self.addCleanup(
            lambda: private_order_photo_storage.__dict__.pop(
                "base_location", None
            )
        )
        self.customer.email = "customer@example.test"
        self.customer.first_name = "Bibi Customer"
        self.customer.save(update_fields=["email", "first_name"])

    def accept_current_quote(self, order):
        order = self.check_order(order)
        order, quote = self.quote(order)
        accepted = accept_quote(
            order_id=order.pk,
            customer=self.customer,
            quote_id=quote.pk,
            quote_revision=quote.revision,
            expected_order_version=order.version,
        )
        return accepted, quote

    def make_photo_review(self, order, *, replacement_reason=""):
        if order.status == Order.Status.DANG_LAM:
            order, _check = record_production_check(
                order_id=order.pk,
                staff=self.staff,
                expected_version=order.version,
                passed=True,
                notes="Kiểm tra đạt.",
            )
        return send_review_photo(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
            photo=image_upload(),
            description="Ảnh bánh hoàn thiện.",
            replacement_reason=replacement_reason,
        )

    def test_quote_acceptance_creates_one_confirmed_event_with_stable_business_key(self):
        order = self.create_order()
        _accepted, quote = self.accept_current_quote(order)

        events = WorkflowEvent.objects.filter(order=order)

        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.event_type, WorkflowEvent.EventType.ORDER_CONFIRMED)
        self.assertEqual(event.business_key, f"quote-accepted:{quote.pk}")
        self.assertEqual(event.payload["quote_version"], quote.version_number)
        self.assertEqual(event.status, WorkflowEvent.Status.PENDING)
        with self.assertRaises(StaleOrderVersion):
            accept_quote(
                order_id=order.pk,
                customer=self.customer,
                quote_id=quote.pk,
                quote_revision=quote.revision,
                expected_order_version=order.version - 1,
            )
        self.assertEqual(WorkflowEvent.objects.filter(order=order).count(), 1)

    def test_quote_confirmation_and_outbox_roll_back_together(self):
        order = self.create_order()
        order = self.check_order(order)
        order, quote = self.quote(order)

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                accept_quote(
                    order_id=order.pk,
                    customer=self.customer,
                    quote_id=quote.pk,
                    quote_revision=quote.revision,
                    expected_order_version=order.version,
                )
                raise RuntimeError("force business transaction rollback")

        order.refresh_from_db()
        quote.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CHO_KHACH_XAC_NHAN)
        self.assertEqual(quote.status, Quote.Status.PENDING)
        self.assertFalse(WorkflowEvent.objects.filter(order=order).exists())

    def test_photo_replacement_versions_create_distinct_review_requested_events(self):
        order = self.create_order()
        order, _quote = self.accept_current_quote(order)
        order = start_production(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
        )
        order, first_review = self.make_photo_review(order)
        order, second_review = self.make_photo_review(
            order,
            replacement_reason="Chỉnh lại lớp trang trí.",
        )

        events = WorkflowEvent.objects.filter(
            order=order,
            event_type=WorkflowEvent.EventType.CAKE_REVIEW_REQUESTED,
        )
        self.assertEqual(events.count(), 2)
        self.assertSetEqual(
            set(events.values_list("payload__cake_review_version", flat=True)),
            {first_review.version_number, second_review.version_number},
        )
        self.assertEqual(events.values("business_key").distinct().count(), 2)

    def test_actual_completion_creates_event_only_after_delivery_and_payment(self):
        order = self.create_order()
        order, _quote = self.accept_current_quote(order)
        order = start_production(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
        )
        order, review = self.make_photo_review(order)
        order = accept_review(
            order_id=order.pk,
            customer=self.customer,
            review_id=review.pk,
            expected_order_version=order.version,
            expected_review_revision=review.revision,
        )
        order, attempt = start_delivery(
            order_id=order.pk,
            staff=self.staff,
            expected_version=order.version,
        )
        order, _attempt = customer_confirm_delivery(
            order_id=order.pk,
            customer=self.customer,
            attempt_id=attempt.pk,
            expected_order_version=order.version,
            expected_attempt_revision=attempt.revision,
        )
        self.assertEqual(order.status, Order.Status.DA_GIAO_CHO_THANH_TOAN)

        order, _payment, created = record_payment(
            order_id=order.pk,
            staff=self.staff,
            expected_order_version=order.version,
            method=Payment.Method.CASH,
            amount=Decimal("825000"),
            received_at=timezone.now(),
            transaction_reference="",
            notes="Đã nhận đủ tiền mặt.",
            idempotency_key=uuid.uuid4(),
        )

        self.assertTrue(created)
        self.assertEqual(order.status, Order.Status.HOAN_TAT)
        self.assertIsNotNone(order.completed_at)
        event = WorkflowEvent.objects.get(
            business_key=f"order-completed:{order.pk}"
        )
        self.assertEqual(event.event_type, WorkflowEvent.EventType.ORDER_COMPLETED)
        self.assertEqual(event.payload["completed_at"], order.completed_at.isoformat())
        self.assertIn("review_deadline", event.payload)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.ORDER_COMPLETED,
            ).count(),
            1,
        )

    def test_rollback_removes_outbox_event_with_business_transaction(self):
        order = self.create_order()

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                create_workflow_event(
                    order=order,
                    event_type=WorkflowEvent.EventType.ORDER_COMPLETED,
                    business_key=f"rollback:{order.pk}",
                )
                raise RuntimeError("rollback transaction")

        self.assertFalse(WorkflowEvent.objects.filter(order=order).exists())

    def test_event_id_and_payload_are_reused_for_same_business_key(self):
        order = self.create_order()
        event = create_workflow_event(
            order=order,
            event_type=WorkflowEvent.EventType.ORDER_CONFIRMED,
            business_key=f"idempotent:{order.pk}",
            quote_version=1,
        )
        repeated = create_workflow_event(
            order=order,
            event_type=WorkflowEvent.EventType.ORDER_CONFIRMED,
            business_key=f"idempotent:{order.pk}",
            quote_version=2,
        )

        self.assertEqual(event.pk, repeated.pk)
        self.assertEqual(event.event_id, repeated.event_id)
        self.assertEqual(event.payload, repeated.payload)
        self.assertEqual(WorkflowEvent.objects.filter(order=order).count(), 1)

    def test_missing_email_creates_visible_failure_without_fake_recipient(self):
        self.customer.email = ""
        self.customer.save(update_fields=["email"])
        order = self.create_order()

        event = create_workflow_event(
            order=order,
            event_type=WorkflowEvent.EventType.ORDER_CONFIRMED,
            business_key=f"missing-email:{order.pk}",
        )

        self.assertEqual(event.status, WorkflowEvent.Status.FAILED)
        self.assertIn("email", event.last_error.lower())
        self.assertNotIn("recipient_email", event.payload)

    def test_outbox_business_key_has_database_uniqueness(self):
        order = self.create_order()
        create_workflow_event(
            order=order,
            event_type=WorkflowEvent.EventType.ORDER_CONFIRMED,
            business_key="db-unique-workflow-key",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                WorkflowEvent.objects.create(
                    event_type=WorkflowEvent.EventType.ORDER_CONFIRMED,
                    order=order,
                    business_key="db-unique-workflow-key",
                    payload={},
                )


class WorkflowDispatcherTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from catalog.models import CakeTemplate, Category

        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="workflow_customer",
            password="ComplexDemoPassword-123",
            email="workflow@example.test",
            first_name="Workflow Customer",
            role=user_model.Role.CUSTOMER,
        )
        self.owner = user_model.objects.create_user(
            username="workflow_owner",
            password="ComplexDemoPassword-123",
            role=user_model.Role.OWNER,
        )
        self.staff = user_model.objects.create_user(
            username="workflow_staff",
            password="ComplexDemoPassword-123",
            role=user_model.Role.STAFF,
        )
        category = Category.objects.create(name="Workflow Cakes")
        cake = CakeTemplate.objects.create(
            category=category,
            name="Workflow Cake",
            reference_price=Decimal("100000"),
        )
        self.order = Order.objects.create(
            customer=self.customer,
            cake_template=cake,
            request_key=uuid.uuid4(),
            payload_fingerprint="workflow-test",
            cake_name_snapshot=cake.name,
            reference_price_snapshot=Decimal("100000"),
            size="20 cm",
            flavor="Dâu",
            quantity=1,
            recipient_name="Người nhận",
            recipient_phone="0900000000",
            delivery_address="Địa chỉ riêng không gửi",
            desired_delivery_at=timezone.now() + timedelta(days=2),
        )

    def create_event(self, **kwargs):
        model_fields = {
            key: kwargs.pop(key)
            for key in (
                "status",
                "attempts",
                "attempts_since_retry",
                "next_attempt_at",
                "lease_until",
                "lease_token",
            )
            if key in kwargs
        }
        defaults = {
            "order": self.order,
            "event_type": WorkflowEvent.EventType.ORDER_CONFIRMED,
            "business_key": f"test-event:{uuid.uuid4()}",
        }
        defaults.update(kwargs)
        event = create_workflow_event(**defaults)
        if model_fields:
            WorkflowEvent.objects.filter(pk=event.pk).update(**model_fields)
            event.refresh_from_db()
        return event

    def fake_response(self, status=202):
        response = MagicMock()
        response.status = status
        response.__enter__.return_value = response
        return response

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="token-never-logged",
        WORKFLOW_TIMEOUT_SECONDS=3,
        SITE_BASE_URL="https://cake.example.test",
    )
    def test_successful_webhook_marks_delivered_without_claiming_email_delivery(self):
        event = self.create_event(quote_version=2)
        opener = MagicMock()
        opener.open.return_value = self.fake_response(202)

        with patch("orders.workflow_events.build_opener", return_value=opener):
            result = dispatch_workflow_events(limit=1)

        event.refresh_from_db()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(event.status, WorkflowEvent.Status.DELIVERED)
        self.assertEqual(event.last_response_status, 202)
        self.assertIsNotNone(event.delivered_at)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer token-never-logged")
        self.assertEqual(request.headers["Idempotency-key"], str(event.event_id))
        payload = json.loads(request.data)
        self.assertEqual(payload["event_id"], str(event.event_id))
        self.assertEqual(
            payload["action_url"],
            f"https://cake.example.test/orders/mine/{self.order.pk}/",
        )
        for forbidden in ("recipient_phone", "delivery_address", "password", "secret"):
            self.assertNotIn(forbidden, payload)

        self.client.force_login(self.owner)
        page = self.client.get(reverse("orders:owner_workflow_event_list"))
        self.assertContains(page, "Workflow đã tiếp nhận")
        self.assertNotContains(page, "Email đã gửi thành công")

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_disabled_workflow_does_not_make_http_request(self):
        event = self.create_event()

        with override_settings(WORKFLOW_ENABLED=False):
            with patch("orders.workflow_events.build_opener") as build_opener:
                result = dispatch_workflow_events(limit=1)

        event.refresh_from_db()
        self.assertTrue(result["disabled"])
        self.assertEqual(event.status, WorkflowEvent.Status.PENDING)
        build_opener.assert_not_called()

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_timeout_and_429_retry_with_backoff_and_retry_after(self):
        timeout_event = self.create_event()
        timeout_opener = MagicMock()
        timeout_opener.open.side_effect = TimeoutError()
        with patch(
            "orders.workflow_events.build_opener",
            return_value=timeout_opener,
        ):
            dispatch_workflow_events(limit=1)
        timeout_event.refresh_from_db()
        self.assertEqual(timeout_event.status, WorkflowEvent.Status.PENDING)
        self.assertIn("kết nối", timeout_event.last_error)
        self.assertGreater(timeout_event.next_attempt_at, timezone.now())

        limited_event = self.create_event()
        limited_opener = MagicMock()
        from urllib.error import HTTPError

        limited_opener.open.side_effect = HTTPError(
            "https://workflow.example.test/hooks/bibi",
            429,
            "Rate limited",
            {"Retry-After": "120"},
            None,
        )
        with patch(
            "orders.workflow_events.build_opener",
            return_value=limited_opener,
        ):
            dispatch_workflow_events(limit=1)
        limited_event.refresh_from_db()
        self.assertEqual(limited_event.status, WorkflowEvent.Status.PENDING)
        self.assertEqual(limited_event.last_response_status, 429)
        self.assertGreaterEqual(
            limited_event.next_attempt_at,
            timezone.now() + timedelta(seconds=119),
        )

        server_error_event = self.create_event()
        server_error_opener = MagicMock()
        from urllib.error import HTTPError

        server_error_opener.open.side_effect = HTTPError(
            "https://workflow.example.test/hooks/bibi",
            503,
            "Unavailable",
            {},
            None,
        )
        with patch(
            "orders.workflow_events.build_opener",
            return_value=server_error_opener,
        ):
            dispatch_workflow_events(limit=1)
        server_error_event.refresh_from_db()
        self.assertEqual(server_error_event.status, WorkflowEvent.Status.PENDING)
        self.assertEqual(server_error_event.last_response_status, 503)

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_authentication_4xx_is_terminal_without_automatic_retry(self):
        from urllib.error import HTTPError

        for status in (401, 403):
            with self.subTest(status=status):
                event = self.create_event()
                opener = MagicMock()
                opener.open.side_effect = HTTPError(
                    "https://workflow.example.test/hooks/bibi",
                    status,
                    "Unauthorized",
                    {},
                    None,
                )
                with patch(
                    "orders.workflow_events.build_opener",
                    return_value=opener,
                ):
                    dispatch_workflow_events(limit=1)
                event.refresh_from_db()
                self.assertEqual(event.status, WorkflowEvent.Status.FAILED)
                self.assertEqual(event.attempts, 1)
                self.assertIn("xác thực", event.last_error)

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_maximum_attempts_end_in_failed_state(self):
        event = self.create_event(
            attempts=MAX_ATTEMPTS - 1,
            attempts_since_retry=MAX_ATTEMPTS - 1,
        )
        opener = MagicMock()
        opener.open.side_effect = TimeoutError()
        with patch("orders.workflow_events.build_opener", return_value=opener):
            dispatch_workflow_events(limit=1)

        event.refresh_from_db()
        self.assertEqual(event.status, WorkflowEvent.Status.FAILED)
        self.assertEqual(event.attempts, MAX_ATTEMPTS)
        self.assertEqual(event.attempts_since_retry, MAX_ATTEMPTS)
        self.assertIn("hết lượt thử", event.last_error)

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_batch_limit_due_filter_and_expired_lease_recovery(self):
        due_one = self.create_event()
        due_two = self.create_event()
        future = self.create_event(
            next_attempt_at=timezone.now() + timedelta(days=1)
        )
        expired_lease = self.create_event(
            status=WorkflowEvent.Status.PROCESSING,
            lease_until=timezone.now() - timedelta(seconds=1),
            lease_token=uuid.uuid4(),
        )
        opener = MagicMock()
        opener.open.return_value = self.fake_response(204)

        with patch("orders.workflow_events.build_opener", return_value=opener):
            result = dispatch_workflow_events(limit=1)

        self.assertEqual(result["processed"], 1)
        self.assertEqual(opener.open.call_count, 1)
        due_one.refresh_from_db()
        due_two.refresh_from_db()
        future.refresh_from_db()
        expired_lease.refresh_from_db()
        self.assertEqual(future.status, WorkflowEvent.Status.PENDING)
        self.assertEqual(expired_lease.status, WorkflowEvent.Status.PENDING)
        self.assertEqual(
            sum(
                event.status == WorkflowEvent.Status.DELIVERED
                for event in (due_one, due_two)
            ),
            1,
        )

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_failed_event_retry_keeps_event_id_and_payload(self):
        event = self.create_event(status=WorkflowEvent.Status.FAILED, attempts=5)
        original_id = event.event_id
        original_payload = event.payload
        original_attempts = event.attempts
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("orders:owner_retry_workflow_event", kwargs={"event_id": event.pk})
        )

        self.assertRedirects(response, reverse("orders:owner_workflow_event_list"))
        event.refresh_from_db()
        self.assertEqual(event.status, WorkflowEvent.Status.PENDING)
        self.assertEqual(event.event_id, original_id)
        self.assertEqual(event.payload, original_payload)
        self.assertEqual(event.attempts, original_attempts)
        self.assertEqual(event.attempts_since_retry, 0)
        retry = WorkflowRetryRequest.objects.get(event=event)
        self.assertEqual(retry.requested_by, self.owner)
        self.assertEqual(retry.attempts_before_retry, original_attempts)

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_expired_processing_lease_can_be_reclaimed_and_completed(self):
        event = self.create_event(
            status=WorkflowEvent.Status.PROCESSING,
            attempts=1,
            attempts_since_retry=1,
            lease_until=timezone.now() - timedelta(seconds=1),
            lease_token=uuid.uuid4(),
        )
        opener = MagicMock()
        opener.open.return_value = self.fake_response(200)
        with patch("orders.workflow_events.build_opener", return_value=opener):
            result = dispatch_workflow_events(limit=1)

        event.refresh_from_db()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(event.status, WorkflowEvent.Status.DELIVERED)
        self.assertEqual(event.attempts, 2)

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="https://workflow.example.test/hooks/bibi",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
    )
    def test_missing_recipient_is_never_posted_to_workflow(self):
        self.customer.email = ""
        self.customer.save(update_fields=["email"])
        event = self.create_event()
        self.assertEqual(event.status, WorkflowEvent.Status.FAILED)
        with patch("orders.workflow_events.build_opener") as build_opener:
            result = dispatch_workflow_events(limit=1)
        self.assertEqual(result["processed"], 0)
        build_opener.assert_not_called()

    def test_owner_monitor_permissions_get_and_csrf_protected_retry(self):
        event = self.create_event(status=WorkflowEvent.Status.FAILED, attempts=5)
        route = reverse("orders:owner_workflow_event_list")
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(route).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(route).status_code, 403)
        self.client.force_login(self.owner)
        before = event.status
        self.assertEqual(self.client.get(route).status_code, 200)
        event.refresh_from_db()
        self.assertEqual(event.status, before)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        post_url = reverse(
            "orders:owner_retry_workflow_event",
            kwargs={"event_id": event.pk},
        )
        self.assertEqual(csrf_client.post(post_url).status_code, 403)
        event.refresh_from_db()
        self.assertEqual(event.status, WorkflowEvent.Status.FAILED)

    def test_retry_request_does_not_send_http_in_view(self):
        event = self.create_event(status=WorkflowEvent.Status.FAILED, attempts=MAX_ATTEMPTS)
        self.client.force_login(self.owner)
        with patch("orders.workflow_events.build_opener") as build_opener:
            self.client.post(
                reverse(
                    "orders:owner_retry_workflow_event",
                    kwargs={"event_id": event.pk},
                )
            )
        build_opener.assert_not_called()
        event.refresh_from_db()
        self.assertEqual(event.status, WorkflowEvent.Status.PENDING)

    @override_settings(
        WORKFLOW_ENABLED=True,
        WORKFLOW_WEBHOOK_URL="http://external.example.test/hooks",
        WORKFLOW_WEBHOOK_TOKEN="test-token",
        DEBUG=False,
    )
    def test_external_http_webhook_is_rejected_without_request(self):
        self.create_event()
        with patch("orders.workflow_events.build_opener") as build_opener:
            with self.assertRaises(ValueError):
                dispatch_workflow_events(limit=1)
        build_opener.assert_not_called()
