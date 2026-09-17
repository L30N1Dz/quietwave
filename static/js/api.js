/* Thin wrappers over the station API.
   Every call funnels through handle(), so callers deal with one error type. */

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed (${status})`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }
  get isAuth() { return this.status === 401; }
  get isThrottled() { return this.status === 429; }
}

async function handle(response) {
  if (response.status === 204) return null;

  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = null; }
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload && payload.detail);
  }
  return payload;
}

function jsonRequest(method, url, body) {
  return fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(handle);
}

function getRequest(url) {
  return fetch(url, { credentials: 'same-origin' }).then(handle);
}

export const api = {
  config:  () => getRequest('/api/config'),
  session: () => getRequest('/api/session'),

  login:  (passphrase) => jsonRequest('POST', '/auth/login', { passphrase }),
  logout: () => jsonRequest('POST', '/auth/logout'),

  lines: () => getRequest('/api/lines'),

  threads: () => getRequest('/api/threads'),

  createThread: (counterpart_number, counterpart_label) =>
    jsonRequest('POST', '/api/threads', { counterpart_number, counterpart_label }),

  patchThread: (id, counterpart_label) =>
    jsonRequest('PATCH', `/api/threads/${encodeURIComponent(id)}`, { counterpart_label }),

  deleteThread: (id) =>
    jsonRequest('DELETE', `/api/threads/${encodeURIComponent(id)}`),

  markRead: (id) =>
    jsonRequest('POST', `/api/threads/${encodeURIComponent(id)}/read`),

  messages: (id, after) => {
    const query = after ? `?after=${encodeURIComponent(after)}` : '';
    return getRequest(`/api/threads/${encodeURIComponent(id)}/messages${query}`);
  },

  send: (id, body, files) => {
    const form = new FormData();
    form.append('body', body || '');
    for (const file of files || []) form.append('attachments', file, file.name);
    return fetch(`/api/threads/${encodeURIComponent(id)}/messages`, {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    }).then(handle);
  },
};
