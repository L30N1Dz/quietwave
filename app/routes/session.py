"""Login, logout, and the station's public configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.auth import (
    check_throttle,
    clear_failures,
    close_session,
    is_authenticated,
    open_session,
    passphrase_matches,
    record_failure,
)
from app.config import KNOWN_THEMES, Settings
from app.deps import ConfigDep, StoreDep
from app.models import LoginRequest
from app.store.base import Store

router = APIRouter(tags=["session"])


@router.post("/auth/login")
async def login(
    payload: LoginRequest, request: Request, settings: Settings = ConfigDep
) -> dict:
    check_throttle(request)
    if not passphrase_matches(payload.passphrase, settings):
        record_failure(request)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Passphrase rejected.",
        )
    clear_failures(request)
    open_session(request)
    return {"authenticated": True}


@router.post("/auth/logout")
async def logout(request: Request) -> dict:
    close_session(request)
    return {"authenticated": False}


@router.get("/api/session")
async def session_state(request: Request) -> dict:
    return {"authenticated": is_authenticated(request)}


@router.get("/api/config")
async def public_config(request: Request, settings: Settings = ConfigDep) -> dict:
    """Everything the client needs to boot. Nothing secret lives here."""
    return {
        "theme": settings.theme,
        "themes": list(KNOWN_THEMES),
        "poll_interval_ms": settings.poll_interval_ms,
        "max_upload_bytes": settings.max_upload_bytes,
        "authenticated": is_authenticated(request),
        "demo": settings.store_backend == "memory",
    }


@router.get("/healthz")
async def healthz(store: Store = StoreDep, settings: Settings = ConfigDep) -> dict:
    """Unauthenticated liveness probe for systemd / uptime checks."""
    reachable = await store.ping()
    return {
        "status": "ok" if reachable else "degraded",
        "store": settings.store_backend,
        "store_reachable": reachable,
    }
