"""Station management from the command line.

    python -m app.cli check                    # verify config, print webhook URLs
    python -m app.cli add-line +15551234567 --label "Her line"
    python -m app.cli list-lines
    python -m app.cli set-line-active +15551234567 --off
    python -m app.cli serve
    python -m app.cli demo                     # no Twilio, no Mongo, fake traffic
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import sys

from app.config import KNOWN_THEMES, get_settings
from app.phone import InvalidNumber, normalise, pretty
from app.store import build_store


def _print_header(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


async def _with_store(fn):
    settings = get_settings()
    store = build_store(settings)
    await store.startup()
    try:
        return await fn(store, settings)
    finally:
        await store.shutdown()


# ----------------------------------------------------------------------
def cmd_check(args: argparse.Namespace) -> int:
    settings = get_settings()

    _print_header("Configuration")
    print(f"  store backend    {settings.store_backend}")
    print(f"  mongo uri        {settings.mongo_uri}")
    print(f"  mongo database   {settings.mongo_db_name}")
    print(f"  theme            {settings.theme}")
    print(f"  media root       {settings.media_root}")
    print(f"  public base url  {settings.public_base_url}")
    print(f"  signature check  {'on' if settings.validate_twilio_signature else 'OFF'}")
    print(f"  twilio creds     {'present' if settings.twilio_configured else 'MISSING'}")

    _print_header("Point Twilio at these (Messaging Configuration only)")
    print(f"  A message comes in   POST  {settings.webhook_sms_url}")
    print(f"  Status callback URL  POST  {settings.webhook_status_url}")
    print("  Leave Voice Configuration exactly as it is.")

    problems = settings.startup_problems()
    if problems:
        _print_header("Problems that will stop startup")
        for problem in problems:
            print(f"  - {problem}")
    else:
        _print_header("Problems that will stop startup")
        print("  none")

    async def _ping(store, _settings):
        return await store.ping()

    try:
        reachable = asyncio.run(_with_store(_ping))
    except Exception as exc:
        reachable = False
        print(f"\n  store error: {exc}")
    print(f"\n  store reachable  {reachable}")
    return 0 if not problems and reachable else 1


def cmd_add_line(args: argparse.Namespace) -> int:
    try:
        number = normalise(args.number, get_settings().default_country_code)
    except InvalidNumber as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    async def _add(store, _settings):
        existing = await store.get_line_by_number(number)
        if existing:
            print(f"line {number} already exists (id {existing['id']})")
            return existing
        line = await store.create_line(number, args.label or number, True)
        print(f"added line {number} ({line['label']}) id={line['id']}")
        return line

    asyncio.run(_with_store(_add))
    return 0


def cmd_list_lines(args: argparse.Namespace) -> int:
    async def _list(store, _settings):
        lines = await store.list_lines(active_only=False)
        if not lines:
            print("no lines configured; add one with 'add-line'")
            return
        _print_header("Lines")
        for line in lines:
            state = "active" if line.get("active") else "inactive"
            print(
                f"  {line['twilio_number']:>15}  {pretty(line['twilio_number']):>16}"
                f"  {state:<9} {line.get('label') or ''}"
            )

    asyncio.run(_with_store(_list))
    return 0


def cmd_set_line_active(args: argparse.Namespace) -> int:
    try:
        number = normalise(args.number, get_settings().default_country_code)
    except InvalidNumber as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    async def _set(store, _settings):
        line = await store.get_line_by_number(number)
        if line is None:
            print(f"no such line: {number}", file=sys.stderr)
            return None
        updated = await store.update_line(line["id"], active=not args.off)
        print(
            f"line {number} is now "
            f"{'active' if updated.get('active') else 'inactive'}"
        )
        return updated

    asyncio.run(_with_store(_set))
    return 0


def cmd_secret(args: argparse.Namespace) -> int:
    print(secrets.token_urlsafe(48))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Interface-only run: in-memory store, fake gateway, seeded traffic."""
    import uvicorn

    os.environ["QUIETWAVE_STORE"] = "memory"
    os.environ["QUIETWAVE_DEMO"] = "true"
    os.environ["QUIETWAVE_COOKIE_SECURE"] = "false"
    os.environ["QUIETWAVE_VALIDATE_TWILIO_SIGNATURE"] = "false"
    if args.theme:
        os.environ["QUIETWAVE_THEME"] = args.theme
    get_settings.cache_clear()

    settings = get_settings()
    host = args.host or "127.0.0.1"
    port = args.port or settings.port
    print(f"\n  QUIETWAVE demo on http://{host}:{port}")
    print(f"  theme: {settings.theme}  (themes: {', '.join(KNOWN_THEMES)})")
    print("  any passphrase will open the station in demo mode\n")
    uvicorn.run("app.main:app", host=host, port=port, log_level=args.log_level)
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quietwave", description="QUIETWAVE station management"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify configuration and print webhook URLs").set_defaults(
        func=cmd_check
    )

    add = sub.add_parser("add-line", help="provision a Twilio number")
    add.add_argument("number")
    add.add_argument("--label", default=None)
    add.set_defaults(func=cmd_add_line)

    sub.add_parser("list-lines", help="show configured numbers").set_defaults(
        func=cmd_list_lines
    )

    toggle = sub.add_parser("set-line-active", help="activate or deactivate a line")
    toggle.add_argument("number")
    toggle.add_argument("--off", action="store_true", help="deactivate instead")
    toggle.set_defaults(func=cmd_set_line_active)

    sub.add_parser("secret", help="print a fresh SESSION_SECRET").set_defaults(
        func=cmd_secret
    )

    serve = sub.add_parser("serve", help="run the station")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser("demo", help="run with fake data and no Twilio")
    demo.add_argument("--host", default=None)
    demo.add_argument("--port", type=int, default=None)
    demo.add_argument("--theme", default=None, choices=KNOWN_THEMES)
    demo.add_argument("--log-level", default="info")
    demo.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
