"""
Forms for authentication, self-service profile editing, and admin user
management. All widgets are styled consistently with the Schneider-themed
Tailwind input classes via :func:`_style_fields`.
"""

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
    UserCreationForm,
)

User = get_user_model()

# Shared Tailwind classes for form controls.
INPUT_CLASS = (
    "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink "
    "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
)
CHECKBOX_CLASS = "h-4 w-4 rounded border-line text-brand focus:ring-brand/40"


def _style_fields(fields):
    """Apply consistent Tailwind styling to a form's bound widgets."""
    for field in fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault("class", CHECKBOX_CLASS)
        else:
            widget.attrs.setdefault("class", INPUT_CLASS)


class StyledAuthenticationForm(AuthenticationForm):
    """Login form with themed widgets and friendly placeholders."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self.fields)
        self.fields["username"].widget.attrs["placeholder"] = "Username"
        self.fields["password"].widget.attrs["placeholder"] = "Password"


class StyledPasswordChangeForm(PasswordChangeForm):
    """Themed password-change form (used on the self-service page)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self.fields)


class ProfileForm(forms.ModelForm):
    """Self-service profile editing.

    Users may change their own name and email. ``role`` is intentionally
    excluded — it is read-only for non-admins and managed via the admin user
    UI, per the spec.
    """

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self.fields)


class AdminUserCreateForm(UserCreationForm):
    """Admin-only form to create a user with a global role and initial password."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "role")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self.fields)


class AdminUserUpdateForm(forms.ModelForm):
    """Admin-only form to edit a user's details, role, and active status.

    Passwords are managed separately (Django admin or a reset flow), so they are
    not exposed here.
    """

    class Meta:
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style_fields(self.fields)


class UserImportUploadForm(forms.Form):
    """Step 1 of the bulk user import: upload an .xlsx or .csv file."""

    file = forms.FileField(widget=forms.ClearableFileInput(attrs={"class": "hidden"}))

    def clean_file(self):
        f = self.cleaned_data["file"]
        ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext not in {"xlsx", "csv", "txt"}:
            raise forms.ValidationError("Upload an .xlsx or .csv file.")
        return f
