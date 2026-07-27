"""POC URL configuration."""

from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "pocs"

urlpatterns = [
    # The POCs list is merged into the unified Dashboard; keep the name so
    # existing links/redirects resolve, but send users to the dashboard.
    path(
        "",
        RedirectView.as_view(pattern_name="core:dashboard", permanent=False),
        name="list",
    ),
    path("tasks/", views.TasksView.as_view(), name="tasks"),
    path("tests/", views.TestsView.as_view(), name="tests_overview"),
    path("validations/", views.ValidationListView.as_view(), name="validations"),
    path(
        "validations/<int:validation_pk>/",
        views.validation_detail,
        name="validation_detail",
    ),
    path(
        "validations/<int:validation_pk>/decide/",
        views.validation_decide,
        name="validation_decide",
    ),
    path("comments/", views.CommentListView.as_view(), name="comments"),
    path(
        "comments/<int:comment_pk>/decide/",
        views.comment_decide,
        name="comment_decide",
    ),
    path(
        "comments/<str:model>/<int:pk>/create/",
        views.comment_create,
        name="comment_create",
    ),
    path("import/", views.POCImportView.as_view(), name="import"),
    path("create/", views.POCCreateView.as_view(), name="create"),
    path("<int:pk>/", views.POCDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.POCUpdateView.as_view(), name="edit"),
    path("<int:pk>/graph/", views.poc_graph_data, name="poc_graph_data"),
    # --- Requirements & Use Cases (spec Fase 3c) ---
    path("<int:pk>/specs/", views.poc_specs_redirect, name="specs"),
    path(
        "<int:pk>/requirements/create/",
        views.RequirementCreateView.as_view(),
        name="requirement_create",
    ),
    path(
        "<int:pk>/requirements/import/",
        views.RequirementImportView.as_view(),
        name="requirement_import",
    ),
    path(
        "<int:pk>/requirements/import/template/",
        views.requirement_import_template,
        name="requirement_import_template",
    ),
    path(
        "<int:pk>/requirements/export/",
        views.requirement_export,
        name="requirement_export",
    ),
    path(
        "<int:pk>/requirements/bulk-status/",
        views.requirement_bulk_status,
        name="requirement_bulk_status",
    ),
    path(
        "<int:pk>/requirements/bulk-delete/",
        views.requirement_bulk_delete,
        name="requirement_bulk_delete",
    ),
    path(
        "requirements/<int:pk>/edit/",
        views.RequirementUpdateView.as_view(),
        name="requirement_edit",
    ),
    path(
        "requirements/<int:pk>/delete/",
        views.RequirementDeleteView.as_view(),
        name="requirement_delete",
    ),
    path(
        "requirements/<int:pk>/preview/",
        views.requirement_preview,
        name="requirement_preview",
    ),
    path(
        "requirements/<int:pk>/set-gravity/",
        views.requirement_set_gravity,
        name="requirement_set_gravity",
    ),
    path(
        "requirements/<int:pk>/set-field/<str:field>/",
        views.requirement_set_field,
        name="requirement_set_field",
    ),
    path(
        "requirements/<int:pk>/",
        views.RequirementDetailView.as_view(),
        name="requirement_detail",
    ),
    path(
        "<int:pk>/use-cases/create/",
        views.UseCaseCreateView.as_view(),
        name="usecase_create",
    ),
    path(
        "<int:pk>/use-cases/import/",
        views.UseCaseImportView.as_view(),
        name="usecase_import",
    ),
    path(
        "<int:pk>/use-cases/import/template/",
        views.usecase_import_template,
        name="usecase_import_template",
    ),
    path(
        "<int:pk>/use-cases/export/",
        views.usecase_export,
        name="usecase_export",
    ),
    path(
        "<int:pk>/use-cases/bulk-status/",
        views.usecase_bulk_status,
        name="usecase_bulk_status",
    ),
    path(
        "<int:pk>/use-cases/bulk-delete/",
        views.usecase_bulk_delete,
        name="usecase_bulk_delete",
    ),
    path(
        "use-cases/<int:pk>/",
        views.UseCaseDetailView.as_view(),
        name="usecase_detail",
    ),
    path(
        "use-cases/<int:pk>/preview/",
        views.usecase_preview,
        name="usecase_preview",
    ),
    path(
        "use-cases/<int:pk>/set-status/",
        views.usecase_set_status,
        name="usecase_set_status",
    ),
    path(
        "use-cases/<int:pk>/edit/",
        views.UseCaseUpdateView.as_view(),
        name="usecase_edit",
    ),
    path(
        "use-cases/<int:pk>/delete/",
        views.UseCaseDeleteView.as_view(),
        name="usecase_delete",
    ),
    path("<int:pk>/archive/", views.POCArchiveView.as_view(), name="archive"),
    path("<int:pk>/close/", views.POCCloseView.as_view(), name="close"),
    path(
        "<int:pk>/images/upload/",
        views.POCImageUploadView.as_view(),
        name="poc_image_upload",
    ),
    path("<int:pk>/reopen/", views.poc_reopen, name="reopen"),
    path("<int:pk>/delete/", views.POCDeleteView.as_view(), name="delete"),
    # --- Member assignment (admin-only, HTMX) ---
    path("<int:pk>/members/search/", views.member_search, name="member_search"),
    path("<int:pk>/members/add/", views.member_add, name="member_add"),
    path(
        "<int:pk>/members/<int:membership_pk>/role/",
        views.member_role,
        name="member_role",
    ),
    path(
        "<int:pk>/members/<int:membership_pk>/remove/",
        views.member_remove,
        name="member_remove",
    ),
    # --- Phase blueprint builder (admin only, global) ---
    path("phase-templates/", views.PhaseTemplateListView.as_view(), name="phase_template_list"),
    path("phase-templates/apply-all/", views.phase_template_apply_all, name="phase_template_apply_all"),
    path("phase-templates/apply-selected/", views.phase_template_apply_selected, name="phase_template_apply_selected"),
    path("phase-templates/undo/", views.blueprint_undo, name="blueprint_undo"),
    path("phase-templates/restore/<int:version_pk>/", views.blueprint_restore, name="blueprint_restore"),
    path("phase-templates/create/", views.PhaseTemplateCreateView.as_view(), name="phase_template_create"),
    path("phase-templates/<int:parent_pk>/child/", views.PhaseTemplateCreateView.as_view(), name="phase_template_child"),
    path("phase-templates/<int:pk>/edit/", views.PhaseTemplateUpdateView.as_view(), name="phase_template_edit"),
    path("phase-templates/<int:pk>/delete/", views.PhaseTemplateDeleteView.as_view(), name="phase_template_delete"),
    path("phase-templates/<int:pk>/base-task/", views.BaseTaskCreateView.as_view(), name="base_task_create"),
    path("phase-templates/<int:pk>/base-test/", views.BaseTestCreateView.as_view(), name="base_test_create"),
    path("phase-templates/<int:pk>/base-document/", views.BasePhaseDocumentCreateView.as_view(), name="base_document_create"),
    path("base-tasks/<int:pk>/delete/", views.BaseTaskDeleteView.as_view(), name="base_task_delete"),
    path("base-tests/<int:pk>/delete/", views.BaseTestDeleteView.as_view(), name="base_test_delete"),
    path("base-documents/<int:pk>/delete/", views.BasePhaseDocumentDeleteView.as_view(), name="base_document_delete"),
    # --- Functional Analysis template (admin) ---
    path("functional-analysis/", views.FAStepListView.as_view(), name="fa_step_list"),
    path("functional-analysis/create/", views.FAStepCreateView.as_view(), name="fa_step_create"),
    path("functional-analysis/<int:pk>/edit/", views.FAStepUpdateView.as_view(), name="fa_step_edit"),
    path("functional-analysis/<int:pk>/delete/", views.FAStepDeleteView.as_view(), name="fa_step_delete"),
    # --- Phases (per-POC tree) ---
    path("<int:pk>/phases/create/", views.PhaseCreateView.as_view(), name="phase_create"),
    path("<int:pk>/phases/reorder/", views.phase_reorder, name="phase_reorder"),
    path("phases/<int:phase_pk>/set-status/", views.phase_set_status, name="phase_set_status"),
    path("phases/<int:phase_pk>/approve/", views.phase_approve, name="phase_approve"),
    path("phases/<int:phase_pk>/unlock/", views.phase_unlock, name="phase_unlock"),
    path(
        "phases/<int:phase_pk>/subphase/create/",
        views.SubPhaseCreateView.as_view(),
        name="subphase_create",
    ),
    path(
        "phases/<int:phase_pk>/edit/",
        views.PhaseUpdateView.as_view(),
        name="phase_edit",
    ),
    path(
        "phases/<int:phase_pk>/delete/",
        views.PhaseDeleteView.as_view(),
        name="phase_delete",
    ),
    path(
        "phases/<int:phase_pk>/mark-na/",
        views.PhaseMarkNAView.as_view(),
        name="phase_mark_na",
    ),
    path(
        "phases/<int:phase_pk>/unmark-na/",
        views.phase_unmark_na,
        name="phase_unmark_na",
    ),
    path(
        "phases/<int:phase_pk>/",
        views.PhaseDetailView.as_view(),
        name="phase_detail",
    ),
    # --- Tasks ---
    path(
        "phases/<int:phase_pk>/tasks/create/",
        views.TaskCreateView.as_view(),
        name="task_create",
    ),
    path("tasks/<int:task_pk>/edit/", views.TaskUpdateView.as_view(), name="task_edit"),
    path(
        "tasks/<int:task_pk>/delete/",
        views.TaskDeleteView.as_view(),
        name="task_delete",
    ),
    path("tasks/<int:task_pk>/status/", views.task_set_status, name="task_status"),
    path("tasks/<int:task_pk>/notes/", views.task_set_notes, name="task_notes"),
    path(
        "phases/<int:phase_pk>/tasks/bulk-status/",
        views.task_bulk_status,
        name="task_bulk_status",
    ),
    # --- Tests ---
    path(
        "phases/<int:phase_pk>/tests/create/",
        views.TestCreateView.as_view(),
        name="test_create",
    ),
    path("tests/<int:test_pk>/edit/", views.TestUpdateView.as_view(), name="test_edit"),
    path("tests/<int:pk>/preview/", views.test_preview, name="test_preview"),
    path("tests/<int:pk>/", views.TestDetailView.as_view(), name="test_detail"),
    path(
        "tests/<int:test_pk>/delete/",
        views.TestDeleteView.as_view(),
        name="test_delete",
    ),
    path("tests/<int:test_pk>/execute/", views.test_execute, name="test_execute"),
    path(
        "tests/<int:test_pk>/requirements/",
        views.test_link_requirements,
        name="test_link_requirements",
    ),
    path(
        "tests/<int:test_pk>/parameters/",
        views.test_parameters_save,
        name="test_parameters_save",
    ),
    # --- Phase documents (Documentation / Functional Analysis) ---
    path(
        "phases/<int:phase_pk>/documents/create/",
        views.PhaseDocumentCreateView.as_view(),
        name="document_create",
    ),
    path(
        "documents/<int:document_pk>/edit/",
        views.PhaseDocumentUpdateView.as_view(),
        name="document_edit",
    ),
    path(
        "documents/<int:document_pk>/delete/",
        views.PhaseDocumentDeleteView.as_view(),
        name="document_delete",
    ),
    path(
        "documents/<int:document_pk>/preview/",
        views.fa_section_preview,
        name="fa_section_preview",
    ),
    # --- Phase images ---
    path(
        "phases/<int:phase_pk>/images/upload/",
        views.PhaseImageUploadView.as_view(),
        name="image_upload",
    ),
    path(
        "images/<int:image_pk>/delete/",
        views.PhaseImageDeleteView.as_view(),
        name="image_delete",
    ),
    path(
        "phases/<int:phase_pk>/template/download/",
        views.phase_template_download,
        name="phase_template_download",
    ),
]
