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

# --- Test execution status: any progress state can move to any other ---
# (the "blocked" verdict was removed in Fase 3a; the terminal outcomes are now
# governed by the result field + the validation flow, not by this table).
TEST_EXECUTION_TRANSITIONS = {
    "not_tested": {"in_progress", "test_completed", "skipped"},
    "in_progress": {"not_tested", "test_completed", "skipped"},
    "test_completed": {"not_tested", "in_progress", "skipped"},
    "skipped": {"not_tested", "in_progress", "test_completed"},
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


def can_transition_test_execution(current, new):
    return new == current or new in TEST_EXECUTION_TRANSITIONS.get(current, set())
