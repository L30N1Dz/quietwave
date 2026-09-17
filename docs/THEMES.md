# Themes

A theme in QUIETWAVE is **data, not code**. It is two files:

```
static/themes/<id>/
├── theme.css      CSS custom properties consumed by static/css/base.css
└── lexicon.json   every user-visible string
```

`static/css/base.css` owns all structure — layout, scrolling, tap targets,
responsive behaviour — and reads every colour, font, radius and glow from a
custom property. A theme never overrides layout. That separation is what keeps
four themes from becoming four codebases, and it is enforced by
`tests/test_themes.py`.

---

## Adding a theme

### 1. Create the folder

```bash
mkdir static/themes/my-theme
cp static/themes/quietwave/theme.css static/themes/my-theme/theme.css
cp static/themes/quietwave/lexicon.json static/themes/my-theme/lexicon.json
```

Starting from an existing theme guarantees you have every required token and
lexicon key.

### 2. Register it

In `app/config.py`:

```python
KNOWN_THEMES = ("quietwave", "relay-nine", "emberlink", "neonwire", "my-theme")
```

In `static/themes/themes.json` — this is what the in-app picker reads:

```json
{
  "id": "my-theme",
  "name": "MY THEME",
  "description": "One short line, shown under the name.",
  "swatch": { "bg": "#0a0a0a", "accent": "#ffb000" }
}
```

In `static/service-worker.js`, add the id to the `THEMES` array so the theme is
precached with the rest of the shell.

### 3. Write the tokens and the lexicon

Then:

```bash
uv run pytest tests/test_themes.py
uv run python -m app.cli demo --theme my-theme
```

The tests will tell you about any token you missed, any lexicon key that
drifted, and any string you left blank.

---

## Token reference

Every token below must be defined. `tests/test_themes.py` fails the build if
one is missing.

### Surfaces

| Token | Purpose |
|---|---|
| `--bg` | Page background; also drives the Android address-bar colour |
| `--bg-elev` | Raised surfaces: top bar, composer, sheets, toasts |
| `--bg-sunken` | Recessed surfaces: inputs, sigils, chips |
| `--line` | Every hairline border |
| `--hairline` | Border width, normally `1px` |
| `--scrim` | Backdrop behind an open sheet |

### Text

| Token | Purpose |
|---|---|
| `--fg` | Body text |
| `--fg-dim` | Secondary text: previews, field labels |
| `--fg-faint` | Tertiary text: timestamps, placeholders, metadata |

### Accent

| Token | Purpose |
|---|---|
| `--accent` | The theme's signal colour: title, unread state, primary button |
| `--accent-bright` | Hover/active variant |
| `--accent-fg` | Text *on* an accent fill — must pass contrast against `--accent` |

### Message bubbles

| Token | Purpose |
|---|---|
| `--in-bg` / `--in-fg` | Inbound bubble fill and text |
| `--out-bg` / `--out-fg` | Outbound bubble fill and text |
| `--in-line` / `--out-line` | Bubble borders (optional; default transparent) |

### Signal states

| Token | Purpose |
|---|---|
| `--ok` | Reserved for success states |
| `--warn` | Reserved for warnings |
| `--danger` | Failures, destructive actions, login errors |

### Type

| Token | Purpose |
|---|---|
| `--font-ui` | Message text and names. Set it to `var(--font-mono)` for a terminal feel |
| `--font-mono` | Metadata, labels, buttons, timestamps |
| `--font-display` | Titles |
| `--weight-display` | Title weight |
| `--tracking-display` | Title letter-spacing — this carries a lot of the character |
| `--tracking-mono` | Letter-spacing for metadata |
| `--tracking-ui` | Letter-spacing for body text |
| `--size-title` | Top-bar title size |

Use system font stacks. QUIETWAVE deliberately loads no web fonts: it is a
self-hosted, offline-capable app, and a blocking request to a font CDN would
undermine both.

### Geometry

| Token | Purpose |
|---|---|
| `--radius-sm` | Buttons, inputs, chips |
| `--radius-lg` | Sheets |
| `--radius-input` | The composer field — a large value gives the familiar pill |
| `--mark-radius` | Sigils, the FAB, the lock mark. `50%` for circles |
| `--bubble-in-radius` | Inbound bubble corners; accepts the four-value form |
| `--bubble-out-radius` | Outbound bubble corners |

### Optional

These have `var()` fallbacks in `base.css`, so you can omit them.

| Token | Default | Purpose |
|---|---|---|
| `--glow-accent` | none | Box-shadow bloom on accent elements |
| `--glow-text` | none | Text-shadow on titles |
| `--bar-shadow` | none | Shadow under the top bar |
| `--unread-bg` | transparent | Row tint for unread conversations |
| `--texture` | none | Full-screen overlay: scanlines, grain, grid, stars |
| `--texture-size` | auto | Background-size for that overlay |
| `--texture-opacity` | 0 | Overlay strength — keep it low enough to read through |
| `--texture-blend` | normal | Blend mode for the overlay |
| `--transform-meta` | uppercase | Case for metadata |
| `--transform-button` | uppercase | Case for button labels |

The texture renders in a fixed, non-interactive `.carrier` layer above the
content. It is the single most atmospheric token and the easiest one to
overdo — if body text is harder to read with it on, turn it down.

---

## The lexicon

`lexicon.json` is a flat map of key to string. **Every theme must define
exactly the same keys**, which the test suite enforces; a theme that invents
its own key or drops a shared one fails.

Keys are referenced two ways:

- In markup, via attributes — `data-lex` (text content),
  `data-lex-placeholder`, `data-lex-title` (also sets `aria-label`).
- In `static/js/app.js`, via `lex('some.key')`.

Both directions are tested: a key used in the markup or the JavaScript that no
lexicon defines is a test failure, so a typo cannot ship as a screen showing
`thread.snd` to a teenager.

### Writing good microcopy

The lexicon is where a theme stops being a colour scheme. Some guidance:

- **Rename the nouns, not just the verbs.** A conversation is a *channel*, an
  *uplink*, a *link*. That is what makes it feel like a different machine.
- **Delivery states are the best surface you have.** `CONFIRMED` /
  `ACKNOWLEDGED` / `GOT IT` / `LOGGED` are the same state in four different
  worlds, and they appear constantly.
- **Keep destructive confirmations plain.** `confirm.delete` should say clearly
  that messages will be erased. Theme the tone, never the meaning.
- **Watch the length of `thread.placeholder` and `thread.send`.** They share a
  narrow row on a phone. Long values wrap and clip — the composer row is the
  first place to check a new theme at 375px wide.
- **Error strings are read by someone who is stuck.** `auth.error.throttled`
  and the media failure strings should stay comprehensible even in character.

---

## Choosing the active theme

Resolution order, first match wins:

1. The viewer's saved choice in `localStorage` (set via **Station → Band**).
2. `QUIETWAVE_THEME` from the environment.
3. `quietwave`.

The server substitutes the theme id into `index.html` before serving it, so the
correct stylesheet is in the initial HTML and the app paints the right colours
on the first frame. Fetching the theme from JavaScript first would flash an
unstyled interface on every load.

An unknown value in `QUIETWAVE_THEME` fails validation at startup with a
message naming the valid options, rather than booting into a blank page.
