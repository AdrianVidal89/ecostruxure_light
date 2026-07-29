"""Symmetric encryption for the API keys users paste into the AI tools page.

The key is derived from ``SECRET_KEY`` rather than stored separately: a leaked
database dump alone is then useless, and there is no second secret for whoever
deploys this to forget to set. The trade-off is explicit — **rotating
SECRET_KEY invalidates every stored API key** (they decrypt to nothing and the
user is asked to re-enter theirs), which is the correct outcome for a
credential the previous secret could unwrap.

Fernet gives authenticated encryption, so a tampered ciphertext raises rather
than silently decrypting to garbage that would then be sent to a third party.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    # Fernet wants 32 url-safe base64 bytes; SECRET_KEY is arbitrary text, so
    # hash it to a fixed width instead of truncating/padding it by hand.
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    """Encrypt a secret for storage. Returns url-safe text."""
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt a stored secret, or return "" if it can no longer be read.

    Returning "" rather than raising is deliberate: a SECRET_KEY rotation or a
    restored-from-elsewhere row should degrade to "you need to reconnect", not
    to a 500 on every page that touches the connection.
    """
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return ""
