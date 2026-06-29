"""Core models: site-wide branding (a single configurable row)."""

from django.core.validators import FileExtensionValidator
from django.db import models


class SiteBranding(models.Model):
    """Singleton holding the uploaded logo. When set, it replaces the default
    "E" mark in the sidebar and on the login page."""

    logo = models.FileField(
        upload_to="branding/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg", "svg", "webp", "gif"])],
        help_text="Logo image (PNG/JPG/SVG/WEBP/GIF).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "site branding"
        verbose_name_plural = "site branding"

    def __str__(self):
        return "Site branding"

    @classmethod
    def load(cls):
        """Return the single branding row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)
