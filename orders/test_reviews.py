from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from .models import Order, OrderHistory, OrderReview, ReviewReply
from .reviews import (
    REVIEW_AVAILABLE,
    REVIEW_TIME_UNKNOWN,
    review_state,
    submit_order_review,
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
        self.assertEqual(review.comment, "Bánh rất ngon.")
        self.assertEqual(review.customer, self.customer)
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

    def test_comment_is_trimmed_optional_and_limited_to_2000_characters(self):
        order = self.complete_order()
        form = OrderReviewForm({"rating": "4", "comment": "   "})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["comment"], "")
        review, _ = submit_order_review(
            order_id=order.pk,
            customer=self.customer,
            rating=4,
            comment="   ",
        )
        self.assertEqual(review.comment, "")

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
