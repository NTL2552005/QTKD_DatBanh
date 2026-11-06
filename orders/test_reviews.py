import json
from datetime import timedelta
from decimal import Decimal
from urllib.error import URLError
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Order,
    OrderHistory,
    OrderReview,
    ReviewClassificationAudit,
    ReviewClassificationEvent,
    ReviewReply,
)
from .reviews import (
    REVIEW_AVAILABLE,
    REVIEW_TIME_UNKNOWN,
    review_state,
    submit_order_review,
)
from .review_classification import (
    StaleReviewClassification,
    confirm_review_classification,
    dispatch_review_classification_events,
)
from .services import InvalidOrderTransition
from .tests import OrderWorkflowTestCase
from .forms import OrderReviewForm


class OrderReviewTests(OrderWorkflowTestCase):
    def complete_order(self, order=None, *, completed_at=None, status=None):
        order = order or self.create_order()
        completed_at = completed_at or timezone.now() - timedelta(hours=1)
        Order.objects.filter(pk=order.pk).update(
            status=status or Order.Status.HOAN_TAT,
            completed_at=completed_at,
        )
        order.refresh_from_db()
        return order

    def test_owner_of_completed_order_can_submit_once(self):
        order = self.complete_order()

        review, created = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=5,
            comment="  Bánh rất ngon.  ",
        )

        self.assertTrue(created)
        self.assertEqual(review.comment, "  Bánh rất ngon.  ")
        self.assertEqual(review.customer, self.customer)
        self.assertEqual(
            review.classification_status,
            OrderReview.ClassificationStatus.PENDING,
        )
        event = ReviewClassificationEvent.objects.get(review=review)
        self.assertEqual(event.payload["comment"], review.comment)
        self.assertEqual(event.payload["review_id"], str(review.pk))
        self.assertEqual(OrderReview.objects.filter(order=order).count(), 1)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.ORDER_REVIEW_SUBMITTED,
            ).count(),
            1,
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.HOAN_TAT)

    def test_unfinished_and_cancelled_orders_cannot_be_reviewed(self):
        unfinished = self.create_order()
        cancelled = self.complete_order(status=Order.Status.DA_HUY)
        for order in (unfinished, cancelled):
            with self.subTest(status=order.status):
                with self.assertRaises(InvalidOrderTransition):
                    submit_order_review(
                        order_id=order.pk,
                        customer=self.customer,
                        rating=4,
                    )

    def test_review_requires_customer_ownership_and_customer_role(self):
        order = self.complete_order()
        with self.assertRaises(InvalidOrderTransition):
            submit_order_review(
                order_id=order.pk,
                customer=self.other_customer,
                rating=4,
            )
        for user in (self.staff, self.owner):
            with self.subTest(role=user.role):
                with self.assertRaises(InvalidOrderTransition):
                    submit_order_review(
                        order_id=order.pk,
                        customer=user,
                        rating=4,
                    )

    def test_other_customer_cannot_open_or_post_review_for_order(self):
        order = self.complete_order()
        self.client.force_login(self.other_customer)

        response = self.client.get(
            reverse("orders:order_detail", kwargs={"pk": order.pk})
        )
        self.assertEqual(response.status_code, 404)
        response = self.client.post(
            reverse("orders:customer_submit_order_review", kwargs={"pk": order.pk}),
            {"rating": "5", "comment": "Không thuộc đơn"},
        )
        self.assertEqual(response.status_code, 404)

    def test_non_customer_roles_cannot_submit_review(self):
        order = self.complete_order()
        for user in (self.staff, self.owner):
            self.client.force_login(user)
            with self.subTest(role=user.role):
                response = self.client.post(
                    reverse(
                        "orders:customer_submit_order_review",
                        kwargs={"pk": order.pk},
                    ),
                    {"rating": "5", "comment": ""},
                )
                self.assertEqual(response.status_code, 403)

    def test_rating_form_rejects_out_of_range_fractional_and_invalid_values(self):
        for value in ("0", "6", "3.5", "invalid", ""):
            with self.subTest(rating=value):
                form = OrderReviewForm({"rating": value, "comment": ""})
                self.assertFalse(form.is_valid())

        order = self.complete_order()
        for value in (0, 6, Decimal("3.5"), True):
            with self.subTest(service_rating=value):
                with self.assertRaises(ValidationError):
                    submit_order_review(
                        order_id=order.pk,
                        customer=self.customer,
                        rating=value,
                    )

    def test_database_rejects_rating_outside_one_to_five(self):
        order = self.complete_order()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrderReview.objects.create(
                    order=order,
                    customer=self.customer,
                    rating=6,
                )

    def test_comment_is_preserved_optional_and_limited_to_2000_characters(self):
        order = self.complete_order()
        form = OrderReviewForm({"rating": "4", "comment": "   "})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["comment"], "   ")
        review, _ = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=4,
            comment="   ",
        )
        self.assertEqual(review.comment, "   ")
        self.assertEqual(
            review.classification_event.status,
            ReviewClassificationEvent.Status.SKIPPED,
        )
        self.assertEqual(
            review.classification_status,
            OrderReview.ClassificationStatus.NOT_REQUESTED,
        )

        other_order = self.complete_order()
        too_long = "x" * 2001
        form = OrderReviewForm({"rating": "4", "comment": too_long})
        self.assertFalse(form.is_valid())
        with self.assertRaises(ValidationError):
            submit_order_review(
                order_id=other_order.pk,
                customer=self.customer,
                rating=4,
                comment=too_long,
            )

    def test_resubmission_does_not_overwrite_review_or_duplicate_history(self):
        order = self.complete_order()
        original, created = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=2,
            comment="Lần đầu",
        )
        repeated, created_again = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=5,
            comment="Nội dung gửi lại",
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(repeated.pk, original.pk)
        self.assertEqual(repeated.rating, 2)
        self.assertEqual(repeated.comment, "Lần đầu")
        self.assertEqual(OrderReview.objects.filter(order=order).count(), 1)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.ORDER_REVIEW_SUBMITTED,
            ).count(),
            1,
        )


@override_settings(N8N_REVIEW_CALLBACK_KEY="callback-secret")
class ReviewClassificationTests(OrderWorkflowTestCase):
    def complete_order(self):
        order = self.create_order()
        Order.objects.filter(pk=order.pk).update(
            status=Order.Status.HOAN_TAT,
            completed_at=timezone.now() - timedelta(hours=1),
        )
        order.refresh_from_db()
        return order

    def submit_review(self, comment="Bánh rất ngon"):
        return submit_order_review(
            order_id=self.complete_order().pk,
            customer=self.customer,
            rating=5,
            comment=comment,
        )[0]

    def callback_payload(self, review, **overrides):
        payload = {
            "event_id": str(review.classification_event.event_id),
            "review_id": str(review.pk),
            "ai_label": "POSITIVE",
            "ai_confidence": 0.99,
            "ai_reason": "Nhận xét có sắc thái tích cực.",
            "ai_valid": True,
        }
        payload.update(overrides)
        return payload

    def post_callback(self, payload, *, key="callback-secret", client=None):
        client = client or self.client
        return client.post(
            reverse("orders:n8n_review_classification_callback"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_CALLBACK_KEY=key,
        )

    def test_review_is_persisted_when_n8n_connection_fails(self):
        order = self.complete_order()
        self.client.force_login(self.customer)
        with patch(
            "orders.review_classification.build_opener",
            side_effect=URLError("unavailable"),
        ), self.settings(
            DEBUG=True,
            N8N_REVIEW_WEBHOOK_URL="http://127.0.0.1:5678/reviews",
            N8N_REVIEW_WEBHOOK_KEY="test-workflow-key",
            N8N_REVIEW_TIMEOUT_SECONDS=1,
        ):
            response = self.client.post(
                reverse("orders:customer_submit_order_review", kwargs={"pk": order.pk}),
                {"rating": "4", "comment": "  Tốt  "},
            )

        self.assertEqual(response.status_code, 302)
        review = OrderReview.objects.get(order=order)
        event = ReviewClassificationEvent.objects.get(review=review)
        self.assertEqual(review.comment, "  Tốt  ")
        self.assertEqual(event.attempts, 1)
        self.assertEqual(event.status, ReviewClassificationEvent.Status.PENDING)

    def test_dispatch_sends_minimal_payload_and_workflow_header(self):
        review = self.submit_review()
        response = Mock()
        response.status = 202
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        opener = Mock()
        opener.open.return_value = response
        with patch(
            "orders.review_classification.build_opener",
            return_value=opener,
        ), self.settings(
            DEBUG=True,
            N8N_REVIEW_WEBHOOK_URL="http://127.0.0.1:5678/reviews",
            N8N_REVIEW_WEBHOOK_KEY="test-workflow-key",
            N8N_REVIEW_TIMEOUT_SECONDS=1,
        ):
            result = dispatch_review_classification_events(
                event_id=review.classification_event.event_id,
                limit=1,
            )

        self.assertEqual(result["processed"], 1)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("X-workflow-key"), "test-workflow-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            set(payload),
            {"event_id", "review_id", "order_code", "rating", "comment"},
        )
        self.assertEqual(payload["comment"], review.comment)
        self.assertNotIn("customer", payload)
        self.assertNotIn("phone", payload)
        self.assertNotIn("address", payload)
        event = review.classification_event
        event.refresh_from_db()
        self.assertEqual(event.status, ReviewClassificationEvent.Status.SENT)
        ReviewClassificationEvent.objects.filter(pk=event.pk).update(
            status=ReviewClassificationEvent.Status.FAILED,
            attempts=5,
        )
        with patch(
            "orders.review_classification.build_opener",
            return_value=opener,
        ), self.settings(
            DEBUG=True,
            N8N_REVIEW_WEBHOOK_URL="http://127.0.0.1:5678/reviews",
            N8N_REVIEW_WEBHOOK_KEY="test-workflow-key",
            N8N_REVIEW_TIMEOUT_SECONDS=1,
        ):
            retry_result = dispatch_review_classification_events(
                event_id=event.event_id,
                limit=1,
                retry_failed=True,
            )
        event.refresh_from_db()
        self.assertEqual(retry_result["sent"], 1)
        self.assertEqual(event.status, ReviewClassificationEvent.Status.SENT)
        self.assertEqual(event.attempts, 1)

    def test_callback_requires_callback_key_and_valid_matching_event(self):
        review = self.submit_review()
        url = reverse("orders:n8n_review_classification_callback")

        self.assertEqual(
            self.client.post(url, "{}", content_type="application/json").status_code,
            403,
        )
        self.assertEqual(
            self.post_callback(self.callback_payload(review), key="incorrect").status_code,
            403,
        )
        self.assertEqual(
            self.post_callback(
                self.callback_payload(review, review_id="999999")
            ).status_code,
            400,
        )
        self.assertEqual(
            self.post_callback(
                self.callback_payload(review, event_id="00000000-0000-0000-0000-000000000000")
            ).status_code,
            404,
        )

    def test_callback_threshold_routes_094_to_staff_and_095_to_auto(self):
        for confidence, expected in (
            (0.94, OrderReview.ClassificationStatus.PENDING_REVIEW),
            (0.95, OrderReview.ClassificationStatus.AUTO_LABELED),
        ):
            with self.subTest(confidence=confidence):
                review = self.submit_review()
                response = self.post_callback(
                    self.callback_payload(review, ai_confidence=confidence)
                )
                self.assertEqual(response.status_code, 200)
                review.refresh_from_db()
                self.assertEqual(review.classification_status, expected)
                self.assertEqual(
                    review.final_label,
                    "POSITIVE"
                    if expected == OrderReview.ClassificationStatus.AUTO_LABELED
                    else None,
                )

    def test_invalid_callback_output_is_queued_and_invalid_protocol_rejected(self):
        review = self.submit_review()
        response = self.post_callback(
            self.callback_payload(
                review,
                ai_valid=False,
                ai_label=None,
                ai_confidence=None,
                ai_reason="Đầu ra không hợp lệ.",
            )
        )
        self.assertEqual(response.status_code, 200)
        review.refresh_from_db()
        self.assertEqual(
            review.classification_status,
            OrderReview.ClassificationStatus.PENDING_REVIEW,
        )
        self.assertIsNone(review.ai_label)
        self.assertIsNone(review.ai_confidence)

        another_review = self.submit_review()
        invalid = self.post_callback(
            self.callback_payload(another_review, ai_confidence=float("nan"))
        )
        self.assertEqual(invalid.status_code, 400)
        for confidence in (-0.01, 1.01, True):
            with self.subTest(confidence=confidence):
                self.assertEqual(
                    self.post_callback(
                        self.callback_payload(
                            another_review,
                            ai_confidence=confidence,
                        )
                    ).status_code,
                    400,
                )
        self.assertEqual(
            self.post_callback(
                self.callback_payload(another_review, ai_label="UNKNOWN")
            ).status_code,
            400,
        )

    def test_callback_is_idempotent_and_conflicting_replay_is_rejected(self):
        review = self.submit_review()
        payload = self.callback_payload(review, ai_confidence=0.94)

        first = self.post_callback(payload)
        repeated = self.post_callback(payload)
        conflict = self.post_callback({**payload, "ai_label": "NEUTRAL"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(repeated.json()["status"], "duplicate")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(ReviewClassificationAudit.objects.filter(review=review).count(), 0)

    def test_callback_is_csrf_exempt_only_on_callback_route(self):
        review = self.submit_review()
        strict_client = Client(enforce_csrf_checks=True)
        callback = self.post_callback(self.callback_payload(review), client=strict_client)
        self.assertEqual(callback.status_code, 200)

        order = self.complete_order()
        strict_client.force_login(self.customer)
        protected = strict_client.post(
            reverse("orders:customer_submit_order_review", kwargs={"pk": order.pk}),
            {"rating": "5", "comment": "Tốt"},
        )
        self.assertEqual(protected.status_code, 403)

    def test_staff_and_owner_can_review_but_customer_cannot(self):
        review = self.submit_review()
        self.post_callback(
            self.callback_payload(review, ai_confidence=0.94)
        )
        queue_url = reverse("orders:staff_pending_review_classifications")
        detail_url = reverse(
            "orders:staff_review_classification_detail",
            kwargs={"review_id": review.pk},
        )

        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(queue_url).status_code, 403)
        self.assertEqual(self.client.get(detail_url).status_code, 403)

        for reviewer in (self.staff, self.owner):
            self.client.force_login(reviewer)
            self.assertEqual(self.client.get(queue_url).status_code, 200)
            detail = self.client.get(detail_url)
            self.assertEqual(detail.status_code, 200)
            self.assertContains(detail, "Bánh rất ngon")

    def test_staff_confirmation_preserves_original_and_creates_audit(self):
        review = self.submit_review("  Nội dung gốc  ")
        self.post_callback(
            self.callback_payload(review, ai_confidence=0.94)
        )
        detail_url = reverse(
            "orders:staff_review_classification_detail",
            kwargs={"review_id": review.pk},
        )
        self.client.force_login(self.staff)
        detail = self.client.get(detail_url)
        response = self.client.post(
            detail_url,
            {
                "final_label": "NEGATIVE",
                "review_note": "Đánh giá thủ công.",
                "version": detail.context["review"].classification_version,
            },
        )

        self.assertEqual(response.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.comment, "  Nội dung gốc  ")
        self.assertEqual(review.rating, 5)
        self.assertEqual(review.final_label, "NEGATIVE")
        self.assertEqual(review.reviewed_by, self.staff)
        self.assertEqual(review.classification_status, OrderReview.ClassificationStatus.REVIEWED)
        audit = ReviewClassificationAudit.objects.get(review=review)
        self.assertEqual(audit.final_label, "NEGATIVE")
        self.assertEqual(audit.reviewed_by, self.staff)

    def test_stale_review_and_late_callback_do_not_overwrite_human_decision(self):
        review = self.submit_review()
        OrderReview.objects.filter(pk=review.pk).update(
            classification_status=OrderReview.ClassificationStatus.PENDING_REVIEW,
            classification_version=1,
        )
        confirm_review_classification(
            review_id=review.pk,
            reviewer=self.staff,
            final_label="NEUTRAL",
            review_note="Đã xác nhận.",
            version=1,
        )
        with self.assertRaises(StaleReviewClassification):
            confirm_review_classification(
                review_id=review.pk,
                reviewer=self.owner,
                final_label="NEGATIVE",
                review_note="Cập nhật cũ.",
                version=1,
            )
        response = self.post_callback(self.callback_payload(review))
        self.assertEqual(response.json()["status"], "ignored")
        review.refresh_from_db()
        self.assertEqual(review.final_label, "NEUTRAL")
        self.assertEqual(review.reviewed_by, self.staff)


class OrderReviewWindowTests(OrderWorkflowTestCase):
    def complete_order(self, order=None, *, completed_at=None, status=None):
        order = order or self.create_order()
        completed_at = completed_at or timezone.now() - timedelta(hours=1)
        Order.objects.filter(pk=order.pk).update(
            status=status or Order.Status.HOAN_TAT,
            completed_at=completed_at,
        )
        order.refresh_from_db()
        return order

    def test_review_reminder_window_does_not_expire_review_eligibility(self):
        completed_at = timezone.now()
        deadline = completed_at + timedelta(hours=24)
        order = self.complete_order(completed_at=completed_at)

        self.assertEqual(
            review_state(order, now=completed_at - timedelta(microseconds=1))[0],
            "not_open",
        )
        self.assertEqual(review_state(order, now=completed_at)[0], REVIEW_AVAILABLE)
        self.assertEqual(review_state(order, now=deadline - timedelta(microseconds=1))[0], REVIEW_AVAILABLE)
        self.assertEqual(review_state(order, now=deadline)[0], REVIEW_AVAILABLE)
        self.assertEqual(review_state(order, now=deadline + timedelta(days=30))[0], REVIEW_AVAILABLE)

        with patch("orders.reviews.timezone.now", return_value=deadline):
            review, created = submit_order_review(
                order_id=order.pk,
                customer=self.customer,
                rating=5,
            )
        self.assertTrue(created)
        self.assertEqual(review.order_id, order.pk)

    def test_form_remains_submittable_after_the_24_hour_reminder_mark(self):
        completed_at = timezone.now() - timedelta(hours=23)
        order = self.complete_order(completed_at=completed_at)
        self.client.force_login(self.customer)
        detail = self.client.get(reverse("orders:order_detail", kwargs={"pk": order.pk}))
        self.assertContains(detail, "Đánh giá đơn hàng")

        deadline = completed_at + timedelta(hours=24)
        with patch("orders.reviews.timezone.now", return_value=deadline):
            response = self.client.post(
                reverse("orders:customer_submit_order_review", kwargs={"pk": order.pk}),
                {"rating": "5", "comment": "Sau mốc nhắc"},
            )
        self.assertRedirects(response, reverse("orders:order_detail", kwargs={"pk": order.pk}))
        self.assertEqual(OrderReview.objects.get(order=order).comment, "Sau mốc nhắc")
        self.assertEqual(order.status, Order.Status.HOAN_TAT)

    def test_customer_can_review_long_after_the_reminder_milestone(self):
        order = self.complete_order(
            completed_at=timezone.now() - timedelta(days=30)
        )
        self.client.force_login(self.customer)

        response = self.client.post(
            reverse("orders:customer_submit_order_review", kwargs={"pk": order.pk}),
            {
                "rating": "5",
                "comment": "Đánh giá muộn",
                "customer": str(self.other_customer.pk),
                "order_id": "untrusted",
                "customer_id": str(self.other_customer.pk),
            },
        )

        self.assertRedirects(
            response,
            reverse("orders:order_detail", kwargs={"pk": order.pk}),
        )
        review = OrderReview.objects.get(order=order)
        self.assertEqual(review.customer, self.customer)
        self.assertEqual(review.comment, "Đánh giá muộn")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.HOAN_TAT)

    def test_completed_order_without_timestamp_is_not_given_review_window(self):
        order = self.complete_order()
        order.completed_at = None
        order.save(update_fields=["completed_at"])
        self.assertEqual(review_state(order)[0], REVIEW_TIME_UNKNOWN)
        with self.assertRaises(InvalidOrderTransition):
            submit_order_review(
                order_id=order.pk,
                customer=self.customer,
                rating=5,
            )

    def test_completed_at_is_not_reset_when_completion_processing_repeats(self):
        from .delivery import _change_order

        completed_at = timezone.now() - timedelta(days=3)
        order = self.complete_order(completed_at=completed_at)
        _change_order(
            order,
            expected_version=order.version,
            old_status=Order.Status.HOAN_TAT,
            new_status=Order.Status.HOAN_TAT,
        )
        order.refresh_from_db()
        self.assertEqual(order.completed_at, completed_at)

    def test_get_does_not_change_review_or_history_data(self):
        order = self.complete_order()
        self.client.force_login(self.customer)
        before_history = OrderHistory.objects.filter(order=order).count()

        response = self.client.get(reverse("orders:order_detail", kwargs={"pk": order.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(OrderReview.objects.filter(order=order).exists())
        self.assertEqual(OrderHistory.objects.filter(order=order).count(), before_history)

    def test_post_requires_csrf_token(self):
        order = self.complete_order()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.customer)

        response = client.post(
            reverse("orders:customer_submit_order_review", kwargs={"pk": order.pk}),
            {"rating": "5", "comment": ""},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(OrderReview.objects.filter(order=order).exists())

    def test_review_comment_is_html_escaped(self):
        order = self.complete_order()
        submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=5,
            comment="<script>alert(1)</script>",
        )
        self.client.force_login(self.customer)

        response = self.client.get(reverse("orders:order_detail", kwargs={"pk": order.pk}))

        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_only_owner_can_reply_and_reply_is_immutable(self):
        order = self.complete_order()
        review, _ = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=1,
            comment="Nhận xét ban đầu",
        )
        route = reverse("orders:owner_reply_to_review", kwargs={"review_id": review.pk})

        self.client.force_login(self.staff)
        self.assertEqual(self.client.post(route, {"content": "staff"}).status_code, 403)
        with self.assertRaises(InvalidOrderTransition):
            from .reviews import reply_to_order_review

            reply_to_order_review(
                review_id=review.pk,
                owner=self.staff,
                content="Không được phép",
            )

        self.client.force_login(self.owner)
        response = self.client.post(route, {"content": "  Cảm ơn bạn.  "})
        self.assertRedirects(
            response,
            reverse("orders:owner_review_detail", kwargs={"review_id": review.pk}),
        )
        reply = ReviewReply.objects.get(review=review)
        self.assertEqual(reply.owner, self.owner)
        self.assertEqual(reply.content, "Cảm ơn bạn.")
        repeated, created = reply_to_order_review(
            review_id=review.pk,
            owner=self.owner,
            content="Nội dung mới",
        )
        self.assertFalse(created)
        self.assertEqual(repeated.pk, reply.pk)
        review.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((review.rating, review.comment), (1, "Nhận xét ban đầu"))
        self.assertEqual(order.status, Order.Status.HOAN_TAT)
        self.assertEqual(
            OrderHistory.objects.filter(
                order=order,
                action=OrderHistory.Action.ORDER_REVIEW_REPLIED,
            ).count(),
            1,
        )

    def test_customer_cannot_reply_and_owner_reply_is_html_escaped(self):
        order = self.complete_order()
        review, _ = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=5,
            comment="Tốt",
        )
        reply_url = reverse(
            "orders:owner_reply_to_review",
            kwargs={"review_id": review.pk},
        )
        self.client.force_login(self.customer)
        self.assertEqual(
            self.client.post(reply_url, {"content": "Giả mạo"}).status_code,
            403,
        )
        self.assertFalse(ReviewReply.objects.filter(review=review).exists())

        self.client.force_login(self.owner)
        response = self.client.post(
            reply_url,
            {"content": "<script>alert(1)</script>"},
        )
        self.assertRedirects(
            response,
            reverse("orders:owner_review_detail", kwargs={"review_id": review.pk}),
        )
        detail = self.client.get(
            reverse("orders:owner_review_detail", kwargs={"review_id": review.pk})
        )
        self.assertContains(detail, "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertNotContains(detail, "<script>alert(1)</script>")

    def test_owner_reply_get_does_not_create_reply(self):
        order = self.complete_order()
        review, _ = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=4,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse(
                "orders:owner_reply_to_review",
                kwargs={"review_id": review.pk},
            )
        )

        self.assertEqual(response.status_code, 405)
        self.assertFalse(ReviewReply.objects.filter(review=review).exists())

    def test_staff_can_view_review_only_in_permitted_order_detail(self):
        order = self.complete_order()
        submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=4,
            comment="Tốt",
        )
        self.client.force_login(self.staff)

        response = self.client.get(reverse("orders:staff_order_detail", kwargs={"pk": order.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4/5 sao")
        self.assertNotContains(response, "Gửi phản hồi")
        self.assertEqual(
            self.client.get(reverse("orders:owner_review_list")).status_code,
            403,
        )

    def test_owner_filter_and_dashboard_statistics_use_database_reviews(self):
        first = self.complete_order()
        second = self.complete_order(self.create_order(customer=self.other_customer))
        submit_order_review(
            order_id=first.pk,
            customer=self.customer,
            rating=2,
            comment="Một",
        )
        submit_order_review(
            order_id=second.pk,
            customer=self.other_customer,
            rating=4,
            comment="Hai",
        )
        self.client.force_login(self.owner)
        list_response = self.client.get(
            reverse("orders:owner_review_list"),
            {"rating": "2", "replied": "no", "query": first.code},
        )
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.context["page_obj"].paginator.count, 1)

        today = timezone.localdate().isoformat()
        dashboard = self.client.get(
            reverse("accounts:owner_dashboard"),
            {"date_from": today, "date_to": today},
        )
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.context["review_stats"]["total"], 2)
        self.assertEqual(dashboard.context["review_stats"]["average"], 3)
        self.assertEqual(dashboard.context["review_period_pending"], 2)
        self.assertEqual(
            {item["rating"]: item["count"] for item in dashboard.context["review_rating_counts"]},
            {1: 0, 2: 1, 3: 0, 4: 1, 5: 0},
        )

    def test_dashboard_and_detail_status_does_not_change_order_completion(self):
        order = self.complete_order()
        self.client.force_login(self.customer)
        self.client.get(reverse("accounts:customer_dashboard"))
        response = self.client.get(reverse("orders:order_detail", kwargs={"pk": order.pk}))
        self.assertContains(response, "Đánh giá đơn hàng")
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.HOAN_TAT)
