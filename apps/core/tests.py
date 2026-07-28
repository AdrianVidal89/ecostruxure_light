"""
Tests for the admin-only database backup/restore feature.

``DbBackupHelperTests`` exercises the actual SQLite backup/restore mechanics
against an independent temporary file (never the live Django test database —
see the module docstring notes below) using plain ``unittest.TestCase`` so
nothing here touches Django's own test-database transaction machinery.

``DatabaseBackupViewTests`` covers the HTTP layer (permissions, plumbing)
with the backup/restore mechanics mocked out, since the real functions read
``settings.DATABASES`` directly and the live test DB is an in-memory SQLite
URI, not a plain file path.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core import db_backup

User = get_user_model()


def _make_sqlite_file(with_migrations_table=True):
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    conn = sqlite3.connect(path)
    try:
        if with_migrations_table:
            conn.execute("CREATE TABLE django_migrations (id integer primary key)")
        conn.execute("CREATE TABLE dummy (id integer primary key, value text)")
        conn.execute("INSERT INTO dummy (value) VALUES ('hello')")
        conn.commit()
    finally:
        conn.close()
    return path


class DbBackupHelperTests(unittest.TestCase):
    """Plain unittest.TestCase — deliberately NOT django.test.TestCase, so
    nothing here runs inside (or interferes with) Django's own test-database
    transaction/savepoint machinery. Every DB file used is a real temp file
    fully independent of the actual Django test run's in-memory database."""

    def setUp(self):
        self.src_path = _make_sqlite_file()
        self.addCleanup(lambda: os.path.exists(self.src_path) and os.unlink(self.src_path))

    def _patched_name(self, path):
        return mock.patch.dict(
            "django.conf.settings.DATABASES", {"default": {**db_backup.settings.DATABASES["default"], "NAME": path}}
        )

    def test_sqlite_dump_bytes_produces_a_valid_sqlite_file(self):
        with self._patched_name(self.src_path):
            data = db_backup._sqlite_dump_bytes()
        self.assertTrue(data.startswith(db_backup.SQLITE_MAGIC))
        # The dumped bytes are a fully working, independent SQLite database.
        fd, dump_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        try:
            with open(dump_path, "wb") as f:
                f.write(data)
            conn = sqlite3.connect(dump_path)
            try:
                row = conn.execute("SELECT value FROM dummy").fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], "hello")
        finally:
            os.unlink(dump_path)

    def test_restore_rejects_non_sqlite_upload_without_side_effects(self):
        upload = SimpleUploadedFile("not_a_db.txt", b"hello world", content_type="text/plain")
        with self._patched_name(self.src_path):
            with mock.patch.object(db_backup, "save_safety_backup") as safety:
                with self.assertRaises(db_backup.RestoreError):
                    db_backup.restore_backup(upload)
                # An invalid upload never triggers a (pointless, and here
                # unmockable-for-real) safety backup of the current DB.
                safety.assert_not_called()

    def test_restore_rejects_sqlite_file_missing_migrations_table(self):
        other_path = _make_sqlite_file(with_migrations_table=False)
        try:
            with open(other_path, "rb") as f:
                data = f.read()
            upload = SimpleUploadedFile("weird.sqlite3", data, content_type="application/octet-stream")
            with self._patched_name(self.src_path):
                with mock.patch.object(db_backup, "save_safety_backup", return_value="/tmp/whatever"):
                    with mock.patch.object(db_backup, "connections") as connections_mock:
                        with self.assertRaises(db_backup.RestoreError):
                            db_backup.restore_backup(upload)
        finally:
            os.unlink(other_path)

    def test_restore_replaces_the_database_file(self):
        new_path = _make_sqlite_file()
        try:
            conn = sqlite3.connect(new_path)
            try:
                conn.execute("UPDATE dummy SET value='new content'")
                conn.commit()
            finally:
                conn.close()
            with open(new_path, "rb") as f:
                data = f.read()
            upload = SimpleUploadedFile("backup.sqlite3", data, content_type="application/octet-stream")

            with self._patched_name(self.src_path):
                # connections.close_all() would touch Django's real (shared,
                # in-memory) test connection — irrelevant to what's being
                # tested here (the raw file-copy mechanics), so it's mocked
                # out rather than actually invoked.
                with mock.patch.object(db_backup, "connections"):
                    with mock.patch.object(db_backup, "save_safety_backup", return_value="/tmp/whatever"):
                        log = db_backup.restore_backup(upload)

            self.assertIn("Restored", log)
            conn = sqlite3.connect(self.src_path)
            try:
                row = conn.execute("SELECT value FROM dummy").fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], "new content")
        finally:
            os.unlink(new_path)

    def test_postgres_dump_shells_out_to_pg_dump(self):
        fake_result = mock.Mock(returncode=0, stdout=db_backup.PG_CUSTOM_MAGIC + b"...", stderr=b"")
        with mock.patch.object(db_backup, "engine", return_value="postgresql"):
            with mock.patch.object(db_backup.subprocess, "run", return_value=fake_result) as run:
                data = db_backup._postgres_dump_bytes()
        self.assertTrue(data.startswith(db_backup.PG_CUSTOM_MAGIC))
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "pg_dump")
        self.assertIn("-Fc", cmd)

    def test_postgres_restore_shells_out_to_pg_restore(self):
        upload = SimpleUploadedFile(
            "backup.dump", db_backup.PG_CUSTOM_MAGIC + b"...rest", content_type="application/octet-stream"
        )
        fake_result = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(db_backup, "engine", return_value="postgresql"):
            with mock.patch.object(db_backup, "save_safety_backup", return_value="/tmp/whatever"):
                with mock.patch.object(db_backup, "connections"):
                    with mock.patch.object(db_backup.subprocess, "run", return_value=fake_result) as run:
                        log = db_backup.restore_backup(upload)
        self.assertIn("completed", log)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "pg_restore")
        self.assertIn("--clean", cmd)


class DatabaseBackupViewTests(TestCase):
    """HTTP layer: admin-only gate + view plumbing, backup/restore mechanics mocked out."""

    def setUp(self):
        self.admin = User.objects.create_user(
            "bk_admin", password="x", is_active=True, role=User.Role.ADMIN
        )
        self.member = User.objects.create_user(
            "bk_member", password="x", is_active=True, role=User.Role.TEAM_MEMBER
        )

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("core:db_backup"))
        self.assertEqual(resp.status_code, 302)

    def test_non_admin_forbidden(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse("core:db_backup"))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(reverse("core:db_backup_download"))
        self.assertEqual(resp.status_code, 403)

    def test_admin_sees_backup_page(self):
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("core:db_backup"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Download a backup")
        self.assertContains(resp, "Restore from a backup")

    def test_download_streams_backup_with_attachment_header(self):
        self.client.force_login(self.admin)
        with mock.patch(
            "apps.core.views.db_backup.create_backup",
            return_value=("test_backup.sqlite3", b"fake-bytes"),
        ):
            resp = self.client.get(reverse("core:db_backup_download"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"fake-bytes")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertIn("test_backup.sqlite3", resp["Content-Disposition"])

    def test_restore_requires_typed_confirmation(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile("backup.sqlite3", b"whatever", content_type="application/octet-stream")
        resp = self.client.post(
            reverse("core:db_backup"), {"file": upload, "confirm": "nope"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "RESTORE")
        # Never reaches the actual restore mechanics with a bad confirmation.
        with mock.patch("apps.core.views.db_backup.restore_backup") as restore:
            self.client.post(
                reverse("core:db_backup"),
                {"file": SimpleUploadedFile("b.sqlite3", b"x"), "confirm": "nope"},
            )
            restore.assert_not_called()

    def test_restore_success_renders_standalone_done_page(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile("backup.sqlite3", b"whatever", content_type="application/octet-stream")
        with mock.patch(
            "apps.core.views.db_backup.restore_backup", return_value="Restored. Prior state saved to /tmp/x."
        ):
            resp = self.client.post(
                reverse("core:db_backup"), {"file": upload, "confirm": "RESTORE"}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Database restored")
        self.assertContains(resp, "Prior state saved to /tmp/x.")

    def test_restore_failure_shows_error_and_form_again(self):
        self.client.force_login(self.admin)
        upload = SimpleUploadedFile("backup.sqlite3", b"whatever", content_type="application/octet-stream")
        with mock.patch(
            "apps.core.views.db_backup.restore_backup",
            side_effect=db_backup.RestoreError("This doesn't look like a SQLite database file."),
        ):
            resp = self.client.post(
                reverse("core:db_backup"), {"file": upload, "confirm": "RESTORE"}, follow=True
            )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Restore failed")
