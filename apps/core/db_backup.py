"""
Database backup / restore (admin-only feature).

Supports both engines the app runs on: **SQLite** (dev) via the stdlib
``sqlite3`` online backup API (safe against a live connection — no raw
file-copy corruption risk), and **PostgreSQL** (preprod/prod) via the
``pg_dump``/``pg_restore`` binaries, which already run locally on those VMs
(see deploy/DEPLOYMENT.md).

Restore is inherently destructive — it replaces ALL current data. This
module always takes its own timestamped safety copy (``safety_backup_path``)
before touching anything, so the immediately-prior state is recoverable from
the server's filesystem even if a restore goes wrong or the web session is
lost afterwards (e.g. because the session table itself was just replaced).
"""

import datetime
import os
import shutil
import sqlite3
import subprocess
import tempfile

from django.conf import settings
from django.db import connection, connections

SQLITE_MAGIC = b"SQLite format 3\x00"
PG_CUSTOM_MAGIC = b"PGDMP"

BACKUP_DIR = getattr(settings, "BACKUP_DIR", None) or (settings.BASE_DIR / "backups")

# How long a pg_dump/pg_restore subprocess may run before we give up.
SUBPROCESS_TIMEOUT = 900


class RestoreError(Exception):
    """Raised with a message safe to show the admin directly."""


def engine():
    """"sqlite" or "postgresql" — mirrors django.db.connection.vendor."""
    return connection.vendor


def _stamp():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_filename(prefix="ecostruxure_light_backup"):
    ext = "sqlite3" if engine() == "sqlite" else "dump"
    return f"{prefix}_{_stamp()}.{ext}"


def _db_settings():
    d = settings.DATABASES["default"]
    return {
        "name": d["NAME"],
        "user": d.get("USER") or "",
        "password": d.get("PASSWORD") or "",
        "host": d.get("HOST") or "localhost",
        "port": str(d.get("PORT") or "5432"),
    }


def _pg_env():
    env = os.environ.copy()
    password = _db_settings()["password"]
    if password:
        env["PGPASSWORD"] = password
    return env


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------
def create_backup():
    """Return ``(filename, bytes)`` — a full database dump."""
    if engine() == "sqlite":
        return backup_filename(), _sqlite_dump_bytes()
    return backup_filename(), _postgres_dump_bytes()


def _sqlite_dump_bytes():
    db_path = settings.DATABASES["default"]["NAME"]
    fd, tmp_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        src = sqlite3.connect(db_path)
        try:
            dst = sqlite3.connect(tmp_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp_path)


def _postgres_dump_bytes():
    db = _db_settings()
    cmd = [
        "pg_dump", "-Fc",
        "-h", db["host"], "-p", db["port"], "-U", db["user"], db["name"],
    ]
    try:
        result = subprocess.run(
            cmd, env=_pg_env(), capture_output=True, timeout=SUBPROCESS_TIMEOUT
        )
    except FileNotFoundError as exc:
        raise RestoreError("pg_dump is not available on this server.") from exc
    if result.returncode != 0:
        raise RestoreError(
            "pg_dump failed: " + (result.stderr or b"").decode(errors="replace")[-4000:]
        )
    return result.stdout


def save_safety_backup():
    """Write a timestamped backup to ``BACKUP_DIR`` (local filesystem, never
    served over HTTP) — called automatically before every restore so the
    prior state survives even if the restore itself fails partway."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    filename, data = create_backup()
    path = os.path.join(BACKUP_DIR, f"pre_restore_{filename}")
    with open(path, "wb") as f:
        f.write(data)
    return path


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def _write_temp(uploaded_file, suffix):
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        for chunk in uploaded_file.chunks():
            f.write(chunk)
    return tmp_path


def restore_backup(uploaded_file):
    """Overwrite the current database with ``uploaded_file`` (a Django
    ``UploadedFile``). ALL current data is replaced. A safety copy of the
    current database is saved first (see ``save_safety_backup``). Returns a
    short human-readable log string; raises ``RestoreError`` on failure."""
    head = uploaded_file.read(16)
    uploaded_file.seek(0)

    # Validate BEFORE touching anything — an obviously-wrong upload should
    # never trigger a (pointless) safety backup of the current database.
    if engine() == "sqlite":
        if not head.startswith(SQLITE_MAGIC):
            raise RestoreError("This doesn't look like a SQLite database file.")
    elif not head.startswith(PG_CUSTOM_MAGIC):
        raise RestoreError(
            "This doesn't look like a pg_dump custom-format (-Fc) backup file."
        )

    safety_path = save_safety_backup()

    if engine() == "sqlite":
        _sqlite_restore(uploaded_file)
        return f"Restored from the uploaded SQLite file. Prior state saved to {safety_path}."

    log = _postgres_restore(uploaded_file)
    return f"{log}\nPrior state saved to {safety_path}."


def _sqlite_restore(uploaded_file):
    db_path = settings.DATABASES["default"]["NAME"]
    tmp_path = _write_temp(uploaded_file, ".sqlite3")
    try:
        check = sqlite3.connect(tmp_path)
        try:
            tables = {
                row[0]
                for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            check.close()
        if "django_migrations" not in tables:
            raise RestoreError(
                "This SQLite file doesn't look like an EcoStruxure Light database "
                "(no django_migrations table)."
            )
        connections.close_all()
        shutil.copyfile(tmp_path, db_path)
    finally:
        os.unlink(tmp_path)
        connections.close_all()


def _postgres_restore(uploaded_file):
    db = _db_settings()
    tmp_path = _write_temp(uploaded_file, ".dump")
    try:
        connections.close_all()
        cmd = [
            "pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges",
            "-h", db["host"], "-p", db["port"], "-U", db["user"],
            "-d", db["name"], tmp_path,
        ]
        try:
            result = subprocess.run(
                cmd, env=_pg_env(), capture_output=True, text=True,
                timeout=SUBPROCESS_TIMEOUT,
            )
        except FileNotFoundError as exc:
            raise RestoreError("pg_restore is not available on this server.") from exc
        # pg_restore commonly exits non-zero on benign warnings (missing
        # extensions/roles, --no-owner drops ownership statements, etc.) even
        # when the data restore substantively succeeded — the admin sees the
        # full log either way and can judge, rather than us guessing.
        status = "completed" if result.returncode == 0 else f"finished with exit code {result.returncode} (see log)"
        log_tail = (result.stderr or "")[-4000:]
        return f"pg_restore {status}.\n{log_tail}"
    finally:
        os.unlink(tmp_path)
        connections.close_all()
