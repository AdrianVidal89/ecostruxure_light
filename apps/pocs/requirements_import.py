"""
Requirement import from Excel (spec Fase 10c).

``parse_requirements_xlsx`` reads an .xlsx into a per-row preview with row-level
validation (which field fails and why); ``import_requirements`` then creates the
valid rows. The ``code`` is auto-generated (as in the UI) and the POC/user come
from the view — the sheet only carries the descriptive fields of Fase 2.

The classification columns (gravity/operation/functional/category) are
POC-extensible: a value that isn't one of the built-in suggestions is accepted
as-is rather than rejected, and registered as a :class:`RequirementFieldOption`
so it becomes a suggestion on the manual form too (see ``RequirementForm``'s
"+ Add new…" control).
"""

from .audit import record_audit
from .models import Requirement, RequirementFieldOption, UseCase

_CLASSIFICATION_FIELDS = ("req_gravity", "req_operation", "req_functional", "req_category")


def _reverse_map(choices):
    """Accept either the stored value or the human label (case-insensitive)."""
    m = {}
    for value, label in choices:
        m[value.lower()] = value
        m[label.lower()] = value
    return m


_GRAVITY = _reverse_map(Requirement.Gravity.choices)
_OPERATION = _reverse_map(Requirement.Operation.choices)
_FUNCTIONAL = _reverse_map(Requirement.Functional.choices)
_CATEGORY = _reverse_map(Requirement.Category.choices)

_PREDEFINED_MAPS = {
    "req_gravity": _GRAVITY,
    "req_operation": _OPERATION,
    "req_functional": _FUNCTIONAL,
    "req_category": _CATEGORY,
}


def _custom_map(poc, field_name):
    """This POC's previously-added custom values for ``field_name``, by lowercase."""
    if poc is None:
        return {}
    return {
        v.lower(): v
        for v in poc.requirement_field_options.filter(field=field_name).values_list(
            "value", flat=True
        )
    }


# Header name → model field (all matched case-insensitively).
_HEADER_ALIASES = {
    "sub_system": {"sub_system", "subsystem", "sub system", "system"},
    "req_gravity": {"req_gravity", "gravity"},
    "req_operation": {"req_operation", "operation"},
    "req_functional": {"req_functional", "functional"},
    "req_category": {"req_category", "category"},
    "validation_criteria": {"validation_criteria", "validation criteria", "validation"},
    "life_cycle_phase": {"life_cycle_phase", "life cycle phase", "lifecycle"},
    "reference_documentations": {"reference_documentations", "references", "reference"},
    "remarks": {"remarks", "notes"},
    "use_cases": {"use_cases", "use cases", "usecases", "use_case_codes"},
}


def _norm(value):
    return str(value if value is not None else "").strip()


def parse_requirements_xlsx(file, poc=None):
    """Return a list of preview rows: {row, data, errors, valid}.

    ``poc`` supplies the POC's own previously-added custom classification
    values, so a value the POC has used before resolves to its existing exact
    spelling instead of creating a near-duplicate. A value seen for the first
    time is accepted as-is (see module docstring).

    An optional ``use_cases`` column may list existing Use Case codes of this
    POC (comma-separated) to link on import — left out or blank, a requirement
    is imported with no use cases linked (the previous, only behaviour).
    """
    import openpyxl

    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = next(rows_iter, None)
    if not headers:
        return []

    col_field = {}
    for idx, h in enumerate(headers):
        hn = _norm(h).lower()
        for field, aliases in _HEADER_ALIASES.items():
            if hn in aliases:
                col_field[idx] = field

    custom_maps = {f: _custom_map(poc, f) for f in _CLASSIFICATION_FIELDS}
    uc_by_code = {uc.code: uc.id for uc in poc.use_cases.all()} if poc is not None else {}

    # A requirement has no natural business key, but two rows sharing the same
    # validation criteria are, in practice, the same requirement — used to
    # reject re-imports of the same sheet (or accidental copies) as duplicates.
    existing_criteria = set()
    if poc is not None:
        existing_criteria = {
            v for v in poc.requirements.exclude(validation_criteria="")
            .values_list("validation_criteria", flat=True)
        }
    seen_criteria = set()

    preview = []
    for r_i, row in enumerate(rows_iter, start=2):
        if row is None or all(_norm(c) == "" for c in row):
            continue
        data = {}
        for idx, field in col_field.items():
            if idx < len(row):
                data[field] = _norm(row[idx])

        errors = []

        def resolve(field, label):
            raw = data.get(field, "")
            if not raw:
                errors.append(f"{label} is required")
                return ""
            key = raw.lower()
            if key in _PREDEFINED_MAPS[field]:
                return _PREDEFINED_MAPS[field][key]
            if key in custom_maps[field]:
                return custom_maps[field][key]
            # Not a known value yet — accepted as a new custom one (registered
            # on import so it becomes a suggestion from now on).
            return raw

        use_case_ids, unknown_ucs = [], []
        for code in (data.get("use_cases", "") or "").split(","):
            code = code.strip()
            if not code:
                continue
            if code in uc_by_code:
                use_case_ids.append(uc_by_code[code])
            else:
                unknown_ucs.append(code)
        if unknown_ucs:
            errors.append("Unknown use case code(s): " + ", ".join(unknown_ucs))

        validation_criteria = data.get("validation_criteria", "")
        if validation_criteria:
            if validation_criteria in existing_criteria or validation_criteria in seen_criteria:
                errors.append(
                    "Duplicate requirement — this validation criteria already exists"
                )
            else:
                seen_criteria.add(validation_criteria)

        clean = {
            "sub_system": data.get("sub_system", ""),
            "req_gravity": resolve("req_gravity", "Gravity"),
            "req_operation": resolve("req_operation", "Operation"),
            "req_functional": resolve("req_functional", "Functional"),
            "req_category": resolve("req_category", "Category"),
            "validation_criteria": validation_criteria,
            "life_cycle_phase": data.get("life_cycle_phase", ""),
            "reference_documentations": data.get("reference_documentations", ""),
            "remarks": data.get("remarks", ""),
            "use_case_ids": use_case_ids,
        }
        preview.append(
            {"row": r_i, "data": clean, "errors": errors, "valid": not errors}
        )
    return preview


def import_requirements(poc, rows, user):
    """Create Requirements from validated preview rows. Returns the count."""
    created = 0
    for row in rows:
        if not row.get("valid"):
            continue
        data = dict(row["data"])
        use_case_ids = data.pop("use_case_ids", [])
        req = Requirement.objects.create(
            poc=poc, created_by=user, modified_by=user, **data
        )
        if use_case_ids:
            req.use_cases.set(use_case_ids)
        record_audit(req, "requirement_created", user, {"code": {"before": None, "after": req.code}})
        created += 1
        for field_name in _CLASSIFICATION_FIELDS:
            value = data.get(field_name)
            if value and value not in dict(Requirement.predefined_choices(field_name)):
                RequirementFieldOption.objects.get_or_create(
                    poc=poc, field=field_name, value=value
                )
    return created


# ---------------------------------------------------------------------------
# Use Cases (spec Fase 10c)
# ---------------------------------------------------------------------------
_UC_PRIORITY = _reverse_map(UseCase.Priority.choices)
_UC_STATUS = _reverse_map(UseCase.Status.choices)

_UC_HEADER_ALIASES = {
    "title": {"title", "name", "use case", "use_case"},
    "description": {"description", "desc"},
    "actor": {"actor", "role"},
    "priority": {"priority"},
    "status": {"status"},
    "remarks": {"remarks", "notes"},
    "requirements": {"requirements", "requirement", "reqs", "requirement_codes"},
}


def parse_usecases_xlsx(file, poc):
    """Return preview rows for a Use Case import (validated against ``poc``).

    Title is required and must be unique within the POC (and the batch). Priority
    defaults to Medium and status to Draft when blank. A ``requirements`` column
    may list existing requirement codes (comma-separated) to link.
    """
    import openpyxl

    wb = openpyxl.load_workbook(file, read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = next(rows_iter, None)
    if not headers:
        return []

    col_field = {}
    for idx, h in enumerate(headers):
        hn = _norm(h).lower()
        for field, aliases in _UC_HEADER_ALIASES.items():
            if hn in aliases:
                col_field[idx] = field

    existing_titles = set(poc.use_cases.values_list("title", flat=True))
    req_by_code = {r.code: r.id for r in poc.requirements.all()}
    seen_titles = set()

    preview = []
    for r_i, row in enumerate(rows_iter, start=2):
        if row is None or all(_norm(c) == "" for c in row):
            continue
        data = {}
        for idx, field in col_field.items():
            if idx < len(row):
                data[field] = _norm(row[idx])

        errors = []
        title = data.get("title", "")
        if not title:
            errors.append("Title is required")
        elif title in existing_titles or title.lower() in seen_titles:
            errors.append(f"Title “{title}” already exists in this POC")
        else:
            seen_titles.add(title.lower())

        def resolve_optional(field, mapping, label, default):
            raw = data.get(field, "")
            if not raw:
                return default
            val = mapping.get(raw.lower())
            if not val:
                errors.append(f"{label} “{raw}” is not a valid option")
                return default
            return val

        priority = resolve_optional("priority", _UC_PRIORITY, "Priority", "medium")
        status = resolve_optional("status", _UC_STATUS, "Status", "draft")

        req_ids, unknown = [], []
        for code in (data.get("requirements", "") or "").split(","):
            code = code.strip()
            if not code:
                continue
            if code in req_by_code:
                req_ids.append(req_by_code[code])
            else:
                unknown.append(code)
        if unknown:
            errors.append("Unknown requirement code(s): " + ", ".join(unknown))

        clean = {
            "title": title,
            "description": data.get("description", ""),
            "actor": data.get("actor", ""),
            "priority": priority,
            "status": status,
            "remarks": data.get("remarks", ""),
            "requirement_ids": req_ids,
        }
        preview.append(
            {"row": r_i, "data": clean, "errors": errors, "valid": not errors}
        )
    return preview


def import_usecases(poc, rows, user):
    """Create Use Cases from validated preview rows. Returns the count."""
    created = 0
    for row in rows:
        if not row.get("valid"):
            continue
        data = dict(row["data"])
        req_ids = data.pop("requirement_ids", [])
        uc = UseCase.objects.create(
            poc=poc, created_by=user, modified_by=user, **data
        )
        if req_ids:
            uc.requirements.set(req_ids)
        record_audit(uc, "usecase_created", user, {"code": {"before": None, "after": uc.code}})
        created += 1
    return created


# ---------------------------------------------------------------------------
# Downloadable .xlsx templates (headers + example row + valid-options legend)
# ---------------------------------------------------------------------------
def _build_template_xlsx(headers, example_row, legend):
    """Build an .xlsx with a ``Template`` sheet and a ``Suggested options`` sheet.

    ``legend`` is a list of ``(column_name, [suggested_values])`` shown on the
    second sheet as a guide — these columns aren't restricted to the list.
    """
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Template"
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    if example_row:
        ws.append(example_row)
    for col in ws.columns:
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[col[0].column_letter].width = max(14, width + 2)

    if legend:
        ws2 = wb.create_sheet("Suggested options")
        ws2.append(["Column", "Suggested values (not exclusive — other text is accepted)"])
        for cell in ws2[1]:
            cell.font = Font(bold=True)
        for column_name, values in legend:
            ws2.append([column_name, ", ".join(values)])
        for col in ws2.columns:
            width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
            ws2.column_dimensions[col[0].column_letter].width = max(14, min(width + 2, 80))

    import io

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_requirements_template_xlsx(poc=None):
    """Return an in-memory .xlsx matching the Requirement import format.

    ``poc`` (optional) adds that POC's own previously-added custom
    classification values to the "Suggested options" sheet, alongside the
    built-in ones — the columns are not limited to either list (see module
    docstring).
    """
    headers = [
        "sub_system",
        "req_gravity",
        "req_operation",
        "req_functional",
        "req_category",
        "validation_criteria",
        "life_cycle_phase",
        "reference_documentations",
        "remarks",
        "use_cases",
    ]
    example_row = [
        "PLC Controller",
        Requirement.Gravity.IMPOSES_MVP.label,
        Requirement.Operation.CONTROL_OPERATION.label,
        Requirement.Functional.PERFORMANCE.label,
        Requirement.Category.NORMAL_OPERATION.label,
        "The system shall respond within 200ms",
        "Design",
        "SPEC-001",
        "",
        "",
    ]
    legend = [
        (field_name, [label for _, label in Requirement.field_choices(poc, field_name)])
        for field_name in _CLASSIFICATION_FIELDS
    ]
    legend.append(
        ("use_cases", ["Comma-separated existing use case codes, e.g. POC-001-UC001, POC-001-UC002 — leave blank to link none"])
    )
    return _build_template_xlsx(headers, example_row, legend)


def build_usecases_template_xlsx():
    """Return an in-memory .xlsx matching the Use Case import format."""
    headers = [
        "title",
        "description",
        "actor",
        "priority",
        "status",
        "remarks",
        "requirements",
    ]
    example_row = [
        "Operator starts the process",
        "The operator triggers the process from the HMI",
        "Operator",
        UseCase.Priority.MEDIUM.label,
        UseCase.Status.DRAFT.label,
        "",
        "",
    ]
    legend = [
        ("priority", [label for _, label in UseCase.Priority.choices]),
        ("status", [label for _, label in UseCase.Status.choices]),
        ("requirements", ["Comma-separated existing requirement codes, e.g. REQ-001, REQ-002"]),
    ]
    return _build_template_xlsx(headers, example_row, legend)
