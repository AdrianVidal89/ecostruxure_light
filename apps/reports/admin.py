"""Django admin registration for reports — debug/maintenance only."""

from django.contrib import admin

from .models import GeneratedReport, ReportType


@admin.register(ReportType)
class ReportTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(GeneratedReport)
class GeneratedReportAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "status", "poc", "requested_by", "requested_at")
    list_filter = ("kind", "status")
    search_fields = ("title",)
    readonly_fields = ("requested_at",)
