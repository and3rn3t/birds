"""Optional password on the admin, stored as a hash.

HTTP Basic is the whole mechanism: it is what a browser already knows how to
ask for, and the admin is a handful of routes on a LAN appliance. Unlike
`detector_password`, this secret is never replayed to another service, so there
is no reason to keep it recoverable - only the hash is stored.

Basic sends the password in the clear over plain HTTP. A frame reachable from
beyond the LAN wants a reverse proxy doing TLS, not this.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 240_000
_SALT_BYTES = 16


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    """`pbkdf2_sha256$<iterations>$<salt>$<digest>`, one line, safe in JSON."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "$".join((ALGORITHM, str(ITERATIONS), _encode(salt), _encode(digest)))


def verify(stored: str, password: str) -> bool:
    """Constant-time check against a hash from `hash_password`.

    A hash that will not parse verifies nothing rather than everything: a
    hand-edited settings.json locks the admin out instead of opening it.
    """
    try:
        algorithm, iterations, salt, digest = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt, validate=True), int(iterations)
        )
        return hmac.compare_digest(expected, base64.b64decode(digest, validate=True))
    except (ValueError, binascii.Error):
        return False
