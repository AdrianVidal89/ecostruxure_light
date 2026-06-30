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
    Task,
    Test,
)

_CHECKBOX = {"class": "h-4 w-4 rounded border-line text-brand focus:ring-brand/40"}

User = get_user_model()

# Textareas that should become Markdown editors (EasyMDE) on form pages.
MD_EDITOR_CLASS = "md-editor"

INPUT_CLASS = (
    "w-full rounded-lg border border-line px-3 py-2 text-sm text-ink "
    "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
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
    """Create / edit a POC. ``description`` is edited with EasyMDE client-side."""

    non_blank_fields = ("name",)

    class Meta:
        model = POC
        fields = ("name", "description", "status", "start_date", "end_date")
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            # id_description is the EasyMDE mount point (see poc_form.html).
            "description": forms.Textarea(attrs={"rows": 8}),
            "status": forms.Select(attrs={"class": INPUT_CLASS}),
            "start_date": forms.DateInput(
                attrs={"type": "date", "class": INPUT_CLASS}, format="%Y-%m-%d"
            ),
            "end_date": forms.DateInput(
                attrs={"type": "date", "class": INPUT_CLASS}, format="%Y-%m-%d"
            ),
        }

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
        fields = (
            "name",
            "kind",
            "description",
            "report_template",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "kind": forms.Select(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": INPUT_CLASS}),
            "report_template": forms.ClearableFileInput(attrs={"class": "hidden"}),
        }


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
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].required = False
        self.fields["assigned_to"].empty_label = "— Unassigned —"
        self.fields["assigned_to"].widget.attrs["class"] = INPUT_CLASS
        if poc is not None:
            self.fields["assigned_to"].queryset = _poc_member_queryset(poc)


class MemberTestForm(NonBlankMixin, forms.ModelForm):
    """Test creation for a team member documenting a test they performed.

    No assignee field — the view auto-assigns it to the member so they can then
    record the result inline. Accepts a ``poc`` kwarg for signature parity.
    """

    non_blank_fields = ("title",)

    class Meta:
        model = Test
        fields = ("title", "description", "acceptance_criteria", "expected_result")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "acceptance_criteria": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
            "expected_result": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
        }

    def __init__(self, *args, poc=None, **kwargs):
        super().__init__(*args, **kwargs)


class TestExecutionForm(forms.ModelForm):
    """Inline execution: actual result, verdict, and evidence (file or URL).

    Filled by the assignee (or a lead/admin). The model's
    ``FileExtensionValidator`` on ``evidence_file`` is enforced here via
    ``form.is_valid()``.
    """

    class Meta:
        model = Test
        fields = ("actual_result", "verdict", "evidence_file", "evidence_url")
        widgets = {
            "actual_result": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Markdown supported…",
                    "class": (
                        "w-full rounded-lg border border-line px-3 py-2 text-sm "
                        "focus:outline-none focus:ring-2 focus:ring-brand/40 "
                        "focus:border-brand"
                    ),
                }
            ),
            "verdict": forms.Select(
                attrs={
                    "class": (
                        "rounded-lg border border-line px-2 py-1 text-xs "
                        "font-medium focus:outline-none focus:ring-2 "
                        "focus:ring-brand/40"
                    )
                }
            ),
            "evidence_url": forms.URLInput(
                attrs={"class": INPUT_CLASS, "placeholder": "https://…"}
            ),
            "evidence_file": forms.ClearableFileInput(
                attrs={"class": "text-sm text-ink-muted"}
            ),
        }


class FunctionalAnalysisStepForm(NonBlankMixin, forms.ModelForm):
    """Admin: a step of the global Functional Analysis template (title + guidance)."""

    non_blank_fields = ("title",)

    class Meta:
        model = FunctionalAnalysisStep
        fields = ("title", "description")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 4, "class": MD_EDITOR_CLASS}),
        }


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
    so the title field is disabled when editing such a section.
    """

    non_blank_fields = ("title",)

    class Meta:
        model = PhaseDocument
        fields = ("title", "content")
        widgets = {
            "title": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "content": forms.Textarea(attrs={"rows": 12}),  # EasyMDE mount
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.is_fa_section:
            self.fields["title"].disabled = True


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
