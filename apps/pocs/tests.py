"""Tests for the task state machine, the 'stuck in pending' fix, and derived
progress (Block A)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from datetime import timedelta

from django.utils import timezone

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.pocs.forms import TaskForm
from apps.pocs.importer import import_pocs_from_xlsx
from apps.pocs.models import (
    POC,
    FunctionalAnalysisStep,
    Phase,
    PhaseDocument,
    PhaseKind,
    POCMembership,
    Task,
    Test,
)
from apps.pocs.services import delete_poc
from apps.pocs.state_machine import can_transition_task, task_allowed_statuses

User = get_user_model()


class StateMachineTests(TestCase):
    def test_valid_and_invalid_transitions(self):
        self.assertTrue(can_transition_task("pending", "in_progress"))
        self.assertTrue(can_transition_task("in_progress", "completed"))
        self.assertTrue(can_transition_task("completed", "in_progress"))  # reopen
        self.assertTrue(can_transition_task("in_progress", "pending"))  # revert
        # pending cannot jump straight to completed (must pass in_progress)
        self.assertFalse(can_transition_task("pending", "completed"))

    def test_allowed_statuses_includes_current(self):
        allowed = task_allowed_statuses("pending")
        self.assertIn("pending", allowed)  # current must be representable
        self.assertIn("in_progress", allowed)


class StatusUpdateViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "a_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "a_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.poc = POC.objects.create(name="T", created_by=self.admin, status="active")
        POCMembership.objects.create(
            poc=self.poc, user=self.member, role_in_poc="member"
        )
        self.phase = Phase.objects.create(poc=self.poc, name="P", order=1)
        self.task = Task.objects.create(
            phase=self.phase, title="X", assigned_to=self.member, status="pending"
        )

    def test_member_can_leave_pending(self):
        """Regression: an assigned member could not move a task out of pending."""
        self.client.force_login(self.member)
        resp = self.client.post(
            reverse("pocs:task_status", args=[self.task.id]),
            {"status": "in_progress"},
        )
        self.assertEqual(resp.status_code, 200)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "in_progress")

    def test_invalid_transition_is_noop(self):
        self.client.force_login(self.member)
        self.client.post(
            reverse("pocs:task_status", args=[self.task.id]), {"status": "completed"}
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "pending")  # unchanged

    def test_completing_stamps_and_phase_completes(self):
        self.client.force_login(self.member)
        self.client.post(
            reverse("pocs:task_status", args=[self.task.id]), {"status": "in_progress"}
        )
        self.client.post(
            reverse("pocs:task_status", args=[self.task.id]), {"status": "completed"}
        )
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "completed")
        self.assertIsNotNone(self.task.completed_at)
        self.assertEqual(self.task.completed_by, self.member)
        self.phase.refresh_from_db()
        self.assertEqual(self.phase.status, "completed")


class ProgressDerivationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "p_admin", password="x", is_active=True, role=User.Role.ADMIN
        )

    def test_completed_poc_reads_100_even_without_items(self):
        poc = POC.objects.create(name="C", created_by=self.admin, status="completed")
        self.assertEqual(poc.progress_percent(), 100)  # no "completed but 0%"

    def test_progress_is_completed_phases_over_total(self):
        poc = POC.objects.create(name="D", created_by=self.admin, status="active")
        Phase.objects.create(poc=poc, name="P1", order=1, status="completed")
        Phase.objects.create(poc=poc, name="P2", order=2, status="in_progress")
        # 1 of 2 leaf phases completed → 50% (in_progress doesn't count).
        self.assertEqual(poc.progress_percent(), 50)

    def test_progress_counts_leaf_phases_not_parents(self):
        poc = POC.objects.create(name="E", created_by=self.admin, status="active")
        root = Phase.objects.create(poc=poc, name="Group", order=1)
        Phase.objects.create(poc=poc, parent=root, name="c1", order=1, status="completed")
        Phase.objects.create(poc=poc, parent=root, name="c2", order=2, status="pending")
        # Only the two leaves count (the parent is a grouping) → 1/2 = 50%.
        self.assertEqual(poc.progress_percent(), 50)


class RobustnessTests(TestCase):
    """Block B: validation of minimum fields, null assignee, safe render."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "b_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(name="T", created_by=self.admin, status="active")
        self.phase = Phase.objects.create(poc=self.poc, name="P", order=1)

    def test_blank_title_rejected(self):
        form = TaskForm(data={"title": "   ", "status": "pending"}, poc=self.poc)
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)

    def test_title_is_trimmed(self):
        form = TaskForm(data={"title": "  Survey  ", "status": "pending"}, poc=self.poc)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "Survey")

    def test_phase_renders_with_unassigned_task_and_empty_date(self):
        Task.objects.create(
            phase=self.phase, title="X", assigned_to=None, due_date=None
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("pocs:phase_detail", args=[self.phase.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Unassigned")


class DeletePOCTests(TestCase):
    """Block C: cascade delete + double-confirmation guard."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "c_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "c_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.poc = POC.objects.create(name="Doomed", created_by=self.admin, status="active")
        POCMembership.objects.create(poc=self.poc, user=self.member, role_in_poc="member")
        self.phase = Phase.objects.create(poc=self.poc, name="P", order=1)
        Task.objects.create(phase=self.phase, title="t")
        Test.objects.create(phase=self.phase, title="te")

    def test_delete_poc_cascades(self):
        delete_poc(self.poc)
        self.assertFalse(POC.objects.filter(pk=self.poc.pk).exists())
        self.assertFalse(Phase.objects.filter(poc_id=self.poc.pk).exists())
        self.assertFalse(Task.objects.filter(phase__poc_id=self.poc.pk).exists())
        self.assertFalse(Test.objects.filter(phase__poc_id=self.poc.pk).exists())

    def test_view_requires_matching_name(self):
        self.client.force_login(self.admin)
        # Wrong name → nothing deleted.
        self.client.post(reverse("pocs:delete", args=[self.poc.pk]), {"confirm_name": "nope"})
        self.assertTrue(POC.objects.filter(pk=self.poc.pk).exists())
        # Correct name → deleted.
        self.client.post(reverse("pocs:delete", args=[self.poc.pk]), {"confirm_name": "Doomed"})
        self.assertFalse(POC.objects.filter(pk=self.poc.pk).exists())

    def test_member_cannot_delete(self):
        self.client.force_login(self.member)
        resp = self.client.post(
            reverse("pocs:delete", args=[self.poc.pk]), {"confirm_name": "Doomed"}
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(POC.objects.filter(pk=self.poc.pk).exists())


class FunctionalAnalysisTests(TestCase):
    """Block E: FA phases seed one document per step (title + guidance)."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "e_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(name="FA POC", created_by=self.admin, status="active")
        self.phase = Phase.objects.create(
            poc=self.poc,
            name="Functional Analysis",
            order=1,
            kind=PhaseKind.FUNCTIONAL_ANALYSIS,
        )
        self.step = FunctionalAnalysisStep.objects.create(
            title="Login flow", description="Validate the login", order=1
        )

    def test_fa_phase_seeds_sections_from_steps(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("pocs:phase_detail", args=[self.phase.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Functional Analysis")
        self.assertContains(resp, "Login flow")   # the step seeded as a section
        self.assertContains(resp, "Documents")
        # A PhaseDocument was created for the step (visiting the page seeds it).
        doc = PhaseDocument.objects.get(phase=self.phase, source_fa_step=self.step)
        self.assertEqual(doc.title, "Login flow")
        self.assertTrue(doc.is_fa_section)


class RecordResultInheritsExpectedTests(TestCase):
    """Block F: the Record-result panel shows the Expected result as reference."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "f_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(name="F POC", created_by=self.admin, status="active")
        self.phase = Phase.objects.create(poc=self.poc, name="P", order=1)
        Test.objects.create(
            phase=self.phase, title="T", expected_result="The light turns green"
        )

    def test_expected_result_is_shown_in_record_panel(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("pocs:phase_detail", args=[self.phase.pk]))
        self.assertContains(resp, "Expected result — validate against this")
        self.assertContains(resp, "The light turns green")
        self.assertContains(resp, "Additional notes")


class TasksViewTests(TestCase):
    """Block G: role-based task views + filters."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "g_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "g_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.poc = POC.objects.create(name="G POC", created_by=self.admin, status="active")
        POCMembership.objects.create(poc=self.poc, user=self.member, role_in_poc="member")
        self.phase = Phase.objects.create(poc=self.poc, name="P", order=1)
        self.mine = Task.objects.create(phase=self.phase, title="Mine", assigned_to=self.member)
        self.other = Task.objects.create(phase=self.phase, title="Others task")
        self.overdue = Task.objects.create(
            phase=self.phase, title="Late one", assigned_to=self.member,
            due_date=timezone.localdate() - timedelta(days=3), status="in_progress",
        )

    def test_member_sees_only_assigned(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse("pocs:tasks"))
        self.assertContains(resp, "Mine")
        self.assertNotContains(resp, "Others task")

    def test_admin_is_manager_sees_all(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("pocs:tasks"))
        self.assertContains(resp, "Mine")
        self.assertContains(resp, "Others task")

    def test_overdue_filter(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse("pocs:tasks") + "?f=overdue")
        self.assertContains(resp, "Late one")
        self.assertNotContains(resp, ">Mine<")  # the non-overdue assigned task hidden


class POCCsvImportTests(TestCase):
    """Importing the newer 'PoC List' CSV schema (synonyms, DD/MM/YYYY, HTML)."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "imp_admin", password="x", is_active=True, role=User.Role.ADMIN
        )

    def _csv(self):
        return (
            b"ID,PoC Name,PoC Status,PoC Start,PoC Finish,PoC Description,Served Segments\n"
            b'9001,My CSV PoC,Ongoing,02/12/2024,15/04/2026,<p>Hello <b>world</b></p>,"[""Auto"",""Grid""]"\n'
        )

    def test_dry_run_counts_without_writing(self):
        f = SimpleUploadedFile("PoC List.csv", self._csv(), content_type="text/csv")
        stats = import_pocs_from_xlsx(f, self.admin, dry_run=True)
        self.assertEqual(stats["created"], 1)
        self.assertEqual(stats["errors"], [])
        self.assertFalse(POC.objects.filter(external_id=9001).exists())  # rolled back

    def test_csv_import_maps_fields(self):
        f = SimpleUploadedFile("PoC List.csv", self._csv(), content_type="text/csv")
        import_pocs_from_xlsx(f, self.admin, dry_run=False)
        poc = POC.objects.get(external_id=9001)
        self.assertEqual(poc.name, "My CSV PoC")
        self.assertEqual(poc.external_status, "Ongoing")
        self.assertEqual(poc.status, "active")           # Ongoing → active
        self.assertEqual(poc.execution_start.isoformat(), "2024-12-02")  # DD/MM/YYYY
        self.assertEqual(poc.end_date.isoformat(), "2026-04-15")
        self.assertEqual(poc.description, "Hello world")  # HTML flattened
        self.assertEqual(poc.customer_segment, "Auto, Grid")  # JSON list joined


class PhaseStatusClickTests(TestCase):
    """Click-to-set phase status (manual override)."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "ps_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "ps_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.poc = POC.objects.create(name="PS", created_by=self.admin, status="active")
        POCMembership.objects.create(poc=self.poc, user=self.member, role_in_poc="member")
        self.phase = Phase.objects.create(poc=self.poc, name="Milestone", order=1)

    def test_admin_sets_phase_status(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("pocs:phase_set_status", args=[self.phase.pk]), {"status": "completed"}
        )
        self.assertEqual(resp.status_code, 200)
        self.phase.refresh_from_db()
        self.assertEqual(self.phase.status, "completed")

    def test_member_cannot_set_phase_status(self):
        self.client.force_login(self.member)
        resp = self.client.post(
            reverse("pocs:phase_set_status", args=[self.phase.pk]), {"status": "completed"}
        )
        self.assertEqual(resp.status_code, 403)
        self.phase.refresh_from_db()
        self.assertEqual(self.phase.status, "pending")

    def test_completing_parent_cascades_to_subphases(self):
        child = Phase.objects.create(
            poc=self.poc, name="Sub", parent=self.phase, order=1, status="pending"
        )
        self.client.force_login(self.admin)
        self.client.post(
            reverse("pocs:phase_set_status", args=[self.phase.pk]),
            {"status": "completed"},
        )
        child.refresh_from_db()
        self.assertEqual(child.status, "completed")


class RolePermissionTests(TestCase):
    """The global role set is admin/team_member only; leadership is per-POC and
    a per-POC lead has full control inside that POC (regardless of lead_editable).
    """

    def setUp(self):
        self.admin = User.objects.create_user(
            "r_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.lead = User.objects.create_user(
            "r_lead", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.member = User.objects.create_user(
            "r_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.poc = POC.objects.create(name="R", created_by=self.admin, status="active")
        POCMembership.objects.create(poc=self.poc, user=self.lead, role_in_poc="lead")
        POCMembership.objects.create(poc=self.poc, user=self.member, role_in_poc="member")
        # A phase NOT flagged lead_editable (default) — the lead must still edit it.
        self.phase = Phase.objects.create(
            poc=self.poc, name="Locked-by-old-rules", order=1, lead_editable=False
        )

    def test_no_global_poc_lead_role(self):
        self.assertEqual(
            set(User.Role.values), {"admin", "team_member"}
        )
        self.assertFalse(hasattr(User.Role, "POC_LEAD"))

    def test_lead_can_edit_any_phase_in_their_poc(self):
        from apps.core.mixins import user_can_edit_phase

        self.assertTrue(user_can_edit_phase(self.lead, self.phase))
        self.assertFalse(user_can_edit_phase(self.member, self.phase))

    def test_lead_can_set_phase_status(self):
        self.client.force_login(self.lead)
        resp = self.client.post(
            reverse("pocs:phase_set_status", args=[self.phase.pk]),
            {"status": "completed"},
        )
        self.assertEqual(resp.status_code, 200)
        self.phase.refresh_from_db()
        self.assertEqual(self.phase.status, "completed")

    def test_lead_can_add_members(self):
        newbie = User.objects.create_user(
            "r_newbie", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.client.force_login(self.lead)
        resp = self.client.post(
            reverse("pocs:member_add", args=[self.poc.pk]),
            {"user_id": newbie.pk, "role": "member", "q": ""},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            POCMembership.objects.filter(poc=self.poc, user=newbie).exists()
        )

    def test_plain_member_cannot_add_members(self):
        self.client.force_login(self.member)
        resp = self.client.post(
            reverse("pocs:member_add", args=[self.poc.pk]),
            {"user_id": self.admin.pk, "role": "member", "q": ""},
        )
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Phase kinds: blueprint inheritance + documentation report generation
# ---------------------------------------------------------------------------
import base64
import tempfile
from io import BytesIO

from django.test import override_settings

from apps.pocs.models import (
    BasePhaseDocument,
    PhaseImage,
    PhaseTemplate,
)
from apps.pocs.services import apply_phase_templates

# A 1x1 transparent PNG (local constant — not fetched remotely).
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _minimal_docx_bytes():
    """A valid empty .docx to serve as a report template in tests."""
    from docx import Document

    buf = BytesIO()
    Document().save(buf)
    return buf.getvalue()


class PhaseKindInheritanceTests(TestCase):
    """Creating a POC inherits each node's kind, base documents and FA sections."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "k_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        doc_node = PhaseTemplate.objects.create(
            name="Docs", kind=PhaseKind.DOCUMENTATION, order=1
        )
        BasePhaseDocument.objects.create(
            phase_template=doc_node, title="Intro", content="# Hello", order=1
        )
        PhaseTemplate.objects.create(
            name="FA", kind=PhaseKind.FUNCTIONAL_ANALYSIS, order=2
        )
        FunctionalAnalysisStep.objects.create(title="Step A", order=1)

    def test_inheritance_creates_documents_and_fa_sections(self):
        poc = POC.objects.create(name="K POC", created_by=self.admin, status="active")
        apply_phase_templates(poc)

        doc_phase = poc.phases.get(name="Docs")
        self.assertEqual(doc_phase.kind, PhaseKind.DOCUMENTATION)
        self.assertEqual(doc_phase.documents.count(), 1)
        self.assertEqual(doc_phase.documents.first().title, "Intro")

        fa_phase = poc.phases.get(name="FA")
        self.assertEqual(fa_phase.kind, PhaseKind.FUNCTIONAL_ANALYSIS)
        section = fa_phase.documents.get()
        self.assertTrue(section.is_fa_section)
        self.assertEqual(section.title, "Step A")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DocumentationReportTests(TestCase):
    """A documentation report uses the global default template and embeds images."""

    def setUp(self):
        from apps.reports.models import ReportSettings

        self.admin = User.objects.create_user(
            "d_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(name="D POC", created_by=self.admin, status="active")
        self.phase = Phase.objects.create(
            poc=self.poc, name="Docs", order=1, kind=PhaseKind.DOCUMENTATION
        )
        # Global default template (no per-phase template attached).
        settings_row = ReportSettings.load()
        settings_row.default_template.save(
            "default.docx", SimpleUploadedFile("default.docx", _minimal_docx_bytes()), save=True
        )
        # An uploaded image, referenced from a document by its Markdown snippet.
        self.image = PhaseImage.objects.create(
            phase=self.phase,
            image=SimpleUploadedFile("pic.png", _PNG_1x1, content_type="image/png"),
        )
        PhaseDocument.objects.create(
            phase=self.phase,
            title="Section 1",
            content=f"Some text\n\n{self.image.markdown_snippet}\n\nMore text",
            order=1,
        )

    def test_report_generates_with_embedded_image(self):
        from docx import Document

        from apps.reports.generation import generate_phase_report_from_documents
        from apps.reports.models import GeneratedReport

        report = generate_phase_report_from_documents(self.phase, self.admin)
        self.assertEqual(
            report.status, GeneratedReport.Status.READY, report.error_message
        )
        self.assertTrue(report.output_file)

        report.output_file.open("rb")
        try:
            doc = Document(BytesIO(report.output_file.read()))
        finally:
            report.output_file.close()
        # The referenced image was embedded as an inline shape.
        self.assertGreaterEqual(len(doc.inline_shapes), 1)

    def test_report_endpoint_requires_membership(self):
        outsider = User.objects.create_user(
            "d_out", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.client.force_login(outsider)
        resp = self.client.post(
            reverse("reports:phase_documents", args=[self.phase.pk])
        )
        self.assertEqual(resp.status_code, 403)


class PhaseKindUIRenderTests(TestCase):
    """Smoke tests: the new blueprint/phase/settings pages render without errors."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "u_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(name="U POC", created_by=self.admin, status="active")
        self.doc_phase = Phase.objects.create(
            poc=self.poc, name="Docs", order=1, kind=PhaseKind.DOCUMENTATION
        )
        self.client.force_login(self.admin)

    def test_blueprint_create_form_has_kind_selector(self):
        resp = self.client.get(reverse("pocs:phase_template_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Type")
        self.assertContains(resp, "Functional Analysis")

    def test_documentation_phase_detail_renders(self):
        resp = self.client.get(reverse("pocs:phase_detail", args=[self.doc_phase.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Documents")
        self.assertContains(resp, "Add markdown document")
        self.assertContains(resp, "Images")
        # Both report options are present and clearly divided.
        self.assertContains(resp, "Upload your report")
        self.assertContains(resp, "Generate from Markdown")

    def test_report_settings_page_renders(self):
        resp = self.client.get(reverse("reports:settings"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Default report template")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhaseReportUploadTests(TestCase):
    """A finished report can be attached to a phase (kept as-is, no extraction)."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "up_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(
            name="UP POC", created_by=self.admin, status="active"
        )
        # Works regardless of kind (here: a Test phase).
        self.phase = Phase.objects.create(
            poc=self.poc, name="T", order=1, kind=PhaseKind.TEST
        )
        self.client.force_login(self.admin)

    def test_upload_creates_uploaded_report_and_audits(self):
        from apps.pocs.models import AuditLog
        from apps.reports.models import GeneratedReport

        resp = self.client.post(
            reverse("reports:phase_upload", args=[self.phase.pk]),
            {"report_file": SimpleUploadedFile("final.docx", _minimal_docx_bytes())},
        )
        self.assertEqual(resp.status_code, 302)
        report = GeneratedReport.objects.get(phase=self.phase)
        self.assertEqual(report.kind, GeneratedReport.Kind.UPLOADED)
        self.assertEqual(report.status, GeneratedReport.Status.READY)
        self.assertTrue(report.output_file)
        dl = self.client.get(reverse("reports:download", args=[report.pk]))
        self.assertEqual(dl.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(poc=self.poc, action="report_uploaded").exists()
        )

    def test_disallowed_extension_rejected(self):
        from apps.reports.models import GeneratedReport

        resp = self.client.post(
            reverse("reports:phase_upload", args=[self.phase.pk]),
            {"report_file": SimpleUploadedFile("evil.exe", b"x")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(GeneratedReport.objects.filter(phase=self.phase).exists())

    def test_test_phase_shows_tests_and_both_report_options(self):
        # A Test leaf phase now also exposes the unified Markdown + upload area.
        resp = self.client.get(reverse("pocs:phase_detail", args=[self.phase.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Add test")
        self.assertContains(resp, "Upload your report")
        self.assertContains(resp, "Generate from Markdown")
        self.assertContains(resp, "Add markdown document")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ReportDeletionTests(TestCase):
    """Generated reports can be removed (with permission) and it's audited."""

    def setUp(self):
        from apps.reports.models import GeneratedReport

        self.admin = User.objects.create_user(
            "rd_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "rd_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.poc = POC.objects.create(
            name="RD POC", created_by=self.admin, status="active"
        )
        POCMembership.objects.create(
            poc=self.poc, user=self.member, role_in_poc=POCMembership.Role.MEMBER
        )
        self.report = GeneratedReport.objects.create(
            kind=GeneratedReport.Kind.PHASE,
            title="A report",
            poc=self.poc,
            requested_by=self.admin,
            status=GeneratedReport.Status.READY,
        )
        self.report.output_file.save(
            "r.docx", SimpleUploadedFile("r.docx", _minimal_docx_bytes()), save=True
        )

    def test_admin_can_delete_and_it_is_audited(self):
        from apps.pocs.models import AuditLog
        from apps.reports.models import GeneratedReport

        self.client.force_login(self.admin)
        resp = self.client.post(reverse("reports:delete", args=[self.report.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(GeneratedReport.objects.filter(pk=self.report.pk).exists())
        self.assertTrue(
            AuditLog.objects.filter(poc=self.poc, action="report_deleted").exists()
        )

    def test_plain_member_cannot_delete(self):
        from apps.reports.models import GeneratedReport

        self.client.force_login(self.member)
        resp = self.client.post(reverse("reports:delete", args=[self.report.pk]))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(GeneratedReport.objects.filter(pk=self.report.pk).exists())


class TaskOnAnyPhaseTests(TestCase):
    """Tasks can be added to any phase, including a parent with sub-phases."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "tap_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(
            name="TAP POC", created_by=self.admin, status="active"
        )
        self.parent = Phase.objects.create(poc=self.poc, name="Parent", order=1)
        self.child = Phase.objects.create(
            poc=self.poc, name="Child", parent=self.parent, order=1
        )
        self.client.force_login(self.admin)

    def test_can_create_task_on_parent_phase(self):
        self.assertFalse(self.parent.is_leaf)
        resp = self.client.post(
            reverse("pocs:task_create", args=[self.parent.pk]),
            {"title": "Parent task", "status": "pending"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self.parent.tasks.filter(title="Parent task").exists())

    def test_parent_phase_detail_shows_tasks_section(self):
        resp = self.client.get(reverse("pocs:phase_detail", args=[self.parent.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sub-phases")
        self.assertContains(resp, "Add task")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PhaseTemplateAndZipTests(TestCase):
    """Zip uploads, downloadable phase templates, and .zip-template fallback."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "pt_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(
            name="PT POC", created_by=self.admin, status="active"
        )
        self.phase = Phase.objects.create(
            poc=self.poc, name="Docs", order=1, kind=PhaseKind.DOCUMENTATION
        )
        self.client.force_login(self.admin)

    def test_zip_upload_is_accepted(self):
        from apps.reports.models import GeneratedReport

        resp = self.client.post(
            reverse("reports:phase_upload", args=[self.phase.pk]),
            {"report_file": SimpleUploadedFile("bundle.zip", b"PK\x03\x04zip")},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            GeneratedReport.objects.filter(
                phase=self.phase, kind=GeneratedReport.Kind.UPLOADED
            ).exists()
        )

    def test_download_phase_template(self):
        self.phase.report_template.save(
            "tpl.docx", SimpleUploadedFile("tpl.docx", _minimal_docx_bytes()), save=True
        )
        resp = self.client.get(
            reverse("pocs:phase_template_download", args=[self.phase.pk])
        )
        self.assertEqual(resp.status_code, 200)

    def test_zip_template_falls_back_to_default_for_generation(self):
        from apps.reports.generation import generate_phase_report_from_documents
        from apps.reports.models import GeneratedReport, ReportSettings

        # Phase template is a .zip (download-only); a global .docx default exists.
        self.phase.report_template.save(
            "bundle.zip", SimpleUploadedFile("bundle.zip", b"PK\x03\x04"), save=True
        )
        ReportSettings.load().default_template.save(
            "def.docx", SimpleUploadedFile("def.docx", _minimal_docx_bytes()), save=True
        )
        PhaseDocument.objects.create(
            phase=self.phase, title="Sec", content="body", order=1
        )
        report = generate_phase_report_from_documents(self.phase, self.admin)
        self.assertEqual(
            report.status, GeneratedReport.Status.READY, report.error_message
        )

    def test_download_template_requires_membership(self):
        self.phase.report_template.save(
            "tpl.docx", SimpleUploadedFile("tpl.docx", _minimal_docx_bytes()), save=True
        )
        outsider = User.objects.create_user(
            "pt_out", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )
        self.client.force_login(outsider)
        resp = self.client.get(
            reverse("pocs:phase_template_download", args=[self.phase.pk])
        )
        self.assertEqual(resp.status_code, 403)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TemplateDownloadFallbackTests(TestCase):
    """Download template applies to any phase/kind; falls back to the default."""

    def setUp(self):
        from apps.reports.models import ReportSettings

        self.admin = User.objects.create_user(
            "td_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.poc = POC.objects.create(
            name="TD POC", created_by=self.admin, status="active"
        )
        ReportSettings.load().default_template.save(
            "def.docx", SimpleUploadedFile("def.docx", _minimal_docx_bytes()), save=True
        )
        self.client.force_login(self.admin)

    def test_download_falls_back_to_default_on_test_phase(self):
        # A Test phase with NO own template still offers the default for download.
        phase = Phase.objects.create(
            poc=self.poc, name="T", order=1, kind=PhaseKind.TEST
        )
        resp = self.client.get(reverse("pocs:phase_detail", args=[phase.pk]))
        self.assertContains(resp, "Download template")
        dl = self.client.get(
            reverse("pocs:phase_template_download", args=[phase.pk])
        )
        self.assertEqual(dl.status_code, 200)

    def test_download_shown_on_parent_phase_too(self):
        parent = Phase.objects.create(poc=self.poc, name="Parent", order=1)
        Phase.objects.create(poc=self.poc, name="Child", parent=parent, order=1)
        resp = self.client.get(reverse("pocs:phase_detail", args=[parent.pk]))
        self.assertContains(resp, "Download template")
