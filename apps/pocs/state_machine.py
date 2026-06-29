"""
Centralised state machines for Task status and Test verdict.

Single source of truth for *which* transitions are valid. Status names match
the model ``TextChoices`` values exactly (kept as strings here to avoid an
import cycle). Who may perform a transition is governed separately by the
permission helpers (``user_can_execute_task`` / ``user_can_execute_test``);
this module only answers "is this transition allowed at all?".
"""

# --- Task status: pending → in_progress ↔ blocked → completed (reopenable) ---
TASK_TRANSITIONS = {
    "pending": {"in_progress", "blocked"},
    "in_progress": {"completed", "blocked", "pending"},
    "blocked": {"in_progress", "pending"},
    "completed": {"in_progress"},  # reopen
}

# --- Test verdict: an outcome that can be (re)set to any other outcome ---
TEST_TRANSITIONS = {
    "pending": {"passed", "failed", "blocked", "skipped"},
    "passed": {"pending", "failed", "blocked", "skipped"},
    "failed": {"pending", "passed", "blocked", "skipped"},
    "blocked": {"pending", "passed", "failed", "skipped"},
    "skipped": {"pending", "passed", "failed", "blocked"},
}


def task_next_states(current):
    """Statuses ``current`` may move to (excludes itself)."""
    return TASK_TRANSITIONS.get(current, set())


def can_transition_task(current, new):
    """True if ``current → new`` is a valid task transition (no-op allowed)."""
    return new == current or new in TASK_TRANSITIONS.get(current, set())


def task_allowed_statuses(current):
    """The current status plus its valid targets (for rendering a control)."""
    return [current] + sorted(task_next_states(current))


def test_next_verdicts(current):
    return TEST_TRANSITIONS.get(current, set())


def can_transition_test(current, new):
    return new == current or new in TEST_TRANSITIONS.get(current, set())
