"""Task Gantt/tree builder (spec section 5).

A hybrid tree-list: Phase -> Task -> Subtask hierarchy on the left, duration
bars positioned on a shared date axis on the right. Replaces the old flat
task list (where a task and its sub-task read as the same kind of row) and
the point-marker-only ``build_task_timeline`` (spec Fase 7, now superseded —
a real Gantt needs a start date, not just a due-date marker).

A task with sub-tasks is a "summary" row: its bar spans the earliest
sub-task start to the latest sub-task end, and its progress is the sub-task
completion ratio. It never shows its own start_date/due_date — only the
aggregate — so it reads as visually distinct from its children instead of
"another row at the same level" (the bug this redesign fixes).
"""

from datetime import date


def _annotate(task, by_parent, today):
    """Recursively compute gantt_start/gantt_end/gantt_progress/child_list for
    one task, walking its in-memory children (no extra queries)."""
    children = sorted(
        by_parent.get(task.id, []),
        key=lambda t: (t.due_date or date.max, t.id),
    )
    task.child_list = [_annotate(c, by_parent, today) for c in children]
    task.is_summary = bool(task.child_list)
    if task.is_summary:
        starts = [c.gantt_start for c in task.child_list if c.gantt_start]
        ends = [c.gantt_end for c in task.child_list if c.gantt_end]
        task.gantt_start = min(starts) if starts else None
        task.gantt_end = max(ends) if ends else None
        done = sum(1 for c in task.child_list if c.status == task.Status.COMPLETED)
        task.gantt_progress = round(done / len(task.child_list) * 100)
    else:
        task.gantt_start = task.start_date
        task.gantt_end = task.due_date
        task.gantt_progress = 100 if task.status == task.Status.COMPLETED else 0
    task.is_overdue = bool(
        task.gantt_end
        and task.gantt_end < today
        and task.status != task.Status.COMPLETED
    )
    return task


def _all_rows(task):
    yield task
    for child in task.child_list:
        yield from _all_rows(child)


def _position(row, lo, span):
    start = row.gantt_start or row.gantt_end
    end = row.gantt_end or row.gantt_start
    if not start:
        row.pct_start = row.pct_width = None
        return
    row.pct_start = round((start - lo).days / span * 100, 2)
    end_pct = round((end - lo).days / span * 100, 2)
    # Give a same-day / zero-duration task a sliver of visible width.
    row.pct_width = max(end_pct - row.pct_start, 1.2)


def build_task_gantt(tasks, today=None):
    """``tasks``: a flat iterable of Task (any single grouping scope — e.g.
    one phase's own tasks, or every task across one POC's phases).

    Returns a dict the template renders:
      ``top_level``: root tasks (parent is None, or its parent isn't in this
        set), each annotated with ``.child_list``/``.gantt_start``/
        ``.gantt_end``/``.gantt_progress``/``.is_summary``/``.pct_start``/
        ``.pct_width`` (the latter two ``None`` when undated).
      ``has_dates``: whether ANY row has a date at all (axis is hidden
        otherwise, but the tree still renders every row).
      ``start``/``end``: the axis's date range. ``today_pct``: today's
      position on that axis.
    """
    today = today or date.today()
    tasks = list(tasks)
    by_id = {t.id: t for t in tasks}
    by_parent = {}
    for t in tasks:
        if t.parent_id and t.parent_id in by_id:
            by_parent.setdefault(t.parent_id, []).append(t)
    roots = sorted(
        (t for t in tasks if not (t.parent_id and t.parent_id in by_id)),
        key=lambda t: (t.status == t.Status.COMPLETED, t.due_date or date.max, t.id),
    )
    for r in roots:
        _annotate(r, by_parent, today)

    all_dates = []
    for r in roots:
        for row in _all_rows(r):
            if row.gantt_start:
                all_dates.append(row.gantt_start)
            if row.gantt_end:
                all_dates.append(row.gantt_end)

    if not all_dates:
        for r in roots:
            for row in _all_rows(r):
                row.pct_start = row.pct_width = None
        return {
            "has_dates": False, "top_level": roots,
            "start": None, "end": None, "today_pct": None,
        }

    lo, hi = min(all_dates + [today]), max(all_dates + [today])
    span = (hi - lo).days or 1
    for r in roots:
        for row in _all_rows(r):
            _position(row, lo, span)

    return {
        "has_dates": True,
        "top_level": roots,
        "start": lo,
        "end": hi,
        "today_pct": round((today - lo).days / span * 100, 2),
    }


def gantt_summary(top_level):
    """Flatten a Gantt tree's roots into (total, completed, start, end) — a
    phase-level at-a-glance summary without opening the phase's own page."""
    total = 0
    completed = 0
    dates = []

    def walk(rows):
        nonlocal total, completed
        for r in rows:
            total += 1
            if r.status == r.Status.COMPLETED:
                completed += 1
            if r.gantt_start:
                dates.append(r.gantt_start)
            if r.gantt_end:
                dates.append(r.gantt_end)
            walk(r.child_list)

    walk(top_level)
    return {
        "total": total,
        "completed": completed,
        "start": min(dates) if dates else None,
        "end": max(dates) if dates else None,
    }
