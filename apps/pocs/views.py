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
from django.db.models import Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
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
    user_can_edit_phase,
    user_can_execute_task,
    user_can_execute_test,
    user_can_lead_poc,
    user_is_poc_member,
)

from .forms import (
    BaseTaskForm,
    BaseTestForm,
    FunctionalAnalysisStepForm,
    MemberTestForm,
    PhaseForm,
    PhaseTemplateForm,
    POCForm,
    POCImportForm,
    TaskForm,
    TestExecutionForm,
    TestForm,
)
from .models import (
    POC,
    AuditLog,
    BaseTask,
    BaseTest,
    FunctionalAnalysisStep,
    Phase,
    PhaseTemplate,
    POCMembership,
    Task,
    Test,
)
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
        return view(request, *args, **kwargs)

    return wrapper


def _team_context(poc, request, q=""):
    """Build the context for the team-management panel partial."""
    memberships = poc.memberships.select_related("user").all()
    member_ids = list(memberships.values_list("user_id", flat=True))

    search_results = []
    q = (q or "").strip()
    if q:
        search_results = list(
            User.objects.filter(is_active=True)
            .exclude(id__in=member_ids)
            .filter(
                Q(username__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
            )[:10]
        )

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
    """Audit entries for a POC's Tasks and Tests, newest first.

    AuditLog uses a generic FK, so we filter by content type + the IDs of the
    POC's tasks/tests. Capped at ``limit`` to bound page weight.
    """
    task_ct = ContentType.objects.get_for_model(Task)
    test_ct = ContentType.objects.get_for_model(Test)
    task_ids = list(Task.objects.filter(phase__poc=poc).values_list("id", flat=True))
    test_ids = list(Test.objects.filter(phase__poc=poc).values_list("id", flat=True))
    return (
        AuditLog.objects.filter(
            Q(content_type=task_ct, object_id__in=task_ids)
            | Q(content_type=test_ct, object_id__in=test_ids)
        )
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
        ctx["phase_status_choices"] = Phase.Status.choices
        ctx["can_lead"] = can_lead
        # Audit log is visible to admins and POC leads only.
        if can_lead:
            ctx["audit_logs"] = _poc_audit_logs(poc)
        # Imported business details for the Overview tab (label, value pairs).
        ctx["poc_details"] = [
            ("Status", poc.external_status),
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
        # Reports tab: only phases (any level) that have a template attached.
        ctx["reportable_phases"] = poc.phases.exclude(report_template="").exclude(
            report_template__isnull=True
        )
        ctx["generated_reports"] = poc.generated_reports.select_related("phase")[:50]
        ctx["tabs"] = [
            ("overview", "Overview"),
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
    """Delete a phase. Admins and the POC's lead may delete any phase in the POC
    (deletion is POC-local — it never touches the global blueprint)."""

    model = Phase
    pk_url_kwarg = "phase_pk"
    http_method_names = ["post"]

    def form_valid(self, form):
        parent = self.object.parent
        name = self.object.name
        return_url = _phase_return_url(self.object)
        self.object.delete()
        if parent:
            parent.recalculate_status()
        messages.success(self.request, f"Phase “{name}” deleted.")
        return redirect(return_url)


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
        ctx["children"] = children
        ctx["is_leaf"] = not children
        ctx["can_edit"] = can_edit
        ctx["is_functional_analysis"] = phase.is_functional_analysis
        # Any POC member may add tests on a leaf phase (documenting tests they
        # performed); only editors (admin / editing lead) may add tasks.
        ctx["can_add_test"] = not children
        ctx["can_add_subphase"] = (
            can_edit
            and phase.can_have_children
            and not phase.tasks.exists()
            and not phase.tests.exists()
        )

        # Functional Analysis phases render a different view: the admin's step
        # template + a Markdown skeleton to copy and turn into a report.
        if phase.is_functional_analysis:
            steps = list(FunctionalAnalysisStep.objects.all())
            ctx["fa_steps"] = steps
            ctx["fa_markdown"] = _fa_skeleton(phase, steps)

        if not children and not phase.is_functional_analysis:
            tasks = list(phase.tasks.select_related("assigned_to"))
            for task in tasks:
                task.can_execute = can_edit or task.assigned_to_id == user.id
                task.allowed_statuses = task_allowed_statuses(task.status)
            tests = list(phase.tests.select_related("assigned_to"))
            for test in tests:
                test.can_execute = can_edit or test.assigned_to_id == user.id
            ctx["tasks"] = tasks
            ctx["tests"] = tests

        ctx["poc"] = phase.poc
        ctx["can_lead"] = can_edit  # task/test rows use can_lead for CRUD controls
        ctx["task_status_choices"] = Task.Status.choices
        ctx["test_verdict_choices"] = Test.Verdict.choices
        return ctx


class _PhaseEditCreateMixin(LoginRequiredMixin):
    """For creating Tasks/Tests under a phase (``phase_pk``): require edit rights
    and that the phase is a leaf (no sub-phases)."""

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.phase = get_object_or_404(Phase, pk=kwargs["phase_pk"])
        self.poc = self.phase.poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_edit_phase(request.user, self.phase):
            raise PermissionDenied
        if not self.phase.is_leaf:
            messages.error(
                request, "This phase has sub-phases; add tasks/tests there instead."
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
    """Create a task within a leaf phase (admin or editing lead)."""

    model = Task
    form_class = TaskForm
    template_name = "pocs/task_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["title"] = "Add task"
        return ctx

    def form_valid(self, form):
        form.instance.phase = self.phase
        self.object = form.save()
        self.phase.recalculate_status()
        messages.success(self.request, f"Task “{self.object.title}” created.")
        return redirect("pocs:phase_detail", phase_pk=self.phase.pk)


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


def _render_task_row(request, task):
    """Render a single task row partial (for HTMX swaps)."""
    can_edit = user_can_edit_phase(request.user, task.phase)
    task.can_execute = can_edit or task.assigned_to_id == request.user.id
    task.allowed_statuses = task_allowed_statuses(task.status)
    return render(
        request,
        "pocs/partials/task_row.html",
        {
            "task": task,
            "can_lead": can_edit,
            "task_status_choices": Task.Status.choices,
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
        task.mark_completed(request.user)
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
                task.mark_completed(request.user)
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
        if not self.phase.is_leaf:
            messages.error(
                request, "This phase has sub-phases; add tests there instead."
            )
            return redirect("pocs:phase_detail", phase_pk=self.phase.pk)
        self.is_manager = user_can_edit_phase(request.user, self.phase)
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        return TestForm if self.is_manager else MemberTestForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["poc"] = self.poc
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["phase"] = self.phase
        ctx["poc"] = self.poc
        ctx["title"] = "Add test"
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
    return render(
        request,
        "pocs/partials/test_row.html",
        {
            "test": test,
            "can_lead": can_edit,
            "test_verdict_choices": Test.Verdict.choices,
            "exec_form": form,
        },
    )


@require_POST
def test_execute(request, test_pk):
    """Inline execution: record actual_result, verdict and evidence (HTMX).

    Allowed for the assignee or a lead/admin. Stamps executed_by/at via
    ``Test.save``. The form enforces the evidence file-type whitelist.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    test = get_object_or_404(Test, pk=test_pk)
    if not user_can_execute_test(request.user, test):
        raise PermissionDenied

    form = TestExecutionForm(request.POST, request.FILES, instance=test)
    if not form.is_valid():
        return _render_test_row(request, test, form=form)

    test = form.save(commit=False)
    test.mark_executed(request.user)  # stamps executed_by/at and saves
    test.phase.recalculate_status()
    return _render_test_row(request, test)


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


class PhaseTemplateDeleteView(AdminRequiredMixin, DeleteView):
    model = PhaseTemplate
    template_name = "pocs/phase_template_confirm_delete.html"
    success_url = reverse_lazy("pocs:phase_template_list")

    def form_valid(self, form):
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


class BaseTaskDeleteView(AdminRequiredMixin, DeleteView):
    model = BaseTask
    http_method_names = ["post"]
    success_url = reverse_lazy("pocs:phase_template_list")


class BaseTestDeleteView(AdminRequiredMixin, DeleteView):
    model = BaseTest
    http_method_names = ["post"]
    success_url = reverse_lazy("pocs:phase_template_list")


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
# Functional Analysis template (admin) + skeleton
# ---------------------------------------------------------------------------
def _fa_skeleton(phase, steps):
    """Build the copy-ready Markdown skeleton from the FA steps."""
    lines = [f"# {phase.name}", ""]
    for i, s in enumerate(steps, start=1):
        lines += [f"## Step {i}: {s.title}", ""]
        if s.preconditions:
            lines += ["**Preconditions:**", "", s.preconditions, ""]
        if s.action:
            lines += ["**Action:**", "", s.action, ""]
        if s.expected_result:
            lines += ["**Expected result:**", "", s.expected_result, ""]
        if s.acceptance_criteria:
            lines += ["**Acceptance criteria:**", "", s.acceptance_criteria, ""]
        lines += ["**Actual result:**", "", "_…fill in…_", ""]
        lines += ["**Verdict:** PENDING", "", "---", ""]
    if not steps:
        lines += ["_No Functional Analysis steps defined yet._", ""]
    return "\n".join(lines)


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

    * Manager (admin / POC lead): every task in the POCs they can see, with the
      assignee shown, plus "pending" / "overdue" filters.
    * Team member: only the tasks assigned to them.
    """

    template_name = "pocs/tasks.html"

    def get(self, request):
        from django.utils import timezone

        user = request.user
        today = timezone.localdate()
        qs = Task.objects.select_related("phase__poc", "assigned_to")

        if user.is_admin:
            manager = True
        else:
            # Manager view for the POCs this user leads; for POCs where they are
            # only a member they see just the tasks assigned to them.
            lead_poc_ids = list(
                POCMembership.objects.filter(
                    user=user, role_in_poc=POCMembership.Role.LEAD
                ).values_list("poc_id", flat=True)
            )
            qs = qs.filter(Q(phase__poc_id__in=lead_poc_ids) | Q(assigned_to=user))
            manager = bool(lead_poc_ids)
        qs = qs.distinct()

        flt = request.GET.get("f", "")
        if flt == "pending":
            qs = qs.exclude(status=Task.Status.COMPLETED)
        elif flt == "overdue":
            qs = qs.filter(due_date__lt=today).exclude(status=Task.Status.COMPLETED)

        tasks = list(qs.order_by("phase__poc__name", "phase__order", "id"))
        groups, current = [], None
        for t in tasks:
            t.is_overdue = bool(
                t.due_date and t.due_date < today and t.status != Task.Status.COMPLETED
            )
            poc = t.phase.poc
            if current is None or current["poc"].id != poc.id:
                current = {"poc": poc, "tasks": []}
                groups.append(current)
            current["tasks"].append(t)

        return render(
            request,
            self.template_name,
            {
                "groups": groups,
                "manager": manager,
                "filter": flt,
                "total": len(tasks),
            },
        )


@require_POST
def phase_set_status(request, phase_pk):
    """Click-to-set a phase's status (admin / editing lead).

    A manual override (useful for milestone phases without tasks). Phases that
    have tasks/tests still recompute from them when those change.
    """
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    phase = get_object_or_404(Phase, pk=phase_pk)
    if not user_can_edit_phase(request.user, phase):
        raise PermissionDenied
    new_status = request.POST.get("status")
    descendants = []
    if new_status in dict(Phase.Status.choices):
        # Apply the override to the whole subtree so sub-phases update live.
        phase.set_status_cascade(new_status)
        descendants = [
            {"phase": d, "editable": user_can_edit_phase(request.user, d)}
            for d in Phase.objects.filter(
                id__in=phase.descendant_ids(include_self=False)
            )
        ]
    return render(
        request,
        "pocs/partials/phase_status_update.html",
        {
            "phase": phase,
            "descendants": descendants,
            "editable": True,
            "phase_status_choices": Phase.Status.choices,
            "progress": phase.poc.progress_percent(),
        },
    )
