from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from catalog.models import CakeTemplate, Category
from orders.forms import ReviewPeriodFilterForm
from orders.models import (
    ApprovalRequest,
    IntakeQuoteApproval,
    Order,
    OrderAlternative,
    OrderIncident,
    OrderReview,
)
from orders.reviews import review_deadline

from .decorators import role_required
from .forms import RegistrationForm
from .models import User


def home(request):
    return render(request, "home.html")


def dashboard_url_for(user):
    route_names = {
        User.Role.CUSTOMER: "accounts:customer_dashboard",
        User.Role.STAFF: "accounts:staff_dashboard",
        User.Role.OWNER: "accounts:owner_dashboard",
    }
    return reverse(route_names.get(user.role, "home"))


def register(request):
    if request.user.is_authenticated:
        return redirect(dashboard_url_for(request.user))

    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Đăng ký thành công. Chào mừng bạn đến với BiBi Cake!")
        return redirect(dashboard_url_for(user))
    return render(request, "accounts/register.html", {"form": form})


def sign_in(request):
    if request.user.is_authenticated:
        return redirect(dashboard_url_for(request.user))

    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, "Đăng nhập thành công.")
            next_url = request.POST.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect(dashboard_url_for(user))
        error = "Tên đăng nhập hoặc mật khẩu không đúng."

    return render(
        request,
        "accounts/login.html",
        {"error": error, "next": request.GET.get("next", "")},
    )


def sign_out(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    messages.success(request, "Bạn đã đăng xuất.")
    return redirect("home")


@login_required
@role_required(User.Role.CUSTOMER)
def customer_dashboard(request):
    now = timezone.now()
    total_orders = Order.objects.filter(customer=request.user).count()
    order_counts = [
        {
            "label": label,
            "count": Order.objects.filter(customer=request.user, status=status).count(),
        }
        for status, label in Order.Status.choices
    ]
    reviewable = Order.objects.filter(
        customer=request.user,
        status=Order.Status.HOAN_TAT,
        completed_at__lte=now,
        review__isnull=True,
    )
    reviewable_orders = list(
        reviewable.select_related("cake_template")
        .order_by("completed_at")[:5]
    )
    for order in reviewable_orders:
        order.review_deadline = review_deadline(order.completed_at)
    alternative_orders = list(
        Order.objects.filter(
            customer=request.user,
            status=Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN,
            alternatives__status=OrderAlternative.Status.PENDING_CUSTOMER,
        )
        .select_related("cake_template")
        .order_by("-updated_at")[:5]
    )
    pending_alternative_count = Order.objects.filter(
        customer=request.user,
        status=Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN,
        alternatives__status=OrderAlternative.Status.PENDING_CUSTOMER,
    ).distinct().count()
    return render(
        request,
        "accounts/dashboard.html",
        {
            "role_label": "Khách hàng",
            "order_counts": order_counts,
            "has_orders": total_orders > 0,
            "reviewable_orders": reviewable_orders,
            "reviewable_order_count": reviewable.count(),
            "alternative_orders": alternative_orders,
            "pending_alternative_count": pending_alternative_count,
        },
    )


@login_required
@role_required(User.Role.STAFF)
def staff_dashboard(request):
    queue_statuses = (
        Order.Status.CHO_KIEM_TRA,
        Order.Status.CHO_TRAO_DOI,
        Order.Status.CHO_CHU_DUYET_PHUONG_AN,
        Order.Status.CHO_CHU_DUYET_BAO_GIA,
        Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN,
        Order.Status.CHO_KHACH_XAC_NHAN,
        Order.Status.DANG_LAM,
        Order.Status.CHO_DUYET_ANH,
        Order.Status.CHO_XU_LY_CHINH_SUA,
        Order.Status.SAN_SANG_GIAO,
        Order.Status.DANG_GIAO,
        Order.Status.GIAO_THAT_BAI,
        Order.Status.CHO_XU_LY_SU_CO,
        Order.Status.DA_GIAO_CHO_THANH_TOAN,
    )
    queue_total = Order.objects.filter(status__in=queue_statuses).count()
    order_counts = [
        {
            "label": label,
            "count": Order.objects.filter(status=status).count(),
        }
        for status, label in (
            (Order.Status.CHO_KIEM_TRA, "Cần kiểm tra"),
            (Order.Status.CHO_TRAO_DOI, "Chờ trao đổi"),
            (Order.Status.CHO_CHU_DUYET_PHUONG_AN, "Chờ chủ duyệt phương án"),
            (Order.Status.CHO_CHU_DUYET_BAO_GIA, "Chờ chủ xử lý tiếp nhận"),
            (
                Order.Status.CHO_KHACH_PHAN_HOI_PHUONG_AN,
                "Chờ khách phản hồi phương án",
            ),
            (Order.Status.CHO_KHACH_XAC_NHAN, "Chờ khách xác nhận"),
            (Order.Status.DANG_LAM, "Đang làm bánh"),
            (Order.Status.CHO_DUYET_ANH, "Chờ khách duyệt ảnh"),
            (Order.Status.CHO_XU_LY_CHINH_SUA, "Chờ xử lý chỉnh sửa"),
            (Order.Status.SAN_SANG_GIAO, "Sẵn sàng giao"),
            (Order.Status.DANG_GIAO, "Đang giao"),
            (Order.Status.GIAO_THAT_BAI, "Giao thất bại"),
            (Order.Status.CHO_XU_LY_SU_CO, "Chờ xử lý sự cố"),
            (Order.Status.DA_GIAO_CHO_THANH_TOAN, "Đã giao, chờ thanh toán"),
        )
    ]
    return render(
        request,
        "accounts/dashboard.html",
        {
            "role_label": "Nhân viên",
            "order_counts": order_counts,
            "has_orders": queue_total > 0,
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_dashboard(request):
    catalog_counts = {
        "categories_total": Category.objects.count(),
        "categories_active": Category.objects.filter(is_active=True).count(),
        "cakes_total": CakeTemplate.objects.count(),
        "cakes_public": CakeTemplate.objects.public().count(),
    }
    pending_approvals = ApprovalRequest.objects.filter(
        status=ApprovalRequest.Status.PENDING
    ).count()
    pending_intake_alternatives = OrderAlternative.objects.filter(
        status=OrderAlternative.Status.PENDING_OWNER
    ).count()
    pending_intake_orders = (
        Order.objects.filter(
            Q(status=Order.Status.CHO_CHU_DUYET_BAO_GIA)
            | Q(
                intake_handler_role="OWNER",
                status__in=(
                    Order.Status.CHO_TRAO_DOI,
                    Order.Status.CHO_KHACH_XAC_NHAN,
                ),
            )
            | Q(
                status=Order.Status.CHO_KIEM_TRA,
                intake_handler_role="STAFF",
                intake_quote_approvals__status=IntakeQuoteApproval.Status.APPROVED,
            )
        )
        .distinct()
        .count()
    )
    pending_incidents = OrderIncident.objects.filter(
        status=OrderIncident.Status.PENDING_OWNER
    ).count()
    today = timezone.localdate()
    period_form = ReviewPeriodFilterForm(
        request.GET or None,
        initial={
            "date_from": today - timedelta(days=29),
            "date_to": today,
        },
    )
    if request.GET:
        period_valid = period_form.is_valid()
    else:
        period_valid = True
    review_stats = None
    review_rating_counts = []
    recent_reviews = []
    review_period_pending = None
    if period_valid:
        date_from = (
            period_form.cleaned_data["date_from"]
            if request.GET
            else today - timedelta(days=29)
        )
        date_to = period_form.cleaned_data["date_to"] if request.GET else today
        start = timezone.make_aware(datetime.combine(date_from, time.min))
        end = timezone.make_aware(
            datetime.combine(date_to + timedelta(days=1), time.min)
        )
        filtered_reviews = OrderReview.objects.filter(
            created_at__gte=start,
            created_at__lt=end,
        )
        review_stats = filtered_reviews.aggregate(
            total=Count("pk"),
            average=Avg("rating"),
        )
        counts_by_rating = {
            entry["rating"]: entry["count"]
            for entry in filtered_reviews.values("rating")
            .annotate(count=Count("pk"))
        }
        review_rating_counts = [
            {"rating": rating, "count": counts_by_rating.get(rating, 0)}
            for rating in range(1, 6)
        ]
        recent_reviews = list(
            filtered_reviews.select_related("order", "customer", "reply__owner")
            .order_by("-created_at", "-pk")[:5]
        )
        review_period_pending = filtered_reviews.filter(reply__isnull=True).count()
    else:
        date_from = date_to = None
    return render(
        request,
        "accounts/dashboard.html",
        {
            "role_label": "Chủ cửa hàng",
            "catalog_counts": catalog_counts,
            "pending_approvals": pending_approvals,
            "pending_intake_alternatives": pending_intake_alternatives,
            "pending_intake_orders": pending_intake_orders,
            "pending_incidents": pending_incidents,
            "review_period_form": period_form,
            "review_period_valid": period_valid,
            "review_date_from": date_from,
            "review_date_to": date_to,
            "review_stats": review_stats,
            "review_rating_counts": review_rating_counts,
            "recent_reviews": recent_reviews,
            "review_period_pending": review_period_pending,
        },
    )
