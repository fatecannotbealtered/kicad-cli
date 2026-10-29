"""Base confirm tokens (CLI-SPEC §7, §9): HMAC with a machine-local secret, bound to one plan, used once."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json

PREFIX = "ct_"


class TokenError(Exception):
    """reason is a details.conflict value: token_mismatch or token_expired."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(secret: bytes, claims: dict) -> str:
    body = canonical(claims)
    return PREFIX + _encode(body) + "." + _encode(hmac.new(secret, body, hashlib.sha256).digest())


def verify(secret: bytes, token: str, now: float) -> dict:
    """Return the claims of a token minted on this machine; raise TokenError otherwise."""
    if not isinstance(token, str) or not token.startswith(PREFIX) or token.count(".") != 1 or len(token) > 4096:
        raise TokenError("token_mismatch")
    body_text, mac_text = token[len(PREFIX):].split(".")
    try:
        body, mac = _decode(body_text), _decode(mac_text)
    except (ValueError, binascii.Error) as exc:
        raise TokenError("token_mismatch") from exc
    if not hmac.compare_digest(mac, hmac.new(secret, body, hashlib.sha256).digest()):
        raise TokenError("token_mismatch")
    try:
        claims = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TokenError("token_mismatch") from exc
    if not isinstance(claims, dict) or not isinstance(claims.get("exp"), (int, float)):
        raise TokenError("token_mismatch")
    if claims["exp"] < now:
        raise TokenError("token_expired")
    return claims


def fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
