"""The theme system's guard rails.

A theme is data, not code, so these tests are what stop a fifth theme from
half-existing: every theme must be registered, must ship both files, and must
translate exactly the same set of keys as every other theme.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.config import KNOWN_THEMES

STATIC = Path(__file__).resolve().parent.parent / "static"
THEMES_DIR = STATIC / "themes"
REFERENCE = "quietwave"

# Custom properties css/base.css relies on a theme to define. Optional ones
# (declared with a var() fallback in base.css) are deliberately excluded.
REQUIRED_TOKENS = [
    "--bg", "--bg-elev", "--bg-sunken", "--line", "--hairline", "--scrim",
    "--fg", "--fg-dim", "--fg-faint",
    "--accent", "--accent-bright", "--accent-fg",
    "--in-bg", "--in-fg", "--out-bg", "--out-fg",
    "--ok", "--warn", "--danger",
    "--font-mono", "--font-ui", "--font-display",
    "--weight-display", "--tracking-display", "--tracking-mono", "--tracking-ui",
    "--size-title",
    "--radius-sm", "--radius-lg", "--radius-input", "--mark-radius",
    "--bubble-in-radius", "--bubble-out-radius",
]


def theme_ids() -> list[str]:
    return list(KNOWN_THEMES)


def load_lexicon(theme_id: str) -> dict:
    return json.loads(
        (THEMES_DIR / theme_id / "lexicon.json").read_text(encoding="utf-8")
    )


# ----------------------------------------------------------------------
def test_registry_matches_the_code():
    registry = json.loads((THEMES_DIR / "themes.json").read_text(encoding="utf-8"))
    listed = [theme["id"] for theme in registry["themes"]]
    assert listed == list(KNOWN_THEMES), (
        "static/themes/themes.json and app.config.KNOWN_THEMES disagree"
    )


def test_registry_entries_are_complete():
    registry = json.loads((THEMES_DIR / "themes.json").read_text(encoding="utf-8"))
    for theme in registry["themes"]:
        assert theme["name"], theme
        assert theme["description"], theme
        for key in ("bg", "accent"):
            value = theme["swatch"][key]
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), (theme["id"], key, value)


@pytest.mark.parametrize("theme_id", theme_ids())
def test_theme_ships_both_files(theme_id):
    assert (THEMES_DIR / theme_id / "theme.css").is_file()
    assert (THEMES_DIR / theme_id / "lexicon.json").is_file()


@pytest.mark.parametrize("theme_id", theme_ids())
def test_theme_defines_every_required_token(theme_id):
    css = (THEMES_DIR / theme_id / "theme.css").read_text(encoding="utf-8")
    missing = [token for token in REQUIRED_TOKENS if f"{token}:" not in css]
    assert not missing, f"{theme_id} is missing tokens: {missing}"


@pytest.mark.parametrize("theme_id", theme_ids())
def test_lexicons_all_translate_the_same_keys(theme_id):
    reference = set(load_lexicon(REFERENCE))
    keys = set(load_lexicon(theme_id))
    assert keys == reference, (
        f"{theme_id} lexicon drifted: "
        f"missing={sorted(reference - keys)} extra={sorted(keys - reference)}"
    )


@pytest.mark.parametrize("theme_id", theme_ids())
def test_no_lexicon_entry_is_blank(theme_id):
    for key, value in load_lexicon(theme_id).items():
        assert isinstance(value, str) and value.strip(), f"{theme_id}:{key} is empty"


def test_every_key_used_in_markup_exists_in_the_lexicons():
    """A data-lex attribute with no matching key would render as the key."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r'data-lex(?:-placeholder|-title)?="([^"]+)"', html))
    reference = set(load_lexicon(REFERENCE))
    assert used <= reference, f"markup uses unknown keys: {sorted(used - reference)}"


def test_every_key_used_in_javascript_exists_in_the_lexicons():
    source = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r"lex\('([^']+)'", source))
    reference = set(load_lexicon(REFERENCE))
    assert used <= reference, f"app.js uses unknown keys: {sorted(used - reference)}"


def test_default_theme_is_registered():
    from app.config import Settings

    settings = Settings(_env_file=None, QUIETWAVE_STORE="memory")
    assert settings.theme in KNOWN_THEMES


def test_unknown_theme_is_rejected_at_startup():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, QUIETWAVE_THEME="chartreuse-disco")


def test_service_worker_precaches_every_theme():
    source = (STATIC / "service-worker.js").read_text(encoding="utf-8")
    listed = re.search(r"const THEMES = \[(.*?)\]", source, re.S)
    assert listed, "service worker no longer declares a THEMES list"
    ids = set(re.findall(r"'([^']+)'", listed.group(1)))
    assert ids == set(KNOWN_THEMES), (
        f"service worker theme list drifted: {sorted(ids)}"
    )


def test_service_worker_never_caches_private_routes():
    source = (STATIC / "service-worker.js").read_text(encoding="utf-8")
    for route in ("/api/", "/auth/", "/m/", "/webhooks/"):
        assert route in source, f"service worker must bypass {route}"


def test_icons_referenced_by_the_manifest_exist():
    manifest = json.loads(
        (STATIC / "manifest.webmanifest").read_text(encoding="utf-8")
    )
    for icon in manifest["icons"]:
        path = STATIC.parent / icon["src"].lstrip("/")
        assert path.is_file(), f"manifest points at a missing icon: {icon['src']}"
    assert any(i.get("purpose") == "maskable" for i in manifest["icons"])
