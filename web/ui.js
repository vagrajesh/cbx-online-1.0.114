// Small DOM helpers shared by every view. No framework — just functions.
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const h = (strings, ...vals) => strings.reduce((a, s, i) => a + s + (i < vals.length ? (vals[i] instanceof Raw ? vals[i].s : esc(vals[i])) : ''), '');
class Raw { constructor(s) { this.s = s; } }
export const raw = (s) => new Raw(s);
export const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };

let toastTimer;
export function toast(msg, kind = '') {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = String(msg ?? ''); t.className = 'toast on ' + (kind || ''); t.setAttribute('role', kind === 'err' ? 'alert' : 'status');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => (t.className = 'toast'), kind === 'err' ? 4500 : 2800);
}

// at most n characters, cut at a word, with … when something was left out
export const clip = (t, n) => { const x = String(t ?? '').replace(/\s+/g, ' ').trim(); if (x.length <= n) return x; let c = x.slice(0, n - 1); if (c.lastIndexOf(' ') > n / 2) c = c.slice(0, c.lastIndexOf(' ')); return c.replace(/[\s,;:—–-]+$/, '') + '…'; };

export function pill(text, kind = 'neutral', run = false) { return `<span class="pill ${kind}${run ? ' run' : ''}">${esc(text)}</span>`; }

export const STATUS_PILL = {
  sent: ['Sent · being assessed', 'neutral'], new: ['New', 'neutral'], interview: ['Interview', 'info', true], working: ['Working', 'info'],
  testing: ['Testing', 'warn', true], ready: ['Ready', 'good'], declined: ['Declined', 'crit'], cancelled: ['Cancelled', 'neutral'],
  queued: ['Queued', 'neutral'], read: ['Read', 'info'], ok: ['Ready', 'good'], review: ['Needs you', 'warn'], failed: ['Failed', 'crit'],
  uploading: ['Uploading', 'neutral'], learning: ['C-EXA learning', 'info', true], running: ['Working', 'info', true], done: ['Ready', 'good'],
  todo: ['To do', 'neutral'], live: ['Live', 'good'], draft: ['Draft', 'neutral'], patch: ['Patch', 'warn'], below_gate: ['Not live', 'crit'],
};
export const statusPill = (s) => { const [t, k, r] = STATUS_PILL[s] || [s, 'neutral']; return pill(t, k, r); };

export function fmtDate(iso, opts = {}) {
  if (!iso) return '—';
  const d = new Date(iso); const now = new Date();
  if (Number.isNaN(d.getTime())) return String(iso);
  const same = d.toDateString() === now.toDateString();
  if (opts.rel && d <= now) {
    const s = (now - d) / 1000;
    if (s < 60) return 'just now'; if (s < 3600) return `${Math.floor(s / 60)} min ago`; if (s < 86400 && same) return d.toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' });
    const days = Math.floor(s / 86400); if (days === 1) return 'yesterday'; if (days < 7) return d.toLocaleDateString('en-ZA', { weekday: 'short' });
  }
  if (opts.year) return d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' });
  return same ? d.toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' }) : d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' }) + (opts.time ? ' ' + d.toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' }) : '');
}
export const fmtNum = (n, dp = 0) => (n === null || n === undefined || n === '' ? '—' : Number(n).toLocaleString('en-ZA', { minimumFractionDigits: dp, maximumFractionDigits: dp }).replace(/,/g, ' '));
export const fmtMoney = (n) => (n === null || n === undefined || n === '' ? '—' : 'R ' + fmtNum(n, 2));
export const pct = (x, dp = 1) => (x === null || x === undefined ? '—' : (x * 100).toFixed(dp) + '%');

// Dialogs stack: Escape closes the top-most one only; clicking the backdrop closes (a drag that
// started inside the dialog — selecting text — does not); focus goes to the first field.
export function modal({ title, body, footer, onClose }) {
  const bg = el(`<div class="modal-bg"><div class="modal" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="hd"><h2>${esc(title)}</h2><button class="btn sm ghost" data-close>Close</button></div><div class="bd"></div>${footer ? '<div class="ft"></div>' : ''}</div></div>`);
  const bd = bg.querySelector('.bd'); if (typeof body === 'string') bd.innerHTML = body; else bd.append(body);
  if (footer) { const ft = bg.querySelector('.ft'); if (typeof footer === 'string') ft.innerHTML = footer; else ft.append(footer); }
  // 1.0.83: one way out — the footer's Cancel/Close when there is one, else the header's Close (Escape and the backdrop always work)
  if (bg.querySelector('.ft [data-close]')) bg.querySelector('.hd [data-close]').remove();
  const opener = document.activeElement;
  let closed = false;
  const onKey = (e) => {
    if (!bg.isConnected) { document.removeEventListener('keydown', onKey); return; } // removed by the caller (m.root.remove())
    if (e.key !== 'Escape') return;
    const all = document.querySelectorAll('.modal-bg'); if (all[all.length - 1] !== bg) return;
    e.preventDefault(); close();
  };
  const close = () => {
    if (closed) return; closed = true;
    document.removeEventListener('keydown', onKey);
    bg.remove();
    if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus();
    onClose && onClose();
  };
  let downOnBg = false;
  bg.addEventListener('mousedown', (e) => { downOnBg = e.target === bg; });
  bg.addEventListener('click', (e) => { if ((e.target === bg && downOnBg) || e.target.closest('[data-close]')) close(); downOnBg = false; });
  document.addEventListener('keydown', onKey);
  document.body.append(bg);
  const first = bg.querySelector('.bd input:not([type=hidden]):not([disabled]), .bd select, .bd textarea, .bd button, .ft .btn.primary, .ft button');
  if (first) setTimeout(() => first.focus(), 0);
  return { root: bg, close, body: bd };
}

export function confirmBox(text, okLabel = 'Yes') {
  return new Promise((res) => {
    const m = modal({ title: 'Are you sure?', body: `<p>${esc(text)}</p>`, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>${esc(okLabel)}</button>`, onClose: () => res(false) });
    m.root.querySelector('[data-ok]').onclick = () => { m.root.remove(); res(true); };
  });
}

export function fileDrop({ label, hint, multiple = true, accept = '', onFiles }) {
  const box = el(`<label class="file-in"><b>${esc(label)}</b>${esc(hint || '')}<input type="file" ${multiple ? 'multiple' : ''} accept="${esc(accept)}"><div class="filelist"></div></label>`);
  const input = box.querySelector('input'); const list = box.querySelector('.filelist');
  const handle = (files) => { list.innerHTML = [...files].map((f) => `<span>${esc(f.name)}</span>`).join(''); onFiles([...files]); };
  input.onchange = () => handle(input.files);
  box.ondragover = (e) => { e.preventDefault(); box.classList.add('over'); };
  box.ondragleave = () => box.classList.remove('over');
  box.ondrop = (e) => { e.preventDefault(); box.classList.remove('over'); handle(e.dataTransfer.files); };
  return box;
}

export const initials = (name = '') => String(name).split(/[\s\-]+/).filter((p) => /^[\p{L}\d]/u.test(p)).slice(0, 2).map((p) => p[0].toUpperCase()).join('') || '?';
export const avatar = (name, cls = '') => `<div class="avatar ${cls}">${esc(initials(name))}</div>`;

// The AI's display name comes from branding (app.js sets it at boot). "C-EXA" → "CE" on her avatar;
// a one-word name takes its first two letters, a longer one its initials.
const AI = { name: 'C-EXA', initials: 'CE' };
export function setAiName(name) {
  AI.name = name || 'C-EXA';
  const parts = AI.name.split(/[\s\-]+/).filter(Boolean);
  AI.initials = (parts.length > 1 ? parts.slice(0, 2).map((p) => p[0]) : [AI.name.slice(0, 2)]).join('').toUpperCase() || 'CE';
}
export const aiName = () => AI.name;
export const aiInitials = () => AI.initials;
export const aiAvatar = () => `<div class="avatar" style="background:var(--ai-soft);color:var(--ai)">${esc(AI.initials)}</div>`;

// Minimal markdown-ish rendering for C-EXA text: paragraphs, **bold**, *italic*, line breaks, bullets.
export function md(text) {
  const blocks = String(text || '').split(/\n{2,}/).map((b) => {
    if (/^(-|\*)\s/m.test(b)) return '<ul>' + b.split('\n').filter(Boolean).map((l) => `<li>${inline(l.replace(/^(-|\*)\s+/, ''))}</li>`).join('') + '</ul>';
    return `<p>${inline(b).replace(/\n/g, '<br>')}</p>`;
  });
  return `<div class="md">${blocks.join('')}</div>`;
  function inline(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/\*(.+?)\*/g, '<i>$1</i>').replace(/`(.+?)`/g, '<code>$1</code>'); }
}

// Cancel a request from anywhere (user or admin). Resolves true when cancelled.
export function cancelRequestDialog(api, r, { after } = {}) {
  return new Promise((res) => {
    const live = r.status === 'ready';
    const body = el(`<div style="display:flex;flex-direction:column;gap:12px"><p>Cancel <b>${esc(r.title || 'this request')}</b> (#${esc(r.number)})?${live ? ' It is live — the ticket closes; the tool itself stays until an admin removes it in the Builder.' : ''} Nothing is deleted; the history stays.</p><div class="field"><label>Why? (optional — the other side sees this)</label><input type="text" id="c-note" placeholder="e.g. no longer needed / doing it another way"></div></div>`);
    const m = modal({ title: 'Cancel request', body, footer: `<button class="btn" data-close>Keep it</button><button class="btn primary" id="c-ok" style="background:var(--crit);border-color:var(--crit)">Cancel request</button>`, onClose: () => res(false) });
    m.root.querySelector('#c-ok').onclick = async () => {
      try { const out = await api.post(`/api/requests/${r.id}/cancel`, { note: body.querySelector('#c-note').value }); m.root.remove(); toast(out.switched_off && out.switched_off.length ? `Cancelled · switched off ${out.switched_off.join(', ')}` : 'Request cancelled'); after && after(out); res(true); }
      catch (e) { toast(e.detail || 'Could not cancel', 'err'); }
    };
  });
}

// ---- 1.0.103: which bot is on a document now — a live chip ("● Ivy is reading — 0:42"); amber when the bot has shown no
// sign of life for 3 minutes ("● Ivy — no sign of life for 4 min"); "In line — 3rd" for a queue. One ticker counts every
// chip on the page on, every second, and stops when there is none. m = the server's mark {bot, step, secs, quiet, stuck}.
const BUSY_STEP = { sorting: 'sorting', reading: 'reading', checking: 'checking', matching: 'matching', sap: 'making SAP rows', testing: 'testing', pictures: 'looking at the pictures' };
const BUSY_STUCK = 180;
const busyClock = (s) => { s = Math.max(0, Math.floor(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(x).padStart(2, '0'); };
function busyWords(d) {
  const gone = (Date.now() - Number(d.at)) / 1000, quiet = Number(d.quiet || 0) + gone, stuck = d.stuck === '1' || quiet > BUSY_STUCK;
  const step = BUSY_STEP[d.step] || 'working';
  if (stuck) return [true, `${d.bot} — no sign of life for ${Math.max(1, Math.floor(quiet / 60))} min`];
  return [false, d.short === '1' ? `${step} ${busyClock(Number(d.secs || 0) + gone)}` : `${d.bot} is ${step} — ${busyClock(Number(d.secs || 0) + gone)}`];
}
let busyTick = null;
function busyTicker() {
  if (busyTick) return;
  busyTick = setInterval(() => {
    const chips = document.querySelectorAll('.busychip[data-live]');
    if (!chips.length) { clearInterval(busyTick); busyTick = null; return; }
    chips.forEach((c) => { const [stuck, text] = busyWords(c.dataset); c.classList.toggle('stuck', stuck); c.lastChild.textContent = text; });
  }, 1000);
}
export function busyChipHtml(m, short = false) {
  const d = { bot: String(m.bot || ''), step: String(m.step || ''), secs: String(Number(m.secs) || 0), quiet: String(Number(m.quiet) || 0), stuck: m.stuck ? '1' : '0', at: String(Date.now()), short: short ? '1' : '0' };
  const [stuck, text] = busyWords(d);
  setTimeout(busyTicker, 0);
  return `<span class="busychip${stuck ? ' stuck' : ''}" data-live="1" role="status" data-bot="${esc(d.bot)}" data-step="${esc(d.step)}" data-secs="${d.secs}" data-quiet="${d.quiet}" data-stuck="${d.stuck}" data-at="${d.at}" data-short="${d.short}"><i aria-hidden="true"></i><span>${esc(text)}</span></span>`;
}
export function busyChip(m, short = false) {
  const w = document.createElement('span');
  w.innerHTML = busyChipHtml(m, short);
  return w.firstChild;
}
const ordinal = (n) => { const s = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
export const lineChipHtml = (n) => `<span class="busychip line"><i aria-hidden="true"></i><span>In line — ${esc(ordinal(Number(n) || 0))}</span></span>`;
export function lineChip(n) {
  const w = document.createElement('span');
  w.innerHTML = lineChipHtml(n);
  return w.firstChild;
}
