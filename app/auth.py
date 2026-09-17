"""Station access: one shared passphrase, one signed session cookie.

The threat model is "keep the open internet out of her messages", not
"separate two trusted users from each other". So: a single passphrase, a
long-lived signed cookie so she types it roughly once, and a brute-force
throttle because this endpoint is publicly reachable through the tunnel.
"""

from __future__ import annotations

import hmac
import time

from fastapi import HTTPException, Request, status

from app.config import Settings, get_settings

SESSION_KEY = "station_open"

# Failed attempts per client, inside a rolling window.
_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 300
_failures: dict[str, list[float]] = {}


def _client_key(request: Request) -> str:
    """Identify the caller for throttling.

    Behind Cloudflare Tunnel every request arrives from localhost, so the
    real client address is only in CF-Connecting-IP. Falling back to the
    socket address means that, worst case, the throttle is global rather
    than per-client -- which still stops a brute force.
    """
    forwarded = request.headers.get("cf-connecting-ip")
    if forwarded:
        return forwarded.strip()
    return request.client.host if request.client else "unknown"


def _prune(key: str, now: float) -> list[float]:
    recent = [t for t in _failures.get(key, []) if now - t < _WINDOW_SECONDS]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    return recent


def check_throttle(request: Request) -> None:
    """Refuse further attempts once a client has burned through its budget."""
    now = time.monotonic()
    recent = _prune(_client_key(request), now)
    if len(recent) >= _MAX_ATTEMPTS:
        retry_after = int(_WINDOW_SECONDS - (now - min(recent))) + 1
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Wait and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def record_failure(request: Request) -> None:
    now = time.monotonic()
    key = _client_key(request)
    recent = _prune(key, now)
    recent.append(now)
    _failures[key] = recent


def clear_failures(request: Request) -> None:
    _failures.pop(_client_key(request), None)


def reset_throttle_state() -> None:
    """Test hook; never called by the app itself."""
    _failures.clear()


def passphrase_matches(candidate: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    expected = settings.app_passphrase
    if not expected:
        # Memory/demo mode runs without a passphrase configured; an empty
        # expected value must never authenticate anybody in real mode.
        return settings.store_backend == "memory"
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def open_session(request: Request) -> None:
    request.session[SESSION_KEY] = True


def close_session(request: Request) -> None:
    request.session.clear()


def is_authenticated(request: Request) -> bool:
    try:
        return request.session.get(SESSION_KEY) is True
    except AssertionError:
        # SessionMiddleware not installed -- treat as locked, never as open.
        return False


async def require_session(request: Request) -> None:
    """FastAPI dependency guarding every human-facing API route."""
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Station locked.",
        )
