from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from accounts.decorators import role_required
from accounts.models import User

from .forms import CakeTemplateForm, CategoryForm
from .models import CakeTemplate, Category

PAGE_SIZE = 9
OWNER_PAGE_SIZE = 12
PUBLIC_SORTS = {
    "newest": ("-created_at", "-pk"),
    "price_asc": ("reference_price", "pk"),
    "price_desc": ("-reference_price", "pk"),
}


def cake_list(request):
    cakes = CakeTemplate.objects.public().select_related("category")
    query = request.GET.get("q", "").strip()
    category_slug = request.GET.get("category", "").strip()
    sort_key = request.GET.get("sort", "newest")
    if query:
        cakes = cakes.filter(name__icontains=query)
    if category_slug:
        cakes = cakes.filter(category__slug=category_slug)
    if sort_key not in PUBLIC_SORTS:
        sort_key = "newest"
    cakes = cakes.order_by(*PUBLIC_SORTS[sort_key])

    paginator = Paginator(cakes, PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "catalog/cake_list.html",
        {
            "page_obj": page_obj,
            "categories": Category.objects.filter(is_active=True),
            "search_query": query,
            "selected_category": category_slug,
            "selected_sort": sort_key,
            "pagination_query": query_params.urlencode(),
        },
    )


def cake_detail(request, slug):
    cake = get_object_or_404(
        CakeTemplate.objects.public().select_related("category"),
        slug=slug,
    )
    return render(request, "catalog/cake_detail.html", {"cake": cake})


@login_required
@role_required(User.Role.OWNER)
def owner_category_list(request):
    categories = Category.objects.annotate(
        cake_count=Count("cake_templates")
    ).order_by("name")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if query:
        categories = categories.filter(name__icontains=query)
    if status == "active":
        categories = categories.filter(is_active=True)
    elif status == "inactive":
        categories = categories.filter(is_active=False)
    page_obj = Paginator(categories, OWNER_PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "catalog/owner_category_list.html",
        {
            "page_obj": page_obj,
            "search_query": query,
            "selected_status": status,
            "pagination_query": query_params.urlencode(),
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_category_create(request):
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        category = form.save()
        messages.success(request, f"Đã tạo danh mục “{category.name}”.")
        return redirect("catalog:owner_category_list")
    return render(
        request,
        "catalog/owner_category_form.html",
        {"form": form, "page_title": "Thêm danh mục"},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_category_edit(request, pk):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        category = form.save()
        messages.success(request, f"Đã cập nhật danh mục “{category.name}”.")
        return redirect("catalog:owner_category_list")
    return render(
        request,
        "catalog/owner_category_form.html",
        {"form": form, "category": category, "page_title": "Sửa danh mục"},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_category_toggle(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    category = get_object_or_404(Category, pk=pk)
    category.is_active = not category.is_active
    category.save(update_fields=("is_active", "updated_at"))
    if category.is_active:
        messages.success(request, f"Đã kích hoạt danh mục “{category.name}”.")
    else:
        messages.success(
            request,
            f"Đã ngừng danh mục “{category.name}”; các mẫu bánh thuộc danh mục "
            "này đã được ẩn khỏi trang khách hàng.",
        )
    return redirect("catalog:owner_category_list")


@login_required
@role_required(User.Role.OWNER)
def owner_cake_list(request):
    cakes = CakeTemplate.objects.select_related("category").order_by(
        "-updated_at", "-pk"
    )
    query = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()
    status = request.GET.get("status", "")
    if query:
        cakes = cakes.filter(name__icontains=query)
    if category_id.isdigit():
        cakes = cakes.filter(category_id=int(category_id))
    if status == "available":
        cakes = cakes.filter(is_available=True)
    elif status == "unavailable":
        cakes = cakes.filter(is_available=False)
    page_obj = Paginator(cakes, OWNER_PAGE_SIZE).get_page(request.GET.get("page"))
    query_params = request.GET.copy()
    query_params.pop("page", None)
    return render(
        request,
        "catalog/owner_cake_list.html",
        {
            "page_obj": page_obj,
            "categories": Category.objects.all(),
            "search_query": query,
            "selected_category": category_id,
            "selected_status": status,
            "pagination_query": query_params.urlencode(),
        },
    )


@login_required
@role_required(User.Role.OWNER)
def owner_cake_create(request):
    form = CakeTemplateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        cake = form.save()
        messages.success(request, f"Đã tạo mẫu bánh “{cake.name}”.")
        return redirect("catalog:owner_cake_list")
    return render(
        request,
        "catalog/owner_cake_form.html",
        {"form": form, "page_title": "Thêm mẫu bánh"},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_cake_edit(request, pk):
    cake = get_object_or_404(
        CakeTemplate.objects.select_related("category"),
        pk=pk,
    )
    form = CakeTemplateForm(
        request.POST or None,
        request.FILES or None,
        instance=cake,
    )
    if request.method == "POST" and form.is_valid():
        cake = form.save()
        if form.advisory_verification_reset:
            messages.warning(
                request,
                f"Đã cập nhật mẫu bánh “{cake.name}”. Thông tin tư vấn đã thay đổi "
                "nên trạng thái xác nhận được gỡ; hãy rà soát và xác nhận lại.",
            )
        else:
            messages.success(request, f"Đã cập nhật mẫu bánh “{cake.name}”.")
        return redirect("catalog:owner_cake_list")
    return render(
        request,
        "catalog/owner_cake_form.html",
        {"form": form, "cake": cake, "page_title": "Sửa mẫu bánh"},
    )


@login_required
@role_required(User.Role.OWNER)
def owner_cake_toggle(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    cake = get_object_or_404(CakeTemplate, pk=pk)
    cake.is_available = not cake.is_available
    cake.save(update_fields=("is_available", "updated_at"))
    if cake.is_available:
        messages.success(request, f"Đã mở nhận đặt theo mẫu “{cake.name}”.")
    else:
        messages.success(request, f"Đã ngừng nhận đặt theo mẫu “{cake.name}”.")
    return redirect("catalog:owner_cake_list")
