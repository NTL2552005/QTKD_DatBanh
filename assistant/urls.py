from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("assistant/", views.index, name="index"),
    path("assistant/start/", views.create_session, name="create_session"),
    path(
        "assistant/policies/<str:code>/",
        views.published_policy,
        name="policy_detail",
    ),
    path("assistant/<uuid:session_id>/", views.chat_session, name="chat_session"),
    path(
        "owner/assistant/policies/",
        views.owner_policy_list,
        name="owner_policy_list",
    ),
    path(
        "owner/assistant/policies/add/",
        views.owner_policy_create,
        name="owner_policy_create",
    ),
    path(
        "owner/assistant/policies/<int:policy_id>/edit/",
        views.owner_policy_edit,
        name="owner_policy_edit",
    ),
    path(
        "owner/assistant/policies/<int:policy_id>/publish/",
        views.owner_policy_toggle,
        name="owner_policy_toggle",
    ),
]
