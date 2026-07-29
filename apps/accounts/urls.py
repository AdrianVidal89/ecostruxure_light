"""Accounts URL configuration (auth, profile, admin user management)."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    # --- Authentication ---
    path("login/", views.AppLoginView.as_view(), name="login"),
    path("logout/", views.AppLogoutView.as_view(), name="logout"),
    path(
        "password/change/",
        views.AppPasswordChangeView.as_view(),
        name="password_change",
    ),
    # --- Self-service profile ---
    path("profile/", views.ProfileView.as_view(), name="profile"),
    # --- Admin: user management ---
    path("users/", views.UserListView.as_view(), name="user_list"),
    path("users/import/", views.UserImportView.as_view(), name="user_import"),
    path("users/create/", views.UserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/edit/", views.UserUpdateView.as_view(), name="user_edit"),
    path(
        "users/<int:pk>/toggle-active/",
        views.UserToggleActiveView.as_view(),
        name="user_toggle_active",
    ),
    path(
        "users/<int:pk>/delete/",
        views.UserDeleteView.as_view(),
        name="user_delete",
    ),
]
