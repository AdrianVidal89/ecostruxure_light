"""
Account views: authentication, self-service profile/password, and the
admin-only custom user-management UI (list / create / edit / toggle active).

The Django admin is NOT used as the user-facing management UI (per the spec);
these custom views are the canonical interface.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView, View

from apps.core.mixins import AdminRequiredMixin

from . import importer
from .forms import (
    AdminUserCreateForm,
    AdminUserUpdateForm,
    ProfileForm,
    StyledAuthenticationForm,
    StyledPasswordChangeForm,
    UserImportUploadForm,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class AppLoginView(LoginView):
    """Themed login page. Redirects already-authenticated users away."""

    template_name = "accounts/login.html"
    authentication_form = StyledAuthenticationForm
    redirect_authenticated_user = True


class AppLogoutView(LogoutView):
    """Log out (POST only, per Django defaults) and return to the login page."""

    next_page = reverse_lazy("accounts:login")


class AppPasswordChangeView(LoginRequiredMixin, auth_views.PasswordChangeView):
    """Self-service password change for the logged-in user."""

    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("accounts:profile")

    def form_valid(self, form):
        messages.success(self.request, "Your password has been changed.")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Profile (self-service)
# ---------------------------------------------------------------------------
class ProfileView(LoginRequiredMixin, UpdateView):
    """View and edit your own profile. ``role`` is read-only for non-admins."""

    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("accounts:profile")

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, "Your profile has been updated.")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Admin: user management
# ---------------------------------------------------------------------------
class UserListView(AdminRequiredMixin, ListView):
    """Admin-only list of all users with search."""

    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 25

    def get_queryset(self):
        qs = User.objects.all()
        query = self.request.GET.get("q", "").strip()
        if query:
            from django.db.models import Q

            qs = qs.filter(
                Q(username__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
            )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["query"] = self.request.GET.get("q", "")
        return ctx


class UserCreateView(AdminRequiredMixin, CreateView):
    """Admin-only: create a user, assigning their global role."""

    model = User
    form_class = AdminUserCreateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Create user"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f"User “{form.instance.username}” created.")
        return super().form_valid(form)


class UserUpdateView(AdminRequiredMixin, UpdateView):
    """Admin-only: edit a user's details, role, and active status."""

    model = User
    form_class = AdminUserUpdateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit user · {self.object.username}"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f"User “{form.instance.username}” updated.")
        return super().form_valid(form)


class UserToggleActiveView(AdminRequiredMixin, View):
    """Admin-only: activate / deactivate a user (POST).

    Deactivating blocks login while preserving the user's history and
    relationships (preferred over deletion). Admins cannot deactivate
    themselves.
    """

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        if target == request.user:
            messages.error(request, "You cannot deactivate your own account.")
            return redirect("accounts:user_list")
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        state = "activated" if target.is_active else "deactivated"
        messages.success(request, f"User “{target.username}” {state}.")
        return redirect("accounts:user_list")


# ---------------------------------------------------------------------------
# Bulk user import (admin) — upload → map columns → preview → confirm
# ---------------------------------------------------------------------------
SESSION_KEY = "user_import"


class UserImportView(AdminRequiredMixin, View):
    """Three-stage bulk import. Nothing is written until the preview is confirmed."""

    template_name = "accounts/user_import.html"

    def _mapping_from_post(self, request):
        mapping = {}
        for field, _label, _req in importer.USER_FIELDS:
            raw = request.POST.get(f"map_{field}", "")
            mapping[field] = int(raw) if raw.isdigit() else None
        return mapping

    def get(self, request):
        request.session.pop(SESSION_KEY, None)
        return render(request, self.template_name, {"stage": "upload", "form": UserImportUploadForm()})

    def post(self, request):
        action = request.POST.get("action")

        # Stage 1 → 2: a file was uploaded.
        if "file" in request.FILES:
            form = UserImportUploadForm(request.POST, request.FILES)
            if not form.is_valid():
                return render(request, self.template_name, {"stage": "upload", "form": form})
            try:
                headers, rows = importer.parse_user_file(
                    form.cleaned_data["file"], form.cleaned_data["file"].name
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return render(request, self.template_name, {"stage": "upload", "form": UserImportUploadForm()})
            if not rows:
                messages.error(request, "The file has no data rows.")
                return render(request, self.template_name, {"stage": "upload", "form": UserImportUploadForm()})
            rows = rows[:5000]
            request.session[SESSION_KEY] = {"headers": headers, "rows": rows}
            mapping = importer.auto_map(headers)
            return self._render_preview(request, headers, rows, mapping)

        stored = request.session.get(SESSION_KEY)
        if not stored:
            messages.error(request, "Your import session expired — please upload the file again.")
            return redirect("accounts:user_import")
        headers, rows = stored["headers"], stored["rows"]
        mapping = self._mapping_from_post(request)

        # Stage 3: confirm → commit (with any per-row edits applied).
        if action == "confirm":
            preview, summary = importer.build_preview(headers, rows, mapping)
            self._apply_overrides(request, preview)
            stats = importer.commit_users(
                preview, initial_password=request.POST.get("initial_password", "").strip()
            )
            request.session.pop(SESSION_KEY, None)
            messages.success(
                request,
                f"Import done: {stats['created']} created, {stats['updated']} updated, "
                f"{stats['skipped']} skipped.",
            )
            return render(request, self.template_name, {"stage": "done", "stats": stats})

        # Stage 2: (re)build preview with the chosen mapping.
        return self._render_preview(request, headers, rows, mapping)

    def _apply_overrides(self, request, preview):
        """Apply per-row edits from the preview form (currently the role)."""
        valid_roles = dict(User.Role.choices)
        for prow in preview:
            role = request.POST.get(f"role_{prow['index']}")
            if role in valid_roles:
                prow["data"]["role"] = role
        return preview

    def _render_preview(self, request, headers, rows, mapping):
        preview, summary = importer.build_preview(headers, rows, mapping)
        self._apply_overrides(request, preview)  # keep edits across re-previews
        return render(
            request,
            self.template_name,
            {
                "stage": "preview",
                "headers": headers,
                "user_fields": importer.USER_FIELDS,
                "mapping": mapping,
                "preview": preview,
                "summary": summary,
                "importable": summary["valid"] + summary["warning"],
                "role_choices": User.Role.choices,
            },
        )
