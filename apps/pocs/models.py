"""
POC domain models.

Hierarchy:  POC → Phase → (Task | Test)

* **POC** groups everything; membership is controlled via **POCMembership**
  (per-POC lead/member roles, independent of the global account role).
* **Phase** orders work within a POC; its status can be auto-derived from its
  child Tasks and Tests (see :meth:`Phase.compute_status`).
* **Task** and **Test** are the executable units. Both carry "who/when"
  bookkeeping fields that are set atomically via :meth:`Task.mark_completed`
  / :meth:`Test.mark_executed` so views and signals don't have to repeat the
  logic.
* **AuditLog** is a generic activity record (wired to signals in Step 9).

Markdown fields store raw Markdown; rendering happens at the template layer
(markdown2) in later steps.
"""

import re
from datetime import timedelta

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

USER = settings.AUTH_USER_MODEL

# Test-code abbreviations by Test-phase type (used to build ``Test.test_code``,
# e.g. "Unit Testing" → UT-001). Keyed by a lowercase match on the phase name;
# see ``test_phase_abbreviation`` for the (looser) matching used when
# generating a code — a phase named e.g. "Unitary Testing" still resolves to UT.
TEST_PHASE_ABBREVIATIONS = {
    "unit testing": "UT",
    "integration testing": "IT",
    "system testing": "ST",
    "performing testing": "PT",
    "scalability testing": "SCAT",
    "stress testing": "STT",
    "cybersecurity testing": "CST",
}


def test_phase_abbreviation(phase_name):
    """Resolve a Test-phase name to its ``Test.test_code`` prefix.

    Tries an exact (case-insensitive) match against ``TEST_PHASE_ABBREVIATIONS``
    first, then a looser match on the phase's first word against each known
    abbreviation's first word (so a locally-renamed phase like "Unitary
    Testing" still resolves to "UT"). Falls back to the initials of the phase
    name's words (e.g. "Load Testing" → LT), or "TC" (Test Case) if that's empty.
    """
    name = (phase_name or "").strip().lower()
    if name in TEST_PHASE_ABBREVIATIONS:
        return TEST_PHASE_ABBREVIATIONS[name]
    words = name.split()
    first_word = words[0] if words else ""
    if first_word:
        for key, abbr in TEST_PHASE_ABBREVIATIONS.items():
            key_word = key.split()[0]
            if first_word.startswith(key_word) or key_word.startswith(first_word):
                return abbr
    initials = "".join(w[0] for w in words if w).upper()[:4]
    return initials or "TC"


class PhaseKind(models.TextChoices):
    """What kind of work a phase holds, chosen on the blueprint and inherited.

    * ``TEST`` — the classic structure: Tasks + Tests.
    * ``DOCUMENTATION`` — Tasks + free-form Markdown Documents (with images),
      turned into a report from a template.
    * ``FUNCTIONAL_ANALYSIS`` — Documents pre-seeded from the global Functional
      Analysis template (one section per step, with its guidance), each filled
      with Markdown + images and turned into a report.
    """

    TEST = "test", "Test"
    DOCUMENTATION = "documentation", "Documentation"
    FUNCTIONAL_ANALYSIS = "functional_analysis", "Functional Analysis"


# ---------------------------------------------------------------------------
# POC
# ---------------------------------------------------------------------------
def _board_cell_status(tests):
    """Aggregate a set of tests into one status-board cell state (spec 10a)."""
    if not tests:
        return "none"
    results = [t.result for t in tests]
    if any(r == "not_passed" for r in results):
        return "not_expected"
    if all(r in ("passed", "passed_with_comments") for r in results):
        return "as_expected"
    if any(
        t.execution_status in ("in_progress", "test_completed") for t in tests
    ) or any(results):
        return "in_progress"
    return "not_started"


class POC(models.Model):
    """A Proof of Concept project."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, help_text="Markdown supported.")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey(
        USER,
        on_delete=models.PROTECT,
        related_name="created_pocs",
    )
    members = models.ManyToManyField(
        USER,
        through="POCMembership",
        related_name="pocs",
        blank=True,
    )

    # -----------------------------------------------------------------------
    # Fields imported from the company M365 "PoC Follow-up" list.
    # (Title→name, Pilot Description→description, Execution dates→start/end.)
    # ``external_id`` is the source list item ID — used to upsert on re-import.
    # 'Item Type' and 'Path' are export remnants and intentionally not stored.
    # People columns are stored as plain text (source has names, not accounts).
    # -----------------------------------------------------------------------
    external_id = models.PositiveIntegerField(
        unique=True, null=True, blank=True, help_text="Source M365 list item ID."
    )
    external_status = models.CharField(max_length=50, blank=True)
    l2_wbs = models.CharField("L2 WBS", max_length=120, blank=True)
    initiative = models.CharField(max_length=200, blank=True)
    customer_segment = models.CharField(max_length=120, blank=True)
    customer = models.CharField(max_length=200, blank=True)
    leading_organization = models.CharField(max_length=200, blank=True)
    tendering_start = models.DateField(null=True, blank=True)
    tendering_finish = models.DateField(null=True, blank=True)
    execution_start = models.DateField(null=True, blank=True)
    execution_finish = models.DateField(null=True, blank=True)
    bfo_no = models.CharField("BFO No", max_length=120, blank=True)
    pilot_requestor = models.CharField(max_length=200, blank=True)
    opportunity_leader = models.CharField(max_length=200, blank=True)
    ecostruxure_lead = models.CharField(max_length=200, blank=True)
    pilot_tender_leader = models.CharField(max_length=200, blank=True)
    pilot_tender_tl = models.CharField("Pilot Tender TL", max_length=200, blank=True)
    pilot_pm = models.CharField("Pilot PM", max_length=200, blank=True)
    pilot_exec_tl = models.CharField("Pilot Exec TL", max_length=200, blank=True)
    integration_leader = models.CharField(max_length=200, blank=True)
    investment_type = models.CharField(max_length=120, blank=True)
    leadership = models.CharField(max_length=120, blank=True)
    finance_kpi = models.CharField("Finance KPI", max_length=60, blank=True)
    schedule_kpi = models.CharField("Schedule KPI", max_length=60, blank=True)
    region = models.CharField(max_length=120, blank=True)
    initiative_qua = models.CharField("InitiativeQUA", max_length=200, blank=True)
    initiative_qua_id = models.CharField("InitiativeQUA ID", max_length=60, blank=True)
    proposal_duration = models.PositiveIntegerField(
        null=True, blank=True, help_text="Months."
    )
    external_created = models.DateTimeField(null=True, blank=True)

    # Official closure (spec Fase 6). Once closed the POC is locked (only an
    # admin can revert). ``closure_date`` is the user-entered official date;
    # ``closed_at``/``closed_by`` record who sealed it and when.
    closure_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_pocs",
    )
    closure_conclusion = models.TextField(
        blank=True, help_text="Optional final conclusion / notes (Markdown)."
    )

    # Architecture diagram for the POC quick-view dashboard (spec Fase 10a).
    architecture_image = models.FileField(
        upload_to="poc_architecture/%Y/%m/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "gif", "webp"])],
        help_text="Architecture diagram shown on the POC dashboard (PNG/JPG/GIF/WEBP).",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "POC"
        verbose_name_plural = "POCs"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        wbs_changed = False
        if self.pk:
            old_wbs = POC.objects.filter(pk=self.pk).values_list("l2_wbs", flat=True).first()
            wbs_changed = old_wbs is not None and old_wbs != self.l2_wbs
        super().save(*args, **kwargs)
        if wbs_changed:
            _regenerate_poc_codes(self)

    @property
    def is_closed(self):
        return self.closed_at is not None

    def graph_status(self):
        """Colour band for the central node of the POC overview graph: green
        only while active AND every one of its Use Cases is green (fully
        validated); orange while active with Use Cases still pending, or for
        any non-active lifecycle status (draft, etc.)."""
        if self.status != self.Status.ACTIVE:
            return "orange"
        use_cases = list(self.use_cases.all())
        if use_cases and all(uc.graph_status() == "green" for uc in use_cases):
            return "green"
        return "orange"

    @property
    def all_phases_approved(self):
        """True when the POC has leaf work-phases and all of them are approved.

        Phases marked Not Applicable are excluded — they carry no work to approve.
        """
        leaves = self.phases.filter(children__isnull=True)
        work = [p for p in leaves if p.has_own_items and not p.is_na]
        return bool(work) and all(p.is_approved for p in work)

    def test_timeline(self):
        """Roadmap view: one row per Test-kind leaf phase, its tests plotted
        chronologically on a shared axis (executed tests at their actual
        execution date; not-yet-executed tests at their planned ``target_date``
        when set, else clustered toward the project's target finish date so the
        row still reads left-to-right). A row turns green once every one of its
        tests has passed/skipped; the whole board flags "Testing complete" once
        every row is green.
        """
        today = timezone.now().date()
        phases = list(
            self.phases.filter(kind=PhaseKind.TEST, children__isnull=True)
            .prefetch_related("tests")
        )
        all_tests = [t for p in phases for t in p.tests.all()]
        if not all_tests:
            return None

        executed_dates = [t.executed_at.date() for t in all_tests if t.executed_at]
        planned_dates = [
            t.target_date for t in all_tests if not t.executed_at and t.target_date
        ]
        target_finish = self.execution_finish
        dated = executed_dates + planned_dates
        candidates_end = [today] + dated
        if target_finish:
            candidates_end.append(target_finish)
        axis_start = min([today] + dated)
        axis_end = max(candidates_end) if target_finish or dated else today + timedelta(days=30)
        if axis_end <= axis_start:
            axis_end = axis_start + timedelta(days=30)
        span_days = (axis_end - axis_start).days or 1

        def pos_for(d):
            return max(0, min(100, round((d - axis_start).days / span_days * 100)))

        today_pos = pos_for(today)

        # Not-yet-executed tests with no target_date have no date to plot —
        # spread only those across the tail of the axis (today → end) in id
        # order so they don't overlap; a set target_date plots for real.
        undated_pending = sorted(
            (t for t in all_tests if not t.executed_at and not t.target_date),
            key=lambda t: t.id,
        )
        pending_pos = {}
        if undated_pending:
            tail_start = max(today_pos, 60)
            step = (100 - tail_start) / (len(undated_pending) + 1)
            for i, t in enumerate(undated_pending, start=1):
                pending_pos[t.id] = round(tail_start + step * i)

        def pos_of(t):
            if t.executed_at:
                return pos_for(t.executed_at.date())
            if t.target_date:
                return pos_for(t.target_date)
            return pending_pos[t.id]

        rows = []
        for phase in phases:
            tests = list(phase.tests.all())
            if not tests:
                continue
            markers = [
                {"test": t, "pos": pos_of(t), "status": _board_cell_status([t])}
                for t in tests
            ]
            rows.append({
                "phase": phase,
                "status": _board_cell_status(tests),
                "markers": sorted(markers, key=lambda m: m["pos"]),
            })
        if not rows:
            return None

        return {
            "axis_start": axis_start,
            "axis_end": axis_end,
            "today_pos": today_pos,
            "rows": rows,
            "all_complete": all(r["status"] == "as_expected" for r in rows),
        }

    # --- Progress metrics (consumed by the dashboard) ---------------------
    def progress_percent(self):
        """Overall completion = completed phases / total phases.

        Counted over the **leaf** phases (the actual work units; parent phases
        are groupings). A phase counts as done only when its status is
        ``completed`` — any other state (pending / in progress) is "in progress"
        and does not count. Returns 0 when there are no phases.

        Note: a leaf whose tasks/tests are all finished but not yet approved by
        the phase lead deliberately still counts as "in progress" here (Fase 5
        design) — a milestone/placeholder leaf with no tasks/tests reaches
        "completed" only via a manual status override or being marked Not
        Applicable, both of which already count it as done.
        """
        # A POC marked completed always reads 100% (no "completed but 0%").
        if self.status == self.Status.COMPLETED:
            return 100

        leaves = self.phases.filter(children__isnull=True)
        total = leaves.count()
        if not total:
            return 0
        done = leaves.filter(
            status__in=[Phase.Status.COMPLETED, Phase.Status.NOT_APPLICABLE]
        ).count()
        return round(done / total * 100)

    @property
    def phase_count(self):
        return self.phases.count()


class POCMembership(models.Model):
    """Join table linking a User to a POC with a per-POC role.

    A user can be a ``lead`` in one POC and a ``member`` in another regardless
    of their global account role.
    """

    class Role(models.TextChoices):
        LEAD = "lead", "Lead"
        MEMBER = "member", "Member"

    poc = models.ForeignKey(POC, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        USER, on_delete=models.CASCADE, related_name="poc_memberships"
    )
    role_in_poc = models.CharField(
        max_length=20, choices=Role.choices, default=Role.MEMBER
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "POC membership"
        # A user belongs to a given POC at most once.
        constraints = [
            models.UniqueConstraint(
                fields=["poc", "user"], name="unique_poc_membership"
            )
        ]

    def __str__(self):
        return f"{self.user} · {self.poc} ({self.get_role_in_poc_display()})"

    @property
    def is_lead(self):
        return self.role_in_poc == self.Role.LEAD


# ---------------------------------------------------------------------------
# Phase blueprint (global, admin-defined)
# ---------------------------------------------------------------------------
class PhaseTemplate(models.Model):
    """A node in the global, admin-defined phase hierarchy (max 3 levels).

    The whole forest is deep-copied into every new POC's ``Phase`` tree (see
    ``apps.pocs.services.apply_phase_templates``). Per node the admin sets a
    name, whether POC leads may modify it in their POC (``lead_editable``), an
    optional ``.docx`` report template, and base Tasks/Tests that become real
    instances on inheritance.
    """

    MAX_LEVEL = 3

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)
    lead_editable = models.BooleanField(
        default=False,
        help_text="POC leads may modify this node (its sub-phases and tasks/tests) in their POC.",
    )
    kind = models.CharField(
        max_length=20,
        choices=PhaseKind.choices,
        default=PhaseKind.TEST,
        help_text="Test (tasks+tests), Documentation (tasks+documents) or Functional Analysis.",
    )
    report_template = models.FileField(
        upload_to="report_templates/blueprint/%Y/%m/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["docx", "zip"])],
        help_text="Template file: a Word .docx (used to generate reports) or a "
        ".zip bundle of documents (download-only).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "phase template"

    def __str__(self):
        return self.name

    @property
    def level(self):
        depth, node = 1, self
        while node.parent_id:
            depth += 1
            node = node.parent
        return depth

    @property
    def is_reportable(self):
        return bool(self.report_template)

    @property
    def is_leaf(self):
        return not self.children.exists()

    @property
    def can_have_children(self):
        return self.level < self.MAX_LEVEL

    @property
    def is_functional_analysis(self):
        return self.kind == PhaseKind.FUNCTIONAL_ANALYSIS

    @property
    def is_documentation(self):
        return self.kind == PhaseKind.DOCUMENTATION

    @property
    def is_test(self):
        return self.kind == PhaseKind.TEST


class BaseTask(models.Model):
    """A base task on a blueprint node, instantiated as a real Task per POC."""

    phase_template = models.ForeignKey(
        PhaseTemplate, on_delete=models.CASCADE, related_name="base_tasks"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Markdown supported.")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class BaseTest(models.Model):
    """A base test on a blueprint node, instantiated as a real Test per POC."""

    phase_template = models.ForeignKey(
        PhaseTemplate, on_delete=models.CASCADE, related_name="base_tests"
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Markdown supported.")
    acceptance_criteria = models.TextField(blank=True, help_text="Markdown supported.")
    expected_result = models.TextField(blank=True, help_text="Markdown supported.")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class BasePhaseDocument(models.Model):
    """A base Markdown document on a blueprint node (Documentation phases).

    Instantiated as a real :class:`PhaseDocument` per POC, like base tasks/tests.
    """

    phase_template = models.ForeignKey(
        PhaseTemplate, on_delete=models.CASCADE, related_name="base_documents"
    )
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, help_text="Markdown supported.")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Team — reusable catalog for Phase.mark_external (spec item 4)
# ---------------------------------------------------------------------------
class Team(models.Model):
    """A named external team, shared across phases and POCs.

    Assigned to a Phase marked External (``Phase.mark_external``) — a simple
    reusable catalog, not scoped to any one POC.
    """

    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Phase
# ---------------------------------------------------------------------------
class Phase(models.Model):
    """A stage within a POC. Phases form a tree (max 3 levels): a phase with
    sub-phases delegates its Tasks/Tests to them; only leaf phases hold them."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        NOT_APPLICABLE = "not_applicable", "Not applicable"

    # Statuses a user may pick from the click-to-set dropdown. ``NOT_APPLICABLE``
    # is deliberately excluded — it's only reachable via its own dedicated
    # action (``mark_na()``), which requires a justification (see
    # ``phase_mark_na``). The External flag (``is_external``) is independent of
    # ``status`` and coexists with any of these three.
    MANUAL_STATUS_CHOICES = [
        (Status.PENDING, Status.PENDING.label),
        (Status.IN_PROGRESS, Status.IN_PROGRESS.label),
        (Status.COMPLETED, Status.COMPLETED.label),
    ]

    MAX_LEVEL = 3

    poc = models.ForeignKey(POC, on_delete=models.CASCADE, related_name="phases")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    # Provenance: the blueprint node this phase was inherited from (null for
    # lead-created sub-phases).
    source_template = models.ForeignKey(
        PhaseTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="phases",
    )
    lead_editable = models.BooleanField(default=False)
    kind = models.CharField(
        max_length=20, choices=PhaseKind.choices, default=PhaseKind.TEST
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0, help_text="Manual ordering.")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # Per-phase leader (TL). Introduced early (spec Fase 5) because test-result
    # validation (Fase 3b) is routed to this user. When unset, the POC's leads
    # act as validators as a fallback (see ``user_can_validate_test``).
    phase_leader = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_phases",
    )
    # TL sign-off (spec Fase 5). A phase with work is only "completed" once
    # approved; approval also locks the phase for editing until it's unlocked.
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_phases",
    )
    # Optional Word template (.docx) attached by an admin/lead. When present, a
    # phase report can be generated from this phase's tests (see apps.reports).
    report_template = models.FileField(
        upload_to="report_templates/phase/%Y/%m/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["docx", "zip"])],
        help_text="Template file: a Word .docx (used to generate reports from "
        "Markdown) or a .zip bundle of documents (download-only).",
    )
    # Not Applicable (spec: leads can't delete blueprint-mandated phases, only
    # mark them N/A with a justification — kept for the final report).
    na_reason = models.TextField(
        blank=True, help_text="Why this phase doesn't apply. Markdown supported."
    )
    na_at = models.DateTimeField(null=True, blank=True)
    na_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marked_na_phases",
    )
    # External: the phase applies, but is executed by one or more external
    # Team(s) rather than tracked with tasks/tests here. Independent of
    # ``status`` — a phase can be External AND pending/in_progress/completed at
    # the same time; the parent rollup uses the real ``status``, not this flag.
    is_external = models.BooleanField(default=False)
    teams = models.ManyToManyField("Team", blank=True, related_name="phases")
    external_at = models.DateTimeField(null=True, blank=True)
    external_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marked_external_phases",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.name} ({self.poc})"

    @property
    def is_reportable(self):
        """The phase has a template file attached (downloadable; .docx or .zip)."""
        return bool(self.report_template)

    @property
    def has_docx_template(self):
        """The attached template is a Word .docx usable for generation."""
        return bool(self.report_template) and self.report_template.name.lower().endswith(".docx")

    @property
    def is_functional_analysis(self):
        return self.kind == PhaseKind.FUNCTIONAL_ANALYSIS

    @property
    def is_documentation(self):
        return self.kind == PhaseKind.DOCUMENTATION

    @property
    def is_test(self):
        return self.kind == PhaseKind.TEST

    @property
    def level(self):
        depth, node = 1, self
        while node.parent_id:
            depth += 1
            node = node.parent
        return depth

    @property
    def is_leaf(self):
        return not self.children.exists()

    @property
    def can_have_children(self):
        return self.level < self.MAX_LEVEL

    # --- TL approval / locking (spec Fase 5) -----------------------------
    @property
    def is_approved(self):
        return self.approved_at is not None

    @property
    def is_locked(self):
        """Approved phases are locked for editing until unlocked by a lead."""
        return self.is_approved

    @property
    def has_own_items(self):
        return self.tasks.exists() or self.tests.exists()

    @property
    def is_na(self):
        """Marked Not Applicable (in lieu of deletion for a blueprint phase)."""
        return self.status == self.Status.NOT_APPLICABLE

    @property
    def can_be_marked_external(self):
        return not self.is_external

    @property
    def can_be_deleted(self):
        """Only lead-created phases (no blueprint provenance) may be deleted.

        Blueprint-sourced phases can only be marked Not Applicable, so a lead's
        POC never drifts structurally from the admin-defined blueprint.
        """
        return self.source_template_id is None

    @property
    def can_be_marked_na(self):
        return self.source_template_id is not None and not self.is_na

    def mark_na(self, user, reason):
        """Mark this phase Not Applicable with a mandatory justification."""
        self.status = self.Status.NOT_APPLICABLE
        self.na_reason = reason
        self.na_at = timezone.now()
        self.na_by = user
        # Clear any prior External marking — the two are mutually exclusive
        # on a given phase even though both exist as options (coexist).
        self.is_external = False
        self.external_at = None
        self.external_by = None
        self.save(
            update_fields=[
                "status", "na_reason", "na_at", "na_by",
                "is_external", "external_at", "external_by", "updated_at",
            ]
        )
        self.teams.clear()
        if self.parent_id:
            self.parent.recalculate_status(commit=True)

    def unmark_na(self):
        """Revert a Not Applicable phase back to a normally-derived status."""
        self.na_reason = ""
        self.na_at = None
        self.na_by = None
        self.status = self.Status.PENDING
        self.save(
            update_fields=["status", "na_reason", "na_at", "na_by", "updated_at"]
        )
        self.recalculate_status(commit=True)

    def mark_external(self, user, teams):
        """Mark this phase External — it applies, but is executed by one or
        more external Team(s) rather than tracked here. Independent of
        ``status``: the phase's progress (pending/in_progress/completed) is
        left untouched and remains editable (spec item 1)."""
        self.is_external = True
        self.external_at = timezone.now()
        self.external_by = user
        # Clear any prior Not Applicable marking — the two are mutually
        # exclusive on a given phase even though both exist as options.
        if self.is_na:
            self.status = self.Status.PENDING
        self.na_reason = ""
        self.na_at = None
        self.na_by = None
        self.save(
            update_fields=[
                "status", "is_external", "external_at", "external_by",
                "na_reason", "na_at", "na_by", "updated_at",
            ]
        )
        self.teams.set(teams)
        # Only bubble to the parent — this phase's own ``status`` (progress)
        # is left exactly as it was; recalculating it here would clobber a
        # manually-set value with the "no own work" PENDING fallback.
        if self.parent_id:
            self.parent.recalculate_status(commit=True)

    def unmark_external(self):
        """Revert an External phase — the flag only, ``status`` is untouched."""
        self.is_external = False
        self.external_at = None
        self.external_by = None
        self.save(
            update_fields=["is_external", "external_at", "external_by", "updated_at"]
        )
        self.teams.clear()
        if self.parent_id:
            self.parent.recalculate_status(commit=True)

    @property
    def awaiting_approval(self):
        """Has its own work, all of it settled, but not yet TL-approved."""
        if self.is_approved:
            return False
        tasks = list(self.tasks.all())
        tests = list(self.tests.all())
        if not tasks and not tests:
            return False
        return all(t.is_settled for t in tasks) and all(t.is_settled for t in tests)

    def descendant_ids(self, include_self=True):
        """IDs of this phase and all its descendants."""
        ids = [self.id] if include_self else []
        stack = list(self.children.all())
        while stack:
            node = stack.pop()
            ids.append(node.id)
            stack.extend(node.children.all())
        return ids

    def subtree_tests(self):
        return Test.objects.filter(phase_id__in=self.descendant_ids())

    @property
    def subtree_has_tasks(self):
        """True if this phase or any of its sub-phases (any depth) has a task.

        Used to surface a "has tasks" indicator on collapsed tree rows, so a
        lead browsing e.g. "Engineering" doesn't have to expand every
        sub-phase to discover one has tasks buried in it.
        """
        return Task.objects.filter(phase_id__in=self.descendant_ids()).exists()

    def ancestors(self):
        """This phase's parent chain, nearest first — freshly re-fetched from
        the DB (unlike ``self.parent``, which may be a stale in-memory copy
        from before a status cascade/bubble)."""
        chain = []
        parent_id = self.parent_id
        while parent_id:
            node = Phase.objects.get(pk=parent_id)
            chain.append(node)
            parent_id = node.parent_id
        return chain

    def _own_work_status(self):
        """Status derived from this phase's OWN Tasks/Tests only (not its
        sub-phases). ``None`` when it holds no work of its own.

        Completed only once every own Task is ``completed``, every own Test has
        a terminal execution status (test_completed/skipped) **and** this phase
        itself has been approved by its phase lead (spec Fase 5) — otherwise it
        stays ``in_progress`` ("awaiting approval", surfaced separately in the UI).
        """
        tasks = list(self.tasks.all())
        tests = list(self.tests.all())
        if not tasks and not tests:
            return None

        all_settled = all(t.is_settled for t in tasks) and all(
            t.is_settled for t in tests
        )
        if all_settled:
            return self.Status.COMPLETED if self.is_approved else self.Status.IN_PROGRESS

        any_started = any(t.is_started for t in tasks) or any(
            t.is_started for t in tests
        )
        return self.Status.IN_PROGRESS if any_started else self.Status.PENDING

    def compute_status(self):
        """Derive status from this phase's own work plus its sub-phases' current
        status — recursive, bottom-up aggregation.

        A sub-phase counts as done once it's ``completed`` or marked ``Not
        applicable``. So a phase becomes ``completed`` once every part of it
        is done: its own tasks/tests (if any) *and* every sub-phase —
        including a milestone sub-phase that was completed manually (via the
        click-to-set status control) rather than by finishing real
        tasks/tests. The External flag does NOT count as done by itself — an
        External sub-phase's real ``status`` (set manually, since it has no
        tasks/tests of its own to derive it from) is what's used here.
        """
        parts = []
        own = self._own_work_status()
        if own is not None:
            parts.append(own)
        parts.extend(child.status for child in self.children.all())

        if not parts:
            return self.Status.PENDING

        done_like = (self.Status.COMPLETED, self.Status.NOT_APPLICABLE)
        if all(p in done_like for p in parts):
            return self.Status.COMPLETED
        if all(p == self.Status.PENDING for p in parts):
            return self.Status.PENDING
        return self.Status.IN_PROGRESS

    def recalculate_status(self, commit=True):
        """Persist the derived status and bubble the recalculation to ancestors.

        A phase marked Not Applicable is frozen — it keeps that status until a
        lead/admin explicitly reverts it via ``unmark_na()`` — but ancestors
        are still recalculated (see ``compute_status``, which counts it as
        done). The External flag does NOT freeze status: an External phase's
        progress is real and independently editable (spec item 1).
        """
        if self.is_na:
            if self.parent_id and commit:
                self.parent.recalculate_status(commit=True)
            return self.status

        new_status = self.compute_status()
        if new_status != self.status:
            self.status = new_status
            if commit:
                self.save(update_fields=["status", "updated_at"])
        if self.parent_id and commit:
            self.parent.recalculate_status(commit=True)
        return new_status

    def set_status_cascade(self, status):
        """Manually set this phase's status and apply it to the whole subtree.

        Used by the click-to-set override: marking a parent phase (e.g. as
        ``completed``) flows the same status down to every sub-phase at once,
        then bubbles a recalculation up to ancestors.
        """
        ids = self.descendant_ids()  # includes self
        Phase.objects.filter(id__in=ids).update(
            status=status, updated_at=timezone.now()
        )
        self.status = status
        if self.parent_id:
            self.parent.recalculate_status(commit=True)

    # --- Per-phase metrics ------------------------------------------------
    def progress_percent(self):
        """Derived completion (``completed`` ⇒ 100%).

        A **parent** phase follows its direct sub-phases: it is the average of
        their progress, so 2 of 4 completed sub-phases reads as 50% (each
        sub-phase weighs the same regardless of how many items it holds).
        A **leaf** phase is the ratio of its settled tasks/tests. A phase marked
        Not Applicable also reads 100% — there's nothing left to track there.
        An External phase uses its real ``status`` like any other leaf/parent
        (spec item 1) — it is NOT automatically 100%.
        """
        if self.status in (self.Status.COMPLETED, self.Status.NOT_APPLICABLE):
            return 100
        children = list(self.children.all())
        if children:
            return round(sum(c.progress_percent() for c in children) / len(children))
        # Leaf phase: ratio of settled tasks/tests it holds directly.
        task_total = self.tasks.count()
        test_total = self.tests.count()
        total = task_total + test_total
        if not total:
            return 0
        done = self.tasks.filter(
            status=Task.Status.COMPLETED
        ).count() + self.tests.filter(
            execution_status__in=Test.SETTLED_EXECUTION
        ).count()
        return round(done / total * 100)

    def task_completion_percent(self):
        total = self.tasks.count()
        if not total:
            return 0
        done = self.tasks.filter(status=Task.Status.COMPLETED).count()
        return round(done / total * 100)

    def test_pass_rate_percent(self):
        """Pass rate over executed (terminal) tests."""
        executed = self.tests.filter(
            execution_status__in=Test.SETTLED_EXECUTION
        ).count()
        if not executed:
            return 0
        passed = self.tests.filter(result__in=Test.PASSING_RESULTS).count()
        return round(passed / executed * 100)

    def own_task_summary(self):
        """Task count/completed/date-range for THIS phase's own tasks (not
        sub-phases) — lets the Phases tab card show it at a glance, without
        opening the phase's own page (spec section 5)."""
        tasks = list(self.tasks.all())
        dates = [d for t in tasks for d in (t.start_date, t.due_date) if d]
        return {
            "total": len(tasks),
            "completed": sum(1 for t in tasks if t.status == Task.Status.COMPLETED),
            "start": min(dates) if dates else None,
            "end": max(dates) if dates else None,
        }


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
class Task(models.Model):
    """A unit of work within a Phase, optionally assigned to a team member."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        BLOCKED = "blocked", "Blocked"

    phase = models.ForeignKey(Phase, on_delete=models.CASCADE, related_name="tasks")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="subtasks",
        help_text="Optional — makes this a sub-task needed to complete the parent.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Markdown supported.")
    assigned_to = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tasks",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    start_date = models.DateField(
        null=True, blank=True, help_text="Planned start — shown as the Gantt bar's left edge."
    )
    due_date = models.DateField(null=True, blank=True)
    notes = models.TextField(
        blank=True, help_text="Markdown — filled by the team member on completion."
    )

    # Auto-set via mark_completed(); see save() for timestamp housekeeping.
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_tasks",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    requirements = models.ManyToManyField(
        "Requirement",
        related_name="tasks",
        blank=True,
        help_text="Requirement(s) this task implements.",
    )
    use_cases = models.ManyToManyField(
        "UseCase",
        related_name="tasks",
        blank=True,
        help_text="Use case(s) this task implements.",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.title

    @property
    def is_settled(self):
        """Counts as done for phase-completion/progress purposes."""
        return self.status == self.Status.COMPLETED

    @property
    def is_started(self):
        return self.status in {
            self.Status.IN_PROGRESS,
            self.Status.COMPLETED,
            self.Status.BLOCKED,
        }

    @property
    def has_incomplete_subtasks(self):
        """True while any direct sub-task isn't completed yet — blocks marking
        this (parent) task completed, since the sub-tasks are the work needed
        to get there."""
        return self.subtasks.exclude(status=self.Status.COMPLETED).exists()

    @property
    def subtask_progress_percent(self):
        total = self.subtasks.count()
        if not total:
            return None
        done = self.subtasks.filter(status=self.Status.COMPLETED).count()
        return round(done / total * 100)

    def mark_completed(self, user, notes=None):
        """Mark complete and stamp who/when. Saves the instance.

        Raises ``ValueError`` if a direct sub-task is still incomplete — those
        are the prerequisite work, so the parent can't close before them.
        """
        if self.has_incomplete_subtasks:
            raise ValueError(
                "Complete all sub-tasks before completing this task."
            )
        self.status = self.Status.COMPLETED
        self.completed_by = user
        self.completed_at = timezone.now()
        if notes is not None:
            self.notes = notes
        self.save()

    def save(self, *args, **kwargs):
        # Keep the completion timestamp consistent with the status, so callers
        # that change ``status`` directly still behave sensibly.
        if self.status == self.Status.COMPLETED and self.completed_at is None:
            self.completed_at = timezone.now()
        elif self.status != self.Status.COMPLETED:
            self.completed_at = None
            self.completed_by = None
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
class Test(models.Model):
    """A verification within a Phase, executed by a team member."""

    class ExecutionStatus(models.TextChoices):
        NOT_TESTED = "not_tested", "Not tested"
        IN_PROGRESS = "in_progress", "In progress"
        TEST_COMPLETED = "test_completed", "Test completed"
        SKIPPED = "skipped", "Skipped"

    class Result(models.TextChoices):
        PASSED = "passed", "Passed"
        NOT_PASSED = "not_passed", "Not Passed"
        PASSED_WITH_COMMENTS = "passed_with_comments", "Passed with comments"

    # Execution statuses that count as "terminal" for phase completion & metrics.
    SETTLED_EXECUTION = (ExecutionStatus.TEST_COMPLETED, ExecutionStatus.SKIPPED)
    # Results that count as a pass for the pass-rate metric.
    PASSING_RESULTS = (Result.PASSED, Result.PASSED_WITH_COMMENTS)
    # Results that make the notes field mandatory (see ``clean``/forms).
    RESULTS_REQUIRING_NOTES = (Result.NOT_PASSED, Result.PASSED_WITH_COMMENTS)

    phase = models.ForeignKey(Phase, on_delete=models.CASCADE, related_name="tests")
    comments = GenericRelation("pocs.Comment")
    # Manual ordering within the phase (spec item 10) — also the execution
    # order. Reordering (see the "move up/down" controls, ``test_move``) also
    # renumbers ``test_code`` to match, so the ST-NNN sequence always tracks
    # display/execution order rather than creation order.
    order = models.PositiveIntegerField(default=0, help_text="Manual ordering; also the execution order.")
    # Auto-generated on save, formatted {PHASE_ABBR}-{NNN} (e.g. UT-001, IT-002)
    # — see ``test_phase_abbreviation``/``_generate_test_code``. Must be unique
    # within the owning POC — that rule can't be expressed as a DB constraint
    # (it spans the Test→phase→poc relation), so it is enforced in ``clean()``.
    test_code = models.CharField(
        max_length=50,
        blank=True,
        db_index=True,
        help_text="Auto-generated, e.g. UT-001. Unique within the POC.",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Markdown supported.")
    acceptance_criteria = models.TextField(blank=True, help_text="Markdown supported.")
    expected_result = models.TextField(blank=True, help_text="Markdown supported.")
    actual_result = models.TextField(
        blank=True, help_text="Notes — filled by the team member."
    )
    # Execution and result are two separate concerns (spec Fase 3a):
    #  * execution_status — how far the run got.
    #  * result — the outcome, only meaningful once the run is completed.
    execution_status = models.CharField(
        max_length=20,
        choices=ExecutionStatus.choices,
        default=ExecutionStatus.NOT_TESTED,
    )
    result = models.CharField(
        max_length=25,
        choices=Result.choices,
        blank=True,
        help_text="Outcome once the test is completed (blank until then).",
    )
    # Requirements of the same POC this test verifies (spec Fase 3d).
    requirements = models.ManyToManyField(
        "Requirement", related_name="tests", blank=True
    )
    assigned_to = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tests",
    )
    target_date = models.DateField(
        null=True,
        blank=True,
        help_text="Planned execution date — drives the testing roadmap timeline "
        "before the test has an actual execution date.",
    )
    evidence_url = models.URLField(
        null=True, blank=True, help_text="Alternative to an uploaded file."
    )

    # Auto-set via mark_executed(); see save() for timestamp housekeeping.
    executed_at = models.DateTimeField(null=True, blank=True)
    executed_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="executed_tests",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Sorted by test_code (e.g. ST-001, ST-002, …) rather than the
        # ``order``/``id`` fields — the zero-padded numeric suffix sorts
        # correctly as plain text, and reordering (test_reorder) always keeps
        # test_code in sync with the desired position, so this guarantees the
        # visible list always reads ST-001, ST-002, … regardless of any
        # order/id drift in older data (spec item 10 follow-up).
        ordering = ["test_code"]

    def __str__(self):
        return self.title

    @property
    def poc(self):
        return self.phase.poc

    @property
    def has_open_comment(self):
        """True while an un-Acked review comment is pending on this test."""
        return self.comments.filter(status=Comment.Status.OPEN).exists()

    @property
    def is_settled(self):
        """Terminal execution status (test_completed / skipped)."""
        return self.execution_status in self.SETTLED_EXECUTION

    @property
    def is_started(self):
        return self.execution_status != self.ExecutionStatus.NOT_TESTED

    def graph_status(self):
        """Colour band for the POC overview graph: gray (not tested/in
        progress), green (passed), orange (skipped), red (not passed) —
        this is what cascades up to colour its linked Requirement."""
        if self.result in self.PASSING_RESULTS:
            return "green"
        if self.result == self.Result.NOT_PASSED:
            return "red"
        if self.execution_status == self.ExecutionStatus.SKIPPED:
            return "orange"
        return "gray"

    @property
    def needs_validation(self):
        """A finished outcome requires a phase lead's validation (spec 3b).

        Only a terminal result (passed / not passed / passed with comments) or a
        ``skipped`` execution triggers approval; intermediate progress does not.
        """
        return bool(self.result) or (
            self.execution_status == self.ExecutionStatus.SKIPPED
        )

    @property
    def pending_validation(self):
        """The open (undecided) validation request for this test, if any."""
        return self.validations.filter(
            status=TestValidation.Decision.PENDING
        ).first()

    @property
    def display_execution_status(self):
        """Execution status for the UI: the pending proposal's if one exists,
        else the persisted value — so a submitted-but-unapproved outcome
        doesn't misleadingly look untouched."""
        pv = self.pending_validation
        return pv.execution_status if pv else self.execution_status

    @property
    def display_execution_status_label(self):
        return self.ExecutionStatus(self.display_execution_status).label

    @property
    def display_result(self):
        """Result for the UI — see ``display_execution_status``."""
        pv = self.pending_validation
        return pv.result if pv else self.result

    @property
    def display_result_label(self):
        return self.Result(self.display_result).label if self.display_result else ""

    @property
    def display_actual_result(self):
        """Notes for the UI — see ``display_execution_status``."""
        pv = self.pending_validation
        return pv.actual_result if pv else self.actual_result

    @property
    def display_evidence_files(self):
        """Evidence for the UI: the pending proposal's own uploads while one is
        awaiting approval (they aren't attached to the Test yet), else the
        Test's own."""
        pv = self.pending_validation
        return pv.evidence_files.all() if pv else self.evidence_files.all()

    @property
    def display_evidence_url(self):
        pv = self.pending_validation
        return pv.evidence_url if pv else self.evidence_url

    def clean(self):
        """Model-level validation.

        * ``test_code`` uniqueness within the owning POC (can't be a DB
          constraint — it spans Test→phase→poc).
        * mandatory notes when the result is Not Passed / Passed with comments,
          or when the execution is Skipped (spec 3a).
        """
        super().clean()
        if self.test_code and self.phase_id:
            clash = (
                Test.objects.filter(
                    phase__poc_id=self.phase.poc_id, test_code=self.test_code
                )
                .exclude(pk=self.pk)
                .exists()
            )
            if clash:
                raise ValidationError(
                    {"test_code": "This test code is already used in this POC."}
                )
        if (
            self.result in self.RESULTS_REQUIRING_NOTES
            or self.execution_status == self.ExecutionStatus.SKIPPED
        ) and not (self.actual_result or "").strip():
            raise ValidationError(
                {"actual_result": "Notes are required for this outcome."}
            )

    def mark_executed(self, user, execution_status=None, result=None, actual_result=None):
        """Record an execution outcome and stamp who/when. Saves the instance."""
        if execution_status is not None:
            self.execution_status = execution_status
        if result is not None:
            self.result = result
        if actual_result is not None:
            self.actual_result = actual_result
        self.executed_by = user
        self.executed_at = timezone.now()
        self.save()

    def _generate_test_code(self):
        abbr = test_phase_abbreviation(self.phase.name)
        prefix = f"{abbr}-"
        poc_id = self.phase.poc_id
        n = (
            Test.objects.filter(phase__poc_id=poc_id, test_code__startswith=prefix).count()
            + 1
        )
        code = f"{prefix}{n:03d}"
        while (
            Test.objects.filter(phase__poc_id=poc_id, test_code=code)
            .exclude(pk=self.pk)
            .exists()
        ):
            n += 1
            code = f"{prefix}{n:03d}"
        return code

    def save(self, *args, **kwargs):
        # Auto-assign a code on first save — linked to the POC (unique within
        # it) and to the test-phase type (its abbreviation), e.g. UT-001.
        if not self.test_code and self.phase_id:
            self.test_code = self._generate_test_code()
        # Keep the execution timestamp consistent with the execution status, and
        # clear the result once the test is back to "not tested".
        if self.execution_status != self.ExecutionStatus.NOT_TESTED:
            if self.executed_at is None:
                self.executed_at = timezone.now()
        else:
            self.executed_at = None
            self.executed_by = None
            self.result = ""
        super().save(*args, **kwargs)

# ---------------------------------------------------------------------------
# TestValidation — proposed test outcome awaiting a phase lead's approval (3b)
# ---------------------------------------------------------------------------
class TestValidation(models.Model):
    """A proposed test outcome held until a phase lead validates it.

    When an executor records a terminal outcome (a result, or a ``skipped``
    execution), the change is NOT applied to the Test directly. Instead the
    proposed execution status / result / notes / evidence are stored here as a
    PENDING request. A phase lead (or admin) then approves — which copies the
    proposal onto the Test — or rejects it (the Test stays unchanged).
    """

    class Decision(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    test = models.ForeignKey(
        Test, on_delete=models.CASCADE, related_name="validations"
    )

    # The proposed outcome (mirrors the Test execution fields).
    execution_status = models.CharField(
        max_length=20, choices=Test.ExecutionStatus.choices
    )
    result = models.CharField(max_length=25, choices=Test.Result.choices, blank=True)
    actual_result = models.TextField(blank=True)
    evidence_url = models.URLField(null=True, blank=True)

    submitted_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_test_validations",
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=10, choices=Decision.choices, default=Decision.PENDING
    )
    decided_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decided_test_validations",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"Validation of {self.test} ({self.get_status_display()})"

    def apply_to_test(self, decided_by):
        """Approve: copy the proposal onto the Test and stamp the decision."""
        test = self.test
        test.execution_status = self.execution_status
        test.result = self.result
        test.actual_result = self.actual_result
        if self.evidence_url:
            test.evidence_url = self.evidence_url
        test.mark_executed(self.submitted_by or decided_by)
        # Re-parent the proposal's evidence files onto the test — no copying.
        self.evidence_files.update(test=test, validation=None)
        self.status = self.Decision.APPROVED
        self.decided_by = decided_by
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "decided_by", "decided_at"])
        test.phase.recalculate_status()

    def reject(self, decided_by, comment=""):
        """Reject: leave the Test unchanged, record who/when/why."""
        for f in self.evidence_files.all():
            f.file.delete(save=False)
            f.delete()
        self.status = self.Decision.REJECTED
        self.decided_by = decided_by
        self.decided_at = timezone.now()
        self.decision_comment = comment
        self.save(
            update_fields=["status", "decided_by", "decided_at", "decision_comment"]
        )


EVIDENCE_EXTENSIONS = [
    "jpg", "jpeg", "png", "gif", "svg", "pdf", "txt", "log", "xlsx", "docx"
]
EVIDENCE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "svg"}


class EvidenceFile(models.Model):
    """A file attached as evidence for a test run (many per test).

    Attached either to a ``Test`` directly (applied outcomes) or to a pending
    ``TestValidation`` (held proposals) — exactly one of the two is set at a
    time. On approval the validation's files are re-parented onto the Test
    (``TestValidation.apply_to_test``); on rejection they're deleted.
    """

    test = models.ForeignKey(
        Test, on_delete=models.CASCADE, null=True, blank=True,
        related_name="evidence_files",
    )
    validation = models.ForeignKey(
        TestValidation, on_delete=models.CASCADE, null=True, blank=True,
        related_name="evidence_files",
    )
    file = models.FileField(
        upload_to="evidence/%Y/%m/",
        validators=[FileExtensionValidator(EVIDENCE_EXTENSIONS)],
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="uploaded_evidence",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.file.name.rsplit("/", 1)[-1]

    @property
    def is_image(self):
        ext = self.file.name.rsplit(".", 1)[-1].lower() if "." in self.file.name else ""
        return ext in EVIDENCE_IMAGE_EXTENSIONS


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------
class AuditLog(models.Model):
    """Generic activity record for any audited object (Task/Test/…).

    Populated via Django signals in Step 9. ``details`` stores a small
    before/after JSON payload.
    """

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    # Direct link to the owning POC so an entry remains discoverable even after
    # its target object is deleted (the generic FK can't be queried then). Null
    # for entries not tied to a POC (e.g. custom reports).
    poc = models.ForeignKey(
        POC,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="audit_logs",
    )

    action = models.CharField(
        max_length=50,
        help_text='e.g. "status_changed", "result_added", "file_uploaded".',
    )
    actor = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.action} · {self.content_type} #{self.object_id}"

    @property
    def action_label(self):
        """Human-readable action phrase for display, e.g. 'status changed on'."""
        return self.action.replace("_", " ") + " on"

    @property
    def target_label(self):
        """Friendly noun for the audited model (instead of the raw model name)."""
        return {
            "task": "task",
            "test": "test",
            "phasedocument": "document",
            "phaseimage": "image",
            "generatedreport": "report",
            "requirement": "requirement",
            "usecase": "use case",
            "comment": "comment",
        }.get(self.content_type.model, self.content_type.model)


# ---------------------------------------------------------------------------
# Functional Analysis template (global, admin-defined strict structure)
# ---------------------------------------------------------------------------
class FunctionalAnalysisStep(models.Model):
    """An ordered step of the global Functional Analysis template.

    Each step is just a ``title`` plus a ``description`` (guidance). A Functional
    Analysis phase seeds one :class:`PhaseDocument` per step (title locked, the
    description shown as help); the user fills each section with Markdown and
    images and turns the whole set into a report.

    ``section_kind`` lets the admin wire a step to the POC's own Use Cases or
    Requirements registry instead of free text: the seeded section's content is
    then kept in sync with that data (see ``ensure_fa_documents``) and the POC
    lead can only preview it, not edit it.
    """

    class SectionKind(models.TextChoices):
        USE_CASES = "use_cases", "Use Cases"
        REQUIREMENTS = "requirements", "Requirements"
        OTHER = "other", "Other"

    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)
    description = models.TextField(
        blank=True, help_text="Guidance shown to the user. Markdown supported."
    )
    section_kind = models.CharField(
        max_length=20, choices=SectionKind.choices, default=SectionKind.OTHER,
        help_text="Use Cases/Requirements sections are auto-filled from the POC's "
                   "registry and locked for editing; Other is free Markdown.",
    )

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "functional analysis step"

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# Phase documents & images (Documentation + Functional Analysis phases)
# ---------------------------------------------------------------------------
class PhaseDocument(models.Model):
    """A Markdown section within a Documentation / Functional Analysis phase.

    * Documentation phases: free-form documents the user creates/renames/deletes.
    * Functional Analysis phases: one document per ``FunctionalAnalysisStep``,
      identified by ``source_fa_step`` — its title is locked to the step and the
      step's description is shown as guidance; the user only edits ``content``.

    Images uploaded to the phase (see :class:`PhaseImage`) can be referenced from
    ``content`` and are embedded into the generated report.
    """

    phase = models.ForeignKey(
        Phase, on_delete=models.CASCADE, related_name="documents"
    )
    source_fa_step = models.ForeignKey(
        FunctionalAnalysisStep,
        # CASCADE (not SET_NULL): a POC's Functional Analysis structure must
        # always mirror the admin's current template — if a step is removed, its
        # seeded section disappears everywhere too, instead of surviving as an
        # orphaned "Other" document that duplicates a later, differently-shaped
        # section seeded for a replacement step.
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="phase_documents",
    )
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, help_text="Markdown supported.")
    order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title

    @property
    def is_fa_section(self):
        """True for a section seeded from a Functional Analysis step (locked title)."""
        return self.source_fa_step_id is not None

    @property
    def guidance(self):
        """Help text for FA sections (the step's description); empty otherwise."""
        return self.source_fa_step.description if self.source_fa_step_id else ""

    @property
    def section_kind(self):
        """The step's ``section_kind`` for an FA section; ``OTHER`` otherwise."""
        if self.source_fa_step_id:
            return self.source_fa_step.section_kind
        return FunctionalAnalysisStep.SectionKind.OTHER

    @property
    def is_locked_section(self):
        """True for a Use Cases/Requirements section — content is auto-filled and
        read-only for the POC lead (see ``ensure_fa_documents``)."""
        return self.section_kind != FunctionalAnalysisStep.SectionKind.OTHER


class PhaseImage(models.Model):
    """An image uploaded to a phase, referenced from a document's Markdown.

    The phase detail page offers a copy-ready ``![caption](url)`` snippet; the
    report generator resolves those URLs back to the stored file and embeds it.
    """

    phase = models.ForeignKey(Phase, on_delete=models.CASCADE, related_name="images")
    image = models.FileField(
        upload_to="phase_images/%Y/%m/",
        validators=[
            FileExtensionValidator(["png", "jpg", "jpeg", "gif", "webp", "svg"])
        ],
        help_text="PNG/JPG/GIF/WEBP/SVG.",
    )
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="phase_images",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.caption or self.image.name.rsplit("/", 1)[-1]

    @property
    def markdown_snippet(self):
        return f"![{self.caption}]({self.image.url})"


class POCImage(models.Model):
    """An image uploaded to a POC's official-closure conclusion (same shape as
    ``PhaseImage``, but scoped to the whole POC rather than a phase)."""

    poc = models.ForeignKey(POC, on_delete=models.CASCADE, related_name="images")
    image = models.FileField(
        upload_to="poc_images/%Y/%m/",
        validators=[
            FileExtensionValidator(["png", "jpg", "jpeg", "gif", "webp", "svg"])
        ],
        help_text="PNG/JPG/GIF/WEBP/SVG.",
    )
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="poc_images",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.caption or self.image.name.rsplit("/", 1)[-1]

    @property
    def markdown_snippet(self):
        return f"![{self.caption}]({self.image.url})"


# ---------------------------------------------------------------------------
# Requirements & Use Cases (per-POC registry)
# ---------------------------------------------------------------------------
def _poc_code_prefix(poc):
    """Human-readable POC prefix for auto-generated codes: POC-{l2_wbs|id}."""
    token = (poc.l2_wbs or "").strip().replace(" ", "") or str(poc.pk)
    return f"POC-{token}"


def _regenerate_poc_codes(poc):
    """Re-derive every Requirement/UseCase code of ``poc``.

    Codes embed the POC's L2 WBS as their prefix (falling back to the POC id
    when WBS is blank). When the WBS is set or changed after requirements/use
    cases already exist, their codes are stale — rebuild them all (in creation
    order, so the numeric suffix stays stable) instead of leaving the old
    id-based prefix in place.
    """
    prefix = _poc_code_prefix(poc)
    for n, req in enumerate(poc.requirements.order_by("id"), start=1):
        cat = Requirement.CATEGORY_ABBR.get(req.req_category, "XX")
        op = Requirement.OPERATION_ABBR.get(req.req_operation, "XX")
        new_code = f"{prefix}-{cat}-{op}-{n:03d}"
        if new_code != req.code:
            req.code = new_code
            req.save(update_fields=["code"])
    for n, uc in enumerate(poc.use_cases.order_by("id"), start=1):
        new_code = f"{prefix}-UC{n:03d}"
        if new_code != uc.code:
            uc.code = new_code
            uc.save(update_fields=["code"])


class Requirement(models.Model):
    """A formal requirement of a POC.

    Identified by an auto-generated ``code`` (``POC-{l2_wbs}-{CAT}-{OP}-{NNN}``);
    the user never types it. Requirements are a plain list — they carry NO manual
    approval; a requirement is *validated* implicitly once the tests linked to it
    all pass/skip (see :meth:`is_validated`).
    """

    # Abbreviations used to build the code (spec: "depende de la categoría").
    CATEGORY_ABBR = {
        "normal_operation": "NO",
        "maintenance_mode": "MM",
    }
    OPERATION_ABBR = {
        "navigation": "NAV",
        "cybersecurity": "CS",
        "scalability": "SC",
        "iam_management": "IAM",
        "control_operation": "CO",
        "acquire_visualize": "AV",
        "ui_ux_feature": "UX",
    }

    class Gravity(models.TextChoices):
        IMPOSES_MVP = "imposes_mvp", "Imposes (MVP)"
        OPTIONALLY = "optionally", "Optionally"
        PREFERRED = "preferred", "Preferred but not required"

    class Operation(models.TextChoices):
        NAVIGATION = "navigation", "Navigation"
        CYBERSECURITY = "cybersecurity", "Cybersecurity"
        SCALABILITY = "scalability", "Scalability"
        IAM_MANAGEMENT = "iam_management", "IAM Management"
        CONTROL_OPERATION = "control_operation", "Control & Operation"
        ACQUIRE_VISUALIZE = "acquire_visualize", "Acquire & Visualize"
        UI_UX_FEATURE = "ui_ux_feature", "UI/UX Feature"

    class Functional(models.TextChoices):
        PERFORMANCE = "performance", "Performance"
        OPERABILITY = "operability", "Operability"
        SAFETY_SECURITY = "safety_security", "Safety & Security"
        EVOLVABILITY_DURABILITY = "evolvability_durability", "Evolvability & Durability"

    class Category(models.TextChoices):
        NORMAL_OPERATION = "normal_operation", "Normal Operation"
        MAINTENANCE_MODE = "maintenance_mode", "Maintenance Mode"

    class LifeCyclePhase(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        REJECTED = "rejected", "Rejected"

    # Classification fields are POC-extensible (see RequirementFieldOption):
    # these TextChoices are offered as suggestions, not enforced as the only
    # valid values — a POC lead may add new ones, and the Excel importer
    # accepts any non-empty text for them.
    FIELD_CHOICE_SOURCES = {
        "req_gravity": Gravity,
        "req_operation": Operation,
        "req_functional": Functional,
        "req_category": Category,
        "life_cycle_phase": LifeCyclePhase,
    }

    code = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        help_text="Auto-generated, e.g. POC-6000020869-NO-CS-001.",
    )
    # Readable identifier (spec item 9) — used as the display header in the
    # graph, the UC×Requirement matrix and chip/preview links, in place of
    # ``code`` (which stays unchanged for audit/traceability). Auto-generated,
    # never user-editable. Unique per-POC only (not globally, unlike ``code``
    # — see ``_generate_req_id``): two different POCs may end up with the
    # same ``req_id`` (e.g. both have a "Navigation_001"), an accepted risk
    # since nothing today lists Requirements across POCs under one identifier.
    req_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="Auto-generated, readable identifier, e.g. Navigation_001.",
    )
    poc = models.ForeignKey(
        POC, on_delete=models.CASCADE, related_name="requirements"
    )
    sub_system = models.CharField(max_length=255, blank=True)
    comments = GenericRelation("pocs.Comment")

    req_gravity = models.CharField(max_length=100)
    req_operation = models.CharField(max_length=100)
    req_functional = models.CharField(max_length=100)
    req_category = models.CharField(max_length=100)

    description = models.TextField(blank=True)
    validation_criteria = models.TextField(blank=True)
    life_cycle_phase = models.CharField(max_length=255, blank=True)
    reference_documentations = models.TextField(blank=True)
    remarks = models.TextField(blank=True)

    created_by = models.ForeignKey(
        USER,
        on_delete=models.PROTECT,
        related_name="created_requirements",
    )
    created_date = models.DateTimeField(auto_now_add=True)
    modified_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_requirements",
    )
    modified_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        constraints = [
            models.UniqueConstraint(
                fields=["poc", "req_id"], name="unique_requirement_req_id_per_poc"
            ),
        ]

    def __str__(self):
        return self.code

    def _generate_code(self):
        prefix = _poc_code_prefix(self.poc)
        cat = self.CATEGORY_ABBR.get(self.req_category, "XX")
        op = self.OPERATION_ABBR.get(self.req_operation, "XX")
        n = Requirement.objects.filter(poc=self.poc).count() + 1
        code = f"{prefix}-{cat}-{op}-{n:03d}"
        while Requirement.objects.filter(code=code).exclude(pk=self.pk).exists():
            n += 1
            code = f"{prefix}-{cat}-{op}-{n:03d}"
        return code

    def _req_id_prefix(self):
        """Slug of ``sub_system`` (e.g. "Fleet Integration" -> "FleetIntegration").
        Falls back to the category abbreviation (spec item 9) when blank,
        since ``sub_system`` is free text and may be empty."""
        words = re.findall(r"[A-Za-z0-9]+", self.sub_system or "")
        if words:
            return "".join(w[:1].upper() + w[1:] for w in words)
        return self.CATEGORY_ABBR.get(self.req_category, "XX")

    def _generate_req_id(self):
        """Numbered per (poc, sub_system) — same free-text bucket a blank
        ``sub_system`` shares, regardless of category (spec item 9)."""
        prefix = self._req_id_prefix()
        n = Requirement.objects.filter(
            poc=self.poc, sub_system=self.sub_system
        ).count() + 1
        req_id = f"{prefix}_{n:03d}"
        while (
            Requirement.objects.filter(poc=self.poc, req_id=req_id)
            .exclude(pk=self.pk)
            .exists()
        ):
            n += 1
            req_id = f"{prefix}_{n:03d}"
        return req_id

    def save(self, *args, **kwargs):
        if not self.code and self.poc_id:
            self.code = self._generate_code()
        if not self.req_id and self.poc_id:
            self.req_id = self._generate_req_id()
        super().save(*args, **kwargs)

    @classmethod
    def predefined_choices(cls, field_name):
        """The built-in (value, label) suggestions for a classification field.

        ``sub_system`` (and any other field with no ``FIELD_CHOICE_SOURCES``
        entry) has no built-in suggestions — every value comes from
        :class:`RequirementFieldOption` (see ``field_choices``).
        """
        source = cls.FIELD_CHOICE_SOURCES.get(field_name)
        return list(source.choices) if source else []

    @classmethod
    def field_choices(cls, poc, field_name):
        """Predefined suggestions plus ``poc``'s own custom additions.

        Classification fields (gravity/operation/functional/category) are not
        a closed set: a POC lead can add new values via "+ Add new" on the
        form, and the Excel importer accepts any non-empty text — see
        :class:`RequirementFieldOption`.
        """
        predefined = cls.predefined_choices(field_name)
        seen = {v for v, _ in predefined}
        custom = []
        if poc is not None:
            custom = list(
                poc.requirement_field_options.filter(field=field_name)
                .exclude(value__in=seen)
                .order_by("value")
                .values_list("value", flat=True)
            )
        return predefined + [(v, v) for v in custom]

    def _display_for(self, field_name):
        value = getattr(self, field_name)
        return dict(self.predefined_choices(field_name)).get(value, value)

    def get_req_gravity_display(self):
        return self._display_for("req_gravity")

    def get_req_operation_display(self):
        return self._display_for("req_operation")

    def get_req_functional_display(self):
        return self._display_for("req_functional")

    def get_req_category_display(self):
        return self._display_for("req_category")

    def get_life_cycle_phase_display(self):
        return self._display_for("life_cycle_phase")

    @property
    def has_open_comment(self):
        """True while an un-Acked review comment is pending on this requirement.

        Drives the orange "needs attention" halo in the UI until the POC lead
        marks the comment Applied or Rejected.
        """
        return self.comments.filter(status=Comment.Status.OPEN).exists()

    @property
    def is_validated(self):
        """True once every linked test is settled and passed or skipped.

        A requirement with no linked tests is not validated. A linked test that
        is Not Passed, or not yet finished, keeps it unvalidated (spec 4/user).
        """
        tests = list(self.tests.all())
        if not tests:
            return False
        for t in tests:
            if t.execution_status == Test.ExecutionStatus.SKIPPED:
                continue
            if (
                t.execution_status == Test.ExecutionStatus.TEST_COMPLETED
                and t.result in Test.PASSING_RESULTS
            ):
                continue
            return False
        return True

    def graph_status(self):
        """Colour band for the POC overview graph — cascades from its linked
        Tests' own ``graph_status()``: red if any failed, else orange if any
        was skipped, else green once every linked test passed, else gray
        (no tests yet, or none decided one way or the other)."""
        statuses = [t.graph_status() for t in self.tests.all()]
        if "red" in statuses:
            return "red"
        if "orange" in statuses:
            return "orange"
        if statuses and all(s == "green" for s in statuses):
            return "green"
        return "gray"


class RequirementSnapshot(models.Model):
    """A file attached to a Requirement (``reference_snapshots`` — many per req)."""

    requirement = models.ForeignKey(
        Requirement, on_delete=models.CASCADE, related_name="snapshots"
    )
    file = models.FileField(upload_to="requirement_snapshots/%Y/%m/")
    caption = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requirement_snapshots",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.caption or self.file.name.rsplit("/", 1)[-1]


class RequirementFieldOption(models.Model):
    """A custom value added to a Requirement classification field, scoped to a POC.

    ``Requirement.Gravity`` / ``Operation`` / ``Functional`` / ``Category`` are
    offered as suggestions, but a POC lead may add new ones from the "+ Add
    new" control on the requirement form; the Excel importer registers any
    unrecognised value the same way so it becomes a future suggestion too.
    """

    class Field(models.TextChoices):
        GRAVITY = "req_gravity", "Gravity"
        OPERATION = "req_operation", "Operation"
        FUNCTIONAL = "req_functional", "Functional"
        CATEGORY = "req_category", "Category"
        LIFE_CYCLE_PHASE = "life_cycle_phase", "Lifecycle Status"
        SUB_SYSTEM = "sub_system", "Sub-System"

    poc = models.ForeignKey(
        POC, on_delete=models.CASCADE, related_name="requirement_field_options"
    )
    field = models.CharField(max_length=20, choices=Field.choices)
    value = models.CharField(max_length=100)
    created_by = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["field", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["poc", "field", "value"], name="unique_requirement_field_option"
            )
        ]

    def __str__(self):
        return f"{self.get_field_display()}: {self.value}"


class UseCase(models.Model):
    """A use case of a POC, optionally linked to one or more Requirements."""

    class Priority(models.TextChoices):
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        DEPRECATED = "deprecated", "Deprecated"
        REJECTED = "rejected", "Rejected"

    code = models.CharField(
        max_length=100,
        unique=True,
        blank=True,
        help_text="Auto-generated, e.g. POC-6000020869-UC001.",
    )
    external_code = models.CharField(
        "External code",
        max_length=100,
        blank=True,
        db_index=True,
        help_text="This use case's own code in your source documentation "
        "(e.g. UC001) — kept only for cross-reference, never generated or "
        "enforced by Light.",
    )
    poc = models.ForeignKey(POC, on_delete=models.CASCADE, related_name="use_cases")
    comments = GenericRelation("pocs.Comment")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    actor = models.CharField(
        max_length=255, blank=True, help_text='Who runs it, e.g. "Operator".'
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.MEDIUM
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    remarks = models.TextField(blank=True)

    created_by = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="created_use_cases"
    )
    created_date = models.DateTimeField(auto_now_add=True)
    modified_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modified_use_cases",
    )
    modified_date = models.DateTimeField(auto_now=True)

    requirements = models.ManyToManyField(
        Requirement, related_name="use_cases", blank=True
    )

    class Meta:
        ordering = ["code"]
        constraints = [
            # Title unique within a POC (code is globally unique on its own).
            models.UniqueConstraint(
                fields=["poc", "title"], name="unique_usecase_title_per_poc"
            )
        ]

    def __str__(self):
        return f"{self.code} · {self.title}"

    def _generate_code(self):
        prefix = _poc_code_prefix(self.poc)
        n = UseCase.objects.filter(poc=self.poc).count() + 1
        code = f"{prefix}-UC{n:03d}"
        while UseCase.objects.filter(code=code).exclude(pk=self.pk).exists():
            n += 1
            code = f"{prefix}-UC{n:03d}"
        return code

    def save(self, *args, **kwargs):
        if not self.code and self.poc_id:
            self.code = self._generate_code()
        super().save(*args, **kwargs)

    @property
    def is_approved(self):
        """Approved/finished when it has requirements and all are validated."""
        reqs = list(self.requirements.all())
        return bool(reqs) and all(r.is_validated for r in reqs)

    @property
    def has_open_comment(self):
        """True while an un-Acked review comment is pending on this use case."""
        return self.comments.filter(status=Comment.Status.OPEN).exists()

    def graph_status(self):
        """Colour band for the POC overview graph — cascades from its linked
        Requirements: red if any MVP ("Imposes (MVP)") requirement is red (a
        non-MVP/"nice to have" requirement being red does NOT force this to
        red); else orange while not every requirement is green; green once
        they all are; gray if it has no requirements yet."""
        reqs = list(self.requirements.all())
        if not reqs:
            return "gray"
        statuses = [(r.graph_status(), r.req_gravity) for r in reqs]
        if any(
            status == "red" and gravity == Requirement.Gravity.IMPOSES_MVP
            for status, gravity in statuses
        ):
            return "red"
        if all(status == "green" for status, _ in statuses):
            return "green"
        return "orange"


# ---------------------------------------------------------------------------
# Comments / feedback (Requirement & Use Case review loop)
# ---------------------------------------------------------------------------
class Comment(models.Model):
    """A review/feedback THREAD on a :class:`Requirement`, :class:`UseCase`,
    or :class:`Test` (improvement brief item 3).

    Any POC member may open one (this row is the thread's opening message)
    and any POC member may add replies (:class:`CommentMessage`) — a real
    multi-message thread, not a single message + one Lead Ack. Only a POC
    lead (or admin) may close it ("Close Thread"); a closed thread carries no
    separate Applied/Rejected verdict (simplified per spec — just who closed
    it and when). While OPEN, the commented item shows an orange halo (see
    ``has_open_comment`` on the target models) until it's closed.
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    # Direct POC link so comments stay queryable (e.g. the central Comments
    # page) without walking the generic FK — mirrors AuditLog.poc.
    poc = models.ForeignKey(POC, on_delete=models.CASCADE, related_name="comments")

    text = models.TextField()
    author = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="authored_comments"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.OPEN
    )
    closed_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closed_comments",
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"Comment #{self.pk} on {self.content_object}"

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def last_message(self):
        """The most recent message in the thread — a reply if there is one,
        otherwise the opening message itself (as a duck-typed stand-in).
        Uses ``.all()`` (not ``.last()``, which reorders via a fresh query)
        so a prefetched ``messages`` cache is reused with no extra query."""
        msgs = list(self.messages.all())
        return msgs[-1] if msgs else self

    def last_activity_at(self):
        return self.last_message().created_at

    def close(self, user):
        """Close the thread (spec item 3) — a Lead/Admin-only action, no
        separate Applied/Rejected verdict."""
        self.status = self.Status.CLOSED
        self.closed_by = user
        self.closed_at = timezone.now()
        self.save(update_fields=["status", "closed_by", "closed_at"])

    def reply(self, user, text):
        """Add a message to the thread (spec item 3) — any POC member."""
        return self.messages.create(author=user, text=text)


class CommentMessage(models.Model):
    """A reply within a :class:`Comment` thread (spec item 3) — any POC
    member may add one. The root Comment's own ``text``/``author``/
    ``created_at`` remains the thread's opening message."""

    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        USER, on_delete=models.PROTECT, related_name="comment_replies"
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Reply #{self.pk} on Comment #{self.comment_id}"


# ---------------------------------------------------------------------------
# Blueprint version history (spec Fase 8 — undo / restore)
# ---------------------------------------------------------------------------
class BlueprintVersion(models.Model):
    """A JSON snapshot of the whole phase blueprint, for undo / restore.

    Snapshots are taken *before* each blueprint mutation, so restoring the most
    recent one reverts the last change. ``data`` holds the serialised forest of
    :class:`PhaseTemplate` nodes plus their base tasks/tests/documents.
    """

    data = models.JSONField()
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="blueprint_versions",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Blueprint @ {self.created_at:%Y-%m-%d %H:%M} ({self.note})"
