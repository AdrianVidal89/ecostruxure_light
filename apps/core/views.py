"""
Core views.

The dashboard is the post-login landing page: a grid of the POCs the user
belongs to (admins see all), plus a global stats row for admins.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect, render
from django.views.generic import TemplateView, View

from .forms import BrandingForm
from .mixins import AdminRequiredMixin
from .models import SiteBranding


class DashboardView(LoginRequiredMixin, TemplateView):
    """Post-login landing: assigned POCs (all, for admins) + admin stats."""

    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Imported here to avoid a core → pocs import at app-load time.
        from apps.pocs.models import POC

        user = self.request.user
        if user.is_admin:
            pocs = POC.objects.all()
        else:
            pocs = POC.objects.filter(memberships__user=user).distinct()

        ctx["pocs"] = pocs.order_by("-created_at")

        if user.is_admin:
            ctx["stats"] = {
                "total": POC.objects.count(),
                "active": POC.objects.filter(status=POC.Status.ACTIVE).count(),
                "completed": POC.objects.filter(
                    status=POC.Status.COMPLETED
                ).count(),
            }
        return ctx


class BrandingView(AdminRequiredMixin, View):
    """Admin: upload / replace / remove the site logo."""

    template_name = "core/branding.html"

    def get(self, request):
        obj = SiteBranding.load()
        return render(request, self.template_name, {"form": BrandingForm(instance=obj), "branding_obj": obj})

    def post(self, request):
        obj = SiteBranding.load()
        if request.POST.get("remove"):
            if obj.logo:
                obj.logo.delete(save=False)
            obj.logo = None
            obj.save()
            messages.success(request, "Logo removed — the default mark is back.")
            return redirect("core:branding")

        old = obj.logo.name if obj.logo else None
        form = BrandingForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            if old and "logo" in request.FILES:  # replace → drop the old file
                obj.logo.storage.delete(old)
            form.save()
            messages.success(request, "Logo updated.")
            return redirect("core:branding")
        return render(request, self.template_name, {"form": form, "branding_obj": obj})
