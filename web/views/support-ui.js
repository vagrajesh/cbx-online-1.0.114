// Support round 2 (1.0.109, 23 Oct): the ticket page's pieces — the conversation as calm cards, the reply editor (pictures where
// they are pasted, like Teams), previews of attachments, Ask AXL, and quick find. Used by tickets.js (the requester's page and
// the desk's) and support.js (the desk's lists).
import { api } from '../api.js';
import { esc, toast, fmtDate, modal, initials } from '../ui.js';

// ---- small helpers ----------------------------------------------------------------------------------------------------------
export const fileUrl = (tid, f, how = '') => `/api/support/tickets/${tid}/files/${f.n}${how === 'inline' ? '/inline' : how === 'view' ? '?view=1' : ''}`;
const PIC = /\.(png|jpe?g|gif|webp|bmp)$/i;
export const isPic = (name) => PIC.test(name || '');
export function fmtSize(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
const EXT_KIND = { pdf: 'PDF', doc: 'DOC', docx: 'DOC', xls: 'XLS', xlsx: 'XLS', csv: 'CSV', txt: 'TXT', eml: 'MAIL', msg: 'MAIL', zip: 'ZIP', ppt: 'PPT', pptx: 'PPT', tif: 'TIF', tiff: 'TIF' };
const extOf = (name) => (String(name || '').split('.').pop() || '').toLowerCase();
const fileIcon = (name) => `<span class="tk-fi" data-k="${esc(EXT_KIND[extOf(name)] || 'FILE')}" aria-hidden="true">${esc(EXT_KIND[extOf(name)] || 'FILE')}</span>`;

// The words of a mail, with the quoted chain (an Outlook "From: … Sent:" block, "On … wrote:", "-----Original Message-----",
// "> " lines) folded away under "Show quoted text".
const QUOTE = /^\s*(?:(?:From|Van|De|Von)\s*:.*\n\s*(?:Sent|Date|Datum|Envoyé|Gesendet)\s*:|On .{4,80} wrote:\s*$|-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,}|_{10,}\s*$|>)/im;
export function splitQuoted(text) {
  const t = String(text || '');
  const m = QUOTE.exec(t);
  if (!m || m.index < 1) return [t, ''];
  const head = t.slice(0, m.index).replace(/\s+$/, '');
  const rest = t.slice(m.index).trim();
  return head && rest.length > 40 ? [head, rest] : [t, ''];
}
const textHtml = (s) => esc(s).replace(/\n/g, '<br>');

// The editor's safe HTML (the server keeps only p br b strong i em ul ol li and <img data-n>), shown again through the same
// short list — never innerHTML of what came from the server.
const KEEP = new Set(['P', 'BR', 'B', 'STRONG', 'I', 'EM', 'UL', 'OL', 'LI']);
function richNode(html, picFor) {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  const out = document.createElement('div');
  out.className = 'tk-rich';
  const walk = (src, dst) => {
    src.childNodes.forEach((n) => {
      if (n.nodeType === 3) { dst.append(document.createTextNode(n.nodeValue)); return; }
      if (n.nodeType !== 1) return;
      if (n.tagName === 'IMG') { const p = picFor(n.getAttribute('data-n')); if (p) dst.append(p); return; }
      if (KEEP.has(n.tagName)) { const e = document.createElement(n.tagName.toLowerCase()); dst.append(e); walk(n, e); return; }
      walk(n, dst);  // anything else: its words only
    });
  };
  walk(doc.body, out);
  return out;
}

// ---- the conversation -------------------------------------------------------------------------------------------------------
const SIDE = { requester: 'them', cexa: 'axl', staff: 'desk', note: 'note', admin: 'note' };

/** Draw the thread into `box`. t: the ticket (from GET /tickets/{id}); onPreview(n): open the preview at file n. */
export function drawThread(box, t, { admin = false, onPreview } = {}) {
  box.innerHTML = '';
  for (const m of t.thread) box.append(messageNode(t, m, { admin, onPreview }));
}

/** Put a node into the thread where its time belongs (the conversation is oldest first). */
export function placeByTime(box, node, at) {
  const after = [...box.children].find((x) => x.dataset.at && at && x.dataset.at > at);
  if (after) box.insertBefore(node, after); else box.append(node);
}

function messageNode(t, m, { admin, onPreview }) {
  if (m.quiet) {
    const q = document.createElement('div');
    q.className = 'tk-sys';
    q.dataset.at = m.at || '';
    // 1.0.111: + "It belongs here" (the reverse: the mail joins this ticket as the requester's reply)
    q.innerHTML = `<span>${esc(m.text)} · ${esc(fmtDate(m.at, { rel: true }))}</span>${m.new_problem ? ` <button type="button" class="btn sm ghost" data-newprob="${m.new_problem}">Make it a new ticket</button> <button type="button" class="btn sm ghost" data-belongs="${m.new_problem}">It belongs here</button>` : ''}`;
    return q;
  }
  const side = SIDE[m.who] || 'them';
  const card = document.createElement('div');
  card.className = `tk-m ${side}`;
  card.dataset.at = m.at || '';
  const label = m.who === 'note' ? 'Note — the team only' : m.who === 'admin' ? 'Admins only' : '';
  const how = m.mailed ? 'e-mailed' : m.via === 'mail' ? 'by e-mail' : '';
  card.innerHTML = `<div class="tk-m-hd">${side === 'axl' ? '' : `<span class="tk-av" aria-hidden="true">${esc(initials(m.name || '?'))}</span>`}<b>${esc(m.name || '')}</b>
    <span class="tk-m-meta">${label ? esc(label) + ' · ' : ''}${esc(fmtDate(m.at, { rel: true }))}${how ? ' · ' + esc(how) : ''}</span></div><div class="tk-m-bd"></div>`;
  const bd = card.querySelector('.tk-m-bd');
  const files = m.files || [];
  const byN = new Map(files.map((f) => [String(f.n), f]));
  const inBody = new Set();
  if (m.html) {
    bd.append(richNode(m.html, (n) => {
      const f = byN.get(String(n));
      if (!f) return null;
      inBody.add(f.n);
      const a = document.createElement('button');
      a.type = 'button'; a.className = 'tk-inl'; a.title = f.name;
      a.dataset.prev = f.n;
      const img = document.createElement('img');
      img.src = fileUrl(t.id, f, 'view'); img.alt = f.name; img.loading = 'lazy';
      a.append(img);
      return a;
    }));
  } else if (m.text) {
    const [head, quoted] = splitQuoted(m.text);
    const tx = document.createElement('div');
    tx.className = 'tk-text';
    tx.innerHTML = textHtml(head);
    bd.append(tx);
    if (quoted) {
      const more = document.createElement('div');
      more.className = 'tk-quoted';
      more.innerHTML = `<button type="button" class="tk-qbtn">Show quoted text</button><div class="tk-qtext" hidden>${textHtml(quoted)}</div>`;
      const b = more.querySelector('button'), q = more.querySelector('.tk-qtext');
      b.onclick = () => { q.hidden = !q.hidden; b.textContent = q.hidden ? 'Show quoted text' : 'Hide quoted text'; };
      bd.append(more);
    }
  }
  const pics = files.filter((f) => f.picture && !inBody.has(f.n));
  const docs = files.filter((f) => !f.picture && !inBody.has(f.n));
  if (pics.length) {
    const g = document.createElement('div');
    g.className = 'tk-grid';
    const shown = pics.length > 4 ? pics.slice(0, 4) : pics;
    g.innerHTML = shown.map((f, i) => {
      const over = pics.length > 4 && i === 3 ? pics.length - 3 : 0;
      return `<button type="button" class="tk-th" data-prev="${f.n}" title="${esc(f.name)}"><img src="${fileUrl(t.id, f, 'view')}" alt="${esc(f.name)}" loading="lazy">${over ? `<span class="tk-more">+${over}</span>` : ''}</button>`;
    }).join('');
    bd.append(g);
  }
  if (docs.length) {
    const c = document.createElement('div');
    c.className = 'tk-files';
    c.innerHTML = docs.map((f) => `<button type="button" class="tk-file" data-prev="${f.n}" title="Preview ${esc(f.name)}">${fileIcon(f.name)}<span class="tk-fn">${esc(f.name)}</span><span class="tk-fs">${esc(fmtSize(f.size))}</span></button>`).join('');
    bd.append(c);
  }
  if (admin && m.left_out && m.left_out.length) {
    const l = document.createElement('div');
    l.className = 'tk-left';
    l.textContent = `${m.left_out.length} ${m.left_out.length === 1 ? 'picture or file' : 'pictures or files'} left out: ${m.left_out.map((x) => `${x.name} — ${x.why}`).join('; ')}`;
    bd.append(l);
  }
  card.querySelectorAll('[data-prev]').forEach((b) => (b.onclick = () => onPreview && onPreview(+b.dataset.prev)));
  return card;
}

// ---- previews -----------------------------------------------------------------------------------------------------------------
/** The preview dialog: pictures (zoom, next/prev), PDFs (the browser's viewer, or its pages as pictures), text/CSV, mails, and
 * for anything else its name, size and Download. `files`: the ticket's files in thread order; `n`: the one to open. */
export function openPreview(tid, files, n) {
  let i = Math.max(0, files.findIndex((f) => f.n === n));
  const body = document.createElement('div');
  body.className = 'pv';
  const foot = document.createElement('div');
  foot.className = 'row pv-ft';
  const m = modal({ title: files[i] ? files[i].name : 'Preview', body, footer: foot });
  m.root.querySelector('.modal').classList.add('tk-wide', 'pv-modal');
  const onKey = (e) => {
    if (!m.root.isConnected) { document.removeEventListener('keydown', onKey); return; }
    if (e.target.closest && e.target.closest('input,textarea')) return;
    if (e.key === 'ArrowRight' && i < files.length - 1) { i += 1; show(); }
    if (e.key === 'ArrowLeft' && i > 0) { i -= 1; show(); }
  };
  document.addEventListener('keydown', onKey);
  async function show() {
    const f = files[i];
    m.root.querySelector('.modal h2').textContent = f.name;
    foot.innerHTML = `<span class="small muted">${esc(fmtSize(f.size))}${files.length > 1 ? ` · ${i + 1} of ${files.length}` : ''}</span><span style="flex:1"></span>
      ${files.length > 1 ? `<button type="button" class="btn sm" data-pv="-1" ${i ? '' : 'disabled'}>Previous</button><button type="button" class="btn sm" data-pv="1" ${i < files.length - 1 ? '' : 'disabled'}>Next</button>` : ''}
      <a class="btn sm primary" href="${fileUrl(tid, f)}" download>Download</a>`;
    foot.querySelectorAll('[data-pv]').forEach((b) => (b.onclick = () => { i += +b.dataset.pv; show(); }));
    if (isPic(f.name)) {
      body.innerHTML = `<div class="pv-img"><img src="${fileUrl(tid, f, 'inline')}" alt="${esc(f.name)}"></div><p class="small muted pv-hint">Click the picture to see it at full size.</p>`;
      const box = body.querySelector('.pv-img');
      box.querySelector('img').onclick = () => box.classList.toggle('zoom');
      return;
    }
    body.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    let p;
    try { p = await api.get(`/api/support/tickets/${tid}/files/${f.n}/preview`); } catch (e) { body.innerHTML = `<p class="muted">${esc(e.detail || 'Could not open it.')}</p>`; return; }
    if (files[i] !== f) return;  // moved on meanwhile
    if (p.kind === 'pdf') {
      const pages = () => {
        const n = Math.min(p.pages || 0, 10);
        body.innerHTML = n ? `<div class="pv-pages">${Array.from({ length: n }, (_, k) => `<img src="/api/support/tickets/${tid}/files/${f.n}/page/${k + 1}" alt="Page ${k + 1}" loading="lazy">`).join('')}</div>${p.pages > 10 ? `<p class="small muted">The first 10 of ${p.pages} pages — download it for the rest.</p>` : ''}`
          : '<p class="muted">This PDF can\'t be shown here — download it to open it.</p>';
      };
      if (navigator.pdfViewerEnabled === false) { pages(); return; }
      body.innerHTML = `<iframe class="pv-pdf" src="${fileUrl(tid, f, 'inline')}" title="${esc(f.name)}"></iframe><p class="small muted pv-hint">Not showing? <button type="button" class="linkbtn" id="pv-pages">Show the pages as pictures</button></p>`;
      body.querySelector('#pv-pages').onclick = pages;
      return;
    }
    if (p.kind === 'text') {
      body.innerHTML = p.csv ? csvTable(p.text) : `<pre class="pv-text">${esc(p.text)}</pre>`;
      if (p.cut) body.insertAdjacentHTML('beforeend', '<p class="small muted">Only the first 200 KB — download it for the rest.</p>');
      return;
    }
    if (p.kind === 'mail' && p.mail) {
      const x = p.mail;
      const row = (k, v) => (v ? `<div class="tk-kv"><span class="muted small">${esc(k)}</span><span class="small">${esc(v)}</span></div>` : '');
      body.innerHTML = `<div class="pv-mail">${row('Subject', x.subject)}${row('From', x.from)}${row('To', x.to)}${row('Cc', x.cc)}${row('Date', x.date)}${x.attachments && x.attachments.length ? row('Attached', x.attachments.join(', ')) : ''}</div><pre class="pv-text">${esc(x.text || '(no words)')}</pre>`;
      return;
    }
    body.innerHTML = `<div class="pv-file">${fileIcon(f.name)}<div><b>${esc(f.name)}</b><div class="small muted">${esc(fmtSize(p.size))}${p.kind === 'office' ? ' · an Office file — download it to open it' : ''}</div></div></div>`;
  }
  show();
  return m;
}

function csvTable(text) {
  const rows = [];
  let row = [], cell = '', q = false;
  const s = String(text || '');
  for (let k = 0; k < s.length && rows.length < 300; k++) {
    const c = s[k];
    if (q) { if (c === '"' && s[k + 1] === '"') { cell += '"'; k++; } else if (c === '"') q = false; else cell += c; continue; }
    if (c === '"') q = true;
    else if (c === ',' || c === ';') { row.push(cell); cell = ''; }
    else if (c === '\n') { row.push(cell.replace(/\r$/, '')); rows.push(row); row = []; cell = ''; }
    else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  if (!rows.length) return '<p class="muted">Empty.</p>';
  const [hd, ...rest] = rows;
  return `<div class="tbl pv-csv"><table><thead><tr>${hd.map((x) => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rest.map((r) => `<tr>${r.map((x) => `<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${rows.length >= 300 ? '<p class="small muted">The first 300 rows.</p>' : ''}`;
}

/** A picture not saved yet (in the editor) — just the picture, large. */
function previewBlob(url, name) {
  const body = document.createElement('div');
  body.className = 'pv-img';
  body.innerHTML = `<img src="${url}" alt="${esc(name)}">`;
  body.querySelector('img').onclick = () => body.classList.toggle('zoom');
  const m = modal({ title: name || 'Picture', body });
  m.root.querySelector('.modal').classList.add('tk-wide', 'pv-modal');
}

// ---- the reply editor ------------------------------------------------------------------------------------------------------
const OK_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];
const MAX_PICS = 10, MAX_PIC = 5 * 1024 * 1024;

/** A small editor: words, bold, a list, and pictures pasted (Ctrl+V), dropped or picked — they sit where they were put (small;
 * click to enlarge; × to remove). value() → {html (pictures as <img data-k="i">), text, files}. */
export function editor({ placeholder = '', label = 'Your message' } = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'ed';
  wrap.innerHTML = `<div class="ed-box" contenteditable="true" role="textbox" aria-multiline="true" aria-label="${esc(label)}" data-ph="${esc(placeholder)}"></div>
    <div class="ed-tools"><button type="button" class="ed-b" data-cmd="bold" title="Bold (Ctrl+B)" aria-label="Bold"><b>B</b></button><button type="button" class="ed-b" data-cmd="insertUnorderedList" title="A list" aria-label="A list">•≡</button><button type="button" class="ed-b" data-pick title="Add a picture — or paste one (Ctrl+V)" aria-label="Add a picture">Picture</button><input type="file" accept="${OK_TYPES.join(',')}" multiple hidden></div>`;
  const box = wrap.querySelector('.ed-box'), input = wrap.querySelector('input[type=file]');
  const pics = new Map();  // key → {file, url}
  let seq = 0;
  const count = () => box.querySelectorAll('.ed-pic').length;
  let last = null;
  const keep = () => { const sel = window.getSelection(); if (sel && sel.rangeCount && box.contains(sel.getRangeAt(0).startContainer)) last = sel.getRangeAt(0).cloneRange(); };
  ['keyup', 'mouseup', 'input', 'blur'].forEach((ev) => box.addEventListener(ev, keep));
  // a picture goes on its own line, where the caret is; the caret then sits on the line under it (a zero-width space holds that line:
  // Chrome has nowhere to put a caret after a picture at the end of a line — it is taken out again when the message is sent)
  const ZW = '\u200b';
  const lineStart = (r) => {
    const c = r.startContainer, o = r.startOffset;
    if (c.nodeType === 3) return !c.nodeValue.slice(0, o).replace(/\u200b/g, '').trim() && !(c.previousSibling && c.previousSibling.nodeName !== 'BR');
    const prev = c.childNodes[o - 1];
    return !prev || prev.nodeName === 'BR' || /^(DIV|P|LI|UL|OL)$/.test(prev.nodeName);
  };
  const place = (node, range) => {
    const sel = window.getSelection();
    let r = range;
    if (!r) r = sel && sel.rangeCount && box.contains(sel.getRangeAt(0).commonAncestorContainer) ? sel.getRangeAt(0) : null;
    if (!r && last && box.contains(last.startContainer)) r = last;  // "Picture" took the focus: where the caret was
    if (!r) { r = document.createRange(); r.selectNodeContents(box); r.collapse(false); }
    r.deleteContents();
    const frag = document.createDocumentFragment(), tail = document.createTextNode(ZW);
    if (!lineStart(r)) frag.append(document.createElement('br'));
    frag.append(node, document.createElement('br'), tail);
    r.insertNode(frag);
    const after = document.createRange();
    after.setStart(tail, 1); after.collapse(true);
    sel.removeAllRanges(); sel.addRange(after);
  };
  function addFiles(list, range) {
    for (const f of list) {
      if (!OK_TYPES.includes(f.type)) { toast(`${f.name || 'That file'}: only PNG, JPEG, GIF or WebP pictures go in the words — attach other files separately.`, 'err'); continue; }
      if (f.size > MAX_PIC) { toast(`${f.name || 'That picture'} is over 5 MB.`, 'err'); continue; }
      if (count() >= MAX_PICS) { toast(`At most ${MAX_PICS} pictures in one message.`, 'err'); break; }
      const k = 'p' + (++seq), url = URL.createObjectURL(f);
      pics.set(k, { file: f, url });
      const span = document.createElement('span');
      span.className = 'ed-pic'; span.contentEditable = 'false'; span.dataset.key = k;
      span.innerHTML = `<img src="${url}" alt="${esc(f.name || 'pasted picture')}"><button type="button" class="ed-x" aria-label="Remove the picture" title="Remove">×</button>`;
      span.querySelector('img').onclick = () => previewBlob(url, f.name || 'Pasted picture');
      span.querySelector('.ed-x').onclick = (e) => { e.preventDefault(); span.remove(); URL.revokeObjectURL(url); pics.delete(k); };
      place(span, range);
      range = null;
    }
  }
  box.addEventListener('paste', (e) => {
    const items = [...(e.clipboardData ? e.clipboardData.items : [])];
    const imgs = items.filter((x) => x.kind === 'file' && x.type.startsWith('image/')).map((x) => x.getAsFile()).filter(Boolean);
    e.preventDefault();
    if (imgs.length) { addFiles(imgs); return; }
    const text = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
    if (text) document.execCommand('insertText', false, text);  // words only: no foreign markup, links or tracking pictures
  });
  box.addEventListener('dragover', (e) => { if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); wrap.classList.add('over'); } });
  box.addEventListener('dragleave', () => wrap.classList.remove('over'));
  box.addEventListener('drop', (e) => {
    wrap.classList.remove('over');
    const fs = [...(e.dataTransfer ? e.dataTransfer.files : [])];
    if (!fs.length) { e.preventDefault(); return; }
    e.preventDefault();
    let r = null;
    if (document.caretRangeFromPoint) r = document.caretRangeFromPoint(e.clientX, e.clientY);
    if (r && !box.contains(r.startContainer)) r = null;
    addFiles(fs, r);
  });
  box.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && ['u', 'U'].includes(e.key)) e.preventDefault();  // underline isn't kept: don't offer it
  });
  wrap.querySelectorAll('[data-cmd]').forEach((b) => (b.onmousedown = (e) => { e.preventDefault(); box.focus(); document.execCommand(b.dataset.cmd); }));
  wrap.querySelector('[data-pick]').onclick = () => input.click();
  input.onchange = () => { addFiles([...input.files]); input.value = ''; };
  return {
    node: wrap,
    box,
    focus: () => box.focus(),
    empty: () => !box.innerText.replace(/\u200b/g, '').trim() && !count(),
    clear: () => { pics.forEach((p) => URL.revokeObjectURL(p.url)); pics.clear(); box.innerHTML = ''; },
    value() {
      const clone = box.cloneNode(true);
      const files = [];
      clone.querySelectorAll('.ed-pic').forEach((s) => {
        const p = pics.get(s.dataset.key);
        if (!p) { s.remove(); return; }
        const img = document.createElement('img');
        img.setAttribute('data-k', String(files.length));
        files.push(p.file.name ? p.file : new File([p.file], `pasted-${files.length + 1}.${(p.file.type.split('/')[1] || 'png').replace('jpeg', 'jpg')}`, { type: p.file.type }));
        s.replaceWith(img);
      });
      const text = box.innerText.replace(/\u00a0/g, ' ').replace(/\u200b/g, '').trim();
      return { html: clone.innerHTML.replace(/\u200b/g, ''), text, files };
    },
  };
}

/** Send the editor's message: words, marks and pictures in one request. */
export function sendRich(url, ed, fields = {}) {
  const v = ed.value();
  const fd = new FormData();
  fd.append('html', v.html);
  fd.append('text', v.text);
  for (const [k, x] of Object.entries(fields)) fd.append(k, x);
  v.files.forEach((f) => fd.append('pics', f, f.name));
  return api.post(url, fd);
}

// ---- Ask AXL ----------------------------------------------------------------------------------------------------------------
let drawer = null;
/** The Ask AXL panel on a ticket (staff and admins): a short chat about it; drafts and actions come as cards a person confirms. */
export async function askAxl(t, { aiName = 'AXL', onChanged } = {}) {
  if (drawer && drawer.isConnected && drawer.dataset.t === String(t.id)) { drawer.querySelector('textarea, input')?.focus(); return drawer; }
  closeAxl();
  drawer = document.createElement('aside');
  drawer.className = 'axl-panel';
  drawer.dataset.t = String(t.id);
  drawer.setAttribute('aria-label', `Ask ${aiName}`);
  drawer.innerHTML = `<div class="axl-hd"><b>Ask ${esc(aiName)}</b><span class="small muted">about ${esc(t.ref)} · the requester never sees this</span><button type="button" class="btn sm ghost" data-x>Close</button></div>
    <div class="axl-list"><div class="loading"><span class="spin"></span></div></div><div class="axl-in"></div>`;
  document.body.append(drawer);
  const here = location.hash;
  const gone = () => { if (location.hash.split('?')[0] !== here.split('?')[0] || !location.hash.includes(`t=${t.id}`)) closeAxl(); };
  window.addEventListener('hashchange', gone, { once: true });
  drawer.querySelector('[data-x]').onclick = closeAxl;
  const list = drawer.querySelector('.axl-list'), inBox = drawer.querySelector('.axl-in');
  let d;
  try { d = await api.get(`/api/support/tickets/${t.id}/axl`); } catch (e) { list.innerHTML = `<p class="small muted">${esc(e.detail || 'Could not load.')}</p>`; return drawer; }
  if (!d.on) { list.innerHTML = `<p class="axl-off">${esc(aiName)} is off. ${esc(d.why_off || '')}</p>`; return drawer; }
  const draw = (chat) => {
    list.innerHTML = chat.length ? '' : `<p class="small muted">Ask about this ticket — "what is the SAP error?", "ask her for the journal number", "this was wrongly assigned", "we don't handle this, it's Reflex's side".</p>`;
    chat.forEach((c) => list.append(lineNode(c)));
    list.scrollTop = list.scrollHeight;
  };
  const lineNode = (c) => {
    const n = document.createElement('div');
    n.className = `axl-line ${c.role}`;
    n.innerHTML = `<div class="small muted">${esc(c.role === 'axl' ? aiName : c.name)} · ${esc(fmtDate(c.at, { rel: true }))}</div><div class="axl-t">${textHtml(c.text)}</div>`;
    if (c.card && c.card.kind) n.append(cardNode(c));
    return n;
  };
  const cardNode = (c) => {
    const k = c.card, box = document.createElement('div');
    box.className = `axl-card ${k.state === 'open' ? '' : 'done'}`;
    const title = { draft: `A mail to ${k.to || 'the requester'}`, reassign: `Move it to ${k.name}`, system: `Set the system to ${k.name}`, not_relevant: 'Not relevant — close it' }[k.kind] || '';
    const why = k.kind === 'reassign' ? k.why : k.reason;
    const hasText = k.kind === 'draft' || k.kind === 'not_relevant';
    if (k.state !== 'open') {
      const said = { sent: 'Sent', done: 'Done', discarded: 'Discarded', closed: 'Closed silently' }[k.state] || k.state;
      box.innerHTML = `<div class="small"><b>${esc(title)}</b> — ${esc(said)}${k.by ? ' by ' + esc(k.by) : ''}</div>${hasText && k.state === 'sent' ? `<div class="small muted axl-t">${textHtml(k.text || '')}</div>` : ''}`;
      return box;
    }
    const canSend = d.can_work, canAdmin = d.can_assign;
    const btns = k.kind === 'draft' ? `<button type="button" class="btn sm" data-ed>Edit</button>${canSend ? '<button type="button" class="btn sm primary" data-do="send">Send</button>' : ''}`
      : k.kind === 'not_relevant' ? (canAdmin ? `<button type="button" class="btn sm" data-ed>Edit</button><button type="button" class="btn sm primary" data-do="send">Send and close</button><button type="button" class="btn sm" data-do="close_silently">Close silently</button>` : '')
        : (k.kind === 'reassign' && !canAdmin) ? '' : '<button type="button" class="btn sm primary" data-do="confirm">Confirm</button>';
    box.innerHTML = `<div class="small"><b>${esc(title)}</b>${why ? `<span class="muted"> — ${esc(why)}</span>` : ''}</div>
      ${hasText ? `<div class="axl-draft">${textHtml(k.text || '')}</div><textarea class="axl-edit" rows="6" hidden>${esc(k.text || '')}</textarea>` : ''}
      <div class="row axl-btns">${btns}<button type="button" class="btn sm ghost" data-do="discard">Discard</button></div>
      ${!btns ? `<div class="small muted">${k.kind === 'draft' ? 'Take it first to send it.' : 'The owner or an admin confirms this.'}</div>` : ''}`;
    const ed = box.querySelector('[data-ed]');
    if (ed) ed.onclick = () => { const ta = box.querySelector('.axl-edit'); ta.hidden = false; box.querySelector('.axl-draft').hidden = true; ed.remove(); ta.focus(); };
    box.querySelectorAll('[data-do]').forEach((b) => (b.onclick = async () => {
      const ta = box.querySelector('.axl-edit');
      if (b.dataset.do === 'send' && ta && !ta.value.trim()) { toast('Write the mail first.', 'err'); return; }
      box.querySelectorAll('button').forEach((x) => (x.disabled = true));
      try {
        const r = await api.post(`/api/support/tickets/${t.id}/axl/${c.id}/card`, { do: b.dataset.do, text: ta ? ta.value : '' });
        draw(r.chat);
        toast({ send: k.kind === 'not_relevant' ? 'Sent and closed' : 'Sent to the requester', confirm: 'Done', close_silently: 'Closed — nobody was told', discard: 'Discarded' }[b.dataset.do]);
        if (b.dataset.do !== 'discard' && onChanged) onChanged();
      } catch (e) { toast(e.detail || 'Did not work', 'err'); box.querySelectorAll('button').forEach((x) => (x.disabled = false)); }
    }));
    return box;
  };
  draw(d.chat);
  inBox.innerHTML = `<textarea rows="2" maxlength="1000" placeholder="Ask ${esc(aiName)} about this ticket"></textarea><button type="button" class="btn sm primary">Ask</button>`;
  const ta = inBox.querySelector('textarea'), go = inBox.querySelector('button');
  const ask = async () => {
    const q = ta.value.trim(); if (!q) return;
    go.disabled = true; ta.disabled = true;
    const wait = document.createElement('div');
    wait.className = 'axl-line person';
    wait.innerHTML = `<div class="axl-t">${textHtml(q)}</div><div class="small muted"><span class="spin"></span> ${esc(aiName)} is thinking…</div>`;
    list.append(wait); list.scrollTop = list.scrollHeight;
    try { const r = await api.post(`/api/support/tickets/${t.id}/axl`, { text: q }); ta.value = ''; draw(r.chat); if (onChanged) onChanged(true); } catch (e) { wait.remove(); toast(e.detail || `${aiName} didn't answer`, 'err'); }
    go.disabled = false; ta.disabled = false; ta.focus();
  };
  go.onclick = ask;
  ta.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } };
  ta.focus();
  return drawer;
}
export function closeAxl() { if (drawer) { drawer.remove(); drawer = null; } }

// 1.0.109 (review): the words are marked on the plain text, then each piece escaped — a match never splits an HTML entity
export function markWords(s, words) {
  const str = String(s == null ? '' : s);
  const ws = (words || []).filter(Boolean).map((w) => String(w).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!ws.length) return esc(str);
  const rx = new RegExp(ws.join('|'), 'gi');
  let out = '', i = 0;
  for (const m of str.matchAll(rx)) {
    if (!m[0]) continue;
    out += esc(str.slice(i, m.index)) + `<mark>${esc(m[0])}</mark>`;
    i = m.index + m[0].length;
  }
  return out + esc(str.slice(i));
}  // markWords end

// ---- quick find ---------------------------------------------------------------------------------------------------------------
let slashBound = false;
/** A search box for the support pages: "28" or "T-0028" + Enter opens the ticket; words search what the person may see. "/" focuses it. */
export function quickFind(host, ctx) {
  const box = document.createElement('div');
  box.className = 'qf';
  box.innerHTML = `<input type="search" placeholder="Find a ticket" aria-label="Find a ticket (press / )" autocomplete="off"><kbd class="qf-k" aria-hidden="true">/</kbd><div class="qf-res" hidden></div>`;
  host.append(box);
  const input = box.querySelector('input'), res = box.querySelector('.qf-res');
  let timer = null, seq = 0, last = null;
  const mark = markWords;
  const run = async (jump) => {
    const q = input.value.trim();
    if (!q) { res.hidden = true; return; }
    const my = ++seq;
    let r;
    try { r = await api.get('/api/support/find?q=' + encodeURIComponent(q)); } catch (e) { if (my === seq) { res.hidden = false; res.innerHTML = `<div class="qf-empty">${esc(e.detail || 'Could not search.')}</div>`; } return; }
    if (my !== seq) return;
    last = r;
    if (r.jump) {
      if (jump) { res.hidden = true; ctx.go('support-tickets', { t: r.jump.id }); return; }
      res.hidden = false; res.innerHTML = `<button type="button" class="qf-it" data-t="${r.jump.id}"><b>${esc(r.jump.ref)}</b> <span class="muted small">— press Enter to open it</span></button>`;
    } else if (/^\s*(?:t-?\s*)?\d{1,7}\s*$/i.test(q)) {
      res.hidden = false; res.innerHTML = '<div class="qf-empty">No ticket with that number that you can see.</div>';
    } else {
      res.hidden = false;
      res.innerHTML = r.results.length ? r.results.map((x) => `<button type="button" class="qf-it" data-t="${x.id}"><span><b>${mark(x.title, r.words)}</b> <span class="mono small muted">${esc(x.ref)}</span>${x.removed ? ' <span class="small muted">(removed)</span>' : ''}</span>
        <span class="small muted">${mark(x.requester, r.words)} · ${esc(x.word)}</span>${x.hits.map((h) => `<span class="small qf-hit"><span class="muted">${esc(h.field)}:</span> ${mark(h.snippet, r.words)}</span>`).join('')}</button>`).join('')
        : '<div class="qf-empty">Nothing found.</div>';
    }
    res.querySelectorAll('[data-t]').forEach((b) => (b.onclick = () => { res.hidden = true; ctx.go('support-tickets', { t: b.dataset.t }); }));
  };
  input.oninput = () => { clearTimeout(timer); timer = setTimeout(() => run(false), 250); };
  input.onkeydown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); clearTimeout(timer); if (last && last.jump && input.value.trim()) run(true); else run(true); }
    if (e.key === 'Escape') { res.hidden = true; input.blur(); }
    if (e.key === 'ArrowDown') { const f = res.querySelector('[data-t]'); if (f) { e.preventDefault(); f.focus(); } }
  };
  res.addEventListener('keydown', (e) => {
    const items = [...res.querySelectorAll('[data-t]')], k = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' && k < items.length - 1) { e.preventDefault(); items[k + 1].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); (k > 0 ? items[k - 1] : input).focus(); }
    if (e.key === 'Escape') { res.hidden = true; input.focus(); }
  });
  document.addEventListener('click', function off(e) { if (!box.isConnected) { document.removeEventListener('click', off); return; } if (!box.contains(e.target)) res.hidden = true; });
  if (!slashBound) {
    slashBound = true;
    document.addEventListener('keydown', (e) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
      const a = document.activeElement;
      if (a && (a.closest('input, textarea, select, [contenteditable="true"]'))) return;
      if (document.querySelector('.modal-bg')) return;
      const q = document.querySelector('.qf input');
      if (q) { e.preventDefault(); q.focus(); q.select(); }
    });
  }
  return box;
}
