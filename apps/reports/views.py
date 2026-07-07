"""
Report views.

* Global Reports area (any user): custom-report converter + the user's
  generated reports.
* Report-type library management (admin only).
* Phase-report generation (POC members) and a permission-checked download.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.files.base import ContentFile
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from apps.core.mixins import AdminRequiredMixin
from apps.pocs.audit import record_audit
from apps.pocs.models import Phase, POCMembership

from .forms import CustomReportForm, ReportSettingsForm, ReportTypeForm
from .generation import generate_custom_report, generate_phase_report
from .models import GeneratedReport, ReportSettings, ReportType


# ---------------------------------------------------------------------------
# Global Reports area (custom reports)
# ---------------------------------------------------------------------------
class ReportsHomeView(LoginRequiredMixin, TemplateView):
    """Custom-report converter + the current user's generated reports."""

    template_name = "reports/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = CustomReportForm()
        ctx["has_types"] = ReportType.objects.filter(is_active=True).exists()
        ctx["my_reports"] = GeneratedReport.objects.filter(
            requested_by=self.request.user, kind=GeneratedReport.Kind.CUSTOM
        )[:50]
        ctx["is_admin"] = self.request.user.is_admin
        return ctx


@require_POST
def custom_generate(request):
    """Generate a custom report from an uploaded source (any authenticated user)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    form = CustomReportForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error[0])
        return redirect("reports:home")

    # A file takes precedence; otherwise wrap the pasted Markdown as a source.
    source = form.cleaned_data.get("source_file")
    if not source:
        text = form.cleaned_data["source_text"]
        source = ContentFile(text.encode("utf-8"), name="pasted.md")

    report = generate_custom_report(
        form.cleaned_data["report_type"], source, request.user
    )
    if report.status == GeneratedReport.Status.ERROR:
        messages.error(request, f"Report generation failed: {report.error_message}")
    else:
        messages.success(request, "Report generated.")
    return redirect("reports:home")


# ---------------------------------------------------------------------------
# Report-type library (admin only)
# ---------------------------------------------------------------------------
class ReportTypeListView(AdminRequiredMixin, ListView):
    model = ReportType
    template_name = "reports/report_type_list.html"
    context_object_name = "report_types"


class ReportTypeCreateView(AdminRequiredMixin, CreateView):
    model = ReportType
    form_class = ReportTypeForm
    template_name = "reports/report_type_form.html"
    success_url = reverse_lazy("reports:type_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Create report type"
        return ctx

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, f"Report type “{form.instance.name}” created.")
        return super().form_valid(form)


class ReportTypeUpdateView(AdminRequiredMixin, UpdateView):
    model = ReportType
    form_class = ReportTypeForm
    template_name = "reports/report_type_form.html"
    success_url = reverse_lazy("reports:type_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit report type · {self.object.name}"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f"Report type “{form.instance.name}” updated.")
        return super().form_valid(form)


class ReportTypeDeleteView(AdminRequiredMixin, DeleteView):
    model = ReportType
    template_name = "reports/report_type_confirm_delete.html"
    success_url = reverse_lazy("reports:type_list")

    def form_valid(self, form):
        messages.success(self.request, f"Report type “{self.object.name}” deleted.")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Global report settings (admin) — default template fallback
# ---------------------------------------------------------------------------
class ReportSettingsView(AdminRequiredMixin, UpdateView):
    """Admin: upload/replace the global default report template (singleton)."""

    model = ReportSettings
    form_class = ReportSettingsForm
    template_name = "reports/report_settings.html"
    success_url = reverse_lazy("reports:settings")

    def get_object(self, queryset=None):
        return ReportSettings.load()

    def form_valid(self, form):
        messages.success(self.request, "Default report template saved.")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# Phase report generation (POC members)
# ---------------------------------------------------------------------------
def _is_poc_member(user, poc):
    return user.is_admin or POCMembership.objects.filter(poc=poc, user=user).exists()


@require_POST
def phase_report_generate(request, phase_pk):
    """Generate a phase report from its tests (POC members + admin).

    No pre-check on ``phase.is_reportable`` — that only reflects the phase's
    *own* template, but generation also falls back to the global default
    (see ``resolve_phase_template_path``); let it raise there instead, same
    as the documents-based generator (``phase_documents_report``).
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not _is_poc_member(request.user, phase.poc):
        raise PermissionDenied

    report = generate_phase_report(phase, request.user)
    if report.status == GeneratedReport.Status.ERROR:
        messages.error(request, f"Report generation failed: {report.error_message}")
    else:
        messages.success(request, f"Report for “{phase.name}” generated.")
    return redirect("pocs:phase_detail", phase_pk=phase.pk)


# ---------------------------------------------------------------------------
# Download (permission-checked)
# ---------------------------------------------------------------------------
def report_download(request, pk):
    """Stream a generated report's output, enforcing access rules."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    report = get_object_or_404(GeneratedReport, pk=pk)

    if report.poc_id:
        if not _is_poc_member(request.user, report.poc):
            raise PermissionDenied
    else:
        # Custom report not tied to a POC: only the requester or an admin.
        if not (request.user.is_admin or report.requested_by_id == request.user.id):
            raise PermissionDenied

    if not report.output_file:
        raise Http404("This report has no output file.")
    return FileResponse(
        report.output_file.open("rb"),
        as_attachment=True,
        filename=report.output_file.name.rsplit("/", 1)[-1],
    )


def _can_delete_report(user, report):
    """Who may remove a generated report.

    Phase reports: admin, the POC lead, or whoever requested it. Custom reports
    (no POC): admin or the requester.
    """
    if user.is_admin or report.requested_by_id == user.id:
        return True
    if report.poc_id:
        from apps.core.mixins import user_can_lead_poc

        return user_can_lead_poc(user, report.poc)
    return False


@require_POST
def report_delete(request, pk):
    """Delete a generated report and its files (permission-checked)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    report = get_object_or_404(GeneratedReport, pk=pk)
    if not _can_delete_report(request.user, report):
        raise PermissionDenied

    poc_id = report.poc_id
    title = report.title
    # Remove the stored files first so they don't linger as orphans.
    if report.output_file:
        report.output_file.delete(save=False)
    if report.source_file:
        report.source_file.delete(save=False)
    record_audit(
        report, "report_deleted", request.user, {"title": {"before": title, "after": None}}
    )
    report.delete()
    messages.success(request, f"Report “{title}” removed.")

    # Return to wherever the report was listed.
    if poc_id:
        return redirect(f"{reverse('pocs:detail', args=[poc_id])}?tab=reports")
    return redirect("reports:home")


@require_POST
def phase_report_upload(request, phase_pk):
    """Attach a finished report file to a phase (kept as-is, no extraction).

    Stored as a READY ``GeneratedReport`` of kind ``uploaded`` so it shows up in
    the POC's Reports tab and is downloadable / removable like any other report.
    Allowed for POC members + admin.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not _is_poc_member(request.user, phase.poc):
        raise PermissionDenied

    from apps.pocs.forms import PhaseReportUploadForm

    form = PhaseReportUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, form.errors.get("report_file", ["Upload failed."])[0])
        return redirect("pocs:phase_detail", phase_pk=phase.pk)

    f = form.cleaned_data["report_file"]
    report = GeneratedReport(
        kind=GeneratedReport.Kind.UPLOADED,
        title=f"{phase.name} — {f.name}",
        poc=phase.poc,
        phase=phase,
        requested_by=request.user,
        status=GeneratedReport.Status.READY,
    )
    from .generation import mark_after_closure

    mark_after_closure(report, phase.poc)
    report.output_file.save(f.name, f, save=True)
    record_audit(report, "report_uploaded", request.user,
                 {"file": {"before": None, "after": f.name}})
    messages.success(request, f"Report “{f.name}” attached.")
    return redirect("pocs:phase_detail", phase_pk=phase.pk)


@require_POST
def phase_documents_report(request, phase_pk):
    """Generate a phase report from its documents (Documentation / FA phases).

    Allowed for POC members. Uses the phase's template or the global default;
    embeds any images referenced from the documents' Markdown.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not _is_poc_member(request.user, phase.poc):
        raise PermissionDenied

    from apps.pocs.services import ensure_fa_documents

    from .generation import generate_phase_report_from_documents

    # Locked (Use Cases/Requirements) FA sections must reflect the POC's latest
    # data even if nobody opened the phase page since the last change.
    ensure_fa_documents(phase)
    report = generate_phase_report_from_documents(phase, request.user)
    if report.status == GeneratedReport.Status.ERROR:
        messages.error(request, f"Report generation failed: {report.error_message}")
    else:
        messages.success(request, f"Report for “{phase.name}” generated.")
    return redirect("pocs:phase_detail", phase_pk=phase.pk)
