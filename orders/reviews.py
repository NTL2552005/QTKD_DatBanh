from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User

from .models import Order, OrderHistory, OrderReview, ReviewReply
from .services import InvalidOrderTransition

REVIEW_AVAILABLE = "available"
REVIEW_SUBMITTED = "submitted"
REVIEW_EXPIRED = "expired"
REVIEW_NOT_COMPLETED = "not_completed"
REVIEW_TIME_UNKNOWN = "time_unknown"
REVIEW_NOT_OPEN = "not_open"


def review_deadline(completed_at):
    if completed_at is None:
        return None
    return completed_at + timedelta(hours=settings.ORDER_REVIEW_WINDOW_HOURS)


def review_state(order, *, review=None, now=None):
    if review is None:
        try:
            review = order.review
        except OrderReview.DoesNotExist:
            review = None
    if review is not None:
        return REVIEW_SUBMITTED, review_deadline(order.completed_at)
    if order.status != Order.Status.HOAN_TAT:
        return REVIEW_NOT_COMPLETED, None
    if order.completed_at is None:
        return REVIEW_TIME_UNKNOWN, None
    deadline = review_deadline(order.completed_at)
    current_time = now or timezone.now()
    if current_time < order.completed_at:
        return REVIEW_NOT_OPEN, deadline
    return REVIEW_AVAILABLE, deadline


@transaction.atomic
def submit_order_review(*, order_id, customer, rating, comment=""):
    if customer.role != User.Role.CUSTOMER:
        raise InvalidOrderTransition("Chỉ khách hàng được gửi đánh giá.")
    order = Order.objects.filter(pk=order_id, customer=customer).first()
    if order is None:
        raise InvalidOrderTransition("Không tìm thấy đơn hàng của khách.")

    existing = OrderReview.objects.filter(order=order).first()
    if existing is not None:
        return existing, False

    if order.status != Order.Status.HOAN_TAT:
        raise InvalidOrderTransition("Chỉ đánh giá được đơn đã hoàn tất.")
    if order.completed_at is None:
        raise InvalidOrderTransition(
            "Chưa xác định được thời điểm hoàn tất đơn; chưa thể nhận đánh giá."
        )
    now = timezone.now()
    deadline = review_deadline(order.completed_at)
    if now < order.completed_at:
        raise InvalidOrderTransition("Thời gian gửi đánh giá chưa bắt đầu.")
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        raise ValidationError({"rating": "Điểm đánh giá phải là số nguyên từ 1 đến 5."})
    comment = comment.strip()
    if len(comment) > 2000:
        raise ValidationError({"comment": "Nhận xét không được vượt quá 2.000 ký tự."})

    try:
        with transaction.atomic():
            review = OrderReview.objects.create(
                order=order,
                customer=customer,
                rating=rating,
                comment=comment,
            )
    except IntegrityError:
        existing = OrderReview.objects.filter(order=order).first()
        if existing is None:
            raise
        return existing, False

    OrderHistory.objects.create(
        order=order,
        actor=customer,
        action=OrderHistory.Action.ORDER_REVIEW_SUBMITTED,
        from_status=order.status,
        to_status=order.status,
        message="Khách gửi đánh giá sau khi đơn hoàn tất.",
        details={"review_id": review.pk, "rating": review.rating},
        quote=order.current_quote,
    )
    return review, True


@transaction.atomic
def reply_to_order_review(*, review_id, owner, content):
    if owner.role != User.Role.OWNER:
        raise InvalidOrderTransition("Chỉ chủ cửa hàng được phản hồi đánh giá.")
    review = (
        OrderReview.objects.select_related("order")
        .filter(pk=review_id)
        .first()
    )
    if review is None:
        raise InvalidOrderTransition("Không tìm thấy đánh giá.")

    existing = ReviewReply.objects.filter(review=review).first()
    if existing is not None:
        return existing, False

    content = content.strip()
    if not content:
        raise ValidationError({"content": "Vui lòng nhập nội dung phản hồi."})
    if len(content) > 2000:
        raise ValidationError({"content": "Phản hồi không được vượt quá 2.000 ký tự."})

    try:
        with transaction.atomic():
            reply = ReviewReply.objects.create(
                review=review,
                owner=owner,
                content=content,
            )
    except IntegrityError:
        existing = ReviewReply.objects.filter(review=review).first()
        if existing is None:
            raise
        return existing, False

    OrderHistory.objects.create(
        order=review.order,
        actor=owner,
        action=OrderHistory.Action.ORDER_REVIEW_REPLIED,
        from_status=review.order.status,
        to_status=review.order.status,
        message="Chủ cửa hàng phản hồi đánh giá.",
        details={"review_id": review.pk, "reply_id": reply.pk},
        quote=review.order.current_quote,
    )
    return reply, True
