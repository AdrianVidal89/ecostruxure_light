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

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

USER = settings.AUTH_USER_MODEL


# ---------------------------------------------------------------------------
# POC
# ---------------------------------------------------------------------------
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

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "POC"
        verbose_name_plural = "POCs"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    # --- Progress metrics (consumed by the dashboard) ---------------------
    def progress_percent(self):
        """Overall completion = completed phases / total phases.

        Counted over the **leaf** phases (the actual work units; parent phases
        are groupings). A phase counts as done only when its status is
        ``completed`` — any other state (pending / in progress) is "in progress"
        and does not count. Returns 0 when there are no phases.
        """
        # A POC marked completed always reads 100% (no "completed but 0%").
        if self.status == self.Status.COMPLETED:
            return 100

        leaves = self.phases.filter(children__isnull=True)
        total = leaves.count()
        if not total:
            return 0
        done = leaves.filter(status=Phase.Status.COMPLETED).count()
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
    is_functional_analysis = models.BooleanField(
        default=False,
        help_text="Render this phase with the Functional Analysis template instead of tasks/tests.",
    )
    report_template = models.FileField(
        upload_to="report_templates/blueprint/%Y/%m/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["docx"])],
        help_text="Word template (.docx); only nodes with one can generate a report.",
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
    is_functional_analysis = models.BooleanField(default=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0, help_text="Manual ordering.")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # Optional Word template (.docx) attached by an admin/lead. When present, a
    # phase report can be generated from this phase's tests (see apps.reports).
    report_template = models.FileField(
        upload_to="report_templates/phase/%Y/%m/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["docx"])],
        help_text="Word template (.docx) enabling report generation for this phase.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.name} ({self.poc})"

    @property
    def is_reportable(self):
        """A phase report can be generated once a .docx template is attached."""
        return bool(self.report_template)

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

    def compute_status(self):
        """Derive status from the Tasks/Tests in this phase's whole subtree.

        Completed only when every Task is ``completed`` and every Test has a
        terminal verdict (passed/failed/skipped); in_progress once anything has
        started; otherwise pending. For a parent phase this aggregates its
        sub-phases; for a leaf it's just its own items.
        """
        ids = self.descendant_ids()
        tasks = list(Task.objects.filter(phase_id__in=ids))
        tests = list(Test.objects.filter(phase_id__in=ids))

        if not tasks and not tests:
            return self.Status.PENDING

        all_settled = all(t.is_settled for t in tasks) and all(
            t.is_settled for t in tests
        )
        if all_settled:
            return self.Status.COMPLETED

        any_started = any(t.is_started for t in tasks) or any(
            t.is_started for t in tests
        )
        return self.Status.IN_PROGRESS if any_started else self.Status.PENDING

    def recalculate_status(self, commit=True):
        """Persist the derived status and bubble the recalculation to ancestors."""
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
        """Derived completion of the phase's whole subtree (completed ⇒ 100%)."""
        if self.status == self.Status.COMPLETED:
            return 100
        ids = self.descendant_ids()
        task_total = Task.objects.filter(phase_id__in=ids).count()
        test_total = Test.objects.filter(phase_id__in=ids).count()
        total = task_total + test_total
        if not total:
            return 0
        done = Task.objects.filter(
            phase_id__in=ids, status=Task.Status.COMPLETED
        ).count() + Test.objects.filter(
            phase_id__in=ids, verdict__in=Test.SETTLED_VERDICTS
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
        executed = self.tests.filter(verdict__in=Test.SETTLED_VERDICTS).count()
        if not executed:
            return 0
        passed = self.tests.filter(verdict=Test.Verdict.PASSED).count()
        return round(passed / executed * 100)


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

    def mark_completed(self, user, notes=None):
        """Mark complete and stamp who/when. Saves the instance."""
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

    class Verdict(models.TextChoices):
        PENDING = "pending", "Pending"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed"
        BLOCKED = "blocked", "Blocked"
        SKIPPED = "skipped", "Skipped"

    # Verdicts that count as "executed/terminal" for phase completion & metrics.
    SETTLED_VERDICTS = (Verdict.PASSED, Verdict.FAILED, Verdict.SKIPPED)

    phase = models.ForeignKey(Phase, on_delete=models.CASCADE, related_name="tests")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Markdown supported.")
    acceptance_criteria = models.TextField(blank=True, help_text="Markdown supported.")
    expected_result = models.TextField(blank=True, help_text="Markdown supported.")
    actual_result = models.TextField(
        blank=True, help_text="Markdown — filled by the team member."
    )
    verdict = models.CharField(
        max_length=20, choices=Verdict.choices, default=Verdict.PENDING
    )
    assigned_to = models.ForeignKey(
        USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_tests",
    )
    evidence_file = models.FileField(
        upload_to="evidence/%Y/%m/",
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(
                ["jpg", "jpeg", "png", "gif", "pdf", "txt", "log"]
            )
        ],
        help_text="Image, PDF, txt or log.",
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
        ordering = ["id"]

    def __str__(self):
        return self.title

    @property
    def is_settled(self):
        """Terminal verdict (passed/failed/skipped)."""
        return self.verdict in self.SETTLED_VERDICTS

    @property
    def is_started(self):
        return self.verdict != self.Verdict.PENDING

    def mark_executed(self, user, verdict=None, actual_result=None):
        """Record an execution result and stamp who/when. Saves the instance."""
        if verdict is not None:
            self.verdict = verdict
        if actual_result is not None:
            self.actual_result = actual_result
        self.executed_by = user
        self.executed_at = timezone.now()
        self.save()

    def save(self, *args, **kwargs):
        # Keep the execution timestamp consistent with the verdict.
        if self.verdict != self.Verdict.PENDING and self.executed_at is None:
            self.executed_at = timezone.now()
        elif self.verdict == self.Verdict.PENDING:
            self.executed_at = None
            self.executed_by = None
        super().save(*args, **kwargs)


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


# ---------------------------------------------------------------------------
# Functional Analysis template (global, admin-defined strict structure)
# ---------------------------------------------------------------------------
class FunctionalAnalysisStep(models.Model):
    """An ordered step of the global Functional Analysis template.

    Phases flagged ``is_functional_analysis`` render these steps for the user to
    copy into a Markdown document and generate the report from it.
    """

    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=255)
    preconditions = models.TextField(blank=True, help_text="Markdown supported.")
    action = models.TextField(blank=True, help_text="What to do. Markdown supported.")
    expected_result = models.TextField(blank=True, help_text="Markdown supported.")
    acceptance_criteria = models.TextField(blank=True, help_text="Markdown supported.")

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "functional analysis step"

    def __str__(self):
        return self.title
