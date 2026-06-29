"""Forms for report-type management (admin) and custom report generation."""

from django import forms

from .generation import ACCEPTED_SOURCE_EXTS
from .models import ReportType

INPUT_CLASS = (
    "w-full rounded-lg border border-line px-3 py-2 text-sm text-ink "
    "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
)


class ReportTypeForm(forms.ModelForm):
    """Admin: create/edit a custom report type with its .docx template."""

    class Meta:
        model = ReportType
        fields = ("name", "description", "template", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"class": INPUT_CLASS}),
            "description": forms.Textarea(attrs={"rows": 3, "class": INPUT_CLASS}),
            # Hidden: the drag-and-drop zone provides the UI. No ``accept`` —
            # on managed Windows it makes the native dialog load Office/OneDrive
            # handlers and open very slowly; type is validated server-side.
            "template": forms.ClearableFileInput(attrs={"class": "hidden"}),
            "is_active": forms.CheckboxInput(
                attrs={"class": "h-4 w-4 rounded border-line text-brand focus:ring-brand/40"}
            ),
        }


class CustomReportForm(forms.Form):
    """Pick a report type and provide a source — pasted Markdown or a file.

    Pasting text (or drag-and-drop) is the primary path so the conversion never
    depends on the native file-open dialog.
    """

    report_type = forms.ModelChoiceField(
        queryset=ReportType.objects.filter(is_active=True),
        empty_label="— Select a report type —",
        widget=forms.Select(attrs={"class": INPUT_CLASS}),
    )
    source_text = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 10,
                "placeholder": "# Paste or type your Markdown here…",
                "class": (
                    "w-full rounded-lg border border-line px-3 py-2 text-sm font-mono "
                    "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
                ),
            }
        ),
    )
    source_file = forms.FileField(
        required=False,
        # No ``accept``: keeps the native dialog fast on managed Windows; the
        # extension is validated in clean_source_file().
        widget=forms.ClearableFileInput(attrs={"class": "hidden"}),
    )

    def clean_source_file(self):
        f = self.cleaned_data.get("source_file")
        if not f:
            return f
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in ACCEPTED_SOURCE_EXTS:
            raise forms.ValidationError(
                "Unsupported file type. Upload .md (recommended), .docx or .txt."
            )
        return f

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("source_text") or "").strip()
        has_file = bool(cleaned.get("source_file"))
        if not text and not has_file:
            raise forms.ValidationError(
                "Provide a source: paste Markdown or attach a file."
            )
        return cleaned
