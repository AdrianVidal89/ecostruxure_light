"""POC URL configuration."""

from django.urls import path

from . import views

app_name = "pocs"

urlpatterns = [
    path("", views.POCListView.as_view(), name="list"),
    path("tasks/", views.TasksView.as_view(), name="tasks"),
    path("import/", views.POCImportView.as_view(), name="import"),
    path("create/", views.POCCreateView.as_view(), name="create"),
    path("<int:pk>/", views.POCDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.POCUpdateView.as_view(), name="edit"),
    path("<int:pk>/archive/", views.POCArchiveView.as_view(), name="archive"),
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
    path(
        "tests/<int:test_pk>/delete/",
        views.TestDeleteView.as_view(),
        name="test_delete",
    ),
    path("tests/<int:test_pk>/execute/", views.test_execute, name="test_execute"),
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
