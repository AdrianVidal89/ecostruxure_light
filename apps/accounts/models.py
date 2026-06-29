"""
Custom user model.

``User`` extends Django's :class:`~django.contrib.auth.models.AbstractUser` and
adds a global ``role`` that drives application-wide permissions:

* ``admin``        — full access; manages the whole application and every POC.
* ``team_member``  — no application-wide rights. May only act on POCs they are
  assigned to; on POCs where they are the per-POC **lead** they can manage
  everything inside that POC (see ``POCMembership``).

The two global roles deliberately do NOT include a "POC lead": leadership is a
per-POC fact, not a global one. Per-POC roles (lead vs member) are modelled
separately via ``POCMembership`` (built in the POC app), so the same team member
can lead one POC and merely belong to another.

The custom model is intentionally introduced at scaffold time: Django requires
``AUTH_USER_MODEL`` to be set before the first migration. Authentication views,
the profile page and the admin user-management UI are added in Step 2.
"""

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Application user with a global role."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        TEAM_MEMBER = "team_member", "Team Member"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.TEAM_MEMBER,
        help_text="Global role governing application-wide permissions.",
    )

    class Meta:
        ordering = ["username"]

    def __str__(self):
        full = self.get_full_name()
        return f"{full} ({self.username})" if full else self.username

    # --- Convenience role checks (used across views/templates) -------------
    @property
    def is_admin(self):
        """True for global admins (also covers Django superusers)."""
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_team_member(self):
        return self.role == self.Role.TEAM_MEMBER
