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
    """
    return user_can_lead_poc(user, phase.poc)


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
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user_can_lead_poc(user, task.phase.poc):
        return True
    return task.assigned_to_id == user.id


def user_can_execute_test(user, test):
    """True if ``user`` may record results for ``test`` (lead/admin or assignee)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user_can_lead_poc(user, test.phase.poc):
        return True
    return test.assigned_to_id == user.id


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
        return super().dispatch(request, *args, **kwargs)
