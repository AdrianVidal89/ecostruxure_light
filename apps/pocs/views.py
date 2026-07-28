"""
POC views.

Two access tiers:

* **Admin only** — list management actions, create/edit/archive, and the
  member-assignment endpoints (admin or the POC's lead, via ``@poc_lead_required``).
* **POC members** — the POC detail page (``POCMemberRequiredMixin``); admins
  bypass the membership check.

The member-assignment panel is HTMX-driven: search / add / remove / role-change
all re-render a single ``team_panel`` partial, so the page never fully reloads.
"""

import logging
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db.models import Case, F, IntegerField, Max, Q, When
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from apps.core.mixins import (
    AdminRequiredMixin,
    POCLeadRequiredMixin,
    POCMemberRequiredMixin,
    user_can_delete_phase,
    user_can_edit_phase,
    user_can_execute_task,
    user_can_execute_test,
    user_can_lead_poc,
    user_can_mark_external,
    user_can_mark_na,
    user_can_validate_phase,
    user_can_validate_test,
    user_is_poc_member,
)

from .forms import (
    BasePhaseDocumentForm,
    BaseTaskForm,
    BaseTestForm,
    CommentDecisionForm,
    CommentForm,
    FunctionalAnalysisStepForm,
    MemberTestForm,
    PhaseDocumentForm,
    PhaseForm,
    PhaseImageForm,
    PhaseMarkExternalForm,
    PhaseMarkNAForm,
    PhaseReportUploadForm,
    PhaseTemplateForm,
    POCCloseForm,
    POCForm,
    POCImageForm,
    POCImportForm,
    RequirementForm,
    RequirementImportForm,
    TaskForm,
    TestExecutionForm,
    TestForm,
    TestRequirementsForm,
    TestValidationDecisionForm,
    UseCaseForm,
)
from .models import (
    POC,
    AuditLog,
    BasePhaseDocument,
    BaseTask,
    BaseTest,
    Comment,
    EVIDENCE_EXTENSIONS,
    EvidenceFile,
    FunctionalAnalysisStep,
    Phase,
    PhaseDocument,
    PhaseImage,
    PhaseKind,
    PhaseTemplate,
    POCImage,
    POCMembership,
    Requirement,
    Task,
    Test,
    TestValidation,
    UseCase,
)
from .audit import record_audit
from .gantt import build_phase_task_tree, build_task_gantt, gantt_summary
from .state_machine import can_transition_task, task_allowed_statuses

User = get_user_model()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def admin_required(view):
    """Function-view decorator: anon → login, authenticated non-admin → 403."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_admin:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def poc_lead_required(view):
    """Function-view decorator for POC-scoped endpoints.

    Anon → login; allowed only for admins or the lead of the POC identified by
    the ``pk`` URL kwarg (everyone else → 403). The resolved POC is passed to the
    view as a keyword argument is avoided — views re-fetch it as they already do.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        poc = get_object_or_404(POC, pk=kwargs.get("pk"))
        if not user_can_lead_poc(request.user, poc):
            raise PermissionDenied
        if poc.is_closed:
            raise PermissionDenied("This POC is closed and locked for editing.")
        return view(request, *args, **kwargs)

    return wrapper


def _team_context(poc, request, q=""):
    """Build the context for the team-management panel partial."""
    memberships = poc.memberships.select_related("user").all()
    member_ids = list(memberships.values_list("user_id", flat=True))

    q = (q or "").strip()
    addable = User.objects.filter(is_active=True).exclude(id__in=member_ids)
    if q:
        addable = addable.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )
    search_results = list(addable.order_by("first_name", "last_name", "username")[:10])

    return {
        "poc": poc,
        "memberships": memberships,
        "search_results": search_results,
        "q": q,
        # Admin or the POC's lead may manage this POC's members.
        "can_manage": user_can_lead_poc(request.user, poc),
        "role_choices": POCMembership.Role.choices,
    }


def _render_members_update(request, poc, q=""):
    """Render the members list plus an out-of-band refresh of the search results.

    Used by add/remove/role-change so both regions stay in sync from one
    response without a full page reload.
    """
    return render(
        request, "pocs/partials/members_update.html", _team_context(poc, request, q)
    )


def _poc_audit_logs(poc, limit=200):
    """All audit entries for a POC, newest first.

    Entries carry a direct ``poc`` link (set on write), so this covers tasks,
    tests, documents, images, generated/imported reports — including actions on
    objects that have since been deleted. Capped at ``limit``.
    """
    return (
        AuditLog.objects.filter(poc=poc)
        .select_related("actor", "content_type")
        .order_by("-timestamp")[:limit]
    )


# ---------------------------------------------------------------------------
# POC list & CRUD
# ---------------------------------------------------------------------------
class POCListView(LoginRequiredMixin, ListView):
    """List POCs.

    Admins see every POC plus management actions; other users see only the POCs
    they belong to. The list is not scoped to a single POC, so per-row access is
    enforced by the membership-filtered queryset rather than by
    ``POCMemberRequiredMixin``.
    """

    model = POC
    template_name = "pocs/poc_list.html"
    context_object_name = "pocs"
    paginate_by = 20

    def get_queryset(self):
        user = self.request.user
        qs = POC.objects.all() if user.is_admin else POC.objects.filter(
            memberships__user=user
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(Q(name__icontains=query) | Q(description__icontains=query))
        status = self.request.GET.get("status", "").strip()
        if status:
            qs = qs.filter(status=status)
        return qs.distinct()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["query"] = self.request.GET.get("q", "")
        ctx["status_filter"] = self.request.GET.get("status", "")
        ctx["status_choices"] = POC.Status.choices
        ctx["can_manage"] = self.request.user.is_admin
        return ctx


class POCCreateView(AdminRequiredMixin, CreateView):
    """Admin-only: create a POC."""

    model = POC
    form_class = POCForm
    template_name = "pocs/poc_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Create POC"
        return ctx

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        # Inherit the global phase blueprint into the new POC.
        from .services import apply_phase_templates

        apply_phase_templates(self.object)
        messages.success(self.request, f"POC “{form.instance.name}” created.")
        return response

    def get_success_url(self):
        return reverse_lazy("pocs:detail", args=[self.object.pk])


class POCUpdateView(POCLeadRequiredMixin, UpdateView):
    """Edit a POC's details (admin or the POC's lead).

    Creating, archiving and deleting POCs stays admin-only (provisioning), but a
    lead may edit everything about a POC they lead.
    """

    model = POC
    form_class = POCForm
    template_name = "pocs/poc_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit POC · {self.object.name}"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f"POC “{form.instance.name}” updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("pocs:detail", args=[self.object.pk])


class POCArchiveView(AdminRequiredMixin, View):
    """Admin-only: toggle a POC between archived and active (POST)."""

    def post(self, request, pk):
        poc = get_object_or_404(POC, pk=pk)
        if poc.status == POC.Status.ARCHIVED:
            poc.status = POC.Status.ACTIVE
            verb = "restored"
        else:
            poc.status = POC.Status.ARCHIVED
            verb = "archived"
        poc.save(update_fields=["status", "updated_at"])
        messages.success(request, f"POC “{poc.name}” {verb}.")
        return redirect("pocs:detail", pk=poc.pk)


class POCCloseView(POCLeadRequiredMixin, View):
    """Official closure of a POC (spec Fase 6a) — admin or the POC's lead.

    Irreversible (only an admin can revert). Can be done even with pending work.
    On closure the POC is locked and the final report is generated and sealed.
    """

    template_name = "pocs/poc_close.html"

    def get(self, request, pk):
        poc = self.poc
        return render(
            request,
            self.template_name,
            {"poc": poc, "form": POCCloseForm(), "all_approved": poc.all_phases_approved},
        )

    def post(self, request, pk):
        poc = self.poc
        form = POCCloseForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"poc": poc, "form": form, "all_approved": poc.all_phases_approved},
            )

        poc.closure_date = form.cleaned_data["closure_date"]
        poc.closure_conclusion = form.cleaned_data["closure_conclusion"]
        poc.closed_at = timezone.now()
        poc.closed_by = request.user
        poc.status = POC.Status.COMPLETED
        poc.save(
            update_fields=[
                "closure_date", "closure_conclusion", "closed_at",
                "closed_by", "status", "updated_at",
            ]
        )
        record_audit(poc, "poc_closed", request.user, poc=poc)

        # Generate & seal the final report (covers all phases; pending ones are
        # flagged inside the document).
        from apps.reports.generation import generate_final_report

        report = generate_final_report(poc, request.user, poc.closure_conclusion)
        if report.status == "error":
            messages.warning(
                request,
                f"POC closed, but the final report failed to generate: "
                f"{report.error_message}",
            )
        else:
            messages.success(
                request, f"POC “{poc.name}” officially closed. Final report generated."
            )
        return redirect("pocs:detail", pk=poc.pk)


@admin_required
@require_POST
def poc_reopen(request, pk):
    """Admin-only: revert an official closure, unlocking the POC (spec Fase 6a)."""
    poc = get_object_or_404(POC, pk=pk)
    if not poc.is_closed:
        return redirect("pocs:detail", pk=poc.pk)
    poc.closed_at = None
    poc.closed_by = None
    poc.closure_date = None
    poc.save(update_fields=["closed_at", "closed_by", "closure_date", "updated_at"])
    record_audit(poc, "poc_reopened", request.user, poc=poc)
    messages.success(request, f"POC “{poc.name}” reopened — editing is unlocked.")
    return redirect("pocs:detail", pk=poc.pk)


class POCDeleteView(AdminRequiredMixin, View):
    """Admin-only: permanently delete a POC and all its data (POST).

    Double confirmation: the request must echo the POC name in ``confirm_name``
    (the UI requires typing it). Cascade-deletes everything via ``delete_poc``.
    """

    http_method_names = ["post"]

    def post(self, request, pk):
        poc = get_object_or_404(POC, pk=pk)
        if request.POST.get("confirm_name", "").strip() != poc.name:
            messages.error(
                request,
                "The confirmation text didn’t match the POC name — nothing was deleted.",
            )
            return redirect("pocs:detail", pk=poc.pk)
        from .services import delete_poc

        name = delete_poc(poc)
        messages.success(
            request, f"POC “{name}” and all its data were permanently deleted."
        )
        return redirect("pocs:list")


class POCDetailView(POCMemberRequiredMixin, DetailView):
    """POC detail with tabs. Visible to members and admins.

    Overview and Team tabs are implemented now; Phases / Audit Log / Reports
    tabs are placeholders wired up in later build steps.
    """

    model = POC
    template_name = "pocs/poc_detail.html"
    context_object_name = "poc"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        poc = self.object
        # Flatten the team context so the same variable names are available
        # both here and in the HTMX partial responses.
        can_lead = user_can_lead_poc(self.request.user, poc)
        ctx.update(_team_context(poc, self.request))
        # Phases tab shows the tree from its roots; the node partial recurses.
        ctx["phase_roots"] = poc.phases.filter(parent__isnull=True)
        ctx["is_admin"] = self.request.user.is_admin
        ctx["is_lead_member"] = POCMembership.objects.filter(
            poc=poc, user=self.request.user, role_in_poc=POCMembership.Role.LEAD
        ).exists()
        ctx["phase_status_choices"] = Phase.MANUAL_STATUS_CHOICES
        ctx["can_lead"] = can_lead
        # Official closure (spec Fase 6).
        ctx["is_closed"] = poc.is_closed
        ctx["can_close"] = can_lead and not poc.is_closed
        ctx["all_phases_approved"] = poc.all_phases_approved
        # Testing roadmap: one row per Test-phase, tests plotted chronologically.
        ctx["test_timeline"] = poc.test_timeline()
        # Tasks are no longer shown on the Overview tab — minimalism: they
        # only live in the dedicated Tasks workspace (pocs:tasks), spec item 4.
        # Audit log is visible to admins and POC leads only.
        if can_lead:
            ctx["audit_logs"] = _poc_audit_logs(poc)
        # Imported business details for the Overview tab (label, value pairs).
        # "Status" mirrors the POC's own lifecycle status (single source of truth),
        # not the imported external_status, so it always matches the header badge.
        ctx["poc_details"] = [
            ("Status", poc.get_status_display()),
            ("Customer", poc.customer),
            ("Customer Segment", poc.customer_segment),
            ("Initiative", poc.initiative),
            ("Region", poc.region),
            ("Leading Organization", poc.leading_organization),
            ("L2 WBS", poc.l2_wbs),
            ("BFO No", poc.bfo_no),
            ("Investment Type", poc.investment_type),
            ("Leadership", poc.leadership),
            ("Finance KPI", poc.finance_kpi),
            ("Schedule KPI", poc.schedule_kpi),
            ("Tendering", f"{poc.tendering_start or '—'} → {poc.tendering_finish or '—'}"
                if poc.tendering_start or poc.tendering_finish else ""),
            ("Execution", f"{poc.execution_start or '—'} → {poc.execution_finish or '—'}"
                if poc.execution_start or poc.execution_finish else ""),
            ("Proposal Duration", f"{poc.proposal_duration} months" if poc.proposal_duration else ""),
            ("Pilot Requestor", poc.pilot_requestor),
            ("Opportunity Leader", poc.opportunity_leader),
            ("EcoStruxure Lead", poc.ecostruxure_lead),
            ("Pilot Tender Leader", poc.pilot_tender_leader),
            ("Pilot Tender TL", poc.pilot_tender_tl),
            ("Pilot PM", poc.pilot_pm),
            ("Pilot Exec TL", poc.pilot_exec_tl),
            ("Integration Leader", poc.integration_leader),
            ("Initiative (QUA)", poc.initiative_qua),
        ]
        # Show the Details panel whenever there is any business data beyond the
        # always-present Status (covers both imported and manually-edited POCs).
        ctx["has_details"] = any(
            value for label, value in ctx["poc_details"] if label != "Status"
        )
        generated_reports = list(
            poc.generated_reports.select_related("phase", "requested_by", "report_type")[:50]
        )
        # A report can be removed by an admin, the POC lead, or its requester —
        # EXCEPT the sealed final report, which only an admin may remove (Fase 6).
        for report in generated_reports:
            if report.kind == "final":
                report.can_delete = ctx["is_admin"]
            else:
                report.can_delete = (
                    ctx["is_admin"]
                    or can_lead
                    or report.requested_by_id == self.request.user.id
                )
        ctx["generated_reports"] = generated_reports
        # Group reports by where they came from (the phase they were generated
        # for, or the custom ReportType used) instead of one flat "folder" —
        # falls back to the kind label (Final/Uploaded/Custom) when neither applies.
        report_groups = {}
        for report in generated_reports:
            if report.phase_id:
                label = report.phase.name
            elif report.report_type_id:
                label = report.report_type.name
            else:
                label = report.get_kind_display()
            report_groups.setdefault(label, []).append(report)
        ctx["generated_report_groups"] = sorted(
            report_groups.items(), key=lambda kv: kv[0].lower()
        )
        # Requirements & Use Cases tab (spec Fase 4a). Prefetch tests so the
        # derived is_validated / is_approved don't trigger per-row queries.
        requirements_qs = poc.requirements.prefetch_related("use_cases", "tests")
        ctx["requirements"] = requirements_qs
        # Shown to POC leads/admins only (can_manage, set below): drill-down
        # lists behind the two "Requirements" stat pills — which requirements
        # still need a test (with a picker to assign one), and which already
        # have one (with an eye preview on both sides). Built from the
        # prefetch cache above (r.tests.all() doesn't re-query).
        requirements_without_test = []
        requirements_with_test = []
        for r in requirements_qs:
            linked_tests = list(r.tests.all())
            if linked_tests:
                for t in linked_tests:
                    requirements_with_test.append((r, t))
            else:
                requirements_without_test.append(r)
        ctx["requirements_without_test"] = requirements_without_test
        ctx["requirements_without_test_count"] = len(requirements_without_test)
        ctx["requirements_with_test"] = requirements_with_test
        ctx["requirements_with_test_count"] = len(requirements_qs) - len(requirements_without_test)
        # Leaf Test-kind phases are the only valid targets for a new test
        # (mirrors TestCreateView.dispatch) — offered as the "which phase"
        # picker in the "Assign test" flow.
        ctx["test_target_phases"] = list(
            poc.phases.filter(kind=PhaseKind.TEST, children__isnull=True)
        )
        use_cases_qs = list(poc.use_cases.prefetch_related("requirements__tests"))
        ctx["use_cases"] = use_cases_qs
        # Analogous to requirements_without_test above (spec item 6): use
        # cases with zero linked requirements, for the matching alert/modal.
        usecases_without_requirements = [uc for uc in use_cases_qs if not uc.requirements.all()]
        ctx["usecases_without_requirements"] = usecases_without_requirements
        ctx["usecases_without_requirements_count"] = len(usecases_without_requirements)
        ctx["can_manage"] = can_lead
        # Filter dropdown options for the Requirements table (client-side, spec 4a).
        ctx["requirement_filter_fields"] = {
            field_name: Requirement.field_choices(poc, field_name)
            for field_name in (
                "req_gravity", "req_operation", "req_functional", "req_category", "life_cycle_phase",
            )
        }
        # Column-sort ranks for the Requirements table: sorting these choice
        # fields alphabetically wouldn't respect their natural progression
        # (e.g. Draft -> Active -> Rejected), so rank by position instead.
        ctx["requirement_rank_maps"] = {
            field_name: {
                value: idx for idx, (value, _label) in enumerate(ctx["requirement_filter_fields"][field_name])
            }
            for field_name in ("req_gravity", "req_operation", "req_category", "life_cycle_phase")
        }
        # (dataset key, header label) for each sortable column in the Requirements
        # table — keys must match the `data-*` attributes set on each `<tr>`.
        ctx["sortable_req_columns"] = [
            ("code", "Code"),
            ("externalCode", "External code"),
            ("subsystem", "Sub-system"),
            ("gravityRank", "Gravity"),
            ("operationRank", "Operation"),
            ("categoryRank", "Category"),
            ("lifecycleRank", "Lifecycle Status"),
            ("validated", "Validated"),
            ("usecases", "Use cases"),
        ]
        # Filter dropdown options for the Use Cases grid (mirrors the above).
        ctx["usecase_filter_fields"] = {
            "status": UseCase.Status.choices,
            "priority": UseCase.Priority.choices,
        }
        ctx["tabs"] = [
            ("overview", "Overview"),
            ("specs", "Use Cases & Requirements"),
            ("phases", "Phases"),
            ("team", "Team"),
            ("audit", "Audit Log"),
            ("reports", "Reports"),
        ]
        return ctx


# ---------------------------------------------------------------------------
# Member assignment (admin or POC lead, HTMX)
# ---------------------------------------------------------------------------
@poc_lead_required
@require_GET
def member_search(request, pk):
    """Return the search-results fragment filtered by ``q`` (HTMX)."""
    poc = get_object_or_404(POC, pk=pk)
    ctx = _team_context(poc, request, q=request.GET.get("q", ""))
    return render(request, "pocs/partials/search_results.html", ctx)


@poc_lead_required
@require_POST
def member_add(request, pk):
    """Add a user to the POC with a per-POC role (HTMX)."""
    poc = get_object_or_404(POC, pk=pk)
    role = request.POST.get("role", POCMembership.Role.MEMBER)
    if role not in dict(POCMembership.Role.choices):
        role = POCMembership.Role.MEMBER
    target = get_object_or_404(User, pk=request.POST.get("user_id"), is_active=True)
    POCMembership.objects.get_or_create(
        poc=poc, user=target, defaults={"role_in_poc": role}
    )
    return _render_members_update(request, poc, q=request.POST.get("q", ""))


@poc_lead_required
@require_POST
def member_role(request, pk, membership_pk):
    """Change a member's per-POC role (HTMX)."""
    poc = get_object_or_404(POC, pk=pk)
    membership = get_object_or_404(POCMembership, pk=membership_pk, poc=poc)
    role = request.POST.get("role")
    if role in dict(POCMembership.Role.choices):
        membership.role_in_poc = role
        membership.save(update_fields=["role_in_poc"])
    return _render_members_update(request, poc, q=request.POST.get("q", ""))


@poc_lead_required
@require_POST
def member_remove(request, pk, membership_pk):
    """Remove a member from the POC (HTMX)."""
    poc = get_object_or_404(POC, pk=pk)
    membership = get_object_or_404(POCMembership, pk=membership_pk, poc=poc)
    membership.delete()
    return _render_members_update(request, poc, q=request.POST.get("q", ""))


# ---------------------------------------------------------------------------
# Phases (tree; structural edits gated by user_can_edit_phase)
# ---------------------------------------------------------------------------
def _detail_phases_url(poc):
    """Detail URL anchored on the Phases tab."""
    return f"{reverse('pocs:detail', args=[poc.pk])}?tab=phases"


def _phase_return_url(phase):
    """Where to go after editing a phase: its parent's page, or the POC tab."""
    if phase.parent_id:
        return reverse("pocs:phase_detail", args=[phase.parent_id])
    return _detail_phases_url(phase.poc)


class PhaseCreateView(POCLeadRequiredMixin, CreateView):
    """Create a top-level phase in a POC (admin or the POC's lead)."""

    model = Phase
    form_class = PhaseForm
    template_name = "pocs/phase_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.poc = get_object_or_404(POC, pk=kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["title"] = "Add phase"
        return ctx

    def form_valid(self, form):
        form.instance.poc = self.poc
        last = self.poc.phases.filter(parent__isnull=True).aggregate(m=Max("order"))["m"]
        form.instance.order = (last or 0) + 1
        self.object = form.save()
        messages.success(self.request, f"Phase “{self.object.name}” created.")
        return redirect(_detail_phases_url(self.poc))


class SubPhaseCreateView(LoginRequiredMixin, CreateView):
    """Add a sub-phase under a phase (admin, or the POC's lead).

    Only allowed when the parent can still nest (< 3 levels) and has no Tasks/
    Tests of its own (a phase holds either sub-phases or tasks/tests).
    """

    model = Phase
    form_class = PhaseForm
    template_name = "pocs/phase_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.parent = get_object_or_404(Phase, pk=kwargs["phase_pk"])
        self.poc = self.parent.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_edit_phase(request.user, self.parent):
            raise PermissionDenied
        if not self.parent.can_have_children:
            messages.error(request, "Maximum phase depth (3 levels) reached.")
            return redirect("pocs:phase_detail", phase_pk=self.parent.pk)
        if self.parent.tasks.exists() or self.parent.tests.exists():
            messages.error(
                request,
                "This phase already has tasks/tests; it can't also contain sub-phases.",
            )
            return redirect("pocs:phase_detail", phase_pk=self.parent.pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["parent_phase"] = self.parent
        ctx["title"] = f"Add sub-phase under “{self.parent.name}”"
        return ctx

    def form_valid(self, form):
        obj = form.save(commit=False)
        obj.poc = self.poc
        obj.parent = self.parent
        last = self.parent.children.aggregate(m=Max("order"))["m"]
        obj.order = (last or 0) + 1
        obj.save()
        self.parent.recalculate_status()
        messages.success(self.request, f"Sub-phase “{obj.name}” created.")
        return redirect("pocs:phase_detail", phase_pk=self.parent.pk)


class _PhaseEditMixin(LoginRequiredMixin):
    """Resolve a phase from ``phase_pk`` and require structural-edit permission."""

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.phase = get_object_or_404(Phase, pk=kwargs["phase_pk"])
        self.poc = self.phase.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_edit_phase(request.user, self.phase):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class PhaseUpdateView(_PhaseEditMixin, UpdateView):
    """Edit a phase. Admins and the POC's lead get the same full form."""

    model = Phase
    form_class = PhaseForm
    template_name = "pocs/phase_form.html"
    pk_url_kwarg = "phase_pk"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["title"] = f"Edit phase · {self.object.name}"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, f"Phase “{form.instance.name}” updated.")
        form.save()
        return redirect(_phase_return_url(self.object))


class PhaseDeleteView(_PhaseEditMixin, DeleteView):
    """Delete a phase. Admins may delete any phase; a POC lead may only delete
    phases they created themselves (no blueprint provenance) — a blueprint
    phase must be marked Not Applicable instead (``PhaseMarkNAView``), so a
    lead's POC never structurally drifts from the admin-maintained blueprint.
    """

    model = Phase
    pk_url_kwarg = "phase_pk"
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        # Stricter than the base ``_PhaseEditMixin`` check (edit rights are not
        # enough here — deletion additionally requires blueprint provenance).
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_delete_phase(request.user, self.phase):
            raise PermissionDenied
        return super(_PhaseEditMixin, self).dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        parent = self.object.parent
        name = self.object.name
        return_url = _phase_return_url(self.object)
        self.object.delete()
        if parent:
            parent.recalculate_status()
        messages.success(self.request, f"Phase “{name}” deleted.")
        return redirect(return_url)


class PhaseMarkNAView(_PhaseEditMixin, View):
    """Mark a blueprint-sourced phase Not Applicable, with a mandatory reason.

    The alternative to deletion for phases a lead can't remove (see
    ``PhaseDeleteView``); the reason is stored verbatim for the final report.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_mark_na(request.user, self.phase):
            raise PermissionDenied
        return super(_PhaseEditMixin, self).dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "pocs/phase_na_form.html",
            {"phase": self.phase, "poc": self.poc, "form": PhaseMarkNAForm()},
        )

    def post(self, request, *args, **kwargs):
        form = PhaseMarkNAForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "pocs/phase_na_form.html",
                {"phase": self.phase, "poc": self.poc, "form": form},
            )
        self.phase.mark_na(request.user, form.cleaned_data["na_reason"])
        record_audit(self.phase, "phase_marked_na", request.user)
        messages.success(request, f"Phase “{self.phase.name}” marked as Not Applicable.")
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)


@require_POST
def phase_unmark_na(request, phase_pk):
    """Revert a Not Applicable phase back to a normally-derived status."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_can_edit_phase(request.user, phase):
        raise PermissionDenied
    if not phase.is_na:
        return redirect("pocs:phase_detail", phase_pk=phase.pk)
    phase.unmark_na()
    record_audit(phase, "phase_na_reverted", request.user)
    messages.success(request, f"Phase “{phase.name}” restored to Pending.")
    return redirect("pocs:phase_detail", phase_pk=phase.pk)


class PhaseMarkExternalView(_PhaseEditMixin, View):
    """Mark a phase External, assigning one or more Team(s) (spec item 4 —
    coexists with Not Applicable, see ``PhaseMarkNAView``)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_mark_external(request.user, self.phase):
            raise PermissionDenied
        return super(_PhaseEditMixin, self).dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        return render(
            request,
            "pocs/phase_external_form.html",
            {"phase": self.phase, "poc": self.poc, "form": PhaseMarkExternalForm()},
        )

    def post(self, request, *args, **kwargs):
        form = PhaseMarkExternalForm(request.POST)
        if not form.is_valid():
            return render(
                request,
                "pocs/phase_external_form.html",
                {"phase": self.phase, "poc": self.poc, "form": form},
            )
        teams = form.save()
        self.phase.mark_external(request.user, teams)
        record_audit(
            self.phase, "phase_marked_external", request.user,
            {"teams": {"before": None, "after": ", ".join(t.name for t in teams)}},
        )
        messages.success(
            request,
            f"Phase “{self.phase.name}” marked as External ({', '.join(t.name for t in teams)}).",
        )
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)


@require_POST
def phase_unmark_external(request, phase_pk):
    """Revert an External phase back to a normally-derived status."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_can_edit_phase(request.user, phase):
        raise PermissionDenied
    if not phase.is_external:
        return redirect("pocs:phase_detail", phase_pk=phase.pk)
    phase.unmark_external()
    record_audit(phase, "phase_external_reverted", request.user)
    messages.success(request, f"Phase “{phase.name}” restored to Pending.")
    return redirect("pocs:phase_detail", phase_pk=phase.pk)


@require_POST
def phase_reorder(request, pk):
    """Persist a drag-and-drop reorder of sibling phases (HTMX → 204).

    Allowed for admins, or leads when every reordered phase is lead-editable.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    poc = get_object_or_404(POC, pk=pk)
    ordered_ids = [int(i) for i in request.POST.getlist("phase_ids") if i.isdigit()]
    phases = list(poc.phases.filter(id__in=ordered_ids))
    if not all(user_can_edit_phase(request.user, p) for p in phases):
        raise PermissionDenied
    by_id = {p.id: p for p in phases}
    for index, pid in enumerate(ordered_ids, start=1):
        phase = by_id.get(pid)
        if phase and phase.order != index:
            Phase.objects.filter(pk=pid).update(order=index)
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# Task ordering (spec Fase 7); the Gantt/tree itself lives in .gantt (section 5)
# ---------------------------------------------------------------------------
def order_tasks(qs):
    """Default task ordering: by due date, with completed tasks always last.

    Tasks without a due date sort after dated ones (spec Fase 7).
    """
    return qs.order_by(
        Case(
            When(status=Task.Status.COMPLETED, then=1),
            default=0,
            output_field=IntegerField(),
        ),
        F("due_date").asc(nulls_last=True),
        "id",
    )




# ---------------------------------------------------------------------------
# Phase detail & Task management
# ---------------------------------------------------------------------------
class PhaseDetailView(POCMemberRequiredMixin, DetailView):
    """A phase. Leaf phases show Tasks/Tests; parent phases show sub-phases."""

    model = Phase
    template_name = "pocs/phase_detail.html"
    context_object_name = "phase"
    pk_url_kwarg = "phase_pk"

    def get_poc(self):
        if not hasattr(self, "_poc"):
            self._poc = get_object_or_404(Phase, pk=self.kwargs["phase_pk"]).poc
        return self._poc

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        phase = self.object
        user = self.request.user
        can_edit = user_can_edit_phase(user, phase)

        children = list(phase.children.all())
        is_leaf = not children
        ctx["children"] = children
        ctx["is_leaf"] = is_leaf
        ctx["can_edit"] = can_edit
        # Manual status override at the top of a leaf phase (spec: parent
        # phases still derive their status from sub-phases, shown further down).
        ctx["phase_status_choices"] = Phase.MANUAL_STATUS_CHOICES
        # Delete vs. Mark N/A (spec): a lead can't delete a blueprint phase.
        ctx["can_delete"] = user_can_delete_phase(user, phase)
        ctx["can_mark_na"] = user_can_mark_na(user, phase)
        ctx["is_na"] = phase.is_na
        ctx["can_mark_external"] = user_can_mark_external(user, phase)
        ctx["is_external"] = phase.is_external
        # Phase approval / locking (spec Fase 5).
        ctx["is_approved"] = phase.is_approved
        ctx["is_locked"] = phase.is_locked
        ctx["awaiting_approval"] = phase.awaiting_approval
        ctx["has_own_items"] = phase.has_own_items
        ctx["can_validate_phase"] = user_can_validate_phase(user, phase)
        ctx["phase_kind"] = phase.kind
        ctx["is_functional_analysis"] = phase.is_functional_analysis
        ctx["is_documentation"] = phase.is_documentation
        ctx["is_test"] = phase.is_test
        # Tests stay on leaf Test phases; any POC member may add one.
        ctx["can_add_test"] = is_leaf and phase.is_test
        # A phase may carry tasks and still gain sub-phases; only the presence of
        # Tests (leaf-only) blocks delegating to sub-phases.
        ctx["can_add_subphase"] = (
            can_edit and phase.can_have_children and not phase.tests.exists()
        )
        # Tasks are no longer displayed here — minimalism: they only live in
        # the dedicated Tasks workspace (`pocs:tasks`), spec item 4.

        # A template is downloadable from ANY phase (any kind): the phase's own
        # if attached, otherwise the global default.
        ctx["has_template_file"] = bool(phase.report_template) or _has_default_template()

        if is_leaf:
            # Tests on leaf Test phases.
            if phase.is_test:
                tests = list(
                    phase.tests.select_related("assigned_to").prefetch_related(
                        "requirements", "validations"
                    )
                )
                can_validate = user_can_validate_test(user, tests[0]) if tests else False
                for test in tests:
                    test.can_execute = can_edit or test.assigned_to_id == user.id
                    test.can_validate = can_validate
                ctx["tests"] = tests
                ctx["poc_requirements"] = phase.poc.requirements.all()
                # Drag-and-drop "unlinked requirements" panel (spec item 2):
                # only worth showing to someone who could actually drop onto
                # at least one test card here, and only when there's a
                # requirement of this POC not yet linked to ANY test.
                ctx["can_link_requirements"] = any(t.can_execute for t in tests)
                ctx["unlinked_requirements"] = list(
                    phase.poc.requirements.filter(tests__isnull=True).order_by("code")
                )

            # FA phases seed one document section per step (idempotent).
            if phase.is_functional_analysis:
                from .services import ensure_fa_documents

                ensure_fa_documents(phase)

            # The Markdown cluster (documents + images) and the upload option are
            # available on EVERY leaf phase, regardless of kind.
            ctx["documents"] = list(phase.documents.select_related("source_fa_step"))
            ctx["images"] = list(phase.images.all())
            ctx["image_form"] = PhaseImageForm()
            ctx["upload_form"] = PhaseReportUploadForm()
            # Generation needs a usable .docx (phase's own, or the global default);
            # a .zip phase template is download-only.
            ctx["is_reportable"] = phase.has_docx_template or _has_default_template()
            ctx["uploaded_reports"] = list(
                phase.generated_reports.filter(
                    kind="uploaded"
                ).select_related("requested_by")
            )

        ctx["poc"] = phase.poc
        ctx["can_lead"] = can_edit  # test rows use can_lead for CRUD controls
        ctx["test_execution_choices"] = Test.ExecutionStatus.choices
        ctx["test_result_choices"] = Test.Result.choices
        return ctx


class _PhaseEditCreateMixin(LoginRequiredMixin):
    """For creating items under a phase (``phase_pk``): require edit rights.

    ``require_leaf`` (default True) also blocks parent phases — used for content
    that only belongs on a leaf (documents, images). Tasks set it False so they
    can be added to any phase.
    """

    require_leaf = True

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.phase = get_object_or_404(Phase, pk=kwargs["phase_pk"])
        self.poc = self.phase.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_edit_phase(request.user, self.phase):
            raise PermissionDenied
        if self.require_leaf and not self.phase.is_leaf:
            messages.error(
                request, "This phase has sub-phases; add it on a leaf phase instead."
            )
            return redirect("pocs:phase_detail", phase_pk=self.phase.pk)
        return super().dispatch(request, *args, **kwargs)


class _ItemEditMixin(LoginRequiredMixin):
    """For editing/deleting a Task/Test: resolve it and require phase-edit rights.

    Subclasses set ``model`` and ``url_kwarg``.
    """

    url_kwarg = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.item = get_object_or_404(self.model, pk=kwargs[self.url_kwarg])
        self.phase = self.item.phase
        self.poc = self.phase.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_edit_phase(request.user, self.phase):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class TaskCreateView(_PhaseEditCreateMixin, CreateView):
    """Create a task within any phase (admin or editing lead).

    ``?parent=<task_id>`` (same phase) makes it a sub-task — one of the
    prerequisite steps needed to complete that parent task.
    """

    require_leaf = False  # tasks may be added to parent phases too

    model = Task
    form_class = TaskForm
    template_name = "pocs/task_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.parent_task = None
        parent_id = request.GET.get("parent") or request.POST.get("parent")
        if parent_id:
            self.parent_task = get_object_or_404(
                Task, pk=parent_id, phase=self.phase
            )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["parent_task"] = self.parent_task
        ctx["title"] = (
            f"Add sub-task · {self.parent_task.title}"
            if self.parent_task
            else "Add task"
        )
        return ctx

    def form_valid(self, form):
        form.instance.phase = self.phase
        form.instance.parent = self.parent_task
        self.object = form.save()
        self.phase.recalculate_status()
        messages.success(self.request, f"Task “{self.object.title}” created.")
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)


class TaskCreateForPocView(LoginRequiredMixin, CreateView):
    """POC-level "+ Add Task" entry point (Tasks workspace, spec item 4).

    ``TaskCreateView`` (above) is scoped to one phase via the URL, and the
    Tasks view only ever builds a folder row for phases that already have at
    least one Task — so a phase with zero tasks had no "Add task" link at
    all. Here the phase is picked from a dropdown of the POC's phases
    instead, so any phase can receive its first task.

    Only membership is checked in ``dispatch`` — the *edit* right depends on
    which phase the user picks, and that isn't known until the form is
    submitted, so ``user_can_edit_phase`` is re-checked in ``form_valid``.
    """

    model = Task
    form_class = TaskForm
    template_name = "pocs/task_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.poc = get_object_or_404(POC, pk=kwargs["pk"])

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_is_poc_member(request.user, self.poc):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["title"] = "Add task"
        return ctx

    def form_valid(self, form):
        phase = form.cleaned_data.get("phase")
        if phase is None:
            form.add_error("phase", "Select a phase.")
            return self.form_invalid(form)
        if not user_can_edit_phase(self.request.user, phase):
            form.add_error("phase", "You don't have edit rights on that phase.")
            return self.form_invalid(form)
        form.instance.phase = phase
        self.object = form.save()
        phase.recalculate_status()
        messages.success(self.request, f"Task “{self.object.title}” created.")
        return redirect("pocs:phase_detail", phase_pk=phase.pk)


class TaskUpdateView(_ItemEditMixin, UpdateView):
    """Edit a task (admin or editing lead)."""

    model = Task
    url_kwarg = "task_pk"
    form_class = TaskForm
    template_name = "pocs/task_form.html"
    pk_url_kwarg = "task_pk"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["title"] = f"Edit task · {self.object.title}"
        return ctx

    def form_valid(self, form):
        self.object = form.save()
        self.object.phase.recalculate_status()
        messages.success(self.request, f"Task “{self.object.title}” updated.")
        return redirect("pocs:phase_detail", phase_pk=self.object.phase.pk)


class TaskDeleteView(_ItemEditMixin, DeleteView):
    """Delete a task (admin or editing lead). POST only."""

    model = Task
    url_kwarg = "task_pk"
    pk_url_kwarg = "task_pk"
    http_method_names = ["post"]

    def form_valid(self, form):
        phase = self.object.phase
        title = self.object.title
        self.object.delete()
        phase.recalculate_status()
        messages.success(self.request, f"Task “{title}” deleted.")
        return redirect("pocs:phase_detail", phase_pk=phase.pk)


def _annotate_task_tree(task, can_edit, user_id):
    """Annotate a task (can_lead/can_execute/allowed_statuses) and recursively
    its sub-tasks, attaching each level as ``.child_list`` for template
    rendering."""
    task.can_lead = can_edit
    task.can_execute = can_edit or task.assigned_to_id == user_id
    task.allowed_statuses = task_allowed_statuses(task.status)
    task.child_list = list(task.subtasks.select_related("assigned_to"))
    for sub in task.child_list:
        _annotate_task_tree(sub, can_edit, user_id)
    return task


def _render_task_row(request, task, error=None):
    """Render a single task row partial (for HTMX swaps)."""
    can_edit = user_can_edit_phase(request.user, task.phase)
    _annotate_task_tree(task, can_edit, request.user.id)
    return render(
        request,
        "pocs/partials/task_row.html",
        {
            "task": task,
            "can_lead": can_edit,
            "bulk_enabled": bool(
                can_edit and task.phase.is_test and task.phase.is_leaf
            ),
            "task_status_choices": Task.Status.choices,
            "error": error,
        },
    )


@require_POST
def task_set_status(request, task_pk):
    """Inline status update (HTMX).

    Team members may set in_progress/completed/blocked on their own tasks; leads
    and admins may set any status. Completing stamps completed_by/at; reverting
    clears them (handled in ``Task.save``/``mark_completed``).
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    task = get_object_or_404(Task, pk=task_pk)
    if not user_can_execute_task(request.user, task):
        raise PermissionDenied

    new_status = request.POST.get("status")
    # Validate against the centralized state machine (single source of truth).
    if new_status not in dict(Task.Status.choices) or not can_transition_task(
        task.status, new_status
    ):
        # Invalid/illegal transition → re-render unchanged (no-op).
        return _render_task_row(request, task)

    if new_status == task.status:
        return _render_task_row(request, task)

    if new_status == Task.Status.COMPLETED:
        try:
            task.mark_completed(request.user)
        except ValueError as exc:
            return _render_task_row(request, task, error=str(exc))
    else:
        task.status = new_status
        task.save()

    task.phase.recalculate_status()
    return _render_task_row(request, task)


@require_POST
def task_bulk_status(request, phase_pk):
    """Set the same status on several tasks of a leaf phase at once.

    Manager convenience ("autoasignar progreso") — gated to admin / editing
    lead. Skips tasks where the transition isn't valid and reports the counts.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_can_edit_phase(request.user, phase):
        raise PermissionDenied

    new_status = request.POST.get("status")
    ids = [int(i) for i in request.POST.getlist("task_ids") if i.isdigit()]
    applied = skipped = 0
    if new_status in dict(Task.Status.choices) and ids:
        for task in phase.tasks.filter(id__in=ids):
            if task.status == new_status or not can_transition_task(
                task.status, new_status
            ):
                skipped += 1
                continue
            if new_status == Task.Status.COMPLETED:
                try:
                    task.mark_completed(request.user)
                except ValueError:
                    skipped += 1
                    continue
            else:
                task.status = new_status
                task.save()
            applied += 1
        phase.recalculate_status()
    messages.success(
        request, f"Bulk update: {applied} task(s) changed, {skipped} skipped."
    )
    return redirect("pocs:phase_detail", phase_pk=phase.pk)


@require_POST
def task_set_notes(request, task_pk):
    """Inline notes (Markdown) update (HTMX)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    task = get_object_or_404(Task, pk=task_pk)
    if not user_can_execute_task(request.user, task):
        raise PermissionDenied

    task.notes = request.POST.get("notes", "")
    task.save(update_fields=["notes", "updated_at"])
    return _render_task_row(request, task)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestCreateView(LoginRequiredMixin, CreateView):
    """Create a test within a leaf phase.

    Open to any POC member (members document tests they performed) as well as
    admins/editing leads. Leads/admins get the full form (incl. assignee);
    plain members get a reduced form and the test is auto-assigned to them so
    they can record the result.
    """

    model = Test
    template_name = "pocs/test_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.phase = get_object_or_404(Phase, pk=kwargs["phase_pk"])
        self.poc = self.phase.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_is_poc_member(request.user, self.poc):
            raise PermissionDenied
        if self.poc.is_closed:
            messages.error(request, "This POC is closed and locked.")
            return redirect("pocs:phase_detail", phase_pk=self.phase.pk)
        if not self.phase.is_leaf:
            messages.error(
                request, "This phase has sub-phases; add tests there instead."
            )
            return redirect("pocs:phase_detail", phase_pk=self.phase.pk)
        if not self.phase.is_test:
            messages.error(request, "Tests can only be added on Test phases.")
            return redirect("pocs:phase_detail", phase_pk=self.phase.pk)
        self.is_manager = user_can_edit_phase(request.user, self.phase)
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        return TestForm if self.is_manager else MemberTestForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_initial(self):
        # Pre-check requirements passed from the "Assign test" flow on the
        # Requirements panel (?requirements=<id>&requirements=<id>...) — only
        # keep ids that actually belong to this POC.
        initial = super().get_initial()
        raw_ids = self.request.GET.getlist("requirements")
        if raw_ids:
            valid_ids = list(
                self.poc.requirements.filter(
                    pk__in=[i for i in raw_ids if i.isdigit()]
                ).values_list("pk", flat=True)
            )
            if valid_ids:
                initial["requirements"] = valid_ids
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["title"] = "Add test"
        ctx["preselected_requirement_count"] = len(
            self.get_initial().get("requirements", [])
        )
        return ctx

    def form_valid(self, form):
        form.instance.phase = self.phase
        # Members document their own tests: auto-assign so they can record it.
        if not self.is_manager:
            form.instance.assigned_to = self.request.user
        self.object = form.save()
        self.phase.recalculate_status()
        messages.success(self.request, f"Test “{self.object.title}” created.")
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)


class TestUpdateView(_ItemEditMixin, UpdateView):
    """Edit a test definition (admin or editing lead)."""

    model = Test
    url_kwarg = "test_pk"
    form_class = TestForm
    template_name = "pocs/test_form.html"
    pk_url_kwarg = "test_pk"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["title"] = f"Edit test · {self.object.title}"
        return ctx

    def form_valid(self, form):
        self.object = form.save()
        self.object.phase.recalculate_status()
        messages.success(self.request, f"Test “{self.object.title}” updated.")
        return redirect("pocs:phase_detail", phase_pk=self.object.phase.pk)


class TestDeleteView(_ItemEditMixin, DeleteView):
    """Delete a test (admin or editing lead). POST only."""

    model = Test
    url_kwarg = "test_pk"
    pk_url_kwarg = "test_pk"
    http_method_names = ["post"]

    def form_valid(self, form):
        phase = self.object.phase
        title = self.object.title
        self.object.delete()
        phase.recalculate_status()
        messages.success(self.request, f"Test “{title}” deleted.")
        return redirect("pocs:phase_detail", phase_pk=phase.pk)


def _render_test_row(request, test, form=None):
    """Render a single test row partial (for HTMX swaps).

    ``form`` carries a bound TestExecutionForm when re-rendering after a failed
    save (e.g. an invalid evidence file type), so errors display inline.
    """
    can_edit = user_can_edit_phase(request.user, test.phase)
    test.can_execute = can_edit or test.assigned_to_id == request.user.id
    test.can_validate = user_can_validate_test(request.user, test)
    return render(
        request,
        "pocs/partials/test_row.html",
        {
            "test": test,
            "can_lead": can_edit,
            "test_execution_choices": Test.ExecutionStatus.choices,
            "test_result_choices": Test.Result.choices,
            "exec_form": form,
            "comment_form": CommentForm(),
        },
    )


@require_POST
def test_execute(request, test_pk):
    """Inline execution: record execution status / result / notes / evidence.

    Progress changes (not tested / in progress, or an admin's manual change)
    apply immediately. A finished outcome — a result, or a ``skipped`` execution
    — is held as a ``TestValidation`` proposal for a phase lead to approve
    (spec 3b); the Test itself is left unchanged until then.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    test = get_object_or_404(Test, pk=test_pk)
    if not user_can_execute_test(request.user, test):
        raise PermissionDenied

    # Block a new submission while an outcome is already awaiting validation
    # (admins may still override manually).
    if test.pending_validation and not request.user.is_admin:
        messages.error(request, "This test already has an outcome awaiting validation.")
        return _render_test_row(request, test)

    form = TestExecutionForm(request.POST, request.FILES, test=test)
    evidence_files = request.FILES.getlist("evidence_files")
    bad_ext = [
        f.name for f in evidence_files
        if f.name.rsplit(".", 1)[-1].lower() not in EVIDENCE_EXTENSIONS
    ]
    if bad_ext:
        form.add_error(
            None,
            f"Unsupported evidence file type: {', '.join(bad_ext)}. "
            f"Allowed: {', '.join(EVIDENCE_EXTENSIONS)}.",
        )
    if not form.is_valid():
        return _render_test_row(request, test, form=form)

    cd = form.cleaned_data
    is_terminal = bool(cd["result"]) or cd["execution_status"] == Test.ExecutionStatus.SKIPPED

    if is_terminal and not request.user.is_admin:
        # Hold the change as a proposal; do NOT touch the Test yet.
        validation = TestValidation.objects.create(
            test=test,
            execution_status=cd["execution_status"],
            result=cd["result"],
            actual_result=cd["actual_result"],
            evidence_url=cd.get("evidence_url") or None,
            submitted_by=request.user,
        )
        for f in evidence_files:
            EvidenceFile.objects.create(
                validation=validation, file=f, uploaded_by=request.user
            )
        record_audit(
            test,
            "validation_requested",
            request.user,
            {"outcome": {"before": None, "after": cd["result"] or cd["execution_status"]}},
        )
        messages.success(request, "Outcome submitted — a phase lead must validate it.")
        return _render_test_row(request, test)

    # Apply directly (progress update, or an admin's manual change).
    test.execution_status = cd["execution_status"]
    test.result = cd["result"]
    test.actual_result = cd["actual_result"]
    if cd.get("evidence_url"):
        test.evidence_url = cd["evidence_url"]
    test.mark_executed(request.user)  # stamps executed_by/at; save() housekeeps
    for f in evidence_files:
        EvidenceFile.objects.create(test=test, file=f, uploaded_by=request.user)
    test.phase.recalculate_status()
    return _render_test_row(request, test)


@require_POST
def test_link_requirements(request, test_pk):
    """Link a Test to Requirements of its POC (spec Fase 3d, HTMX)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    test = get_object_or_404(Test, pk=test_pk)
    if not user_can_execute_test(request.user, test):
        raise PermissionDenied
    form = TestRequirementsForm(request.POST, poc=test.phase.poc)
    if form.is_valid():
        test.requirements.set(form.cleaned_data["requirements"])
    return _render_test_row(request, test)


@require_POST
def test_link_requirement_add(request, test_pk):
    """Link ONE Requirement to a Test — additive counterpart to
    ``test_link_requirements`` above (which replaces the whole set). Backs the
    drag-and-drop "unlinked requirements" panel on the phase page (spec item
    2): dropping a requirement chip onto a test card posts here via HTMX.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    test = get_object_or_404(Test, pk=test_pk)
    if not user_can_execute_test(request.user, test):
        raise PermissionDenied
    requirement_id = request.POST.get("requirement_id", "")
    if not requirement_id.isdigit():
        return HttpResponseBadRequest("Invalid requirement_id.")
    # Scoped to the test's own POC — never let a client link a requirement
    # from an unrelated POC just by guessing its id.
    requirement = get_object_or_404(
        Requirement, pk=int(requirement_id), poc=test.phase.poc
    )
    test.requirements.add(requirement)
    return _render_test_row(request, test)


@require_POST
def test_link_requirement_remove(request, test_pk, requirement_pk):
    """Unlink ONE Requirement from a Test (the "x" on a linked chip,
    test_row.html) — makes the drag-and-drop pool/test linkage bidirectional.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    test = get_object_or_404(Test, pk=test_pk)
    if not user_can_execute_test(request.user, test):
        raise PermissionDenied
    requirement = get_object_or_404(
        Requirement, pk=requirement_pk, poc=test.phase.poc
    )
    test.requirements.remove(requirement)
    return _render_test_row(request, test)


# ---------------------------------------------------------------------------
# Test-outcome validation (spec Fase 3b)
# ---------------------------------------------------------------------------
def _pending_validations_for(user):
    """Pending validations ``user`` is allowed to decide (spec 3b)."""
    qs = TestValidation.objects.filter(
        status=TestValidation.Decision.PENDING
    ).select_related("test__phase__poc", "submitted_by")
    if user.is_admin:
        return qs
    lead_poc_ids = list(
        POCMembership.objects.filter(
            user=user, role_in_poc=POCMembership.Role.LEAD
        ).values_list("poc_id", flat=True)
    )
    # A POC lead can validate any phase of their POC (even ones with a
    # sub-leader); a phase leader can validate their own phase.
    return qs.filter(
        Q(test__phase__phase_leader=user)
        | Q(test__phase__poc_id__in=lead_poc_ids)
    )


class ValidationListView(LoginRequiredMixin, View):
    """Page listing the test outcomes awaiting the current user's validation."""

    template_name = "pocs/validations.html"

    def get(self, request):
        validations = list(_pending_validations_for(request.user))
        return render(
            request,
            self.template_name,
            {"validations": validations, "total": len(validations)},
        )


def validation_detail(request, validation_pk):
    """Modal/panel with the proposed outcome and approve/reject controls."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    validation = get_object_or_404(
        TestValidation.objects.select_related("test__phase__poc", "submitted_by"),
        pk=validation_pk,
    )
    if not user_can_validate_test(request.user, validation.test):
        raise PermissionDenied
    return render(
        request,
        "pocs/partials/validation_detail.html",
        {"validation": validation, "form": TestValidationDecisionForm()},
    )


@require_POST
def validation_decide(request, validation_pk):
    """Approve (apply the proposal) or reject a pending test outcome."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    validation = get_object_or_404(TestValidation, pk=validation_pk)
    if not user_can_validate_test(request.user, validation.test):
        raise PermissionDenied
    if validation.status != TestValidation.Decision.PENDING:
        messages.info(request, "This outcome has already been decided.")
        return redirect("pocs:validations")

    form = TestValidationDecisionForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "pocs/partials/validation_detail.html",
            {"validation": validation, "form": form},
        )

    if form.cleaned_data["decision"] == "approve":
        validation.apply_to_test(request.user)
        record_audit(validation.test, "validation_approved", request.user)
        messages.success(request, "Outcome approved and applied.")
    else:
        validation.reject(request.user, form.cleaned_data["comment"])
        record_audit(
            validation.test,
            "validation_rejected",
            request.user,
            {"comment": {"before": None, "after": form.cleaned_data["comment"]}},
        )
        messages.success(request, "Outcome rejected.")
    return redirect("pocs:validations")


# ---------------------------------------------------------------------------
# Comments / feedback on Requirements & Use Cases
# ---------------------------------------------------------------------------
COMMENT_TARGET_MODELS = {"usecase": UseCase, "requirement": Requirement, "test": Test}


def _comment_target_url(obj):
    """Where a comment on ``obj`` (a UseCase, Requirement, or Test) lives."""
    if isinstance(obj, UseCase):
        return reverse("pocs:usecase_detail", args=[obj.pk])
    if isinstance(obj, Test):
        return reverse("pocs:test_detail", args=[obj.pk])
    return reverse("pocs:requirement_detail", args=[obj.pk])


def _pending_comments_for(user):
    """Open comments this user may Ack: admins see every POC's, a POC lead
    only theirs (mirrors ``_pending_validations_for``)."""
    qs = Comment.objects.filter(status=Comment.Status.OPEN)
    if user.is_admin:
        return qs
    lead_poc_ids = POCMembership.objects.filter(
        user=user, role_in_poc=POCMembership.Role.LEAD
    ).values_list("poc_id", flat=True)
    return qs.filter(poc_id__in=lead_poc_ids)


@require_POST
def comment_create(request, model, pk):
    """Add a review comment to a Requirement or Use Case (any POC member)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    target_model = COMMENT_TARGET_MODELS.get(model)
    if target_model is None:
        raise Http404
    obj = get_object_or_404(target_model, pk=pk)
    if not user_is_poc_member(request.user, obj.poc):
        raise PermissionDenied
    form = CommentForm(request.POST)
    if form.is_valid():
        comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(target_model),
            object_id=obj.pk,
            poc=obj.poc,
            text=form.cleaned_data["text"],
            author=request.user,
        )
        record_audit(comment, "comment_added", request.user)
        messages.success(request, "Comment added.")
    else:
        messages.error(request, "Comment can't be empty.")
    return redirect(_comment_target_url(obj))


class CommentListView(LoginRequiredMixin, View):
    """Page listing open comments awaiting the current user's Ack."""

    template_name = "pocs/comments.html"

    def get(self, request):
        comments = list(
            _pending_comments_for(request.user).select_related(
                "author", "poc", "content_type"
            )
        )
        for c in comments:
            c.target_url = _comment_target_url(c.content_object)
        return render(
            request,
            self.template_name,
            {"comments": comments, "total": len(comments)},
        )


@require_POST
def comment_decide(request, comment_pk):
    """A POC lead's Ack on a pending comment: mark Applied or Rejected."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    comment = get_object_or_404(Comment, pk=comment_pk)
    if not user_can_lead_poc(request.user, comment.poc):
        raise PermissionDenied
    if comment.status != Comment.Status.OPEN:
        messages.info(request, "This comment has already been decided.")
        return redirect("pocs:comments")

    form = CommentDecisionForm(request.POST)
    if form.is_valid():
        decision = form.cleaned_data["decision"]
        note = form.cleaned_data["note"]
        comment.ack(request.user, decision, note)
        details = {"note": {"before": None, "after": note}} if note else {}
        record_audit(comment, f"comment_{decision}", request.user, details)
        messages.success(request, f"Comment marked {comment.get_status_display()}.")
    return redirect("pocs:comments")


# ---------------------------------------------------------------------------
# Requirements & Use Cases (per-POC registry — spec Fase 3c)
# ---------------------------------------------------------------------------
def poc_specs_redirect(request, pk):
    """Back-compat: the specs registry is now a tab on the POC detail page."""
    return redirect(f"{reverse('pocs:detail', args=[pk])}?tab=specs")


def _field_details(model, field_names, old, new):
    """Build an Audit Log ``details`` dict by diffing scalar model fields.

    ``old`` is ``None`` for a creation (every non-empty field is logged as
    ``before: None``). Many-to-many fields are skipped — callers that need
    those diffed do it themselves (see ``_SpecUpdateMixin.audit_m2m_field``).
    Long ``TextField`` values are noted as changed rather than dumped in full,
    matching the convention used for phase documents.
    """
    details = {}
    for field_name in field_names:
        field = model._meta.get_field(field_name)
        if field.many_to_many:
            continue
        new_val = getattr(new, field_name)
        old_val = getattr(old, field_name) if old is not None else None
        if old_val == new_val:
            continue
        if old is None and not new_val:
            continue
        if field.get_internal_type() == "TextField":
            details[field_name] = {"before": None if old is None else "(changed)", "after": "(set)" if old is None else "(updated)"}
        else:
            details[field_name] = {"before": old_val, "after": new_val}
    return details


class _SpecCreateMixin(POCLeadRequiredMixin):
    """Resolve the POC (``pk``) and stamp created_by/modified_by on save.

    Subclasses set ``audit_kind`` (e.g. ``"requirement"``) so the created
    object is logged to the POC's Audit Log.
    """

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.poc = get_object_or_404(POC, pk=kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_success_url(self):
        return f"{reverse('pocs:detail', args=[self.poc.pk])}?tab=specs"

    def form_valid(self, form):
        form.instance.poc = self.poc
        form.instance.created_by = self.request.user
        form.instance.modified_by = self.request.user
        response = super().form_valid(form)
        record_audit(
            self.object, f"{self.audit_kind}_created", self.request.user,
            _field_details(self.model, form.Meta.fields, None, self.object),
        )
        messages.success(self.request, self.success_message)
        return response


class _SpecUpdateMixin(LoginRequiredMixin):
    """Edit a Requirement/UseCase; gated to the POC's leads/admins.

    Subclasses set ``audit_kind`` and, if the form links another model via a
    many-to-many (e.g. ``requirements`` on Use Case), ``audit_m2m_field`` so
    that linkage change is captured too.
    """

    audit_m2m_field = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, pk=kwargs["pk"])
        self.poc = self.object.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_lead_poc(request.user, self.poc):
            raise PermissionDenied
        if self.poc.is_closed:
            raise PermissionDenied("This POC is closed and locked for editing.")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.object

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_success_url(self):
        next_url = self.request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}, require_https=self.request.is_secure()
        ):
            return next_url
        return f"{reverse('pocs:detail', args=[self.poc.pk])}?tab=specs"

    def form_valid(self, form):
        old = self.model.objects.get(pk=self.object.pk)
        old_m2m_ids = None
        if self.audit_m2m_field:
            old_m2m_ids = set(getattr(old, self.audit_m2m_field).values_list("pk", flat=True))
        form.instance.modified_by = self.request.user
        response = super().form_valid(form)
        details = _field_details(self.model, form.Meta.fields, old, self.object)
        if self.audit_m2m_field:
            new_m2m_ids = set(getattr(self.object, self.audit_m2m_field).values_list("pk", flat=True))
            if old_m2m_ids != new_m2m_ids:
                details[self.audit_m2m_field] = {"before": len(old_m2m_ids), "after": len(new_m2m_ids)}
        if details:
            record_audit(self.object, f"{self.audit_kind}_updated", self.request.user, details)
        messages.success(self.request, self.success_message)
        return response


class RequirementCreateView(_SpecCreateMixin, CreateView):
    model = Requirement
    form_class = RequirementForm
    template_name = "pocs/spec_form.html"
    success_message = "Requirement created."
    audit_kind = "requirement"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["title"] = "Add requirement"
        ctx["kind_label"] = "requirement"
        return ctx


class RequirementUpdateView(_SpecUpdateMixin, UpdateView):
    model = Requirement
    form_class = RequirementForm
    template_name = "pocs/spec_form.html"
    success_message = "Requirement updated."
    audit_kind = "requirement"
    audit_m2m_field = "use_cases"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["title"] = f"Edit requirement · {self.object.code}"
        ctx["kind_label"] = "requirement"
        return ctx


class UseCaseCreateView(_SpecCreateMixin, CreateView):
    model = UseCase
    form_class = UseCaseForm
    template_name = "pocs/spec_form.html"
    success_message = "Use case created."
    audit_kind = "usecase"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["title"] = "Add use case"
        ctx["kind_label"] = "use case"
        return ctx


class UseCaseDetailView(POCMemberRequiredMixin, DetailView):
    """Full-page, read-only view of a Use Case (comfortable long-form reading)."""

    model = UseCase
    template_name = "pocs/usecase_detail.html"
    context_object_name = "usecase"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("tasks")

    def get_poc(self):
        if not hasattr(self, "_poc"):
            self._poc = get_object_or_404(UseCase, pk=self.kwargs["pk"]).poc
        return self._poc

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["can_manage"] = user_can_lead_poc(self.request.user, self.poc)
        ctx["comments"] = self.object.comments.select_related("author", "resolved_by")
        ctx["comment_form"] = CommentForm()
        ctx["comment_target"] = "usecase"
        return ctx


class RequirementDetailView(POCMemberRequiredMixin, DetailView):
    """Full-page, read-only view of a Requirement (mirrors UseCaseDetailView).

    Also the home of its comments thread — Requirements have no other
    per-item page (only the quick-look ``requirement_preview`` modal).
    """

    model = Requirement
    template_name = "pocs/requirement_detail.html"
    context_object_name = "req"

    def get_queryset(self):
        return super().get_queryset().prefetch_related("tasks")

    def get_poc(self):
        if not hasattr(self, "_poc"):
            self._poc = get_object_or_404(Requirement, pk=self.kwargs["pk"]).poc
        return self._poc

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["can_manage"] = user_can_lead_poc(self.request.user, self.poc) and not self.poc.is_closed
        ctx["comments"] = self.object.comments.select_related("author", "resolved_by")
        ctx["comment_form"] = CommentForm()
        ctx["comment_target"] = "requirement"
        ctx.update(_requirement_classification_ctx(self.request, self.object))
        ctx.update(_requirement_lifecycle_ctx(self.request, self.object))
        ctx.update(_requirement_title_ctx(self.request, self.object, field_ctx="detail"))
        return ctx


def _requirement_classification_ctx(request, req):
    return {
        "req": req,
        "can_manage": user_can_lead_poc(request.user, req.poc) and not req.poc.is_closed,
        "gravity_choices": Requirement.field_choices(req.poc, "req_gravity"),
        "operation_choices": Requirement.field_choices(req.poc, "req_operation"),
        "category_choices": Requirement.field_choices(req.poc, "req_category"),
    }


def _requirement_lifecycle_ctx(request, req):
    return {
        "req": req,
        "can_manage": user_can_lead_poc(request.user, req.poc) and not req.poc.is_closed,
        "lifecycle_choices": Requirement.field_choices(req.poc, "life_cycle_phase"),
    }


def _requirement_title_ctx(request, req, field_ctx="preview"):
    is_page_title = field_ctx == "detail"
    return {
        "req": req,
        "can_manage": user_can_lead_poc(request.user, req.poc) and not req.poc.is_closed,
        "field_ctx": field_ctx,
        "is_page_title": is_page_title,
        "heading_class": "text-2xl font-semibold text-ink" if is_page_title else "text-lg font-semibold text-ink",
    }


def _requirement_preview_ctx(request, req, next_url=None):
    if next_url and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = None
    ctx = {"req": req, "can_manage": user_can_lead_poc(request.user, req.poc) and not req.poc.is_closed, "next_url": next_url}
    ctx.update(_requirement_classification_ctx(request, req))
    ctx.update(_requirement_lifecycle_ctx(request, req))
    ctx.update(_requirement_title_ctx(request, req, field_ctx="preview"))
    return ctx


def requirement_preview(request, pk):
    """Read-only fragment for the "eye" preview popup (used when linking).

    Accepts ``?next=<url>`` (e.g. the Use Case page it was opened from) so
    the "Edit" button can carry it through to the full edit form and return
    there on save instead of the POC specs tab (see _SpecUpdateMixin.get_success_url).
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    req = get_object_or_404(Requirement, pk=pk)
    if not user_is_poc_member(request.user, req.poc):
        raise PermissionDenied
    if request.GET.get("brief"):
        # Fast path for the delayed hover-card (static/js/entity_hover_preview.js)
        # — every chip that links a Requirement reuses this same endpoint,
        # no per-template description plumbing needed (spec: hover-card
        # everywhere a Requirement/Use Case link appears).
        return JsonResponse({"title": req.code, "description": req.description or ""})
    return render(request, "pocs/partials/requirement_preview.html", _requirement_preview_ctx(request, req, next_url=request.GET.get("next")))


@require_POST
def requirement_set_gravity(request, pk):
    """Quick gravity change from the classification row (preview popup or
    requirement detail page).

    Same effect as the full edit form (modified_by/modified_date stamp,
    audited as "requirement_updated") but inline, mirroring
    ``usecase_set_status`` — no navigation away from wherever the row was
    shown. Validated against ``Requirement.field_choices`` (predefined +
    POC-custom ``RequirementFieldOption`` values) rather than the fixed
    ``Gravity`` enum, so a custom value like "High" stays selectable instead
    of silently falling back to the first predefined option in the dropdown.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    req = get_object_or_404(Requirement, pk=pk)
    if not user_can_lead_poc(request.user, req.poc):
        raise PermissionDenied
    if req.poc.is_closed:
        raise PermissionDenied("This POC is closed and locked for editing.")
    new_gravity = request.POST.get("req_gravity", "")
    if new_gravity in dict(Requirement.field_choices(req.poc, "req_gravity")) and new_gravity != req.req_gravity:
        old_gravity = req.req_gravity
        req.req_gravity = new_gravity
        req.modified_by = request.user
        req.save(update_fields=["req_gravity", "modified_by", "modified_date"])
        record_audit(
            req, "requirement_updated", request.user,
            {"req_gravity": {"before": old_gravity, "after": new_gravity}},
        )
    return render(request, "pocs/partials/requirement_classification.html", _requirement_classification_ctx(request, req))


_REQUIREMENT_CHOICE_FIELDS = {"req_operation", "req_category", "life_cycle_phase"}
_REQUIREMENT_TEXT_FIELDS = {"sub_system"}


@require_POST
def requirement_set_field(request, pk, field):
    """Generic quick-edit for requirement classification/lifecycle/title fields.

    Covers req_operation, req_category, life_cycle_phase (validated against
    Requirement.field_choices, same as requirement_set_gravity) and
    sub_system (free text — it doubles as the requirement's title). Reused
    by both the preview popup and the requirement detail page: each field
    group re-renders only its own fragment (classification row / lifecycle
    block / title), self-swapped via htmx outerHTML, so a save reflects
    immediately without a full page/modal reload.
    """
    if field not in _REQUIREMENT_CHOICE_FIELDS | _REQUIREMENT_TEXT_FIELDS:
        raise Http404
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    req = get_object_or_404(Requirement, pk=pk)
    if not user_can_lead_poc(request.user, req.poc):
        raise PermissionDenied
    if req.poc.is_closed:
        raise PermissionDenied("This POC is closed and locked for editing.")
    new_value = request.POST.get("value", "").strip()
    if field in _REQUIREMENT_TEXT_FIELDS:
        valid = len(new_value) <= 255
    else:
        valid = new_value in dict(Requirement.field_choices(req.poc, field))
    if valid and new_value != getattr(req, field):
        old_value = getattr(req, field)
        setattr(req, field, new_value)
        req.modified_by = request.user
        req.save(update_fields=[field, "modified_by", "modified_date"])
        record_audit(
            req, "requirement_updated", request.user,
            {field: {"before": old_value, "after": new_value}},
        )
    if field == "life_cycle_phase":
        return render(request, "pocs/partials/requirement_lifecycle.html", _requirement_lifecycle_ctx(request, req))
    if field == "sub_system":
        field_ctx = request.GET.get("ctx", "preview")
        return render(request, "pocs/partials/requirement_title.html", _requirement_title_ctx(request, req, field_ctx=field_ctx))
    return render(request, "pocs/partials/requirement_classification.html", _requirement_classification_ctx(request, req))


def _usecase_preview_ctx(request, uc):
    return {
        "usecase": uc,
        "can_manage": user_can_lead_poc(request.user, uc.poc) and not uc.poc.is_closed,
        "status_choices": UseCase.Status.choices,
    }


def usecase_preview(request, pk):
    """Read-only fragment for the "eye" preview popup (used when linking)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    uc = get_object_or_404(UseCase, pk=pk)
    if not user_is_poc_member(request.user, uc.poc):
        raise PermissionDenied
    if request.GET.get("brief"):
        return JsonResponse({"title": f"{uc.code} · {uc.title}", "description": uc.description or ""})
    return render(request, "pocs/partials/usecase_preview.html", _usecase_preview_ctx(request, uc))


@require_POST
def usecase_set_status(request, pk):
    """Quick status change from the preview popup.

    Same effect as the full edit form (POCLeadRequiredMixin-equivalent gate,
    modified_by/modified_date stamp, audited as "usecase_updated") but inline
    — no navigation away from wherever the popup was opened.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    uc = get_object_or_404(UseCase, pk=pk)
    if not user_can_lead_poc(request.user, uc.poc):
        raise PermissionDenied
    if uc.poc.is_closed:
        raise PermissionDenied("This POC is closed and locked for editing.")
    new_status = request.POST.get("status", "")
    if new_status in dict(UseCase.Status.choices) and new_status != uc.status:
        old_status = uc.status
        uc.status = new_status
        uc.modified_by = request.user
        uc.save(update_fields=["status", "modified_by", "modified_date"])
        record_audit(
            uc, "usecase_updated", request.user,
            {"status": {"before": old_status, "after": new_status}},
        )
    return render(request, "pocs/partials/usecase_preview.html", _usecase_preview_ctx(request, uc))


def test_preview(request, pk):
    """Read-only fragment for the "eye" preview popup — used by the Overview
    graph so clicking a test node reads it in place, same pattern as
    ``requirement_preview``/``usecase_preview``."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    test = get_object_or_404(Test, pk=pk)
    if not user_is_poc_member(request.user, test.phase.poc):
        raise PermissionDenied
    if request.GET.get("brief"):
        return JsonResponse({"title": test.test_code or test.title, "description": test.description or ""})
    return render(request, "pocs/partials/test_preview.html", {"test": test})


class TestDetailView(POCMemberRequiredMixin, DetailView):
    """Full-page, read-only-by-default view of one Test (spec item 5) — the
    "I want the full picture" destination, reachable from both the tests
    overview and the phase page. Reuses ``partials/test_row.html`` (the exact
    execute/notes/parameters panel already shown inline on the phase page),
    so there is one implementation of "everything about this test", not two.
    """

    model = Test
    template_name = "pocs/test_detail.html"
    context_object_name = "test"

    def get_queryset(self):
        return super().get_queryset().select_related(
            "phase__poc", "assigned_to"
        ).prefetch_related("requirements", "validations")

    def get_poc(self):
        if not hasattr(self, "_poc"):
            self._poc = get_object_or_404(Test, pk=self.kwargs["pk"]).phase.poc
        return self._poc

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        test = self.object
        user = self.request.user
        can_edit = user_can_edit_phase(user, test.phase)
        test.can_execute = can_edit or test.assigned_to_id == user.id
        test.can_validate = user_can_validate_test(user, test)
        ctx["poc"] = self.poc
        ctx["phase"] = test.phase
        ctx["can_lead"] = can_edit
        ctx["test_execution_choices"] = Test.ExecutionStatus.choices
        ctx["test_result_choices"] = Test.Result.choices
        ctx["comments"] = test.comments.select_related("author", "resolved_by")
        ctx["comment_form"] = CommentForm()
        ctx["comment_target"] = "test"
        ctx["can_manage"] = can_edit
        return ctx


def poc_graph_data(request, pk):
    """JSON graph for the Overview "map" view: POC -> Use Cases -> Requirements
    -> Tests, coloured by each node's ``graph_status()``.

    Requirements with no use case, and tests with no linked requirement, are
    still included — wired straight to the POC node — so nothing in the POC
    is silently dropped from the map just because it isn't fully linked up yet.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    poc = get_object_or_404(POC, pk=pk)
    if not user_is_poc_member(request.user, poc):
        raise PermissionDenied

    use_cases = list(poc.use_cases.prefetch_related("requirements"))
    requirements = list(poc.requirements.prefetch_related("tests", "use_cases"))
    all_tests = list(
        Test.objects.filter(phase__poc=poc).select_related("phase")
    )

    nodes = [{"id": "poc", "type": "poc", "label": poc.name, "status": poc.graph_status(),
              "description": poc.description}]
    edges = []

    linked_req_ids = set()
    for uc in use_cases:
        nodes.append(
            {"id": f"uc-{uc.id}", "type": "usecase", "label": uc.title,
             "status": uc.graph_status(), "url": reverse("pocs:usecase_preview", args=[uc.id]),
             "detailUrl": reverse("pocs:usecase_detail", args=[uc.id]), "detailLabel": "Go to Use Case",
             "description": uc.description}
        )
        edges.append({"source": "poc", "target": f"uc-{uc.id}"})
        for req in uc.requirements.all():
            linked_req_ids.add(req.id)
            edges.append({"source": f"uc-{uc.id}", "target": f"req-{req.id}"})

    linked_test_ids = set()
    for req in requirements:
        nodes.append(
            {"id": f"req-{req.id}", "type": "requirement", "label": req.code,
             "status": req.graph_status(), "url": reverse("pocs:requirement_preview", args=[req.id]),
             "detailUrl": reverse("pocs:requirement_detail", args=[req.id]), "detailLabel": "Go to Requirement",
             "description": req.description}
        )
        if req.id not in linked_req_ids:
            # Orphan requirement (no use case yet) — still shown, wired to the POC.
            edges.append({"source": "poc", "target": f"req-{req.id}"})
        for t in req.tests.all():
            linked_test_ids.add(t.id)
            edges.append({"source": f"req-{req.id}", "target": f"test-{t.id}"})

    for t in all_tests:
        nodes.append(
            {"id": f"test-{t.id}", "type": "test", "label": t.test_code or t.title,
             "status": t.graph_status(), "url": reverse("pocs:test_preview", args=[t.id]),
             "detailUrl": f"{reverse('pocs:phase_detail', args=[t.phase_id])}#test-{t.id}",
             "detailLabel": "Open in phase",
             "description": t.description}
        )
        if t.id not in linked_test_ids:
            # Orphan test (no requirement linked yet) — still shown, wired to the POC.
            edges.append({"source": "poc", "target": f"test-{t.id}"})

    return JsonResponse({"nodes": nodes, "edges": edges})


class UseCaseUpdateView(_SpecUpdateMixin, UpdateView):
    model = UseCase
    form_class = UseCaseForm
    template_name = "pocs/spec_form.html"
    success_message = "Use case updated."
    audit_kind = "usecase"
    audit_m2m_field = "requirements"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["poc"] = self.poc
        ctx["title"] = f"Edit use case · {self.object.code}"
        ctx["kind_label"] = "use case"
        return ctx


class _SpecDeleteMixin(LoginRequiredMixin):
    """Delete a Requirement/UseCase; gated to the POC's leads/admins. POST only."""

    http_method_names = ["post"]

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.object = get_object_or_404(self.model, pk=kwargs["pk"])
        self.poc = self.object.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_lead_poc(request.user, self.poc):
            raise PermissionDenied
        if self.poc.is_closed:
            raise PermissionDenied("This POC is closed and locked for editing.")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        return self.object

    def get_success_url(self):
        return f"{reverse('pocs:detail', args=[self.poc.pk])}?tab=specs"


class RequirementDeleteView(_SpecDeleteMixin, DeleteView):
    model = Requirement

    def form_valid(self, form):
        code = self.object.code
        record_audit(self.object, "requirement_deleted", self.request.user,
                     {"code": {"before": code, "after": None}})
        response = super().form_valid(form)
        messages.success(self.request, f"Requirement “{code}” deleted.")
        return response


class UseCaseDeleteView(_SpecDeleteMixin, DeleteView):
    model = UseCase

    def form_valid(self, form):
        code = self.object.code
        record_audit(self.object, "usecase_deleted", self.request.user,
                     {"code": {"before": code, "after": None}})
        response = super().form_valid(form)
        messages.success(self.request, f"Use case “{code}” deleted.")
        return response


# ---------------------------------------------------------------------------
# Requirements / Use Cases — bulk actions on a filtered selection
# ---------------------------------------------------------------------------
def _specs_redirect(poc):
    return f"{reverse('pocs:detail', args=[poc.pk])}?tab=specs"


@poc_lead_required
@require_POST
def requirement_bulk_status(request, pk):
    """Set the same lifecycle status on several requirements at once."""
    poc = get_object_or_404(POC, pk=pk)
    new_status = request.POST.get("status", "")
    ids = [int(i) for i in request.POST.getlist("requirement_ids") if i.isdigit()]
    valid_statuses = {v for v, _ in Requirement.field_choices(poc, "life_cycle_phase")}
    applied = 0
    if new_status in valid_statuses and ids:
        for req in poc.requirements.filter(pk__in=ids):
            if req.life_cycle_phase == new_status:
                continue
            old = req.life_cycle_phase
            req.life_cycle_phase = new_status
            req.modified_by = request.user
            req.save(update_fields=["life_cycle_phase", "modified_by", "modified_date"])
            record_audit(req, "requirement_updated", request.user,
                         {"life_cycle_phase": {"before": old, "after": new_status}})
            applied += 1
    messages.success(request, f"Lifecycle status updated on {applied} requirement(s).")
    return redirect(_specs_redirect(poc))


@poc_lead_required
@require_POST
def requirement_bulk_delete(request, pk):
    """Permanently delete several requirements at once."""
    poc = get_object_or_404(POC, pk=pk)
    ids = [int(i) for i in request.POST.getlist("requirement_ids") if i.isdigit()]
    deleted = 0
    for req in poc.requirements.filter(pk__in=ids):
        code = req.code
        record_audit(req, "requirement_deleted", request.user,
                     {"code": {"before": code, "after": None}})
        req.delete()
        deleted += 1
    messages.success(request, f"Deleted {deleted} requirement(s).")
    return redirect(_specs_redirect(poc))


@poc_lead_required
@require_POST
def usecase_bulk_status(request, pk):
    """Set the same status on several use cases at once."""
    poc = get_object_or_404(POC, pk=pk)
    new_status = request.POST.get("status", "")
    ids = [int(i) for i in request.POST.getlist("usecase_ids") if i.isdigit()]
    valid_statuses = dict(UseCase.Status.choices)
    applied = 0
    if new_status in valid_statuses and ids:
        for uc in poc.use_cases.filter(pk__in=ids):
            if uc.status == new_status:
                continue
            old = uc.status
            uc.status = new_status
            uc.modified_by = request.user
            uc.save(update_fields=["status", "modified_by", "modified_date"])
            record_audit(uc, "usecase_updated", request.user,
                         {"status": {"before": old, "after": new_status}})
            applied += 1
    messages.success(request, f"Status updated on {applied} use case(s).")
    return redirect(_specs_redirect(poc))


@poc_lead_required
@require_POST
def usecase_bulk_delete(request, pk):
    """Permanently delete several use cases at once."""
    poc = get_object_or_404(POC, pk=pk)
    ids = [int(i) for i in request.POST.getlist("usecase_ids") if i.isdigit()]
    deleted = 0
    for uc in poc.use_cases.filter(pk__in=ids):
        code = uc.code
        record_audit(uc, "usecase_deleted", request.user,
                     {"code": {"before": code, "after": None}})
        uc.delete()
        deleted += 1
    messages.success(request, f"Deleted {deleted} use case(s).")
    return redirect(_specs_redirect(poc))


class RequirementImportView(POCLeadRequiredMixin, View):
    """Import Requirements from an .xlsx with a per-row validated preview (10c)."""

    template_name = "pocs/requirement_import.html"

    def _session_key(self):
        return f"req_import_{self.poc.pk}"

    def get(self, request, pk):
        return render(
            request,
            self.template_name,
            {"poc": self.poc, "form": RequirementImportForm()},
        )

    def post(self, request, pk):
        from .requirements_import import (
            import_requirements,
            parse_requirements_md,
            parse_requirements_xlsx,
        )

        # Stage 2: confirm — import the previously-parsed valid rows.
        if request.POST.get("confirm"):
            rows = request.session.get(self._session_key())
            if not rows:
                messages.error(request, "Nothing to import — please upload the file again.")
                return redirect("pocs:requirement_import", pk=self.poc.pk)
            created, updated = import_requirements(self.poc, rows, request.user)
            request.session.pop(self._session_key(), None)
            parts = []
            if created:
                parts.append(f"created {created}")
            if updated:
                parts.append(f"updated {updated}")
            messages.success(request, f"Requirements: {', '.join(parts) or 'nothing to import'}.")
            return redirect(f"{reverse('pocs:detail', args=[self.poc.pk])}?tab=specs")

        # Stage 1: upload — parse and show the validated preview.
        form = RequirementImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request, self.template_name, {"poc": self.poc, "form": form}
            )
        uploaded = form.cleaned_data["file"]
        parse_fn = parse_requirements_md if uploaded.name.lower().endswith(".md") else parse_requirements_xlsx
        try:
            preview = parse_fn(uploaded, self.poc)
        except Exception:  # noqa: BLE001
            logger.exception("Requirement import parse failed")
            messages.error(request, "Could not read the file — is it a valid .xlsx or .md?")
            return render(
                request, self.template_name, {"poc": self.poc, "form": RequirementImportForm()}
            )

        valid_rows = [r for r in preview if r["valid"]]
        request.session[self._session_key()] = valid_rows
        return render(
            request,
            self.template_name,
            {
                "poc": self.poc,
                "form": RequirementImportForm(),
                "preview": preview,
                "valid_count": len(valid_rows),
                "error_count": len(preview) - len(valid_rows),
            },
        )


def requirement_import_template(request, pk):
    """Download the .xlsx template expected by the Requirement importer."""
    poc = get_object_or_404(POC, pk=pk)
    if not user_is_poc_member(request.user, poc):
        raise PermissionDenied
    from .requirements_import import build_requirements_template_xlsx

    buf = build_requirements_template_xlsx(poc)
    response = HttpResponse(
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="requirements_template.xlsx"'
    return response


def usecase_import_template(request, pk):
    """Download the .xlsx template expected by the Use Case importer."""
    poc = get_object_or_404(POC, pk=pk)
    if not user_is_poc_member(request.user, poc):
        raise PermissionDenied
    from .requirements_import import build_usecases_template_xlsx

    buf = build_usecases_template_xlsx()
    response = HttpResponse(
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="usecases_template.xlsx"'
    return response


def requirement_export(request, pk):
    """Download every current Requirement of this POC as .xlsx or .md
    (``?format=``, defaults to xlsx) — edit it and re-upload via the importer
    (matched by ``code``) to update them in place."""
    poc = get_object_or_404(POC, pk=pk)
    if not user_is_poc_member(request.user, poc):
        raise PermissionDenied
    if request.GET.get("format") == "md":
        from .requirements_import import build_requirements_export_md

        response = HttpResponse(build_requirements_export_md(poc), content_type="text/markdown")
        response["Content-Disposition"] = 'attachment; filename="requirements_export.md"'
        return response
    from .requirements_import import build_requirements_export_xlsx

    buf = build_requirements_export_xlsx(poc)
    response = HttpResponse(
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="requirements_export.xlsx"'
    return response


def usecase_export(request, pk):
    """Download every current Use Case of this POC as .xlsx or .md
    (``?format=``, defaults to xlsx) — edit it and re-upload via the importer
    (matched by ``code``) to update them in place."""
    poc = get_object_or_404(POC, pk=pk)
    if not user_is_poc_member(request.user, poc):
        raise PermissionDenied
    if request.GET.get("format") == "md":
        from .requirements_import import build_usecases_export_md

        response = HttpResponse(build_usecases_export_md(poc), content_type="text/markdown")
        response["Content-Disposition"] = 'attachment; filename="usecases_export.md"'
        return response
    from .requirements_import import build_usecases_export_xlsx

    buf = build_usecases_export_xlsx(poc)
    response = HttpResponse(
        buf.read(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="usecases_export.xlsx"'
    return response


# ---------------------------------------------------------------------------
# Use Case × Requirement matrix (improvement brief item 3)
# ---------------------------------------------------------------------------
_MATRIX_PALETTE_SIZE = 4  # brand / info / warning / danger — see the template


class UCRequirementMatrixView(POCMemberRequiredMixin, View):
    """Grid of Use Cases (rows) × Requirements (columns) to quickly toggle
    links, instead of editing one-by-one from either entity's form."""

    template_name = "pocs/uc_requirement_matrix.html"

    def get(self, request, pk):
        poc = self.poc
        use_cases = list(poc.use_cases.prefetch_related("requirements").order_by("code"))
        requirements = list(
            poc.requirements.order_by("req_category", "req_operation", "code")
        )

        # Colour/group columns by category (contiguous after the ordering
        # above) — reuses the app's existing semantic tokens, cycled, never a
        # new hardcoded palette (spec item 3).
        palette_index = {}
        for req in requirements:
            if req.req_category not in palette_index:
                palette_index[req.req_category] = len(palette_index) % _MATRIX_PALETTE_SIZE
            req.color_token = palette_index[req.req_category]

        linked_map = {uc.id: set() for uc in use_cases}
        for uc in use_cases:
            linked_map[uc.id] = {req.id for req in uc.requirements.all()}

        sub_systems = sorted({r.sub_system for r in requirements if r.sub_system})

        return render(
            request,
            self.template_name,
            {
                "poc": poc,
                "use_cases": use_cases,
                "requirements": requirements,
                "linked_map": linked_map,
                "sub_systems": sub_systems,
                "can_manage": user_can_lead_poc(request.user, poc) and not poc.is_closed,
            },
        )


@require_POST
def matrix_toggle_link(request, pk):
    """Toggle the UC↔Requirement link for one cell of the matrix (JSON)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    poc = get_object_or_404(POC, pk=pk)
    if not user_can_lead_poc(request.user, poc) or poc.is_closed:
        raise PermissionDenied
    uc = get_object_or_404(UseCase, pk=request.POST.get("usecase_id"), poc=poc)
    req = get_object_or_404(Requirement, pk=request.POST.get("requirement_id"), poc=poc)
    if uc.requirements.filter(pk=req.pk).exists():
        uc.requirements.remove(req)
        linked = False
    else:
        uc.requirements.add(req)
        linked = True
    return JsonResponse({"linked": linked})


class UseCaseImportView(POCLeadRequiredMixin, View):
    """Import Use Cases from an .xlsx with a per-row validated preview (10c)."""

    template_name = "pocs/usecase_import.html"

    def _session_key(self):
        return f"uc_import_{self.poc.pk}"

    def get(self, request, pk):
        return render(
            request,
            self.template_name,
            {"poc": self.poc, "form": RequirementImportForm()},
        )

    def post(self, request, pk):
        from .requirements_import import import_usecases, parse_usecases_md, parse_usecases_xlsx

        if request.POST.get("confirm"):
            rows = request.session.get(self._session_key())
            if not rows:
                messages.error(request, "Nothing to import — please upload the file again.")
                return redirect("pocs:usecase_import", pk=self.poc.pk)
            created, updated = import_usecases(self.poc, rows, request.user)
            request.session.pop(self._session_key(), None)
            parts = []
            if created:
                parts.append(f"created {created}")
            if updated:
                parts.append(f"updated {updated}")
            messages.success(request, f"Use cases: {', '.join(parts) or 'nothing to import'}.")
            return redirect(f"{reverse('pocs:detail', args=[self.poc.pk])}?tab=specs")

        form = RequirementImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"poc": self.poc, "form": form})
        uploaded = form.cleaned_data["file"]
        parse_fn = parse_usecases_md if uploaded.name.lower().endswith(".md") else parse_usecases_xlsx
        try:
            preview = parse_fn(uploaded, self.poc)
        except Exception:  # noqa: BLE001
            logger.exception("Use case import parse failed")
            messages.error(request, "Could not read the file — is it a valid .xlsx or .md?")
            return render(
                request, self.template_name, {"poc": self.poc, "form": RequirementImportForm()}
            )

        valid_rows = [r for r in preview if r["valid"]]
        request.session[self._session_key()] = valid_rows
        return render(
            request,
            self.template_name,
            {
                "poc": self.poc,
                "form": RequirementImportForm(),
                "preview": preview,
                "valid_count": len(valid_rows),
                "error_count": len(preview) - len(valid_rows),
            },
        )


# ---------------------------------------------------------------------------
# Phase documents & images (Documentation / Functional Analysis phases)
# ---------------------------------------------------------------------------
class PhaseDocumentCreateView(_PhaseEditCreateMixin, CreateView):
    """Add a free-form Markdown document to any leaf phase (admin / editing lead).

    Functional Analysis sections are seeded from the template; this adds extra
    free documents alongside them (or the only documents, on Test phases).
    """

    model = PhaseDocument
    form_class = PhaseDocumentForm
    template_name = "pocs/phase_document_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["phase"] = self.phase
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["title"] = "Add document"
        ctx["images"] = list(self.phase.images.all())
        return ctx

    def form_valid(self, form):
        form.instance.phase = self.phase
        insert_after = form.cleaned_data.get("insert_after")
        existing = list(self.phase.documents.order_by("order", "id"))
        position = existing.index(insert_after) + 1 if insert_after else 0
        self.object = form.save()
        existing.insert(position, self.object)
        for i, doc in enumerate(existing):
            if doc.order != i:
                doc.order = i
                doc.save(update_fields=["order"])
        record_audit(self.object, "document_created", self.request.user,
                     {"title": {"before": None, "after": self.object.title}})
        messages.success(self.request, f"Document “{self.object.title}” created.")
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)


class PhaseDocumentUpdateView(_ItemEditMixin, UpdateView):
    """Edit a phase document's content (and title, unless it's an FA section)."""

    model = PhaseDocument
    url_kwarg = "document_pk"
    form_class = PhaseDocumentForm
    template_name = "pocs/phase_document_form.html"
    pk_url_kwarg = "document_pk"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["title"] = f"Edit document · {self.object.title}"
        ctx["images"] = list(self.phase.images.all())
        return ctx

    def form_valid(self, form):
        # DB still holds the pre-save values (the form instance is already mutated).
        old = PhaseDocument.objects.get(pk=self.object.pk)
        old_title, old_content = old.title, old.content
        self.object = form.save()
        details = {}
        if old_title != self.object.title:
            details["title"] = {"before": old_title, "after": self.object.title}
        if old_content != self.object.content:
            details["content"] = {"before": "(changed)", "after": "(updated)"}
        if details:
            record_audit(self.object, "document_updated", self.request.user, details)
        messages.success(self.request, f"Document “{self.object.title}” updated.")
        return redirect("pocs:phase_detail", phase_pk=self.object.phase_id)


class PhaseDocumentDeleteView(_ItemEditMixin, DeleteView):
    """Delete a phase document (POST). Functional Analysis sections can't be
    deleted — they're owned by the template and would be re-seeded anyway."""

    model = PhaseDocument
    url_kwarg = "document_pk"
    pk_url_kwarg = "document_pk"
    http_method_names = ["post"]

    def form_valid(self, form):
        phase = self.object.phase
        if self.object.is_fa_section:
            messages.error(
                self.request, "Functional Analysis sections can't be deleted."
            )
            return redirect("pocs:phase_detail", phase_pk=phase.pk)
        title = self.object.title
        record_audit(self.object, "document_deleted", self.request.user,
                     {"title": {"before": title, "after": None}})
        self.object.delete()
        messages.success(self.request, f"Document “{title}” deleted.")
        return redirect("pocs:phase_detail", phase_pk=phase.pk)


def fa_section_preview(request, document_pk):
    """Read-only fragment previewing a Functional Analysis section as rendered
    Markdown — for locked (Use Cases/Requirements) sections the POC lead can't
    edit it directly (see ``ensure_fa_documents``); for free ("Other") sections
    it's just a quick look without opening the editor.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    document = get_object_or_404(PhaseDocument, pk=document_pk)
    if not user_is_poc_member(request.user, document.phase.poc):
        raise PermissionDenied
    return render(request, "pocs/partials/fa_section_preview.html", {"document": document})


class POCImageUploadView(POCLeadRequiredMixin, CreateView):
    """Upload an image to a POC (used by the closure-conclusion Markdown
    editor). Mirrors ``PhaseImageUploadView`` but scoped to the whole POC."""

    model = POCImage
    form_class = POCImageForm
    http_method_names = ["post"]

    @property
    def _is_ajax(self):
        return self.request.GET.get("ajax") == "1"

    def form_valid(self, form):
        form.instance.poc = self.poc
        form.instance.uploaded_by = self.request.user
        obj = form.save()
        record_audit(obj, "image_uploaded", self.request.user,
                     {"image": {"before": None, "after": str(obj)}})
        if self._is_ajax:
            return JsonResponse(
                {"url": obj.image.url, "snippet": obj.markdown_snippet, "caption": obj.caption}
            )
        messages.success(self.request, "Image uploaded.")
        return redirect("pocs:close", pk=self.poc.pk)

    def form_invalid(self, form):
        if self._is_ajax:
            return JsonResponse(
                {"error": "Upload failed — use a PNG/JPG/GIF/WEBP/SVG image."}, status=400
            )
        messages.error(self.request, "Upload failed — use a PNG/JPG/GIF/WEBP/SVG image.")
        return redirect("pocs:close", pk=self.poc.pk)


class PhaseImageUploadView(_PhaseEditCreateMixin, CreateView):
    """Upload an image to a phase.

    Normal (form) POSTs redirect back to the phase. When called with ``?ajax=1``
    (from the Markdown report editor), returns JSON with the image URL and a
    ready-to-insert ``![caption](url)`` snippet so the editor can drop the link
    at the cursor automatically — no manual copy/paste.
    """

    model = PhaseImage
    form_class = PhaseImageForm
    http_method_names = ["post"]

    @property
    def _is_ajax(self):
        return self.request.GET.get("ajax") == "1"

    def form_valid(self, form):
        form.instance.phase = self.phase
        form.instance.uploaded_by = self.request.user
        obj = form.save()
        record_audit(obj, "image_uploaded", self.request.user,
                     {"image": {"before": None, "after": str(obj)}})
        if self._is_ajax:
            return JsonResponse(
                {"url": obj.image.url, "snippet": obj.markdown_snippet, "caption": obj.caption}
            )
        messages.success(self.request, "Image uploaded.")
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)

    def form_invalid(self, form):
        if self._is_ajax:
            return JsonResponse(
                {"error": "Upload failed — use a PNG/JPG/GIF/WEBP/SVG image."}, status=400
            )
        messages.error(self.request, "Upload failed — use a PNG/JPG/GIF/WEBP/SVG image.")
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)


class PhaseImageDeleteView(_ItemEditMixin, DeleteView):
    """Delete an uploaded phase image (POST)."""

    model = PhaseImage
    url_kwarg = "image_pk"
    pk_url_kwarg = "image_pk"
    http_method_names = ["post"]

    def form_valid(self, form):
        phase = self.object.phase
        record_audit(self.object, "image_deleted", self.request.user,
                     {"image": {"before": str(self.object), "after": None}})
        if self.object.image:
            self.object.image.delete(save=False)
        self.object.delete()
        messages.success(self.request, "Image deleted.")
        return redirect("pocs:phase_detail", phase_pk=phase.pk)


def phase_template_download(request, phase_pk):
    """Download the template for a phase (.docx or .zip).

    Streams the phase's own ``report_template`` when set; otherwise falls back to
    the global default template (``ReportSettings``). Available to POC members.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_is_poc_member(request.user, phase.poc):
        raise PermissionDenied

    template = phase.report_template
    if not template:
        from apps.reports.models import ReportSettings

        template = ReportSettings.load().default_template
    if not template:
        raise Http404("No template available for this phase.")
    return FileResponse(
        template.open("rb"),
        as_attachment=True,
        filename=template.name.rsplit("/", 1)[-1],
    )


# ---------------------------------------------------------------------------
# Phase blueprint builder (admin only, global)
# ---------------------------------------------------------------------------
class PhaseTemplateListView(AdminRequiredMixin, ListView):
    """Tree view of the global phase blueprint."""

    model = PhaseTemplate
    template_name = "pocs/phase_template_list.html"
    context_object_name = "roots"

    def get_queryset(self):
        return PhaseTemplate.objects.filter(parent__isnull=True)

    def get_context_data(self, **kwargs):
        from .models import BlueprintVersion

        ctx = super().get_context_data(**kwargs)
        ctx["versions"] = BlueprintVersion.objects.select_related("created_by")[:15]
        ctx["all_pocs"] = POC.objects.order_by("name")
        return ctx


class PhaseTemplateCreateView(AdminRequiredMixin, CreateView):
    """Create a blueprint node — a root, or a child of ``parent_pk``."""

    model = PhaseTemplate
    form_class = PhaseTemplateForm
    template_name = "pocs/phase_template_form.html"
    success_url = reverse_lazy("pocs:phase_template_list")

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.parent = None
        if "parent_pk" in kwargs:
            self.parent = get_object_or_404(PhaseTemplate, pk=kwargs["parent_pk"])

    def dispatch(self, request, *args, **kwargs):
        if self.parent is not None:
            if not self.parent.can_have_children:
                messages.error(request, "Maximum blueprint depth (3 levels) reached.")
                return redirect("pocs:phase_template_list")
            if self.parent.base_tasks.exists() or self.parent.base_tests.exists():
                messages.error(
                    request,
                    "This node has base tasks/tests; it can't also have sub-phases.",
                )
                return redirect("pocs:phase_template_list")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["parent"] = self.parent
        ctx["title"] = (
            f"Add sub-phase under “{self.parent.name}”" if self.parent else "Add phase"
        )
        return ctx

    def form_valid(self, form):
        from .services import snapshot_blueprint

        snapshot_blueprint(self.request.user, "Before adding a blueprint node")
        form.instance.parent = self.parent
        siblings = PhaseTemplate.objects.filter(parent=self.parent)
        form.instance.order = (siblings.aggregate(m=Max("order"))["m"] or 0) + 1
        messages.success(self.request, f"Blueprint node “{form.instance.name}” created.")
        return super().form_valid(form)


class PhaseTemplateUpdateView(AdminRequiredMixin, UpdateView):
    model = PhaseTemplate
    form_class = PhaseTemplateForm
    template_name = "pocs/phase_template_form.html"
    success_url = reverse_lazy("pocs:phase_template_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["parent"] = self.object.parent
        ctx["title"] = f"Edit · {self.object.name}"
        return ctx

    def form_valid(self, form):
        from .services import snapshot_blueprint

        snapshot_blueprint(self.request.user, f"Before editing “{self.object.name}”")
        return super().form_valid(form)


class PhaseTemplateDeleteView(AdminRequiredMixin, DeleteView):
    model = PhaseTemplate
    template_name = "pocs/phase_template_confirm_delete.html"
    success_url = reverse_lazy("pocs:phase_template_list")

    def form_valid(self, form):
        from .services import snapshot_blueprint

        snapshot_blueprint(self.request.user, f"Before deleting “{self.object.name}”")
        messages.success(self.request, f"Blueprint node “{self.object.name}” deleted.")
        return super().form_valid(form)


# --- Base tasks / tests on a blueprint node (admin) ---
class _BaseItemCreateMixin(AdminRequiredMixin, CreateView):
    template_name = "pocs/base_item_form.html"
    success_url = reverse_lazy("pocs:phase_template_list")
    item_label = ""

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.node = get_object_or_404(PhaseTemplate, pk=kwargs["pk"])

    def dispatch(self, request, *args, **kwargs):
        if self.node.children.exists():
            messages.error(
                request, "This node has sub-phases; add base tasks/tests on a leaf node."
            )
            return redirect("pocs:phase_template_list")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["node"] = self.node
        ctx["title"] = f"Add base {self.item_label} to “{self.node.name}”"
        return ctx

    def form_valid(self, form):
        from .services import snapshot_blueprint

        snapshot_blueprint(self.request.user, f"Before adding a base {self.item_label}")
        form.instance.phase_template = self.node
        siblings = form.instance.__class__.objects.filter(phase_template=self.node)
        form.instance.order = (siblings.aggregate(m=Max("order"))["m"] or 0) + 1
        messages.success(self.request, f"Base {self.item_label} added.")
        return super().form_valid(form)


class BaseTaskCreateView(_BaseItemCreateMixin):
    model = BaseTask
    form_class = BaseTaskForm
    item_label = "task"


class BaseTestCreateView(_BaseItemCreateMixin):
    model = BaseTest
    form_class = BaseTestForm
    item_label = "test"


class BasePhaseDocumentCreateView(_BaseItemCreateMixin):
    model = BasePhaseDocument
    form_class = BasePhaseDocumentForm
    item_label = "document"


class _BaseItemDeleteView(AdminRequiredMixin, DeleteView):
    http_method_names = ["post"]
    success_url = reverse_lazy("pocs:phase_template_list")

    def form_valid(self, form):
        from .services import snapshot_blueprint

        snapshot_blueprint(self.request.user, "Before deleting a base item")
        return super().form_valid(form)


class BaseTaskDeleteView(_BaseItemDeleteView):
    model = BaseTask


class BaseTestDeleteView(_BaseItemDeleteView):
    model = BaseTest


class BasePhaseDocumentDeleteView(_BaseItemDeleteView):
    model = BasePhaseDocument


@admin_required
@require_POST
def phase_template_apply_all(request):
    """Apply the current blueprint to every POC (admin)."""
    from .services import sync_blueprint_to_pocs

    stats = sync_blueprint_to_pocs()
    messages.success(
        request,
        f"Blueprint applied to {stats['pocs']} POC(s): "
        f"{stats['created']} phase(s) added, {stats['updated']} updated.",
    )
    if stats["missing_templates"]:
        names = ", ".join(sorted(stats["missing_templates"]))
        messages.warning(
            request,
            f"Report template file(s) missing on disk for: {names}. Their "
            "phases were synced without a template — re-upload the file(s) "
            "on the blueprint.",
        )
    return redirect("pocs:phase_template_list")


@admin_required
@require_POST
def phase_template_apply_selected(request):
    """Apply the blueprint to a manually-chosen subset of POCs (spec Fase 8)."""
    from .services import sync_blueprint_to_pocs

    ids = [int(i) for i in request.POST.getlist("poc_ids") if i.isdigit()]
    pocs = list(POC.objects.filter(id__in=ids))
    if not pocs:
        messages.error(request, "Select at least one POC to apply the blueprint to.")
        return redirect("pocs:phase_template_list")
    stats = sync_blueprint_to_pocs(pocs)
    messages.success(
        request,
        f"Blueprint applied to {stats['pocs']} selected POC(s): "
        f"{stats['created']} phase(s) added, {stats['updated']} updated.",
    )
    return redirect("pocs:phase_template_list")


# ---------------------------------------------------------------------------
# Blueprint version history (spec Fase 8 — undo / restore)
# ---------------------------------------------------------------------------
@admin_required
@require_POST
def blueprint_restore(request, version_pk):
    """Restore the blueprint to a saved snapshot (admin)."""
    from .models import BlueprintVersion
    from .services import restore_blueprint, snapshot_blueprint

    version = get_object_or_404(BlueprintVersion, pk=version_pk)
    # Snapshot the current state first, so a restore is itself undoable.
    snapshot_blueprint(request.user, "Before restoring a previous version")
    restore_blueprint(version)
    messages.success(
        request,
        f"Blueprint restored to the version from {version.created_at:%Y-%m-%d %H:%M}.",
    )
    return redirect("pocs:phase_template_list")


@admin_required
@require_POST
def blueprint_undo(request):
    """Revert the most recent blueprint change (restore the latest snapshot)."""
    from .models import BlueprintVersion
    from .services import restore_blueprint

    version = BlueprintVersion.objects.first()  # newest (ordering = -created_at)
    if version is None:
        messages.info(request, "No blueprint history to undo yet.")
        return redirect("pocs:phase_template_list")
    restore_blueprint(version)
    version.delete()  # consume it so repeated undo walks back the history
    messages.success(request, "Last blueprint change reverted.")
    return redirect("pocs:phase_template_list")


# ---------------------------------------------------------------------------
# POC import (admin) — M365 PoC Follow-up .xlsx
# ---------------------------------------------------------------------------
class POCImportView(AdminRequiredMixin, View):
    """Upload the PoC Follow-up Excel to import/update POCs (admin)."""

    template_name = "pocs/poc_import.html"

    def get(self, request):
        return render(request, self.template_name, {"form": POCImportForm()})

    def post(self, request):
        form = POCImportForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        dry_run = form.cleaned_data["dry_run"]
        try:
            from .importer import import_pocs_from_xlsx

            stats = import_pocs_from_xlsx(
                form.cleaned_data["file"], request.user, dry_run=dry_run
            )
        except Exception:
            # Never 500 on a bad file — log the traceback and tell the admin.
            logger.exception("POC import failed for file %r", getattr(
                form.cleaned_data.get("file"), "name", "?"))
            messages.error(
                request,
                "No se pudo importar el archivo. Comprueba que es un .csv/.xlsx "
                "válido (codificación UTF-8) y vuelve a intentarlo. El error se ha "
                "registrado en el log del servidor.",
            )
            return render(request, self.template_name, {"form": POCImportForm()})
        verb = "Validated" if dry_run else "Imported"
        messages.success(
            request,
            f"{verb}: {stats['created']} new, {stats['updated']} updated, "
            f"{stats['skipped']} skipped, {len(stats['errors'])} error(s).",
        )
        return render(
            request,
            self.template_name,
            {"form": POCImportForm(), "stats": stats, "dry_run": dry_run},
        )


# ---------------------------------------------------------------------------
# Functional Analysis template (admin)
# ---------------------------------------------------------------------------
def _has_default_template():
    """True when a global default report template is configured."""
    from apps.reports.models import ReportSettings

    return bool(ReportSettings.load().default_template)


class FAStepListView(AdminRequiredMixin, ListView):
    model = FunctionalAnalysisStep
    template_name = "pocs/fa_step_list.html"
    context_object_name = "steps"


class FAStepCreateView(AdminRequiredMixin, CreateView):
    model = FunctionalAnalysisStep
    form_class = FunctionalAnalysisStepForm
    template_name = "pocs/fa_step_form.html"
    success_url = reverse_lazy("pocs:fa_step_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = "Add step"
        return ctx

    def form_valid(self, form):
        last = FunctionalAnalysisStep.objects.aggregate(m=Max("order"))["m"]
        form.instance.order = (last or 0) + 1
        messages.success(self.request, "Step added.")
        return super().form_valid(form)


class FAStepUpdateView(AdminRequiredMixin, UpdateView):
    model = FunctionalAnalysisStep
    form_class = FunctionalAnalysisStepForm
    template_name = "pocs/fa_step_form.html"
    success_url = reverse_lazy("pocs:fa_step_list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["title"] = f"Edit step · {self.object.title}"
        return ctx


class FAStepDeleteView(AdminRequiredMixin, DeleteView):
    model = FunctionalAnalysisStep
    http_method_names = ["post"]
    success_url = reverse_lazy("pocs:fa_step_list")


# ---------------------------------------------------------------------------
# Role-based task views (Block G)
# ---------------------------------------------------------------------------
class TasksView(LoginRequiredMixin, View):
    """Tasks grouped by POC.

    Any POC member (lead, admin, or plain member) sees every task in the
    POCs they belong to, with the assignee shown, plus "pending" / "overdue"
    filters — this is read-only visibility, it does not change who may
    edit/execute a given task (see ``task.can_execute``). A user with no POC
    membership at all only sees tasks directly assigned to them.
    """

    template_name = "pocs/tasks.html"

    def get(self, request):
        from django.utils import timezone

        user = request.user
        today = timezone.localdate()
        qs = Task.objects.select_related("phase__poc", "assigned_to")

        lead_poc_ids = set(
            POCMembership.objects.filter(
                user=user, role_in_poc=POCMembership.Role.LEAD
            ).values_list("poc_id", flat=True)
        )
        if user.is_admin:
            manager = True
        else:
            # Any POC member can VIEW every task in the POCs they belong to
            # (read-only — this does not change who can edit/execute a task,
            # see task.can_execute/task.can_lead elsewhere); ``assigned_to`` is
            # kept as a fallback for a task assigned to someone outside the
            # POC's membership list.
            member_poc_ids = list(
                POCMembership.objects.filter(user=user).values_list("poc_id", flat=True)
            )
            qs = qs.filter(Q(phase__poc_id__in=member_poc_ids) | Q(assigned_to=user))
            manager = bool(member_poc_ids)
        qs = qs.distinct()

        flt = request.GET.get("f", "")
        if flt == "pending":
            qs = qs.exclude(status=Task.Status.COMPLETED)
        elif flt == "overdue":
            qs = qs.filter(due_date__lt=today).exclude(status=Task.Status.COMPLETED)

        # Admin-only: narrow down to a single assignee.
        assignee_filter = request.GET.get("u", "").strip()
        if user.is_admin and assignee_filter:
            qs = qs.filter(assigned_to_id=assignee_filter)

        # Grouped by POC; within each, by due date with completed tasks last.
        tasks = list(
            qs.select_related("phase").order_by(
                "phase__poc__name",
                Case(
                    When(status=Task.Status.COMPLETED, then=1),
                    default=0,
                    output_field=IntegerField(),
                ),
                F("due_date").asc(nulls_last=True),
                "id",
            )
        )
        for t in tasks:
            t.can_lead = user.is_admin or t.phase.poc_id in lead_poc_ids
            t.can_execute = t.can_lead or t.assigned_to_id == user.id
            t.allowed_statuses = task_allowed_statuses(t.status)

        # One shared date axis per POC (a task and its sub-task must never
        # read as the same kind of row — spec section 5); phases are then
        # sub-headers within that one Gantt so timelines stay comparable
        # across a POC's phases.
        groups, current = [], None
        for t in tasks:
            poc = t.phase.poc
            if current is None or current["poc"].id != poc.id:
                current = {"poc": poc, "tasks": []}
                groups.append(current)
            current["tasks"].append(t)

        # A POC with zero tasks anywhere never produced a group above, so its
        # "+ Add Task" button (below) was unreachable — the exact same bug
        # being fixed at the phase level, just one layer up. Add an empty
        # group for every POC this user can add a task to (lead, or any POC
        # if admin) that doesn't already have one, purely so that entry
        # point always exists — independent of the active quick filter,
        # since "no tasks yet" is precisely when adding the first one matters
        # most.
        seen_poc_ids = {g["poc"].id for g in groups}
        if user.is_admin:
            addable_poc_ids = set(POC.objects.values_list("id", flat=True))
        else:
            addable_poc_ids = lead_poc_ids
        missing_poc_ids = addable_poc_ids - seen_poc_ids
        if missing_poc_ids:
            for poc in POC.objects.filter(id__in=missing_poc_ids).order_by("name"):
                groups.append({"poc": poc, "tasks": []})
            groups.sort(key=lambda g: g["poc"].name)

        for group in groups:
            gantt, top_level_phases = build_phase_task_tree(group["tasks"], today)
            group["gantt"] = gantt
            group["phases"] = top_level_phases
            # POC-level "+ Add Task" (spec item 4): same edit-rights rule as
            # every per-folder "Add task" below, but not limited to phases
            # that already have a folder (i.e. already have >=1 task) — see
            # TaskCreateForPocView.
            group["can_add_task"] = user.is_admin or group["poc"].id in lead_poc_ids

        # "Add task" now lives only here (spec item 4 — tasks are removed from
        # the phase page); annotate every folder with whether this user may
        # add one, same edit-rights rule as everywhere else (can_edit).
        def _annotate_folder(entry):
            entry["can_add_task"] = user.is_admin or entry["phase"].poc_id in lead_poc_ids
            for child in entry["children"]:
                _annotate_folder(child)

        for group in groups:
            for entry in group["phases"]:
                _annotate_folder(entry)

        ctx = {
            "groups": groups,
            "manager": manager,
            "filter": flt,
            "total": len(tasks),
            "task_status_choices": Task.Status.choices,
        }
        if user.is_admin:
            ctx["users"] = User.objects.filter(is_active=True).order_by(
                "first_name", "last_name", "username"
            )
            ctx["assignee_filter"] = assignee_filter

        return render(request, self.template_name, ctx)


class TestsView(LoginRequiredMixin, View):
    """Tests grouped by POC — a quick view of what's still pending (spec 4a).

    Mirrors :class:`TasksView`: any POC member sees every test in the POCs
    they belong to (read-only, with the assignee shown). "Pending" filters
    to tests that aren't settled yet.
    """

    template_name = "pocs/tests_overview.html"

    def get(self, request):
        user = request.user
        qs = Test.objects.select_related("phase__poc", "assigned_to").prefetch_related(
            "validations"
        )

        if user.is_admin:
            manager = True
        else:
            # Any POC member can VIEW every test in the POCs they belong to
            # (read-only — see the comment in TasksView.get for the same fix).
            member_poc_ids = list(
                POCMembership.objects.filter(user=user).values_list("poc_id", flat=True)
            )
            qs = qs.filter(Q(phase__poc_id__in=member_poc_ids) | Q(assigned_to=user))
            manager = bool(member_poc_ids)
        qs = qs.distinct()

        assignee_filter = request.GET.get("u", "").strip()
        if user.is_admin and assignee_filter:
            qs = qs.filter(assigned_to_id=assignee_filter)

        # Stat tiles (spec item 7) reflect this user's/assignee's full scope,
        # independent of the "Pending"/"Awaiting" quick filter applied below —
        # otherwise the tiles would just mirror whatever filter is selected.
        scoped_tests = list(qs)
        stats = {
            "total": len(scoped_tests),
            "passed": sum(1 for t in scoped_tests if t.result in Test.PASSING_RESULTS),
            "pending": sum(
                1 for t in scoped_tests if t.execution_status not in Test.SETTLED_EXECUTION
            ),
            "awaiting": sum(1 for t in scoped_tests if t.pending_validation),
        }

        flt = request.GET.get("f", "")
        if flt == "pending":
            qs = qs.exclude(execution_status__in=Test.SETTLED_EXECUTION)
        elif flt == "awaiting":
            qs = qs.filter(validations__status=TestValidation.Decision.PENDING).distinct()

        tests = list(qs.order_by("phase__poc__name", "phase__order", "id"))
        groups, current = [], None
        for t in tests:
            poc = t.phase.poc
            if current is None or current["poc"].id != poc.id:
                current = {"poc": poc, "tests": [], "phases": []}
                groups.append(current)
            current["tests"].append(t)

        # Sub-group by Phase within each POC (spec item 6 — Gestalt grouping
        # by similarity, mirroring the recursive Phase grouping already used
        # for Tasks). Tests are ordered by phase__order above, so same-phase
        # runs are contiguous.
        for group in groups:
            phase_groups, current_phase = [], None
            for t in group["tests"]:
                if current_phase is None or current_phase["phase"].id != t.phase_id:
                    current_phase = {"phase": t.phase, "tests": []}
                    phase_groups.append(current_phase)
                current_phase["tests"].append(t)
            group["phases"] = phase_groups

        ctx = {
            "groups": groups,
            "manager": manager,
            "filter": flt,
            "total": len(tests),
            "stats": stats,
        }
        if user.is_admin:
            ctx["users"] = User.objects.filter(is_active=True).order_by(
                "first_name", "last_name", "username"
            )
            ctx["assignee_filter"] = assignee_filter
        return render(request, self.template_name, ctx)


@require_POST
def phase_set_status(request, phase_pk):
    """Click-to-set a phase's status (admin / editing lead).

    A manual override (useful for milestone phases without tasks). Phases that
    have tasks/tests still recompute from them when those change. ``Not
    applicable`` is intentionally excluded — it can only be reached via the
    dedicated Mark N/A flow, which requires a written reason.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_can_edit_phase(request.user, phase):
        raise PermissionDenied
    new_status = request.POST.get("status")
    descendants = []
    ancestors = []
    if new_status in dict(Phase.MANUAL_STATUS_CHOICES):
        # Apply the override to the whole subtree so sub-phases update live.
        phase.set_status_cascade(new_status)
        descendants = [
            {"phase": d, "editable": user_can_edit_phase(request.user, d)}
            for d in Phase.objects.filter(
                id__in=phase.descendant_ids(include_self=False)
            )
        ]
        # The cascade also bubbles a recalculation up to ancestors (a parent
        # can flip e.g. in_progress -> completed once every child is done) —
        # re-fetch them fresh so their status controls/dots stay in sync
        # without a page reload.
        ancestors = [
            {"phase": a, "editable": user_can_edit_phase(request.user, a)}
            for a in phase.ancestors()
        ]
    return render(
        request,
        "pocs/partials/phase_status_update.html",
        {
            "phase": phase,
            "descendants": descendants,
            "ancestors": ancestors,
            "editable": True,
            "phase_status_choices": Phase.MANUAL_STATUS_CHOICES,
            "progress": phase.poc.progress_percent(),
        },
    )


# ---------------------------------------------------------------------------
# Phase approval / locking (spec Fase 5)
# ---------------------------------------------------------------------------
@require_POST
def phase_approve(request, phase_pk):
    """TL sign-off: approve a phase (marks it completed and locks it).

    Allowed for the phase leader, the POC's leads, or an admin. The phase's own
    work must be finished (all tasks/tests settled, outcomes validated) first.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_can_validate_phase(request.user, phase):
        raise PermissionDenied

    if phase.is_approved:
        messages.info(request, "This phase is already approved.")
        return redirect("pocs:phase_detail", phase_pk=phase.pk)
    if phase.has_own_items and not phase.awaiting_approval:
        messages.error(
            request,
            "All tasks and tests must be finished (and their outcomes validated) "
            "before the phase can be approved.",
        )
        return redirect("pocs:phase_detail", phase_pk=phase.pk)

    phase.approved_at = timezone.now()
    phase.approved_by = request.user
    phase.save(update_fields=["approved_at", "approved_by", "updated_at"])
    phase.recalculate_status()
    record_audit(phase, "phase_approved", request.user)
    messages.success(request, f"Phase “{phase.name}” approved and locked.")
    return redirect("pocs:phase_detail", phase_pk=phase.pk)


@require_POST
def phase_unlock(request, phase_pk):
    """Unlock an approved phase for editing (phase leader / POC lead / admin)."""
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_can_validate_phase(request.user, phase):
        raise PermissionDenied
    if not phase.is_approved:
        return redirect("pocs:phase_detail", phase_pk=phase.pk)

    phase.approved_at = None
    phase.approved_by = None
    phase.save(update_fields=["approved_at", "approved_by", "updated_at"])
    phase.recalculate_status()
    record_audit(phase, "phase_unlocked", request.user)
    messages.success(request, f"Phase “{phase.name}” unlocked for editing.")
    return redirect("pocs:phase_detail", phase_pk=phase.pk)
