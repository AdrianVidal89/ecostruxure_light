from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("branding/", views.BrandingView.as_view(), name="branding"),
]
