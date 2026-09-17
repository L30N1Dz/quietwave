"""Application assembly.

The store and gateway are constructed once in the lifespan handler and shared
through ``app.state``. Misconfiguration is fatal at startup rather than
mysterious at runtime -- a station that boots but cannot send is worse than
one that refuses to boot and says why.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import Settings, get_settings
from app.demo import seed_demo_traffic
from app.routes import media, session, threads, webhooks
from app.store import build_store
from app.twilio_client import build_gateway

log = logging.getLogger("quietwave")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    # Themes set CSS custom properties from script; keep style-src permissive
    # while script-src stays strict, which is where the real risk lives.
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'none'; "
    "object-src 'none'"
)


class StationLocked(Exception):
    """Raised at startup when configuration cannot support a real station."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    problems = settings.startup_problems()
    if problems:
        detail = "\n".join(f"  - {p}" for p in problems)
        raise StationLocked(
            "QUIETWAVE cannot start with this configuration:\n"
            f"{detail}\n\n"
            "Fix .env (see .env.example), or run the interface-only demo with "
            "QUIETWAVE_STORE=memory."
        )

    Path(settings.media_root).mkdir(parents=True, exist_ok=True)

    app.state.store = build_store(settings)
    app.state.gateway = build_gateway(settings)
    await app.state.store.startup()

    if settings.store_backend == "memory" and settings.seed_demo:
        await seed_demo_traffic(app.state.store)
        log.warning("demo mode: seeded fake traffic into the in-memory store")

    log.info(
        "QUIETWAVE %s up | store=%s | gateway=%s | theme=%s | inbound webhook=%s",
        __version__,
        settings.store_backend,
        getattr(app.state.gateway, "name", "?"),
        settings.theme,
        settings.webhook_sms_url,
    )
    try:
        yield
    finally:
        await app.state.store.shutdown()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="QUIETWAVE",
        description="Station control for one Twilio number.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.effective_session_secret(),
        session_cookie="quietwave_session",
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    app.include_router(webhooks.router)
    app.include_router(session.router)
    app.include_router(threads.router)
    app.include_router(media.router)

    app.mount(
        "/static", StaticFiles(directory=STATIC_DIR, html=False), name="static"
    )

    # ---- the shell -------------------------------------------------
    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        # Server-side substitution so the correct theme paints on first frame:
        # fetching the theme from JS first would flash an unstyled interface.
        html = html.replace("__THEME_ID__", settings.theme)
        return HTMLResponse(
            html, headers={"Cache-Control": "no-cache, must-revalidate"}
        )

    @app.get("/service-worker.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        # Must be served from the root to take a root scope.
        return FileResponse(
            STATIC_DIR / "service-worker.js",
            media_type="application/javascript",
            headers={
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache, must-revalidate",
            },
        )

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc) -> Response:
        # Machine-facing paths keep FastAPI's own handling, which preserves the
        # detail the route raised -- "No such conversation." is a great deal
        # more useful than a blanket "Not found.".
        #
        # /static/ is in this list deliberately: serving the HTML shell with a
        # 200 for a missing stylesheet lets the service worker cache HTML under
        # a .css URL, which is miserable to debug later.
        if request.url.path.startswith(
            ("/api/", "/webhooks/", "/m/", "/auth/", "/static/", "/healthz")
        ):
            return await http_exception_handler(request, exc)

        # Anything else gets the shell, so a stray URL opens the app rather
        # than dead-ending on a JSON blob.
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html.replace("__THEME_ID__", settings.theme))

    return app


app = create_app()
