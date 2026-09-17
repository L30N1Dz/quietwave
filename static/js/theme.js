/* Theming and microcopy.

   A theme is two files under /static/themes/<id>/:
     theme.css     custom properties consumed by css/base.css
     lexicon.json  every user-visible string, keyed identically across themes

   Nothing here knows what any individual theme looks like, which is what
   makes adding a fifth a matter of dropping in a folder. See docs/THEMES.md.
*/

const STORAGE_KEY = 'quietwave.theme';

let registry = [];
let lexicon = {};
let activeId = document.documentElement.dataset.theme || 'quietwave';

/* localStorage throws in some privacy modes; a theme preference is never
   worth breaking the app over. */
function readStored() {
  try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
}
function writeStored(id) {
  try { localStorage.setItem(STORAGE_KEY, id); } catch { /* not important */ }
}

export function currentTheme() { return activeId; }
export function themeRegistry() { return registry; }

export async function loadRegistry() {
  try {
    const response = await fetch('/static/themes/themes.json');
    const payload = await response.json();
    registry = payload.themes || [];
  } catch {
    registry = [];
  }
  return registry;
}

export async function loadLexicon(id) {
  const response = await fetch(`/static/themes/${encodeURIComponent(id)}/lexicon.json`);
  if (!response.ok) throw new Error(`no lexicon for theme ${id}`);
  lexicon = await response.json();
  return lexicon;
}

/** Look up a themed string; falls back to the key so a gap is obvious. */
export function lex(key, fallback) {
  if (Object.prototype.hasOwnProperty.call(lexicon, key)) return lexicon[key];
  return fallback !== undefined ? fallback : key;
}

/** Fill every [data-lex*] element in a subtree from the active lexicon. */
export function applyLexicon(root = document) {
  for (const node of root.querySelectorAll('[data-lex]')) {
    node.textContent = lex(node.dataset.lex, node.textContent);
  }
  for (const node of root.querySelectorAll('[data-lex-placeholder]')) {
    node.placeholder = lex(node.dataset.lexPlaceholder, node.placeholder);
  }
  for (const node of root.querySelectorAll('[data-lex-title]')) {
    const text = lex(node.dataset.lexTitle, node.title);
    node.title = text;
    node.setAttribute('aria-label', text);
  }
  const named = lex('app.name', 'QUIETWAVE');
  document.title = named;
}

/** Swap stylesheets and reload microcopy. */
export async function applyTheme(id, { persist = true } = {}) {
  const sheet = document.getElementById('theme-sheet');
  if (sheet) sheet.href = `/static/themes/${encodeURIComponent(id)}/theme.css`;
  document.documentElement.dataset.theme = id;
  activeId = id;
  if (persist) writeStored(id);

  await loadLexicon(id);
  applyLexicon();
  syncThemeColor();
}

/** Keep the Android address bar / task switcher in step with the theme. */
function syncThemeColor() {
  const background = getComputedStyle(document.documentElement)
    .getPropertyValue('--bg').trim();
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta && background) meta.setAttribute('content', background);
}

/** Decide the boot theme: the viewer's saved choice, else the server default. */
export async function initTheme(serverDefault, available) {
  const stored = readStored();
  const chosen = stored && available.includes(stored) ? stored : serverDefault;
  await applyTheme(chosen, { persist: false });
  return chosen;
}
