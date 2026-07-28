"""Core forms: site branding (logo upload), database restore (admin-only)."""

from django import forms

from .models import SiteBranding


class BrandingForm(forms.ModelForm):
    class Meta:
        model = SiteBranding
        fields = ("logo",)
        # Hidden input — the drag-and-drop zone is the UI (no native dialog).
        widgets = {"logo": forms.ClearableFileInput(attrs={"class": "hidden"})}


class DatabaseRestoreForm(forms.Form):
    """Upload a backup file to restore — destructive, so a typed confirmation
    is required in addition to the file (mirrors POCDeleteView's
    type-the-name pattern elsewhere in the app)."""

    file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "hidden"}))
    confirm = forms.CharField(
        required=True,
        label='Type "RESTORE" to confirm',
        widget=forms.TextInput(attrs={
            "class": "rounded-lg border border-line px-3 py-2 text-sm w-full "
                     "focus:outline-none focus:ring-2 focus:ring-danger/40 focus:border-danger",
            "placeholder": "RESTORE",
            "autocomplete": "off",
        }),
    )

    def clean_confirm(self):
        value = self.cleaned_data["confirm"]
        if value.strip().upper() != "RESTORE":
            raise forms.ValidationError('Type "RESTORE" (all caps) to confirm.')
        return value
