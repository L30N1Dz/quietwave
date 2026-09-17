/* QUIETWAVE client.

   Plain ES modules, no build step. Three screens (locked, channel list,
   channel) swapped in place, with polling for new traffic.

   Rule observed throughout: user-supplied text is only ever written with
   textContent, never innerHTML.
*/

import { api, ApiError } from './api.js';
import {
  applyLexicon, applyTheme, currentTheme, initTheme,
  lex, loadRegistry, themeRegistry,
} from './theme.js';

/* ------------------------------------------------------------------ */
/* elements                                                            */
/* ------------------------------------------------------------------ */
const $ = (id) => document.getElementById(id);

const screens = {
  locked: $('screen-locked'),
  list:   $('screen-list'),
  thread: $('screen-thread'),
};

const lockForm      = $('lock-form');
const passphrase    = $('passphrase');
const lockError     = $('lock-error');
const threadList    = $('thread-list');
const listEmpty     = $('list-empty');
const listSubtitle  = $('list-subtitle');
const messageLog    = $('message-log');
const threadTitle   = $('thread-title');
const threadNumber  = $('thread-number');
const composer      = $('composer');
const composerInput = $('composer-input');
const sendButton    = $('btn-send');
const fileInput     = $('file-input');
const attachStrip   = $('attachment-strip');
const jumpLatest    = $('jump-latest');
const toastNode     = $('toast');

/* ------------------------------------------------------------------ */
/* state                                                               */
/* ------------------------------------------------------------------ */
const state = {
  config: null,
  lines: [],
  threads: [],
  activeThreadId: null,
  messages: [],
  attachments: [],
  pollTimer: null,
  sending: false,
  installPrompt: null,
  tempCounter: 0,
};

/* ------------------------------------------------------------------ */
/* small helpers                                                       */
/* ------------------------------------------------------------------ */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function showScreen(name) {
  for (const [key, node] of Object.entries(screens)) {
    node.hidden = key !== name;
  }
}

let toastTimer = null;
function toast(message, bad = false) {
  toastNode.textContent = message;
  toastNode.classList.toggle('toast--bad', bad);
  toastNode.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastNode.hidden = true; }, 3200);
}

function initials(name) {
  const cleaned = (name || '').replace(/[^\p{L}\p{N} ]/gu, ' ').trim();
  if (!cleaned) return '??';
  const words = cleaned.split(/\s+/);
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/* ---- time ---- */
const DAY_NAMES = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'];

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function relativeTime(iso) {
  if (!iso) return '';
  const then = new Date(iso);
  const minutes = Math.floor((Date.now() - then.getTime()) / 60000);
  if (minutes < 1) return lex('time.now');
  if (minutes < 60) return `${minutes}M`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24 && startOfDay(then) === startOfDay(new Date())) return `${hours}H`;
  const days = Math.floor((startOfDay(new Date()) - startOfDay(then)) / 86400000);
  if (days === 1) return lex('time.yesterday');
  if (days < 7) return DAY_NAMES[then.getDay()];
  return then.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' });
}

function clockTime(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit',
  });
}

function dayLabel(iso) {
  const then = new Date(iso);
  const days = Math.floor((startOfDay(new Date()) - startOfDay(then)) / 86400000);
  if (days === 0) return lex('time.today');
  if (days === 1) return lex('time.yesterday');
  return then.toLocaleDateString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

function statusLabel(message) {
  const key = {
    sending: 'status.sending',
    queued: 'status.queued',
    accepted: 'status.queued',
    sent: 'status.sent',
    delivered: 'status.delivered',
    failed: 'status.failed',
    undelivered: 'status.undelivered',
  }[message.status];
  return key ? lex(key) : '';
}

/* ------------------------------------------------------------------ */
/* lock screen                                                         */
/* ------------------------------------------------------------------ */
lockForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  lockError.hidden = true;
  const value = passphrase.value;
  if (!value) return;

  const submit = lockForm.querySelector('button[type="submit"]');
  submit.disabled = true;
  try {
    await api.login(value);
    passphrase.value = '';
    await enterStation();
  } catch (error) {
    if (error instanceof ApiError && error.isThrottled) {
      lockError.textContent = lex('auth.error.throttled');
    } else if (error instanceof ApiError) {
      lockError.textContent = lex('auth.error.wrong');
    } else {
      lockError.textContent = lex('auth.error.offline');
    }
    lockError.hidden = false;
    passphrase.select();
  } finally {
    submit.disabled = false;
  }
});

// Explicit rather than relying on implicit form submission: the Android
// keyboard's "go" key must open the station, and implicit submission is not
// dependable enough to bet the login screen on.
passphrase.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    lockForm.requestSubmit();
  }
});

function lockOut() {
  stopPolling();
  state.activeThreadId = null;
  state.threads = [];
  state.messages = [];
  showScreen('locked');
  setTimeout(() => passphrase.focus(), 60);
}

/* ------------------------------------------------------------------ */
/* channel list                                                        */
/* ------------------------------------------------------------------ */
function renderThreads() {
  threadList.replaceChildren();
  listEmpty.hidden = state.threads.length > 0;

  for (const thread of state.threads) {
    const row = el('button', 'row');
    row.type = 'button';
    row.setAttribute('role', 'listitem');
    if (thread.unread_count > 0) row.classList.add('row--unread');

    row.append(el('span', 'row__sigil', initials(thread.display_name)));

    const body = el('div', 'row__body');
    const head = el('div', 'row__head');
    head.append(
      el('span', 'row__name', thread.display_name),
      el('span', 'row__time', relativeTime(thread.last_message_at)),
    );
    body.append(head);

    const preview = el('p', 'row__preview');
    if (thread.last_message_direction === 'outbound') {
      preview.append(el('em', null, `${lex('list.you')} `));
    }
    if (thread.last_message_preview) {
      preview.append(document.createTextNode(thread.last_message_preview));
    } else if (thread.last_message_has_media) {
      preview.append(el('em', null, lex('list.attachment')));
    }
    body.append(preview);
    row.append(body);

    if (thread.unread_count > 0) {
      row.append(el('span', 'row__badge', String(Math.min(thread.unread_count, 99))));
    }

    row.addEventListener('click', () => openThread(thread.id));
    threadList.append(row);
  }
}

async function refreshThreads() {
  const payload = await api.threads();
  state.threads = payload.threads;
  renderThreads();
  updateSubtitle();
}

function updateSubtitle() {
  if (state.config && state.config.demo) {
    listSubtitle.textContent = lex('settings.demo');
    return;
  }
  const line = state.lines[0];
  listSubtitle.textContent = line ? line.pretty_number : lex('app.tagline');
}

/* ------------------------------------------------------------------ */
/* channel view                                                        */
/* ------------------------------------------------------------------ */
function activeThread() {
  return state.threads.find((t) => t.id === state.activeThreadId) || null;
}

async function openThread(threadId) {
  state.activeThreadId = threadId;
  state.messages = [];
  state.attachments = [];
  renderAttachments();
  messageLog.replaceChildren();

  const thread = activeThread();
  if (thread) {
    threadTitle.textContent = thread.display_name;
    threadNumber.textContent =
      thread.counterpart_label ? thread.counterpart_number : '';
  }
  showScreen('thread');
  autosize();

  try {
    const payload = await api.messages(threadId);
    state.messages = payload.messages;
    renderMessages({ scroll: 'instant' });
    await api.markRead(threadId);
    await refreshThreads();
  } catch (error) {
    handleError(error);
  }
  composerInput.focus();
}

function latestServerTimestamp() {
  let latest = null;
  for (const message of state.messages) {
    if (message._temp) continue;
    if (!latest || message.created_at > latest) latest = message.created_at;
  }
  return latest;
}

function mergeMessages(incoming) {
  const known = new Set(state.messages.map((m) => m.id));
  let added = 0;
  for (const message of incoming) {
    if (known.has(message.id)) continue;
    state.messages.push(message);
    added += 1;
  }
  if (added) {
    state.messages.sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
  }
  return added;
}

function nearBottom() {
  const slack = messageLog.scrollHeight - messageLog.scrollTop - messageLog.clientHeight;
  return slack < 120;
}

function scrollToBottom(behavior = 'smooth') {
  // After a re-render the final layout is not settled yet, so scrollHeight is
  // short and the newest message ends up just below the fold. Wait a frame.
  requestAnimationFrame(() => {
    messageLog.scrollTo({ top: messageLog.scrollHeight, behavior });
    jumpLatest.hidden = true;
  });
}

function renderMessages({ scroll = false } = {}) {
  const shouldStick = scroll || nearBottom();
  messageLog.replaceChildren();

  if (!state.messages.length) {
    messageLog.append(el('p', 'empty__title', lex('thread.empty')));
    return;
  }

  let lastDay = null;
  let lastDirection = null;

  state.messages.forEach((message, index) => {
    const day = startOfDay(new Date(message.created_at));
    if (day !== lastDay) {
      messageLog.append(el('div', 'daymark', dayLabel(message.created_at)));
      lastDay = day;
      lastDirection = null;
    }

    const wrapper = el('div', `msg msg--${message.direction === 'inbound' ? 'in' : 'out'}`);
    if (message.direction !== lastDirection) wrapper.classList.add('msg--first');
    const failed = message.status === 'failed' || message.status === 'undelivered';
    if (failed) wrapper.classList.add('msg--failed');
    if (message._temp) wrapper.classList.add('msg--pending');
    lastDirection = message.direction;

    if (message.media && message.media.length) {
      wrapper.append(renderMedia(message));
    }
    if (message.body) {
      wrapper.append(el('div', 'bubble', message.body));
    }

    // One timestamp per run of messages, not per message: a stamp on every
    // line turns a conversation into a log file.
    const next = state.messages[index + 1];
    const endsRun = !next
      || next.direction !== message.direction
      || startOfDay(new Date(next.created_at)) !== day;
    if (endsRun || failed || message._temp) {
      wrapper.append(renderMeta(message));
    }

    messageLog.append(wrapper);
  });

  if (shouldStick) scrollToBottom(scroll === 'instant' ? 'auto' : 'smooth');
}

function renderMedia(message) {
  const holder = el('div', 'msg__media');
  for (const item of message.media) {
    const source = item.localUrl || item.url;
    if (!source) {
      const label = item.state === 'failed' ? lex('media.failed') : lex('media.pending');
      holder.append(el('div', 'msg__media--pending', label));
      continue;
    }
    if ((item.content_type || '').startsWith('video/')) {
      const video = el('video');
      video.src = source;
      video.controls = true;
      video.preload = 'metadata';
      holder.append(video);
    } else {
      const image = el('img');
      image.src = source;
      image.alt = '';
      image.loading = 'lazy';
      // An image landing changes the log's height; keep the newest message
      // in view if that is where the reader already was.
      image.addEventListener('load', () => {
        if (nearBottom()) scrollToBottom('auto');
      });
      image.addEventListener('click', () => openLightbox(source, item.content_type));
      holder.append(image);
    }
  }
  return holder;
}

function renderMeta(message) {
  const meta = el('div', 'msg__meta');
  meta.append(el('span', null, clockTime(message.created_at)));

  if (message.direction === 'outbound') {
    const label = statusLabel(message);
    if (label) meta.append(el('span', null, label));
  }

  if (message.status === 'failed' || message.status === 'undelivered') {
    meta.classList.add('msg__meta--failed');
    if (message.error) meta.title = message.error;
    const hasMedia = message.media && message.media.length;
    if (message.body && !hasMedia) {
      const retry = el('button', 'msg__retry', lex('msg.retry'));
      retry.type = 'button';
      retry.addEventListener('click', () => retrySend(message));
      meta.append(retry);
    }
  }
  return meta;
}

messageLog.addEventListener('scroll', () => {
  jumpLatest.hidden = nearBottom();
});
jumpLatest.querySelector('button').addEventListener('click', () => scrollToBottom());

/* ------------------------------------------------------------------ */
/* composing                                                           */
/* ------------------------------------------------------------------ */
function autosize() {
  composerInput.style.height = 'auto';
  composerInput.style.height = `${Math.min(composerInput.scrollHeight, 148)}px`;
}

function updateSendEnabled() {
  const hasText = composerInput.value.trim().length > 0;
  sendButton.disabled = !hasText && state.attachments.length === 0;
}

composerInput.addEventListener('input', () => { autosize(); updateSendEnabled(); });

composerInput.addEventListener('keydown', (event) => {
  // Enter sends on a physical keyboard; on a phone Enter must stay a newline.
  const isDesktop = window.matchMedia('(pointer: fine)').matches;
  if (event.key === 'Enter' && !event.shiftKey && isDesktop) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

$('btn-attach').addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', () => {
  const limit = state.config ? state.config.max_upload_bytes : 5 * 1024 * 1024;
  for (const file of fileInput.files) {
    if (file.size > limit) {
      toast(`${file.name} > ${Math.round(limit / 1024)}KB`, true);
      continue;
    }
    if (state.attachments.length >= 10) break;
    state.attachments.push(file);
  }
  fileInput.value = '';
  renderAttachments();
  updateSendEnabled();
});

function renderAttachments() {
  attachStrip.replaceChildren();
  attachStrip.hidden = state.attachments.length === 0;

  state.attachments.forEach((file, index) => {
    const chip = el('div', 'chip');
    chip.append(el('span', 'chip__name', file.name));
    const drop = el('button', 'chip__drop');
    drop.type = 'button';
    drop.setAttribute('aria-label', `Remove ${file.name}`);
    const icon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#ico-x');
    icon.append(use);
    drop.append(icon);
    drop.addEventListener('click', () => {
      state.attachments.splice(index, 1);
      renderAttachments();
      updateSendEnabled();
    });
    chip.append(drop);
    attachStrip.append(chip);
  });
}

composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  await transmit(composerInput.value.trim(), state.attachments.slice());
});

async function retrySend(message) {
  state.messages = state.messages.filter((m) => m.id !== message.id);
  renderMessages();
  await transmit(message.body, []);
}

async function transmit(body, files) {
  if (!body && !files.length) return;
  if (!state.activeThreadId) return;

  composerInput.value = '';
  autosize();
  state.attachments = [];
  renderAttachments();
  updateSendEnabled();

  const threadId = state.activeThreadId;
  const temp = {
    id: `temp-${++state.tempCounter}`,
    thread_id: threadId,
    direction: 'outbound',
    body,
    media: files.map((file, index) => ({
      index,
      content_type: file.type,
      state: 'stored',
      localUrl: URL.createObjectURL(file),
    })),
    status: 'sending',
    error: null,
    created_at: new Date().toISOString(),
    _temp: true,
  };
  state.messages.push(temp);
  renderMessages({ scroll: true });

  state.sending = true;
  try {
    const payload = await api.send(threadId, body, files);
    const index = state.messages.findIndex((m) => m.id === temp.id);
    if (index >= 0) {
      if (threadId === state.activeThreadId) {
        state.messages[index] = payload.message;
      } else {
        state.messages.splice(index, 1);
      }
    }
    if (payload.message.status === 'failed') {
      toast(payload.message.error || lex('toast.sendFailed'), true);
    }
  } catch (error) {
    temp.status = 'failed';
    temp._temp = false;
    temp.error = error instanceof ApiError ? error.detail : lex('auth.error.offline');
    toast(temp.error || lex('toast.sendFailed'), true);
    if (error instanceof ApiError && error.isAuth) lockOut();
  } finally {
    state.sending = false;
  }

  renderMessages({ scroll: true });
  try { await refreshThreads(); } catch { /* the poll will catch up */ }
}

/* ------------------------------------------------------------------ */
/* lightbox                                                            */
/* ------------------------------------------------------------------ */
function openLightbox(source, contentType) {
  const overlay = el('div', 'lightbox');
  const node = (contentType || '').startsWith('video/') ? el('video') : el('img');
  node.src = source;
  if (node.tagName === 'VIDEO') node.controls = true;
  overlay.append(node);
  overlay.addEventListener('click', () => overlay.remove());
  document.body.append(overlay);
}

/* ------------------------------------------------------------------ */
/* sheets                                                              */
/* ------------------------------------------------------------------ */
function openSheet(id) {
  const sheet = $(id);
  sheet.hidden = false;
  const field = sheet.querySelector('input');
  if (field) setTimeout(() => field.focus(), 60);
}
function closeSheets() {
  for (const sheet of document.querySelectorAll('.sheet-backdrop')) sheet.hidden = true;
}

for (const button of document.querySelectorAll('[data-close-sheet]')) {
  button.addEventListener('click', closeSheets);
}
for (const backdrop of document.querySelectorAll('.sheet-backdrop')) {
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) closeSheets();
  });
}
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  const lightbox = document.querySelector('.lightbox');
  if (lightbox) { lightbox.remove(); return; }
  closeSheets();
});

/* ---- new channel ---- */
$('btn-new').addEventListener('click', () => {
  $('new-number').value = '';
  $('new-label').value = '';
  $('new-error').hidden = true;
  openSheet('sheet-new');
});

$('new-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const errorNode = $('new-error');
  errorNode.hidden = true;
  try {
    const payload = await api.createThread(
      $('new-number').value, $('new-label').value.trim() || null,
    );
    closeSheets();
    await refreshThreads();
    await openThread(payload.thread.id);
  } catch (error) {
    errorNode.textContent = error instanceof ApiError && error.detail
      ? error.detail : lex('new.error.invalid');
    errorNode.hidden = false;
  }
});

/* ---- channel options ---- */
$('btn-thread-menu').addEventListener('click', () => {
  const thread = activeThread();
  if (!thread) return;
  $('edit-label').value = thread.counterpart_label || '';
  openSheet('sheet-thread');
});

$('thread-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const thread = activeThread();
  if (!thread) return;
  try {
    const payload = await api.patchThread(thread.id, $('edit-label').value.trim());
    const updated = payload.thread;
    threadTitle.textContent = updated.display_name;
    threadNumber.textContent = updated.counterpart_label
      ? updated.counterpart_number : '';
    closeSheets();
    await refreshThreads();
  } catch (error) {
    handleError(error);
  }
});

$('btn-delete-thread').addEventListener('click', async () => {
  const thread = activeThread();
  if (!thread) return;
  if (!window.confirm(lex('confirm.delete'))) return;
  try {
    await api.deleteThread(thread.id);
    closeSheets();
    state.activeThreadId = null;
    await refreshThreads();
    showScreen('list');
    toast(lex('toast.deleted'));
  } catch (error) {
    handleError(error);
  }
});

/* ---- settings ---- */
$('btn-settings').addEventListener('click', () => {
  const line = state.lines[0];
  $('settings-line').textContent = line
    ? `${line.pretty_number}${line.label ? ` · ${line.label}` : ''}`
    : '—';
  $('settings-meta').textContent = state.config && state.config.demo
    ? lex('settings.demo') : '';
  renderThemePicker();
  openSheet('sheet-settings');
});

$('btn-logout').addEventListener('click', async () => {
  try { await api.logout(); } catch { /* locking out regardless */ }
  closeSheets();
  lockOut();
});

$('btn-back').addEventListener('click', () => {
  state.activeThreadId = null;
  showScreen('list');
  refreshThreads().catch(handleError);
});

/* ---- theme picker ---- */
function renderThemePicker() {
  const holder = $('theme-picker');
  holder.replaceChildren();

  for (const theme of themeRegistry()) {
    const option = el('button', 'theme-opt');
    option.type = 'button';
    option.setAttribute('role', 'radio');
    option.setAttribute('aria-checked', String(theme.id === currentTheme()));

    const swatch = el('span', 'theme-opt__swatch');
    swatch.style.background = theme.swatch.bg;
    const dot = el('span', 'theme-opt__dot');
    dot.style.background = theme.swatch.accent;
    swatch.append(dot);

    const text = el('span', 'theme-opt__text');
    text.append(
      el('span', 'theme-opt__name', theme.name),
      el('span', 'theme-opt__desc', theme.description),
    );

    option.append(swatch, text, el('span', 'theme-opt__tick', '●'));
    option.addEventListener('click', async () => {
      await applyTheme(theme.id);
      renderThemePicker();
      renderThreads();
      if (state.activeThreadId) renderMessages();
      updateSubtitle();
    });
    holder.append(option);
  }
}

/* ---- install to home screen (Android/Chrome) ---- */
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  state.installPrompt = event;
  $('btn-install').hidden = false;
});

$('btn-install').addEventListener('click', async () => {
  if (!state.installPrompt) return;
  state.installPrompt.prompt();
  await state.installPrompt.userChoice;
  state.installPrompt = null;
  $('btn-install').hidden = true;
});

/* ------------------------------------------------------------------ */
/* polling                                                             */
/* ------------------------------------------------------------------ */
function startPolling() {
  stopPolling();
  const interval = (state.config && state.config.poll_interval_ms) || 5000;
  state.pollTimer = setInterval(tick, interval);
}
function stopPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function tick() {
  if (document.hidden || state.sending) return;
  try {
    if (state.activeThreadId) {
      const payload = await api.messages(state.activeThreadId, latestServerTimestamp());
      if (payload.messages.length) {
        const stick = nearBottom();
        const added = mergeMessages(payload.messages);
        if (added) {
          renderMessages({ scroll: stick });
          await api.markRead(state.activeThreadId);
        }
      }
    }
    await refreshThreads();
  } catch (error) {
    if (error instanceof ApiError && error.isAuth) lockOut();
    // Network blips are expected on a phone; the next tick retries.
  }
}

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && state.config) tick();
});
window.addEventListener('online', () => toast(lex('toast.online')));
window.addEventListener('offline', () => toast(lex('toast.offline'), true));

/* ------------------------------------------------------------------ */
/* boot                                                                */
/* ------------------------------------------------------------------ */
function handleError(error) {
  if (error instanceof ApiError && error.isAuth) { lockOut(); return; }
  toast(error instanceof ApiError ? error.message : lex('auth.error.offline'), true);
}

async function enterStation() {
  const linesPayload = await api.lines();
  state.lines = linesPayload.lines;
  await refreshThreads();
  showScreen('list');
  startPolling();
}

async function boot() {
  state.config = await api.config();

  const registry = await loadRegistry();
  const available = registry.length
    ? registry.map((t) => t.id)
    : (state.config.themes || []);
  await initTheme(state.config.theme, available);
  applyLexicon();

  if (state.config.authenticated) {
    try {
      await enterStation();
    } catch (error) {
      handleError(error);
      lockOut();
    }
  } else {
    lockOut();
  }

  autosize();
  updateSendEnabled();
}

boot().catch((error) => {
  console.error('boot failed', error);
  lockOut();
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {
      /* offline shell is a bonus, not a requirement */
    });
  });
}
