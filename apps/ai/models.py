"""Per-user AI provider connection.

One row per Mega User. The API key is the user's own — this is deliberately not
an application-wide key: spend, rate limits and revocation stay with whoever
connected the account, and an admin revoking Mega User status never leaves a
shared credential in play.
"""

from django.conf import settings
from django.db import models

from .crypto import decrypt, encrypt


class AIConnection(models.Model):
    """A user's Claude API credentials and model preference."""

    class Model(models.TextChoices):
        # Labels carry the trade-off so the picker doesn't need help text.
        OPUS = "claude-opus-5", "Claude Opus 5 — most capable"
        SONNET = "claude-sonnet-5", "Claude Sonnet 5 — faster, cheaper"
        HAIKU = "claude-haiku-4-5", "Claude Haiku 4.5 — fastest"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_connection",
    )
    # Fernet ciphertext — never the raw key. See apps/ai/crypto.py.
    encrypted_api_key = models.TextField(blank=True)
    # Shown in the UI so a user can tell which key is connected without us ever
    # rendering the secret back to the page.
    key_hint = models.CharField(max_length=20, blank=True)
    model_id = models.CharField(
        max_length=50, choices=Model.choices, default=Model.OPUS
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"AI connection for {self.user}"

    # --- API key handling -------------------------------------------------
    def set_api_key(self, raw_key: str):
        """Store a key encrypted, keeping a last-4 hint for the UI."""
        raw_key = (raw_key or "").strip()
        self.encrypted_api_key = encrypt(raw_key)
        self.key_hint = f"…{raw_key[-4:]}" if len(raw_key) >= 4 else ""

    @property
    def api_key(self) -> str:
        """The decrypted key, or "" when it can no longer be read."""
        return decrypt(self.encrypted_api_key)

    @property
    def is_connected(self) -> bool:
        return bool(self.api_key)
