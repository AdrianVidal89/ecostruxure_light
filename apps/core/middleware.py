"""
Current-user middleware.

Django signals have no access to the request, so we stash the authenticated
user for the duration of each request in a thread-local. The audit-log signals
(apps/pocs/signals.py) read it via :func:`get_current_user` to record the actor.

Saves made outside a request (shell, fixtures, management commands) simply have
no current user, and the audit entry's ``actor`` is left null.
"""

import threading

_state = threading.local()


def get_current_user():
    """Return the authenticated user for the active request, or None."""
    user = getattr(_state, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


class CurrentUserMiddleware:
    """Store ``request.user`` in a thread-local for the request's lifetime."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _state.user = getattr(request, "user", None)
        try:
            return self.get_response(request)
        finally:
            # Always clear so a pooled thread never leaks a user across requests.
            _state.user = None


class NoBackCacheMiddleware:
    """Stop the browser's back/forward cache (bfcache) from replaying a stale
    authenticated page.

    Data here changes from other pages (deleting a test on the phase page,
    then hitting Back to the POC overview) — without this, some browsers
    restore the previous DOM from bfcache instead of re-requesting it, so the
    overview/status board looks stale until a hard refresh. Only applied to
    logged-in users' HTML pages; static assets and anonymous (login) pages are
    left cacheable as before.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, "user", None)
        content_type = response.get("Content-Type", "")
        if getattr(user, "is_authenticated", False) and content_type.startswith("text/html"):
            response["Cache-Control"] = "no-store, must-revalidate"
        return response
