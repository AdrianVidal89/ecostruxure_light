"""Builds the POC briefing the assistant reasons over.

Scope is the whole point: a user's assistant sees exactly the POCs that user can
already open in the UI — their memberships, or everything for an admin. Being a
Mega User unlocks the tooling, never additional data. Widening this function is
therefore a permissions change, not a formatting one.

The briefing is one Markdown document rather than a set of tools the model
calls. For a corpus this size that is both cheaper and more useful: it is
assembled once per request, sent as a cached system prompt (identical bytes on
every turn of a conversation → cache reads at ~0.1x), and it lets the model see
relationships across POCs — which is the cross-checking work this exists for.
Should a workspace outgrow the window, the migration is to tool-based retrieval,
not to a bigger truncation limit.
"""

from django.db.models import Prefetch

# Per-field caps. Long Markdown descriptions are common and a handful of them
# would otherwise crowd out entire POCs.
MAX_FIELD_CHARS = 1200
MAX_POCS = 40


def _clip(text, limit=MAX_FIELD_CHARS):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …[truncated]"


def visible_pocs(user):
    """The POCs ``user`` may read — the same set the UI would show them."""
    from apps.pocs.models import POC, POCMembership

    qs = POC.objects.all()
    if not user.is_admin:
        poc_ids = POCMembership.objects.filter(user=user).values_list("poc_id", flat=True)
        qs = qs.filter(pk__in=poc_ids)
    return qs.order_by("-updated_at" if _has_field(POC, "updated_at") else "name")


def _has_field(model, name):
    return any(f.name == name for f in model._meta.get_fields())


def _poc_markdown(poc):
    lines = [f"## POC: {poc.name}", ""]

    facts = [
        ("Status", poc.get_status_display()),
        ("Customer", poc.customer),
        ("Segment", poc.customer_segment),
        ("Initiative", poc.initiative),
        ("Leading organization", poc.leading_organization),
        ("Start", poc.start_date.isoformat() if poc.start_date else ""),
        ("End", poc.end_date.isoformat() if poc.end_date else ""),
    ]
    for label, value in facts:
        if value:
            lines.append(f"- **{label}:** {value}")
    if poc.description:
        lines += ["", "**Description:**", "", _clip(poc.description)]

    use_cases = list(poc.use_cases.all())
    if use_cases:
        lines += ["", "### Use cases", ""]
        for uc in use_cases:
            lines.append(
                f"- **{uc.code}** — {uc.title} "
                f"({uc.get_priority_display()}, {uc.get_status_display()})"
            )
            if uc.description:
                lines.append(f"  - {_clip(uc.description)}")

    requirements = list(poc.requirements.all())
    if requirements:
        lines += ["", "### Requirements", ""]
        for req in requirements:
            lines.append(
                f"- **{req.req_id}** — {req.sub_system or '(no sub-system)'} "
                f"({req.get_req_gravity_display()})"
            )
            if req.description:
                lines.append(f"  - Description: {_clip(req.description)}")
            if req.validation_criteria:
                lines.append(f"  - Validation criteria: {_clip(req.validation_criteria)}")

    phases = list(poc.phases.all())
    if phases:
        lines += ["", "### Phases", ""]
        for phase in phases:
            lines.append(f"- **{phase.name}** ({phase.get_status_display()})")

    tests = []
    for phase in phases:
        tests.extend(phase.tests.all())
    if tests:
        lines += ["", "### Tests", ""]
        for test in tests:
            outcome = test.get_result_display() if test.result else "no result yet"
            lines.append(
                f"- **{test.test_code or '(no code)'}** — {test.title} "
                f"({test.get_execution_status_display()}, {outcome})"
            )
            if test.expected_result:
                lines.append(f"  - Expected: {_clip(test.expected_result)}")

    lines.append("")
    return "\n".join(lines)


def build_briefing(user):
    """The full Markdown briefing for ``user``, plus a short scope summary."""
    from apps.pocs.models import Phase, Requirement, Test, UseCase

    pocs = list(
        visible_pocs(user).prefetch_related(
            Prefetch("use_cases", queryset=UseCase.objects.order_by("code")),
            Prefetch("requirements", queryset=Requirement.objects.order_by("req_id")),
            Prefetch(
                "phases",
                queryset=Phase.objects.order_by("order").prefetch_related(
                    Prefetch("tests", queryset=Test.objects.order_by("test_code"))
                ),
            ),
        )[:MAX_POCS]
    )
    if not pocs:
        return "", {"pocs": 0, "chars": 0}

    body = "\n".join(_poc_markdown(poc) for poc in pocs)
    briefing = (
        "# POC data available to this user\n\n"
        f"{len(pocs)} POC(s), current as of this request.\n\n" + body
    )
    return briefing, {"pocs": len(pocs), "chars": len(briefing)}
