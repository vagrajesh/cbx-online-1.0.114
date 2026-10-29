// Tiny API client. All calls are same-origin, cookie-authenticated.
export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; this.detail = detail; }
}

const OFFLINE = "Can't reach the server. Check your connection and try again.";
const GENERIC = { 400: "That didn't work. Check what you entered and try again.", 401: 'Please sign in.', 403: "You don't have access to this.", 404: "That isn't here any more.", 413: 'That file is too big.', 500: 'Something went wrong on our side. It has been logged.' };

async function call(method, url, body, opts = {}) {
  const init = { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
  // 1.0.113: the browser's time on the sign-in calls, so the owner hears when the server's clock is off (never used to accept a code)
  if (String(url).startsWith('/api/auth/')) init.headers['X-Client-Time'] = String(Date.now());
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  let r;
  try { r = await fetch(url, init); }
  catch { throw new ApiError(0, OFFLINE); } // network down / server not running / request aborted
  if (r.status === 401 && !opts.quiet) { window.dispatchEvent(new CustomEvent('cbx:unauth')); }
  // Bodies are usually JSON, but a proxy error page, an empty 204 or the SPA shell can come back as text.
  const text = await r.text();
  const ct = r.headers.get('content-type') || '';
  let data = text;
  if (ct.includes('application/json') || /^\s*[[{]/.test(text)) { try { data = text ? JSON.parse(text) : null; } catch { data = text; } }
  if (!r.ok) {
    let detail = data && typeof data === 'object' ? data.detail : '';
    if (Array.isArray(detail)) detail = detail.map((d) => (d && d.msg) || d).join(' '); // FastAPI validation errors
    if (typeof detail !== 'string' || !detail) detail = GENERIC[r.status] || (r.status >= 500 ? GENERIC[500] : r.statusText || `Error ${r.status}`);
    throw new ApiError(r.status, detail);
  }
  return data;
}

export const api = {
  get: (u, o) => call('GET', u, undefined, o),
  post: (u, b, o) => call('POST', u, b, o),
  patch: (u, b, o) => call('PATCH', u, b, o),
  put: (u, b, o) => call('PUT', u, b, o),
  del: (u, o) => call('DELETE', u, undefined, o),
  upload: (u, files, fields = {}, o) => {
    const fd = new FormData();
    for (const f of files || []) fd.append('files', f, f.name);
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    return call('POST', u, fd, o);
  },
};
