"""
Report models.

Two report flows:

* **Phase report** — generated from a Phase's tests using the ``.docx``
  template attached to that phase (``Phase.report_template``). The "test data
  → Word" path.

* **Custom report** — an admin defines a named :class:`ReportType` with a
  ``.docx`` template (e.g. "Functional Analysis"); any user then picks a type,
  uploads a source document (Markdown/Word/text) and the system converts it
  into that template. The "bring-your-own-content → Word" path.

Both record a :class:`GeneratedReport` with the produced ``output_file``.
"""

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

USER = settings.AUTH_USER_MODEL


class ReportSettings(models.Model):
    """Singleton holding the global default report template.

    Used as the fallback when a phase has no ``report_template`` of its own (so
    Documentation / Functional Analysis phases can still produce a report).
    """

    default_template = models.FileField(
        upload_to="report_templates/default/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(["docx"])],
        help_text="Default Word template (.docx) for phases without their own.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "report settings"
        verbose_name_plural = "report settings"

    def __str__(self):
        return "Report settings"

    @classmethod
    def load(cls):
        """Return the single settings row, creating it if needed."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)


class ReportType(models.Model):
    """An admin-defined custom report type with a Word template.

    Available to all users in the global Reports area. The attached ``.docx``
    is the reference template whose cover/TOC/styles the generated report
    inherits.
    """

    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    template = models.FileField(
        upload_to="report_templates/types/",
        validators=[FileExtensionValidator(["docx"])],
        help_text="Word template (.docx) used as the reference document.",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, related_name="report_types"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class GeneratedReport(models.Model):
    """A produced report (phase or custom), with its output .docx."""

    class Kind(models.TextChoices):
        PHASE = "phase", "Phase report"
        CUSTOM = "custom", "Custom report"
        UPLOADED = "uploaded", "Uploaded report"
        FINAL = "final", "Final report"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        ERROR = "error", "Error"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    title = models.CharField(max_length=255)

    # Phase reports are tied to a POC + phase; custom reports to a report type
    # (and optionally a POC, when generated from within one).
    poc = models.ForeignKey(
        "pocs.POC",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="generated_reports",
    )
    phase = models.ForeignKey(
        "pocs.Phase",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_reports",
    )
    report_type = models.ForeignKey(
        ReportType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_reports",
    )

    source_file = models.FileField(
        upload_to="reports/source/%Y/%m/", null=True, blank=True
    )
    output_file = models.FileField(
        upload_to="reports/output/%Y/%m/", null=True, blank=True
    )

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING
    )
    error_message = models.TextField(blank=True)

    requested_by = models.ForeignKey(
        USER, on_delete=models.SET_NULL, null=True, related_name="generated_reports"
    )
    requested_at = models.DateTimeField(auto_now_add=True)

    # Digital signature for a "late" document (spec Fase 6b): set when the report
    # is created after the owning POC's official closure.
    after_closure = models.BooleanField(default=False)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.title} ({self.get_kind_display()})"
