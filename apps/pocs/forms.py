"""Forms for POC, Phase and Task management."""

from django import forms
from django.contrib.auth import get_user_model

from .models import (
    POC,
    BasePhaseDocument,
    BaseTask,
    BaseTest,
    FunctionalAnalysisStep,
    Phase,
    PhaseDocument,
    PhaseImage,
    PhaseTemplate,
    POCImage,
    Requirement,
    RequirementFieldOption,
    Task,
    Test,
    UseCase,
)

_CHECKBOX = {"class": "h-4 w-4 rounded border-line text-brand focus:ring-brand/40"}

User = get_user_model()

# Textareas that should become Markdown editors (EasyMDE) on form pages.
MD_EDITOR_CLASS = "md-editor"

# Sentinel select value for the "+ Add new…" option on Requirement's
# POC-extensible classification fields (gravity/operation/functional/category).
ADD_NEW_OPTION = "__new__"

# Placeholder guidance shown on Requirement.description — per the SESAM
# methodology, a requirement description follows a "The system shall <action>
# <object/parameter> <condition or operating mode>" structure.
REQUIREMENT_DESCRIPTION_PLACEHOLDER = (
    'Follow the SESAM methodology structure: "The system shall <action> '
    '<object/parameter> <condition or operating mode>".\n'
    "e.g. The system shall display the real-time connector status "
    "(offline / available / plugged / charging / error) of every charging "
    "point in normal operation mode."
)

INPUT_CLASS = (
    "w-full rounded-lg border border-line px-3 py-2 text-sm text-ink "
    "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
)


def requirements_locked_by_other_tests(poc, exclude_test_id=None):
    """``{requirement_id: owning Test}`` for requirements already linked to a
    DIFFERENT test in ``poc``.

    A requirement may only ever be covered by one test — testing/validating it
    twice would be a design flaw (each test result should map to exactly one
    requirement outcome). Used to grey out already-claimed requirements in the
    test forms and to reject them server-side (``clean_requirements``).
    """
    locks = {}
    tests_qs = Test.objects.filter(phase__poc=poc).prefetch_related("requirements")
    if exclude_test_id:
        tests_qs = tests_qs.exclude(pk=exclude_test_id)
    for t in tests_qs:
        for r in t.requirements.all():
            locks.setdefault(r.id, t)
    return locks


class LockedCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    """A CheckboxSelectMultiple with some options rendered ``disabled``.

    Set ``.disabled_values`` (an iterable of the option values, as strings)
    after construction — matching options can't be (re)selected. Used to grey
    out requirements already claimed by another test.
    """

    def __init__(self, *args, **kwargs):
        self.disabled_values = set()
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value is not None and str(getattr(value, "value", value)) in self.disabled_values:
            option["attrs"]["disabled"] = True
        return option

# Imported business fields shown in the POC "Details" panel. Editable by
# admins/leads through the edit form (rendered generically — see poc_form.html).
POC_DETAIL_FIELDS = (
    "customer",
    "customer_segment",
    "region",
    "initiative",
    "initiative_qua",
    "initiative_qua_id",
    "leading_organization",
    "l2_wbs",
    "bfo_no",
    "investment_type",
    "leadership",
    "finance_kpi",
    "schedule_kpi",
    "proposal_duration",
    "tendering_start",
    "tendering_finish",
    "execution_start",
    "execution_finish",
    "pilot_requestor",
    "opportunity_leader",
    "ecostruxure_lead",
    "pilot_tender_leader",
    "pilot_tender_tl",
    "pilot_pm",
    "pilot_exec_tl",
    "integration_leader",
)


def _date_input():
    return forms.DateInput(
        attrs={"type": "date", "class": INPUT_CLASS}, format="%Y-%m-%d"
    )


class NonBlankMixin:
    """Reject whitespace-only values and trim the listed text fields.

    Guards against "empty" items (a title/name of only spaces) being saved,
    which would render as blank rows. Set ``non_blank_fields`` on the form.
    """

    non_blank_fields = ()

    def clean(self):
        cleaned = super().clean()
        for field in self.non_blank_fields:
            value = cleaned.get(field)
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    self.add_error(field, "This field can’t be blank.")
                else:
                    cleaned[field] = stripped
        return cleaned


class POCForm(NonBlankMixin, forms.ModelForm):
    """Create / edit a POC. ``description`` is edited with EasyMDE client-side.

    Besides the core fields, every imported business detail (``POC_DETAIL_FIELDS``)
    is editable here; the template renders them generically via ``detail_fields``.
    """

    non_blank_fields = ("name",)

    class Meta:
        model = POC
        fields = (
            "name",
            "description",
            "status",
            "architecture_image",
            "start_date",
            "end_date",
        ) + POC_DETAIL_FIELDS
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            # id_description is the EasyMDE mount point (see poc_form.html).
            "description": forms.Textarea(attrs={"rows": 8}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
            "architecture_image": forms.ClearableFileInput(attrs={"class": "hidden"}),
            "start_date": _date_input(),
            "end_date": _date_input(),
            "tendering_start": _date_input(),
            "tendering_finish": _date_input(),
            "execution_start": _date_input(),
            "execution_finish": _date_input(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # All imported details are optional; give them the shared input styling.
        for name in POC_DETAIL_FIELDS:
            field = self.fields.get(name)
            if field is not None:
                field.required = False
                field.widget.attrs.setdefault("class", INPUT_CLASS)

    def detail_fields(self):
        """Bound fields for the imported "Details" section, in display order."""
        return [self[name] for name in POC_DETAIL_FIELDS]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date cannot be before the start date.")
        return cleaned


# Hidden file widget: the drag-and-drop zone provides the UI. No ``accept`` —
# it makes the native dialog open very slowly on managed Windows.
_HIDDEN_FILE = forms.ClearableFileInput(attrs={"class": "hidden"})


class PhaseForm(NonBlankMixin, forms.ModelForm):
    """Edit a phase. Used by both admins and POC leads — a lead has full control
    over every phase inside their POC.

    ``order`` is set by drag-and-drop and ``status`` is derived, so neither is
    edited here.
    """

    non_blank_fields = ("name",)

    class Meta:
        model = Phase
        # ``kind`` is inherited from the blueprint but a lead may also create
        # phases directly, so it's editable here (Test / Documentation / FA).
        # ``phase_leader`` (spec Fase 5, pulled in early for 3b) routes test
        # validation to a specific team lead for this phase.
        fields = (
            "name",
            "kind",
            "description",
            "phase_leader",
            "report_template",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "kind": forms.Select(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": INPUT_CLASS}),
            "phase_leader": forms.Select(attrs={"class": INPUT_CLASS}),
            "report_template": forms.ClearableFileInput(attrs={"class": "hidden"}),
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phase_leader"].required = False
        self.fields["phase_leader"].empty_label = "— POC leads validate —"
        if poc is not None:
            self.fields["phase_leader"].queryset = _poc_member_queryset(poc)


class PhaseTemplateForm(NonBlankMixin, forms.ModelForm):
    """Admin: a node in the global phase blueprint."""

    non_blank_fields = ("name",)

    class Meta:
        model = PhaseTemplate
        fields = (
            "name",
            "kind",
            "description",
            "report_template",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "kind": forms.Select(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 3, "class": INPUT_CLASS}),
            "report_template": forms.ClearableFileInput(attrs={"class": "hidden"}),
        }


class BaseTaskForm(NonBlankMixin, forms.ModelForm):
    """Admin: a base task on a blueprint node."""

    non_blank_fields = ("title",)

    class Meta:
        model = BaseTask
        fields = ("title", "description")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 3, "class": MD_EDITOR_CLASS}),
        }


class POCImportForm(forms.Form):
    """Admin: upload the M365 PoC Follow-up .xlsx to import POCs."""

    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "hidden"})
    )
    dry_run = forms.BooleanField(
        required=False,
        initial=True,
        label="Validate only (dry run) — preview counts without saving",
        widget=forms.CheckboxInput(
            attrs={"class": "h-4 w-4 rounded border-line text-brand focus:ring-brand/40"}
        ),
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".xlsx", ".csv")):
            raise forms.ValidationError("Please upload an .xlsx or .csv file.")
        return f


class BaseTestForm(NonBlankMixin, forms.ModelForm):
    """Admin: a base test on a blueprint node."""

    non_blank_fields = ("title",)

    class Meta:
        model = BaseTest
        fields = ("title", "description", "acceptance_criteria", "expected_result")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 3, "class": MD_EDITOR_CLASS}),
            "acceptance_criteria": forms.Textarea(attrs={"rows": 3, "class": MD_EDITOR_CLASS}),
            "expected_result": forms.Textarea(attrs={"rows": 3, "class": MD_EDITOR_CLASS}),
        }


class TaskForm(NonBlankMixin, forms.ModelForm):
    """Create / edit a Task (POC lead). ``assigned_to`` is limited to members
    of the task's POC; the description uses EasyMDE client-side."""

    non_blank_fields = ("title",)

    class Meta:
        model = Task
        fields = ("title", "description", "assigned_to", "status", "due_date")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 6}),  # EasyMDE mount
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
            "due_date": forms.DateInput(
                attrs={"type": "date", "class": INPUT_CLASS}, format="%Y-%m-%d"
            ),
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "— Unassigned —"
        self.fields["assigned_to"].widget.attrs["class"] = INPUT_CLASS
        if poc is not None:
            self.fields["assigned_to"].queryset = (
                User.objects.filter(poc_memberships__poc=poc, is_active=True)
                .distinct()
                .order_by("first_name", "username")
            )


def _poc_member_queryset(poc):
    return (
        User.objects.filter(poc_memberships__poc=poc, is_active=True)
        .distinct()
        .order_by("first_name", "username")
    )


class TestForm(NonBlankMixin, forms.ModelForm):
    """Create / edit a Test definition (POC lead).

    Covers the lead-authored fields only. Execution fields (actual_result,
    verdict, evidence) are filled inline by the assignee via TestExecutionForm.
    """

    non_blank_fields = ("title",)

    class Meta:
        model = Test
        fields = (
            "title",
            "description",
            "acceptance_criteria",
            "expected_result",
            "assigned_to",
            "requirements",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "acceptance_criteria": forms.Textarea(
                attrs={"rows": 4, "class": MD_EDITOR_CLASS}
            ),
            "expected_result": forms.Textarea(
                attrs={"rows": 4, "class": MD_EDITOR_CLASS}
            ),
            "requirements": LockedCheckboxSelectMultiple(),
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "— Unassigned —"
        self.fields["assigned_to"].widget.attrs["class"] = INPUT_CLASS
        self.fields["requirements"].required = False
        self.requirement_locks = {}
        if poc is not None:
            self.fields["assigned_to"].queryset = _poc_member_queryset(poc)
            self.fields["requirements"].queryset = poc.requirements.all()
            self.requirement_locks = requirements_locked_by_other_tests(
                poc, exclude_test_id=self.instance.pk
            )
            self.fields["requirements"].widget.disabled_values = {
                str(k) for k in self.requirement_locks
            }

    def clean_requirements(self):
        reqs = self.cleaned_data.get("requirements")
        if reqs:
            clash = [r for r in reqs if r.id in self.requirement_locks]
            if clash:
                codes = ", ".join(r.code for r in clash)
                raise forms.ValidationError(
                    "Already covered by another test — a requirement can only be "
                    f"linked to one test: {codes}."
                )
        return reqs


class MemberTestForm(NonBlankMixin, forms.ModelForm):
    """Test creation for a team member documenting a test they performed.

    No assignee field — the view auto-assigns it to the member so they can then
    record the result inline. Accepts a ``poc`` kwarg for signature parity.
    """

    non_blank_fields = ("title",)

    class Meta:
        model = Test
        fields = (
            "title",
            "description",
            "acceptance_criteria",
            "expected_result",
            "requirements",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "acceptance_criteria": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "expected_result": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "requirements": LockedCheckboxSelectMultiple(),
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["requirements"].required = False
        self.requirement_locks = {}
        if poc is not None:
            self.fields["requirements"].queryset = poc.requirements.all()
            self.requirement_locks = requirements_locked_by_other_tests(
                poc, exclude_test_id=self.instance.pk
            )
            self.fields["requirements"].widget.disabled_values = {
                str(k) for k in self.requirement_locks
            }

    def clean_requirements(self):
        reqs = self.cleaned_data.get("requirements")
        if reqs:
            clash = [r for r in reqs if r.id in self.requirement_locks]
            if clash:
                codes = ", ".join(r.code for r in clash)
                raise forms.ValidationError(
                    "Already covered by another test — a requirement can only be "
                    f"linked to one test: {codes}."
                )
        return reqs


_SELECT_XS = (
    "rounded-lg border border-line px-2 py-1.5 text-sm "
    "focus:outline-none focus:ring-2 focus:ring-brand/40"
)


class TestExecutionForm(forms.ModelForm):
    """Inline execution: execution status, result, notes and evidence.

    Filled by the assignee (or a lead/admin). Enforces the spec 3a rule that
    notes are mandatory when the result is Not Passed / Passed with comments, or
    when the execution is Skipped. The view decides whether the submission is
    applied directly or held as a ``TestValidation`` proposal.

    ``result`` is optional here (blank = no result yet); a blank first option is
    added so the executor can record progress without a verdict.
    """

    class Meta:
        model = Test
        fields = (
            "execution_status",
            "result",
            "actual_result",
            "evidence_url",
        )
        widgets = {
            "actual_result": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Notes / observations about this run…",
                    "class": (
                        "w-full rounded-lg border border-line px-3 py-2 text-sm "
                        "focus:outline-none focus:ring-2 focus:ring-brand/40 "
                        "focus:border-brand"
                    ),
                }
            ),
            "execution_status": forms.Select(attrs={"class": _SELECT_XS}),
            "result": forms.Select(attrs={"class": _SELECT_XS}),
            "evidence_url": forms.URLInput(
                attrs={"class": INPUT_CLASS, "placeholder": "https://…"}
            ),
        }

    def __init__(self, *args, test=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Result is optional (blank = not decided yet).
        self.fields["result"].required = False
        self.fields["result"].widget.choices = [
            ("", "— No result —")
        ] + list(Test.Result.choices)
        # The test being executed — requirements are linked at creation/edit
        # time (TestForm/MemberTestForm), not re-picked here; passed in so the
        # "Not Passed needs a requirement" rule can check what's already
        # attached to the test instead of a resubmitted field.
        self._test = test

    def clean(self):
        cleaned = super().clean()
        execution = cleaned.get("execution_status")
        result = cleaned.get("result")
        notes = (cleaned.get("actual_result") or "").strip()
        needs_notes = (
            result in Test.RESULTS_REQUIRING_NOTES
            or execution == Test.ExecutionStatus.SKIPPED
        )
        if needs_notes and not notes:
            self.add_error(
                "actual_result",
                "Notes are required when the result is Not Passed / Passed with "
                "comments, or when the test is skipped.",
            )
        # A result only makes sense once the test is completed.
        if result and execution != Test.ExecutionStatus.TEST_COMPLETED:
            self.add_error(
                "result",
                "Set the execution status to “Test completed” to record a result.",
            )
        # A "Not Passed" result must be tied to at least one requirement (4b) —
        # checked against the test's already-linked requirements.
        if result == Test.Result.NOT_PASSED and not (
            self._test and self._test.requirements.exists()
        ):
            self.add_error(
                None,
                "Link at least one requirement (in the test's edit form) before "
                "recording a Not Passed result.",
            )
        return cleaned


class FunctionalAnalysisStepForm(NonBlankMixin, forms.ModelForm):
    """Admin: a step of the global Functional Analysis template (title + guidance).

    ``section_kind`` wires the step to the POC's own Use Cases/Requirements
    registry (auto-filled, locked for the POC lead) instead of free text — at
    most one step of each kind is allowed, since each represents the whole
    registry, not a repeatable item.
    """

    non_blank_fields = ("title",)

    class Meta:
        model = FunctionalAnalysisStep
        fields = ("title", "description", "section_kind")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "section_kind": forms.Select(attrs={"class": INPUT_CLASS}),
        }

    def clean_section_kind(self):
        kind = self.cleaned_data["section_kind"]
        if kind != FunctionalAnalysisStep.SectionKind.OTHER:
            clash = (
                FunctionalAnalysisStep.objects.filter(section_kind=kind)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if clash:
                label = dict(FunctionalAnalysisStep.SectionKind.choices)[kind]
                raise forms.ValidationError(
                    f"There's already a “{label}” section — only one is allowed."
                )
        return kind


class BasePhaseDocumentForm(NonBlankMixin, forms.ModelForm):
    """Admin: a base document on a blueprint node (Documentation phases)."""

    non_blank_fields = ("title",)

    class Meta:
        model = BasePhaseDocument
        fields = ("title", "content")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "content": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
        }


class PhaseDocumentForm(NonBlankMixin, forms.ModelForm):
    """Create / edit a phase document. ``content`` uses EasyMDE client-side.

    For Functional Analysis sections the title is locked (set from the FA step),
    so the title field is disabled when editing such a section. Locked sections
    (Use Cases/Requirements) aren't editable at all — the view routes those to a
    read-only preview instead, but the field is disabled here too as a safety net.

    ``insert_after`` (new documents only) lets the POC lead place the new section
    among the phase's existing ones instead of always appending at the end.
    """

    non_blank_fields = ("title",)

    insert_after = forms.ModelChoiceField(
        queryset=PhaseDocument.objects.none(),
        required=False,
        empty_label="At the beginning",
        label="Place after",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )

    class Meta:
        model = PhaseDocument
        fields = ("title", "content")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "content": forms.Textarea(attrs={"rows": 12}),  # EasyMDE mount
        }

    def __init__(self, *args, phase=None, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.is_fa_section:
            self.fields["title"].disabled = True
        if self.instance and getattr(self.instance, "is_locked_section", False):
            self.fields["content"].disabled = True
        if self.instance.pk or phase is None:
            # Editing an existing document, or no phase given: position doesn't apply.
            del self.fields["insert_after"]
        else:
            self.fields["insert_after"].queryset = phase.documents.order_by("order", "id")


class PhaseImageForm(forms.ModelForm):
    """Upload an image to a phase (referenced from documents' Markdown)."""

    class Meta:
        model = PhaseImage
        fields = ("image", "caption")
        widgets = {
            "image": forms.ClearableFileInput(attrs={"class": "hidden"}),
            "caption": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Caption (optional)"}
            ),
        }


class POCImageForm(forms.ModelForm):
    """Upload an image to a POC (used by the closure-conclusion editor)."""

    class Meta:
        model = POCImage
        fields = ("image", "caption")
        widgets = {
            "image": forms.ClearableFileInput(attrs={"class": "hidden"}),
            "caption": forms.TextInput(
                attrs={"class": INPUT_CLASS, "placeholder": "Caption (optional)"}
            ),
        }


class RequirementChoiceField(forms.ModelMultipleChoiceField):
    """A Requirement's bare code isn't enough to recognise it when linking —
    show the sub-system alongside it (used wherever requirements are checked
    off, e.g. linking them to a Use Case)."""

    def label_from_instance(self, obj):
        return f"{obj.code} · {obj.sub_system}" if obj.sub_system else obj.code


class RequirementForm(NonBlankMixin, forms.ModelForm):
    """Create / edit a Requirement of a POC.

    The ``code`` is auto-generated (never entered). A requirement carries no
    manual approval; it may be attached to one or more Use Cases here.

    ``req_gravity``/``req_operation``/``req_functional``/``req_category`` are
    POC-extensible: the dropdown offers the built-in suggestions plus any
    custom value the POC has added before, and a "+ Add new…" option that
    reveals a text box (see the paired ``*_new`` fields) — the typed value is
    saved as a :class:`~apps.pocs.models.RequirementFieldOption` so it becomes
    a suggestion for next time.
    """

    CLASSIFICATION_FIELDS = (
        "req_gravity", "req_operation", "req_functional", "req_category", "life_cycle_phase",
    )

    use_cases = forms.ModelMultipleChoiceField(
        queryset=UseCase.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Use case(s) this requirement responds to — check the ones that apply.",
    )

    # Declared explicitly (rather than auto-generated from the model field) so
    # ``choices`` can be computed per-POC in __init__ — a plain model CharField
    # has no ``choices`` for a ModelForm to pick up. ``@change`` is read by
    # spec_form.html (Alpine) to reveal the paired "*_new" text box when
    # "+ Add new…" is picked.
    req_gravity = forms.ChoiceField(
        choices=[], widget=forms.Select(attrs={"class": INPUT_CLASS, "@change": "v = $event.target.value"})
    )
    req_operation = forms.ChoiceField(
        choices=[], widget=forms.Select(attrs={"class": INPUT_CLASS, "@change": "v = $event.target.value"})
    )
    req_functional = forms.ChoiceField(
        choices=[], widget=forms.Select(attrs={"class": INPUT_CLASS, "@change": "v = $event.target.value"})
    )
    req_category = forms.ChoiceField(
        choices=[], widget=forms.Select(attrs={"class": INPUT_CLASS, "@change": "v = $event.target.value"})
    )
    life_cycle_phase = forms.ChoiceField(
        choices=[], required=False, label="Lifecycle status",
        widget=forms.Select(attrs={"class": INPUT_CLASS, "@change": "v = $event.target.value"}),
    )

    req_gravity_new = forms.CharField(
        required=False, label="New gravity value",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Type the new value…"}),
    )
    req_operation_new = forms.CharField(
        required=False, label="New operation value",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Type the new value…"}),
    )
    req_functional_new = forms.CharField(
        required=False, label="New functional value",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Type the new value…"}),
    )
    req_category_new = forms.CharField(
        required=False, label="New category value",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Type the new value…"}),
    )
    life_cycle_phase_new = forms.CharField(
        required=False, label="New lifecycle status value",
        widget=forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "Type the new value…"}),
    )

    class Meta:
        model = Requirement
        fields = (
            "sub_system",
            "req_gravity",
            "req_operation",
            "req_functional",
            "req_category",
            "description",
            "validation_criteria",
            "life_cycle_phase",
            "reference_documentations",
            "remarks",
        )
        widgets = {
            "sub_system": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={
                "rows": 3, "class": INPUT_CLASS,
                "placeholder": REQUIREMENT_DESCRIPTION_PLACEHOLDER,
            }),
            "validation_criteria": forms.Textarea(attrs={"rows": 3, "class": MD_EDITOR_CLASS}),
            "reference_documentations": forms.Textarea(attrs={"rows": 2, "class": MD_EDITOR_CLASS}),
            "remarks": forms.Textarea(attrs={"rows": 2, "class": MD_EDITOR_CLASS}),
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._poc = poc
        for field_name in self.CLASSIFICATION_FIELDS:
            choices = Requirement.field_choices(poc, field_name)
            current = getattr(self.instance, field_name, "")
            if current and current not in dict(choices):
                choices = [(current, current)] + choices
            if not self.fields[field_name].required:
                choices = [("", "—")] + choices
            self.fields[field_name].choices = choices + [(ADD_NEW_OPTION, "+ Add new…")]
        if poc is not None:
            self.fields["use_cases"].queryset = poc.use_cases.all()
        if self.instance.pk:
            self.fields["use_cases"].initial = self.instance.use_cases.all()

    def clean(self):
        cleaned = super().clean()
        for field_name in self.CLASSIFICATION_FIELDS:
            if cleaned.get(field_name) != ADD_NEW_OPTION:
                continue
            new_value = (cleaned.get(f"{field_name}_new") or "").strip()
            if not new_value:
                self.add_error(f"{field_name}_new", "Please type the new value.")
            else:
                cleaned[field_name] = new_value
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if commit:
            obj.use_cases.set(self.cleaned_data.get("use_cases", []))
            self._persist_new_options()
        return obj

    def _persist_new_options(self):
        if self._poc is None:
            return
        for field_name in self.CLASSIFICATION_FIELDS:
            value = self.cleaned_data.get(field_name)
            if not value or value in dict(Requirement.predefined_choices(field_name)):
                continue
            RequirementFieldOption.objects.get_or_create(
                poc=self._poc, field=field_name, value=value
            )


class UseCaseForm(NonBlankMixin, forms.ModelForm):
    """Create / edit a Use Case, optionally linking Requirements of the POC."""

    non_blank_fields = ("title",)

    requirements = RequirementChoiceField(
        queryset=Requirement.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Requirement(s) this use case relies on — check the ones that apply.",
    )

    class Meta:
        model = UseCase
        fields = (
            "title",
            "description",
            "actor",
            "priority",
            "status",
            "remarks",
            "requirements",
        )
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "actor": forms.TextInput(attrs={"class": INPUT_CLASS, "placeholder": "e.g. Operator"}),
            "priority": forms.Select(attrs={"class": INPUT_CLASS}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
            "remarks": forms.Textarea(attrs={"rows": 2, "class": MD_EDITOR_CLASS}),
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._poc = poc
        self.fields["requirements"].required = False
        if poc is not None:
            self.fields["requirements"].queryset = poc.requirements.all()

    def clean(self):
        cleaned = super().clean()
        # Enforce title-unique-within-POC (the model constraint spans poc+title
        # but poc isn't a form field, so validate here for a friendly message).
        title = cleaned.get("title")
        if title and self._poc is not None:
            clash = (
                UseCase.objects.filter(poc=self._poc, title=title)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if clash:
                self.add_error("title", "A use case with this title already exists in this POC.")
        return cleaned


class TestRequirementsForm(forms.Form):
    """Link a Test to Requirements of the same POC (spec Fase 3d)."""

    requirements = forms.ModelMultipleChoiceField(
        queryset=Requirement.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": INPUT_CLASS, "size": 6}),
    )

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        if poc is not None:
            self.fields["requirements"].queryset = poc.requirements.all()


class TestValidationDecisionForm(forms.Form):
    """A phase lead's decision on a pending test outcome (spec 3b)."""

    decision = forms.ChoiceField(
        choices=(("approve", "Approve"), ("reject", "Reject"))
    )
    comment = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3, "class": INPUT_CLASS, "placeholder": "Optional comment…"}
        ),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == "reject" and not (cleaned.get("comment") or "").strip():
            self.add_error("comment", "Please add a reason when rejecting.")
        return cleaned


class RequirementImportForm(forms.Form):
    """Upload an .xlsx of Requirements to import (spec Fase 10c)."""

    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "hidden"})
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Please upload an .xlsx file.")
        return f


class PhaseMarkNAForm(forms.Form):
    """Mark a blueprint-sourced phase Not Applicable — the reason is mandatory
    so it can be shown, verbatim, in the POC's final report."""

    na_reason = forms.CharField(
        required=True,
        label="Why doesn't this phase apply?",
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "class": MD_EDITOR_CLASS,
                "placeholder": "Explain why this phase is not applicable to this POC…",
            }
        ),
    )


class POCCloseForm(forms.Form):
    """Official closure of a POC (spec Fase 6a): closure date + optional notes."""

    closure_date = forms.DateField(
        widget=_date_input(),
        help_text="Official closure date.",
    )
    closure_conclusion = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 5,
                "class": INPUT_CLASS,
                "placeholder": "Final conclusion / notes for the final report (optional)…",
            }
        ),
        label="Final conclusion / notes",
    )
    confirm = forms.BooleanField(
        required=True,
        label="I understand this is irreversible (only an admin can revert it).",
        widget=forms.CheckboxInput(attrs={"class": _CHECKBOX["class"]}),
    )


class PhaseReportUploadForm(forms.Form):
    """Attach a finished report file to a phase (kept as-is for download)."""

    ALLOWED_EXTS = ("docx", "pdf", "doc", "odt", "xlsx", "pptx", "md", "txt", "zip")

    report_file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"class": "hidden"})
    )

    def clean_report_file(self):
        f = self.cleaned_data["report_file"]
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in self.ALLOWED_EXTS:
            raise forms.ValidationError(
                "Allowed: " + ", ".join(f".{e}" for e in self.ALLOWED_EXTS)
            )
        return f
