"""Core forms: site branding (logo upload)."""

from django import forms

from .models import SiteBranding


class BrandingForm(forms.ModelForm):
    class Meta:
        model = SiteBranding
        fields = ("logo",)
        # Hidden input — the drag-and-drop zone is the UI (no native dialog).
        widgets = {"logo": forms.ClearableFileInput(attrs={"class": "hidden"})}
