"""
Django admin registration — DEBUG / maintenance inspection only.

The user-facing UI for all of these models is built with custom views in later
steps (POC CRUD, phases, tasks, tests, audit log). These registrations exist so
developers can inspect and seed data during development.
"""

from django.contrib import admin

from .models import (
    POC,
    AuditLog,
    BaseTask,
    BaseTest,
    Phase,
    PhaseTemplate,
    POCMembership,
    Task,
    Test,
)


class BaseTaskInline(admin.TabularInline):
    model = BaseTask
    extra = 0


class BaseTestInline(admin.TabularInline):
    model = BaseTest
    extra = 0


@admin.register(PhaseTemplate)
class PhaseTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "order", "lead_editable", "report_template")
    list_filter = ("lead_editable",)
    search_fields = ("name",)
    inlines = [BaseTaskInline, BaseTestInline]


class POCMembershipInline(admin.TabularInline):
    model = POCMembership
    extra = 1
    autocomplete_fields = ["user"]


@admin.register(POC)
class POCAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "start_date", "end_date", "created_by")
    list_filter = ("status",)
    search_fields = ("name", "description")
    inlines = [POCMembershipInline]
    date_hierarchy = "created_at"


@admin.register(Phase)
class PhaseAdmin(admin.ModelAdmin):
    list_display = ("name", "poc", "parent", "order", "status", "lead_editable")
    list_filter = ("status", "poc", "lead_editable")
    search_fields = ("name",)
    ordering = ("poc", "order")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "phase", "status", "assigned_to", "due_date")
    list_filter = ("status", "phase__poc")
    search_fields = ("title", "description")
    autocomplete_fields = ["assigned_to", "completed_by"]


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("title", "phase", "verdict", "assigned_to", "executed_at")
    list_filter = ("verdict", "phase__poc")
    search_fields = ("title", "description")
    autocomplete_fields = ["assigned_to", "executed_by"]


@admin.register(POCMembership)
class POCMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "poc", "role_in_poc")
    list_filter = ("role_in_poc",)
    autocomplete_fields = ["user", "poc"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "content_type", "object_id", "actor", "timestamp")
    list_filter = ("action", "content_type")
    readonly_fields = ("content_type", "object_id", "action", "actor", "timestamp", "details")
