"""
Bulk user importer with column mapping and a mandatory preview.

Flow (driven by the view): parse a file → map columns to User fields → build a
**preview** classifying every row as valid / warning / discarded → only on
explicit confirmation are users written. ``build_preview`` never touches the DB
beyond read-only lookups; ``commit_users`` performs the writes.
"""

import csv
import io
import re

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

User = get_user_model()

# User fields offered for mapping (key, label, required?).
USER_FIELDS = [
    ("username", "Username", False),
    ("email", "Email", False),
    ("first_name", "First name", False),
    ("last_name", "Last name", False),
    ("role", "Role", False),
    ("is_active", "Active", False),
    ("date_joined", "Date joined", False),
]

_HEADER_SYNONYMS = {
    "username": {"username", "user", "login", "user name", "userid", "user id"},
    "email": {"email", "e-mail", "mail", "correo", "correo electronico"},
    "first_name": {"first name", "first", "firstname", "nombre", "given name"},
    "last_name": {"last name", "last", "lastname", "apellido", "apellidos", "surname", "family name"},
    "role": {"role", "rol", "perfil", "profile"},
    "is_active": {"active", "is active", "activo", "enabled", "status", "estado"},
    "date_joined": {"date joined", "joined", "date", "fecha", "fecha alta", "created", "alta"},
}

_ROLE_MAP = {
    "admin": User.Role.ADMIN,
    "administrator": User.Role.ADMIN,
    "administrador": User.Role.ADMIN,
    # "Lead" is no longer a global role — leadership is assigned per POC. Anyone
    # imported as a lead becomes a team member (and is made a POC lead later via
    # the per-POC membership UI).
    "poc lead": User.Role.TEAM_MEMBER,
    "poc_lead": User.Role.TEAM_MEMBER,
    "lead": User.Role.TEAM_MEMBER,
    "líder": User.Role.TEAM_MEMBER,
    "lider": User.Role.TEAM_MEMBER,
    "team member": User.Role.TEAM_MEMBER,
    "team_member": User.Role.TEAM_MEMBER,
    "member": User.Role.TEAM_MEMBER,
    "miembro": User.Role.TEAM_MEMBER,
}

_TRUTHY = {"1", "true", "yes", "y", "si", "sí", "activo", "active", "enabled"}
_FALSY = {"0", "false", "no", "n", "inactive", "inactivo", "disabled"}


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def _stringify(v):
    if v is None:
        return ""
    if hasattr(v, "isoformat"):  # date / datetime → ISO string (session-safe)
        return v.isoformat()
    return str(v).strip()


def parse_user_file(file_obj, filename):
    """Return (headers, rows) as plain strings. Supports .xlsx and .csv/.txt."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [[_stringify(c) for c in r] for r in ws.iter_rows(values_only=True)]
    elif ext in {"csv", "txt"}:
        data = file_obj.read()
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig", errors="replace")
        rows = [[_stringify(c) for c in row] for row in csv.reader(io.StringIO(data))]
    else:
        raise ValueError("Unsupported file type. Upload .xlsx or .csv.")

    rows = [r for r in rows if any(c for c in r)]  # drop blank lines
    if not rows:
        return [], []
    return rows[0], rows[1:]


def auto_map(headers):
    """Best-effort header→field mapping. Returns {field: column_index or None}."""
    norm = [(h or "").strip().lower() for h in headers]
    mapping = {f: None for f, _, _ in USER_FIELDS}
    for field, syns in _HEADER_SYNONYMS.items():
        for i, h in enumerate(norm):
            if h in syns:
                mapping[field] = i
                break
    return mapping


# ---------------------------------------------------------------------------
# Preview (read-only)
# ---------------------------------------------------------------------------
def _cell(row, idx):
    if idx is None or idx == "" or idx is None:
        return ""
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return ""
    return row[idx].strip() if 0 <= idx < len(row) else ""


def _parse_bool(value, msgs):
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    if v:
        msgs.append(f"Unrecognised active value “{value}” → active")
    return True


def _parse_date_joined(value, msgs):
    if not value:
        return None
    dt = parse_datetime(value) or parse_date(value)
    if dt is None:
        msgs.append(f"Invalid date “{value}” → today")
        return None
    return dt


def build_preview(headers, rows, mapping):
    """Classify each row without writing anything.

    Returns (preview_rows, summary). Each preview row: index, data, status
    (valid/warning/discarded), action (create/update/skip), messages.
    """
    existing_usernames = {u.lower() for u in User.objects.values_list("username", flat=True)}
    existing_emails = {
        e.lower() for e in User.objects.values_list("email", flat=True) if e
    }
    seen_usernames, seen_emails = set(), set()

    preview = []
    summary = {"valid": 0, "warning": 0, "discarded": 0, "total": 0}

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        msgs = []
        username = _cell(row, mapping.get("username"))
        email = _cell(row, mapping.get("email")).lower()
        first = _cell(row, mapping.get("first_name"))
        last = _cell(row, mapping.get("last_name"))
        role_raw = _cell(row, mapping.get("role"))
        active = _parse_bool(_cell(row, mapping.get("is_active")), msgs)
        date_joined = _parse_date_joined(_cell(row, mapping.get("date_joined")), msgs)

        # Email validity.
        if email:
            try:
                validate_email(email)
            except ValidationError:
                msgs.append(f"Invalid email “{email}” → ignored")
                email = ""

        # Identity: need a username; derive from email if needed.
        if not username and email:
            username = email.split("@")[0]
            msgs.append("Username derived from email")

        role = _ROLE_MAP.get(role_raw.lower()) if role_raw else None
        if role_raw and role is None:
            msgs.append(f"Unknown role “{role_raw}” → team member")
        if role is None:
            role = User.Role.TEAM_MEMBER

        if not first and not last:
            msgs.append("Name missing")

        data = {
            "username": username,
            "email": email,
            "first_name": first,
            "last_name": last,
            "role": role,
            "is_active": active,
            "date_joined": date_joined.isoformat() if date_joined else "",
        }

        status, action = "valid", "create"
        if not username:
            status, action = "discarded", "skip"
            msgs.append("No username or email — cannot identify the user")
        elif username.lower() in seen_usernames or (email and email in seen_emails):
            status, action = "discarded", "skip"
            msgs.append("Duplicate row in file")
        else:
            seen_usernames.add(username.lower())
            if email:
                seen_emails.add(email)
            if username.lower() in existing_usernames or (
                email and email in existing_emails
            ):
                status, action = "warning", "update"
                msgs.append("Existing user — will be updated")
            elif msgs:
                status = "warning"

        summary[status] += 1
        summary["total"] += 1
        preview.append(
            {"index": i, "data": data, "status": status, "action": action, "messages": msgs}
        )

    return preview, summary


# ---------------------------------------------------------------------------
# Commit (writes)
# ---------------------------------------------------------------------------
def commit_users(preview, initial_password=""):
    """Create/update users for all non-discarded preview rows. Returns stats."""
    stats = {"created": 0, "updated": 0, "skipped": 0}
    with transaction.atomic():
        for prow in preview:
            if prow["action"] == "skip":
                stats["skipped"] += 1
                continue
            d = prow["data"]
            defaults = {
                "email": d["email"],
                "first_name": d["first_name"],
                "last_name": d["last_name"],
                "role": d["role"],
                "is_active": d["is_active"],
            }
            user, created = User.objects.update_or_create(
                username=d["username"], defaults=defaults
            )
            if d["date_joined"]:
                dj = parse_datetime(d["date_joined"]) or parse_date(d["date_joined"])
                if dj is not None:
                    user.date_joined = (
                        dj if hasattr(dj, "hour") else timezone.datetime(
                            dj.year, dj.month, dj.day, tzinfo=timezone.get_default_timezone()
                        )
                    )
                    user.save(update_fields=["date_joined"])
            if created:
                if initial_password:
                    user.set_password(initial_password)
                else:
                    user.set_unusable_password()
                user.save(update_fields=["password"])
                stats["created"] += 1
            else:
                stats["updated"] += 1
    return stats
