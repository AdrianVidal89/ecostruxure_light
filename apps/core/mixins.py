"""
Reusable access-control mixins for class-based views.

These centralise the project's authorization rules so views stay declarative.
``POCMemberRequiredMixin`` (the per-POC membership gate mandated by the spec)
is added in the POC app build step; it will live alongside these.
"""

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404


def user_can_lead_poc(user, poc):
    """True if ``user`` may manage content (phases/tasks/tests) in ``poc``.

    Granted to global admins and to users whose ``POCMembership`` in this POC
    has ``role_in_poc == lead``. (Distinct from membership-level management such
    as assigning members, which is admin-only.)
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_admin:
        return True
    from apps.pocs.models import POCMembership

    return POCMembership.objects.filter(
        poc=poc, user=user, role_in_poc=POCMembership.Role.LEAD
    ).exists()


def user_can_edit_phase(user, phase):
    """True if ``user`` may make STRUCTURAL changes to ``phase`` (rename, add
    sub-phases, add/edit/delete its tasks & tests).

    Granted to admins and to the POC's lead on ANY phase of that POC: a lead has
    full control over everything inside their POC. (Executing assigned
    tasks/tests is governed separately — assignees can always do that.) The
    per-phase ``lead_editable`` flag is no longer consulted here.

    An approved (locked) phase can't be edited by anyone until a validator
    unlocks it (spec Fase 5). A closed POC is fully locked (spec Fase 6).
    """
    if getattr(phase.poc, "closed_at", None):
        return False
    if getattr(phase, "approved_at", None):
        return False
    return user_can_lead_poc(user, phase.poc)


def user_can_delete_phase(user, phase):
    """True if ``user`` may permanently delete ``phase`` from the POC.

    Admins may delete any phase. A POC lead may only delete phases they created
    themselves in the POC (no blueprint provenance) — phases inherited from the
    admin-defined blueprint must be marked Not Applicable instead, so a lead's
    POC never structurally drifts from the blueprint the admin maintains.
    """
    if not user_can_edit_phase(user, phase):
        return False
    if user.is_admin:
        return True
    return phase.can_be_deleted


def user_can_mark_na(user, phase):
    """True if ``user`` may mark ``phase`` Not Applicable (with a reason).

    Only meaningful for blueprint-sourced phases the lead can't delete; a
    phase the lead created directly should just be deleted instead.
    """
    return user_can_edit_phase(user, phase) and phase.can_be_marked_na


def user_is_poc_member(user, poc):
    """True if ``user`` belongs to ``poc`` (any per-POC role) or is an admin."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_admin:
        return True
    from apps.pocs.models import POCMembership

    return POCMembership.objects.filter(poc=poc, user=user).exists()


def user_can_execute_task(user, task):
    """True if ``user`` may update execution fields (status/notes) of ``task``.

    Granted to POC leads/admins and to the team member the task is assigned to.
    Blocked while the task's phase is approved/locked (Fase 5) or the POC is
    closed (Fase 6).
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(task.phase.poc, "closed_at", None):
        return False
    if getattr(task.phase, "approved_at", None):
        return False
    if user_can_lead_poc(user, task.phase.poc):
        return True
    return task.assigned_to_id == user.id


def user_can_execute_test(user, test):
    """True if ``user`` may record results for ``test`` (lead/admin or assignee).

    Blocked while the test's phase is approved/locked (Fase 5) or the POC is
    closed (Fase 6).
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(test.phase.poc, "closed_at", None):
        return False
    if getattr(test.phase, "approved_at", None):
        return False
    if user_can_lead_poc(user, test.phase.poc):
        return True
    return test.assigned_to_id == user.id


def user_can_validate_phase(user, phase):
    """True if ``user`` may validate test outcomes in ``phase`` (spec 3b).

    Validators are: global admins, the phase's leader (``phase_leader``), AND the
    POC's leads. A POC lead always keeps authority over the whole POC even when a
    sub-leader is assigned to a phase — the UI just notes that the phase has its
    own lead.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(phase.poc, "closed_at", None):
        return False  # a closed POC is locked (Fase 6)
    if user.is_admin:
        return True
    if phase.phase_leader_id == user.id:
        return True
    return user_can_lead_poc(user, phase.poc)


def user_can_validate_test(user, test):
    """True if ``user`` may approve/reject ``test``'s pending outcome."""
    return user_can_validate_phase(user, test.phase)


class AdminRequiredMixin(UserPassesTestMixin):
    """Allow only authenticated global admins.

    Behaviour (inherited from :class:`UserPassesTestMixin`):

    * anonymous users are redirected to ``LOGIN_URL``;
    * authenticated non-admins receive an HTTP 403.
    """

    def test_func(self):
        user = self.request.user
        return user.is_authenticated and user.is_admin


class POCMemberRequiredMixin(LoginRequiredMixin):
    """Gate a view behind POC membership — the project's core access rule.

    A request is allowed only if the user is a global admin **or** has a
    :class:`~apps.pocs.models.POCMembership` for the POC. Anonymous users are
    redirected to login; authenticated non-members receive an HTTP 403, so data
    from a POC the user does not belong to is never exposed.

    The POC is resolved from the URL kwarg named by ``poc_pk_url_kwarg``
    (default ``"pk"``) and cached as ``self.poc`` for the view to reuse.
    """

    poc_pk_url_kwarg = "pk"

    def get_poc(self):
        # Imported lazily to avoid an import cycle (core ← pocs).
        from apps.pocs.models import POC

        if not hasattr(self, "_poc"):
            self._poc = get_object_or_404(
                POC, pk=self.kwargs[self.poc_pk_url_kwarg]
            )
        return self._poc

    def dispatch(self, request, *args, **kwargs):
        from apps.pocs.models import POCMembership

        if not request.user.is_authenticated:
            return self.handle_no_permission()

        self.poc = self.get_poc()
        is_member = POCMembership.objects.filter(
            poc=self.poc, user=request.user
        ).exists()
        if not (request.user.is_admin or is_member):
            raise PermissionDenied("You are not a member of this POC.")

        return super().dispatch(request, *args, **kwargs)


class POCLeadRequiredMixin(LoginRequiredMixin):
    """Gate a view behind POC-lead authority (admin or lead-in-this-POC).

    Used for managing a POC's content — phases, and later tasks/tests. Anonymous
    users are redirected to login; authenticated users who are neither admin nor
    a lead of the POC receive an HTTP 403.

    Subclasses must ensure :meth:`get_poc` resolves the relevant POC (the
    default reads the ``poc_pk_url_kwarg`` URL kwarg).
    """

    poc_pk_url_kwarg = "pk"

    def get_poc(self):
        from apps.pocs.models import POC

        if not hasattr(self, "_poc"):
            self._poc = get_object_or_404(
                POC, pk=self.kwargs[self.poc_pk_url_kwarg]
            )
        return self._poc

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.poc = self.get_poc()
        if not user_can_lead_poc(request.user, self.poc):
            raise PermissionDenied("You must be a lead of this POC.")
        if self.poc.is_closed:
            raise PermissionDenied("This POC is closed and locked for editing.")
        return super().dispatch(request, *args, **kwargs)
