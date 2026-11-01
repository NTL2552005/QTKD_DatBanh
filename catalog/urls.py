from django.urls import path

from . import views

app_name = "catalog"

urlpatterns = [
    path("cakes/", views.cake_list, name="cake_list"),
    path("cakes/<slug:slug>/", views.cake_detail, name="cake_detail"),
    path(
        "owner/catalog/categories/",
        views.owner_category_list,
        name="owner_category_list",
    ),
    path(
        "owner/catalog/categories/add/",
        views.owner_category_create,
        name="owner_category_create",
    ),
    path(
        "owner/catalog/categories/<int:pk>/edit/",
        views.owner_category_edit,
        name="owner_category_edit",
    ),
    path(
        "owner/catalog/categories/<int:pk>/toggle/",
        views.owner_category_toggle,
        name="owner_category_toggle",
    ),
    path("owner/catalog/cakes/", views.owner_cake_list, name="owner_cake_list"),
    path(
        "owner/catalog/cakes/add/",
        views.owner_cake_create,
        name="owner_cake_create",
    ),
    path(
        "owner/catalog/cakes/<int:pk>/edit/",
        views.owner_cake_edit,
        name="owner_cake_edit",
    ),
    path(
        "owner/catalog/cakes/<int:pk>/toggle/",
        views.owner_cake_toggle,
        name="owner_cake_toggle",
    ),
]
