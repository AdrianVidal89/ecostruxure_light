"""Tests for the bulk user importer (Block D): preview classification (no DB
writes), commit upsert, and admin gating of the import view."""

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.accounts import importer

User = get_user_model()

HEADERS = ["User", "Email", "First", "Last", "Role"]
MAPPING = {
    "username": 0,
    "email": 1,
    "first_name": 2,
    "last_name": 3,
    "role": 4,
    "is_active": None,
    "date_joined": None,
}


class PreviewTests(TestCase):
    def setUp(self):
        # An existing user → import should flag as "update", not duplicate.
        User.objects.create_user("carol", email="carol@x.com", is_active=True)

    def _rows(self):
        return [
            ["alice", "alice@x.com", "Alice", "A", "admin"],      # valid, new
            ["bob", "not-an-email", "Bob", "B", "weirdrole"],     # warnings
            ["", "", "", "", ""],                                  # discarded (no id)
            ["alice", "alice2@x.com", "Al", "A", "member"],        # discarded (dup)
            ["carol", "carol@x.com", "Carol", "C", "member"],      # warning (update)
        ]

    def test_preview_classifies_rows_without_writing(self):
        before = User.objects.count()
        preview, summary = importer.build_preview(HEADERS, self._rows(), MAPPING)
        self.assertEqual(User.objects.count(), before)  # nothing written
        self.assertEqual(summary["valid"], 1)       # alice
        self.assertEqual(summary["warning"], 2)     # bob, carol
        self.assertEqual(summary["discarded"], 2)   # empty + duplicate
        # bob: invalid email dropped + unknown role → team_member
        bob = next(r for r in preview if r["data"]["username"] == "bob")
        self.assertEqual(bob["data"]["email"], "")
        self.assertEqual(bob["data"]["role"], User.Role.TEAM_MEMBER)
        carol = next(r for r in preview if r["data"]["username"] == "carol")
        self.assertEqual(carol["action"], "update")

    def test_commit_creates_updates_skips(self):
        preview, _ = importer.build_preview(HEADERS, self._rows(), MAPPING)
        stats = importer.commit_users(preview, initial_password="")
        self.assertEqual(stats["created"], 2)   # alice, bob
        self.assertEqual(stats["updated"], 1)   # carol
        self.assertEqual(stats["skipped"], 2)
        alice = User.objects.get(username="alice")
        self.assertEqual(alice.role, User.Role.ADMIN)
        self.assertFalse(alice.has_usable_password())  # no initial password

    def test_commit_with_initial_password(self):
        preview, _ = importer.build_preview(
            HEADERS, [["dave", "dave@x.com", "Dave", "D", "member"]], MAPPING
        )
        importer.commit_users(preview, initial_password="Start!123")
        self.assertTrue(User.objects.get(username="dave").check_password("Start!123"))


class ImportViewAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "d_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "d_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )

    def test_admin_can_open_import(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("accounts:user_import")).status_code, 200)

    def test_member_forbidden(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("accounts:user_import")).status_code, 403)

    def test_full_flow_upload_preview_confirm(self):
        self.client.force_login(self.admin)
        csv = b"User,Email,Role\nzoe,zoe@x.com,member\n"
        upload = SimpleUploadedFile("u.csv", csv, content_type="text/csv")
        r = self.client.post(reverse("accounts:user_import"), {"file": upload})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Preview")  # reached the preview stage
        self.assertFalse(User.objects.filter(username="zoe").exists())  # not yet written
        # Confirm with the mapping (User=0, Email=1, Role=2).
        self.client.post(
            reverse("accounts:user_import"),
            {"action": "confirm", "map_username": "0", "map_email": "1", "map_role": "2"},
        )
        self.assertTrue(User.objects.filter(username="zoe", role="team_member").exists())


class ImportRoleOverrideTests(TestCase):
    """Editing a row's role in the preview is honoured at confirm."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "ovr_admin", password="x", is_active=True, role=User.Role.ADMIN
        )

    def test_role_override_applied_on_confirm(self):
        self.client.force_login(self.admin)
        csv = b"User,Email,Role\nzoe,zoe@x.com,member\n"
        upload = SimpleUploadedFile("u.csv", csv, content_type="text/csv")
        self.client.post(reverse("accounts:user_import"), {"file": upload})  # → preview
        # Confirm, overriding zoe's role (data row is line 2 → index 2).
        self.client.post(
            reverse("accounts:user_import"),
            {
                "action": "confirm",
                "map_username": "0",
                "map_email": "1",
                "map_role": "2",
                "role_2": "admin",
            },
        )
        self.assertEqual(User.objects.get(username="zoe").role, "admin")


class BrandingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            "br_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "br_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )

    def test_admin_can_open_and_member_cannot(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("core:branding")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("core:branding")).status_code, 200)

    def test_upload_sets_logo(self):
        from apps.core.models import SiteBranding

        self.client.force_login(self.admin)
        logo = SimpleUploadedFile("logo.png", b"\x89PNG\r\n\x1a\n fake", content_type="image/png")
        self.client.post(reverse("core:branding"), {"logo": logo})
        self.assertTrue(SiteBranding.load().logo)
        # Cleanup the uploaded file.
        b = SiteBranding.load()
        b.logo.delete(save=False)
