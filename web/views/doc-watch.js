import { api } from '../api.js';
import { esc, toast, modal, confirmBox, pill, el, fileDrop } from '../ui.js';

// Entity Compliance (the tool's slug is still doc-watch — grants and routes key on it).
// The grid: entities down the side, document types across the top (horizontal, full names), one coloured dot per cell.
// Click a dot to open that document, an entity name for its own dashboard (doc-watch-entity.js).

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// status → [wording, pill kind]
export const STATUS = {
  expired: ['Expired', 'crit'],
  expiring: ['Expiring soon', 'warn'],
  ok: ['In date', 'good'],
  no_expiry: ["Doesn't expire", 'good'],
  needs_date: ['No date yet', 'neutral'],
  missing: ['Outstanding', 'neutral'],
  not_required: ['Not required', 'neutral'],
  archived: ['Archived', 'neutral'],
};
export const statusPill = (s) => { const [t, k] = STATUS[s] || [s, 'neutral']; return pill(t, k); };
// Expiry dates are plain calendar days (YYYY-MM-DD) — format them without going through Date/UTC.
export function fmtDay(iso) {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}
export const daysCell = (d) => (d.no_expiry ? '—' : d.days_left === null || d.days_left === undefined ? '—' : d.days_left < 0 ? `<span style="color:var(--crit)">${d.days_left}</span>` : d.status === 'expiring' ? `<span style="color:var(--warn)">${d.days_left}</span>` : String(d.days_left));
export const dot = (d, extra = '') => `<span class="dw-dot ${esc(d ? d.dot : 'open')}" title="${esc(d ? d.dot_title : 'Not set up yet')}"${extra}></span>`;
const fmtSize = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);
const fmtWhen = (iso) => (iso ? fmtDay(iso.slice(0, 10)) : '');
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// ---- the document dialog (opened from the grid and from an entity's page) ---------------------
// One place where a file is uploaded or renewed, the expiry typed in, required / doesn't expire set.
// Nothing here reads a date off the certificate: the person filing it types the expiry themselves.
export async function openDoc(id, ctx, onChange = () => {}) {
  let d;
  try { d = await api.get(`/api/docwatch/${id}`); } catch (e) { toast(e.detail || e.message, 'err'); return; }
  const manage = !!d.can_manage;
  const cands = [];  // the automatic date-reading is retired — people type the expiry themselves
  const readBlock = '';
  const candBlock = cands.length && !d.expiry && !d.no_expiry ? `<div class="dw-cands">${cands.map((c) => `<div class="row" style="gap:10px"><button class="btn sm" data-date="${esc(c.date)}">${fmtDay(c.date)}</button><span class="small muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(c.quote)}">${esc(c.quote)}</span></div>`).join('')}</div>` : '';
  const fileLine = d.file_id
    ? `<div class="row between" style="flex-wrap:wrap;gap:8px"><div class="small" style="min-width:0"><a class="mono" href="/api/docwatch/${d.id}/download?inline=1" target="_blank" rel="noopener" style="color:var(--accent)">${esc(d.file_name)}</a><span class="muted"> · ${fmtSize(d.file_size || 0)}${d.filed_at ? ` · filed ${fmtWhen(d.filed_at)}` : ''}${d.filed_by ? ` by ${esc(d.filed_by)}` : ''}</span></div><a class="btn sm ghost dw-dl" href="/api/docwatch/${d.id}/download">Download</a></div>`
    : `<div class="small muted">Nothing filed yet. Drop the document below, then type its expiry date.</div>`;
  const hist = (d.history || []).length ? `<details class="dw-hist"><summary>${plural(d.history.length, 'earlier file', 'earlier files')} — kept in the warehouse</summary><ul>${[...d.history].reverse().map((h) => `<li>${h.file_id ? `<a href="/api/docwatch/${d.id}/download?file=${h.file_id}&inline=1" target="_blank" rel="noopener" style="color:var(--accent)">${esc(h.file_name || 'file')}</a>` : esc(h.file_name || 'file')}${h.expiry ? ` · expired ${fmtDay(h.expiry)}` : ''}${h.replaced_at ? ` · replaced ${fmtWhen(h.replaced_at)}${h.by ? ` by ${esc(h.by)}` : ''}` : ''}</li>`).join('')}</ul></details>` : '';

  const body = el(`<div style="display:flex;flex-direction:column;gap:14px">
    <div class="row between" style="flex-wrap:wrap;gap:8px"><div class="row" style="gap:8px">${dot(d)}<span class="small">${esc(d.dot_title)}</span></div>${statusPill(d.status)}</div>
    ${d.archived ? '<div class="small muted">This document is archived — it does not count and shows a grey dot.</div>' : ''}
    <div class="dw-file"><div class="eyebrow" style="margin-bottom:6px">${d.file_id ? 'The filed document' : 'The document'}</div>${fileLine}<div id="dw-drop" style="margin-top:8px"></div>${hist}</div>
    ${readBlock}${candBlock}
    <div class="row" style="gap:10px;align-items:flex-start"><div class="field" style="flex:1"><label>Expiry date (type it from the document)</label><input id="expiry" type="date" value="${esc(d.expiry || '')}" ${d.no_expiry ? 'disabled' : ''}></div><div class="field" style="width:150px"><label>Warn (days before)</label><input id="warn" type="number" min="1" max="730" value="${esc(d.warn_days)}" ${d.no_expiry ? 'disabled' : ''}></div></div>
    <div class="dw-toggles">
      <label class="row" style="gap:8px;cursor:pointer"><input id="never" type="checkbox" ${d.no_expiry ? 'checked' : ''} style="width:auto"><span class="small">This document doesn't expire (no renewal, no reminders)</span></label>
      ${manage ? `<label class="row" style="gap:8px;cursor:pointer"><input id="required" type="checkbox" ${d.required ? 'checked' : ''} style="width:auto"><span class="small">Required for ${esc(d.entity || 'this entity')} — unticked shows a grey dot and stops it counting as outstanding</span></label>` : ''}
    </div>
    ${manage ? `<div class="field"><label>Name shown for this document</label><input id="title" type="text" value="${esc(d.title)}" maxlength="200"></div>` : ''}
    <div class="field"><label>Notes</label><input id="notes" type="text" value="${esc(d.notes || '')}" placeholder="Who renews it, reference numbers…"></div>
  </div>`);
  const m = modal({ title: `${d.title} — ${d.entity || 'no entity'}`, body, footer: `<button class="btn" data-close>Cancel</button><button class="btn ghost" id="mail" title="Send someone a message about this document — with a link straight to it">Mail</button>${manage ? `<button class="btn ghost" id="archive">${d.archived ? 'Restore' : 'Archive'}</button>` : ''}<button class="btn primary" id="ok">Save</button>` });
  const q = (s) => body.querySelector(s);
  m.root.querySelector('#mail').onclick = () => mailDialog(d);
  const never = q('#never');
  never.onchange = () => { q('#expiry').disabled = never.checked; q('#warn').disabled = never.checked; };
  body.querySelectorAll('.dw-cands [data-date]').forEach((b) => (b.onclick = () => { never.checked = false; never.onchange(); q('#expiry').value = b.dataset.date; body.querySelectorAll('.dw-cands .btn').forEach((x) => x.classList.remove('primary')); b.classList.add('primary'); }));
  const after = async (msg, kind) => { m.close(); if (msg) toast(msg, kind); ctx.refreshCounts && ctx.refreshCounts(); await onChange(); };
  const read = () => {
    const out = { expiry: never.checked ? '' : q('#expiry').value, no_expiry: never.checked, warn_days: +q('#warn').value || d.warn_days, notes: q('#notes').value };
    if (manage) { out.required = q('#required').checked; out.title = q('#title').value; }
    return out;
  };
  m.root.querySelector('#ok').onclick = async () => { try { await api.patch(`/api/docwatch/${d.id}`, read()); await after('Saved'); } catch (e) { toast(e.detail || e.message, 'err'); } };
  const arch = m.root.querySelector('#archive');
  if (arch) arch.onclick = async () => { try { await api.patch(`/api/docwatch/${d.id}`, { archived: !d.archived }); await after(d.archived ? 'Restored' : 'Archived — it shows a grey dot and no longer counts.'); } catch (e) { toast(e.detail || e.message, 'err'); } };
  // dropping a file here is the only way a document gets filed or renewed — then the person types the date
  const drop = fileDrop({ label: d.file_id ? 'Drop the renewed document here' : 'Drop the document here (PDF)', hint: d.file_id ? ' — the current file moves into the history, then type the new expiry date' : ' — then type its expiry date', multiple: false, accept: '.pdf,.docx,.txt,.png,.jpg,.jpeg', onFiles: async (files) => {
    if (!files.length) return;
    drop.querySelector('b').textContent = 'Filing…';
    try {
      const r = await api.upload(`/api/docwatch/${d.id}/upload`, [files[0]]);
      m.close(); ctx.refreshCounts && ctx.refreshCounts(); await onChange();
      if (r.ask_user) { toast(r.message || 'Filed — now type the expiry date.', 'warn'); openDoc(d.id, ctx, onChange); }
      else if (r.warning) { toast(r.warning, 'warn'); openDoc(d.id, ctx, onChange); }
      else toast(r.message || 'Filed — now type the expiry date.');
    } catch (e) { drop.querySelector('b').textContent = d.file_id ? 'Drop the renewed document here' : 'Drop the document here (PDF)'; toast(e.detail || e.message, 'err'); }
  } });
  q('#dw-drop').append(drop);
}

// ---- mail about one document --------------------------------------------------------------------
// "Press Mail, a small table opens, type a message, it sends" — the mail carries the message, which
// document and company it is about, its status, a link straight to the document and a link to
// CBX-Connect. To is the company's responsible person unless you pick someone else; only people CBX
// knows (or the company's own domain) can be written to.
export async function mailDialog(d) {
  let info;
  try { info = await api.get(`/api/docwatch/${d.id}/mail`); } catch (e) { toast(e.detail || e.message, 'err'); return; }
  const sug = info.suggested;
  const opts = info.recipients.map((r) => `<option value="${esc(r.email)}">${esc(r.name)} — ${esc(r.email)}${r.kind === 'responsible' ? ` (responsible, ${esc(r.entity)})` : ''}</option>`).join('');
  const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
    ${info.mail_ready ? '' : `<div class="small" style="background:var(--warn-soft);color:var(--warn);padding:8px 10px;border-radius:var(--r-sm)">${esc(info.mail_note)} The note still reaches a colleague with an account inside CBX-Connect (Messages).</div>`}
    <div class="field"><label>To</label><input id="mto" type="text" list="mto-list" value="${esc(sug ? sug.email : '')}" placeholder="name@company — start typing to pick someone" autocomplete="off"><datalist id="mto-list">${opts}</datalist>${sug ? `<span class="small muted">${esc(sug.name)} · ${esc(sug.why)}</span>` : '<span class="small muted">No responsible person is linked to this company yet — pick someone.</span>'}</div>
    <div class="field"><label>Message</label><textarea id="mmsg" rows="4" placeholder="e.g. The fire certificate expires next month — please send the renewal when it comes in."></textarea></div>
    <table class="dw-mailinfo small"><tbody>
      <tr><td class="muted">Document</td><td><b>${esc(d.title)}</b> · ${esc(d.entity || 'no company')}</td></tr>
      <tr><td class="muted">Status</td><td>${esc(d.dot_title || '')}${d.expiry ? ` · ${fmtDay(d.expiry)}` : ''}</td></tr>
      <tr><td class="muted">Includes</td><td>your message, which document it is, a link to it and a link to CBX-Connect — signed with your name</td></tr>
      <tr><td class="muted">Link</td><td class="mono" style="word-break:break-all">${esc(info.link)}</td></tr>
    </tbody></table>
    ${info.sent.length ? `<details class="dw-hist"><summary>${info.sent.length} sent before about this document</summary><ul>${info.sent.map((x) => `<li>${fmtWhen(x.at)} · to ${esc(x.to)} · by ${esc(x.by || 'CBX')}${x.ok ? '' : ' · <span style="color:var(--crit)">not sent</span>'} — <span class="muted">${esc(x.preview.split('\n\n')[1] || x.preview)}</span></li>`).join('')}</ul></details>` : ''}
  </div>`);
  const m = modal({ title: `Mail — ${d.title}`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="msend">Send</button>' });
  body.querySelector(sug ? '#mmsg' : '#mto').focus();
  m.root.querySelector('#msend').onclick = async () => {
    const b = m.root.querySelector('#msend'); b.disabled = true; b.innerHTML = '<span class="spin"></span> Sending…';
    try { const r = await api.post(`/api/docwatch/${d.id}/mail`, { to: body.querySelector('#mto').value.trim(), message: body.querySelector('#mmsg').value }); m.close(); toast(r.message, r.ok ? undefined : 'err'); }
    catch (e) { toast(e.detail || e.message, 'err'); b.disabled = false; b.textContent = 'Send'; }
  };
}

// ---- who is responsible -------------------------------------------------------------------------
// The column is a plain name, because that is how finance keeps it. Most of those people do have an
// account here, some don't, and the two are not the same thing: only a linked account can be reached
// by anything this app sends. So they don't look the same — a linked name is dark and carries the
// tag, a name that is only written down stays grey.
export function whoCell(e) {
  const name = (e.responsible || '').trim();
  if (!name) return '<span class="muted">—</span>';
  return e.responsible_user_id
    ? `<span class="dw-who linked" title="${esc(name)} has an account here — anything this page sends reaches them."><b>${esc(name)}</b><i>account</i></span>`
    : `<span class="dw-who" title="Written down only — no account is linked, so nothing sent from here reaches ${esc(name)}."><b>${esc(name)}</b></span>`;
}
export const whoNote = '<div class="dw-note"><b>Responsible person</b> — grey is a name only; <span class="dw-who linked"><i>account</i></span> means it is linked to an account here. Click a name to filter by person.</div>';

// How many records an entity actually has, and how many of them have a file — the archive dialog says
// both out loud, because "archive" has to be visibly different from "delete".
export function recordCounts(cells = [], extra = []) {
  const docs = [...cells.map((c) => c.doc).filter(Boolean), ...extra];
  return { records: docs.length, filed: docs.filter((d) => d.file_id).length };
}

// ---- archive / bring back -----------------------------------------------------------------------
// Archive is not delete. It means "we don't service this entity any more": it leaves the grid, keeps
// every document it ever filed, and comes back with one click. The dialog says that in those words,
// and its button is the amber caution — not the green Save, not the red Delete.
export function archiveDialog(ent, counts = {}, after = () => {}) {
  const n = counts.records || 0;
  const f = counts.filed || 0;
  const kept = n
    ? `Its <b>${plural(n, 'document record', 'document records')}</b> are kept exactly as they are${f ? ` — ${f === n ? 'every one of them has' : `${f} of them have`} a file attached` : ''}. Nothing is thrown away.`
    : 'It has no document records yet, so there is nothing to keep.';
  const body = el(`<div style="display:flex;flex-direction:column;gap:14px">
    <p>Archiving is for an entity <b>we don't service any more</b>. It is not deleting.</p>
    <ul class="dw-what">
      <li><b>${esc(ent.name)}</b> leaves the grid — it stops being counted, and no more reminders go out for it.</li>
      <li>${kept}</li>
      <li>You can <b>bring it back</b> at any time, from Archived entities at the bottom of the grid.</li>
    </ul>
    <div class="field"><label>Why? (optional — it shows in the archived list)</label><input id="a-why" type="text" maxlength="200" placeholder="e.g. contract ended March 2026"></div>
  </div>`);
  const m = modal({ title: `Archive ${ent.name}`, body, footer: '<button class="btn" data-close>Keep it on the grid</button><button class="btn caution" id="a-ok">Archive entity</button>' });
  const ok = m.root.querySelector('#a-ok');
  ok.onclick = async () => {
    ok.disabled = true;
    try {
      const r = await api.post(`/api/docwatch/entities/${ent.id}/archive`, { archived: true, reason: body.querySelector('#a-why').value });
      m.close(); toast(r.message || `'${ent.name}' archived.`); await after(r);
    } catch (e) { ok.disabled = false; toast(e.detail || e.message, 'err'); }
  };
  return m;
}

export async function bringBack(ent, after = () => {}) {
  try {
    const r = await api.post(`/api/docwatch/entities/${ent.id}/archive`, { archived: false });
    toast(r.message || `'${ent.name}' is back on the grid.`); await after(r);
  } catch (e) { toast(e.detail || e.message, 'err'); }
}

// ---- the responsible person is an ACCOUNT -------------------------------------------------------
// Edwin's rule: adding a person adds their account, not a name. Pick someone who already has one, or
// give a name and an email and the account is made on the spot — temporary password shown once, the
// Entity Compliance tool and this company granted, the link set. Either way the person can be reached
// and can open what they answer for.
export async function pickAccountDialog(ent, after = () => {}) {
  let info;
  try { info = await api.get(`/api/docwatch/entities/${ent.id}/access`); }
  catch (e) { toast(e.status === 403 ? "Only an admin or the team's manager can set the responsible person." : (e.detail || e.message), 'err'); return; }
  const users = info.candidates || [];
  const body = el(`<div class="dw-pick">
    <p class="small muted">The responsible person answers for ${esc(ent.name)}'s documents: reminders and messages about them go to this account, and the account can open the company. Pick someone who has an account, or add the person — their account is created here.</p>
    <input id="p-q" type="text" placeholder="Search by name or email" autocomplete="off">
    <div class="scroll" id="p-list"></div>
    ${info.can_add_people ? `<details class="dw-newp"><summary>Add a person who has no account yet</summary>
      <div class="row" style="gap:8px;align-items:flex-start;margin-top:8px"><div class="field" style="flex:1"><label>Full name</label><input id="n-name" type="text" maxlength="120" placeholder="Devin Puckree"></div><div class="field" style="flex:1"><label>Email</label><input id="n-mail" type="text" inputmode="email" placeholder="devin@company.com"></div></div>
      <p class="small muted" style="margin:6px 0 8px">They get an account with a temporary password (shown to you once), the Entity Compliance tool, and this company — and only this company, until you give them more.</p>
      <button class="btn primary sm" id="n-ok" type="button">Add and make responsible</button></details>` : ''}
  </div>`);
  const list = body.querySelector('#p-list');
  const paint = (q = '') => {
    const s = q.trim().toLowerCase();
    const hit = users.filter((u) => !s || (u.name || '').toLowerCase().includes(s) || (u.email || '').toLowerCase().includes(s));
    list.innerHTML = hit.length
      ? hit.map((u) => `<button type="button" data-uid="${u.id}" class="${u.id === ent.responsible_user_id ? 'on' : ''}"><span>${esc(u.name)}</span><span class="who-e">${esc(u.email)}</span></button>`).join('')
      : `<div class="empty small" style="padding:20px">Nobody here matches that${info.can_add_people ? ' — add them below' : ''}.</div>`;
  };
  paint();
  const m = modal({ title: `Responsible person — ${ent.name}`, body, footer: `<button class="btn" data-close>Cancel</button>${ent.responsible_user_id ? '<button class="btn ghost" id="p-none">Unlink the account</button>' : ''}` });
  body.querySelector('#p-q').oninput = (ev) => paint(ev.target.value);
  const done = async (r) => {
    m.close();
    if (r.temp_password) tempPasswordDialog(r.created, r.temp_password, ent);
    else toast(r.message || 'Saved.');
    await after();
  };
  const save = async (uid) => {
    try { await done(await api.post(`/api/docwatch/entities/${ent.id}/responsible`, { user_id: uid })); }
    catch (e) { toast(e.detail || e.message, 'err'); }
  };
  list.addEventListener('click', (ev) => { const b = ev.target.closest('button[data-uid]'); if (b) save(+b.dataset.uid); });
  const none = m.root.querySelector('#p-none');
  if (none) none.onclick = () => save(0);
  const nok = body.querySelector('#n-ok');
  if (nok) nok.onclick = async () => {
    nok.disabled = true;
    try { await done(await api.post(`/api/docwatch/entities/${ent.id}/responsible`, { name: body.querySelector('#n-name').value, email: body.querySelector('#n-mail').value })); }
    catch (e) { toast(e.detail || e.message, 'err'); nok.disabled = false; }
  };
}

// The one time the temporary password is visible: to the person who made the account, right now.
export function tempPasswordDialog(user, pw, ent) {
  const body = el(`<div style="display:flex;flex-direction:column;gap:10px">
    <p><b>${esc(user.name)}</b> now has an account (${esc(user.email)}), the Entity Compliance tool, and <b>${esc(ent.name)}</b> — and is its responsible person.</p>
    <div class="field"><label>Temporary password — give it to them; they choose their own on first sign-in</label><input type="text" readonly value="${esc(pw)}" class="mono" data-select-all></div>
    <p class="small muted">This is the only time it is shown. If it is lost, issue a new one from Users &amp; roles.</p>
  </div>`);
  body.querySelectorAll('[data-select-all]').forEach((i) => (i.onclick = () => i.select()));
  modal({ title: 'Account created', body, footer: '<button class="btn primary" data-close>Done</button>' });
}

// ---- who can see this company -------------------------------------------------------------------
// The narrow grant: one person, one company. Someone given companies this way and switched to
// "only their companies" sees exactly those — the Mozambique controller sees Mozambique.
export async function accessDialog(ent, after = () => {}) {
  let info;
  try { info = await api.get(`/api/docwatch/entities/${ent.id}/access`); }
  catch (e) { toast(e.detail || e.message, 'err'); return; }
  const body = el('<div class="dw-pick"></div>');
  const m = modal({ title: `Who can see ${ent.name}`, body, footer: '<button class="btn primary" data-close>Done</button>' });
  const paint = () => {
    const have = new Set(info.people.map((p) => p.id));
    const cands = info.candidates.filter((u) => !have.has(u.id) && u.role !== 'owner' && u.role !== 'admin');
    body.innerHTML = `
      <p class="small muted">Admins see every company. Everyone else sees the companies open to everyone and their team's — unless they are switched to <b>only their companies</b>, in which case they see exactly what is given to them here.</p>
      ${info.people.length ? `<table class="dw-people"><tbody>${info.people.map((p) => `<tr>
        <td><b>${esc(p.name)}</b>${p.responsible ? ' <span class="pill good">responsible</span>' : ''}<div class="small muted">${esc(p.email)}${p.granted_by ? ` · given by ${esc(p.granted_by)}` : ''}</div></td>
        <td style="white-space:nowrap"><label class="row small" style="gap:6px;cursor:pointer"><input type="checkbox" data-only="${p.id}" ${p.only ? 'checked' : ''} style="width:auto"> only their companies</label></td>
        <td style="text-align:right"><button class="btn sm ghost danger" data-rm="${p.id}" ${p.responsible ? 'title="The responsible person keeps the company — change the responsible person first"' : ''} ${p.responsible ? 'disabled' : ''}>Remove</button></td>
      </tr>`).join('')}</tbody></table>` : '<div class="empty small" style="padding:14px">Nobody has been given this company by name yet.</div>'}
      <div class="row" style="gap:8px;margin-top:10px;align-items:flex-end"><div class="field" style="flex:1"><label>Give it to</label><select id="g-who">${cands.map((u) => `<option value="${u.id}">${esc(u.name)} — ${esc(u.email)}</option>`).join('') || '<option value="">Everyone with an account already has it, or is an admin</option>'}</select></div>
        <label class="row small" style="gap:6px;cursor:pointer;padding-bottom:9px"><input id="g-only" type="checkbox" style="width:auto"> only this company</label>
        <button class="btn primary sm" id="g-ok" ${cands.length ? '' : 'disabled'} style="margin-bottom:2px">Give</button></div>`;
    body.querySelector('#g-ok').onclick = async () => {
      const uid = +body.querySelector('#g-who').value; if (!uid) return;
      try { const r = await api.post(`/api/docwatch/entities/${ent.id}/access`, { user_id: uid, only: body.querySelector('#g-only').checked ? true : null }); info.people = r.people; toast(r.message); paint(); await after(); }
      catch (e) { toast(e.detail || e.message, 'err'); }
    };
    body.querySelectorAll('[data-rm]').forEach((b) => (b.onclick = async () => { try { const r = await api.del(`/api/docwatch/entities/${ent.id}/access/${b.dataset.rm}`); info.people = r.people; toast(r.message); paint(); await after(); } catch (e) { toast(e.detail || e.message, 'err'); } }));
    body.querySelectorAll('[data-only]').forEach((c) => (c.onchange = async () => { try { const r = await api.post(`/api/docwatch/entities/${ent.id}/access/${c.dataset.only}/scope`, { only: c.checked }); info.people = r.people; toast(r.message); paint(); } catch (e) { toast(e.detail || e.message, 'err'); paint(); } }));
  };
  paint();
  return m;
}

// ---- pasting a list in --------------------------------------------------------------------------
// Finance keeps the list of entities in a mail or a spreadsheet, not in here. Retyping thirty-two of
// them by hand is how a grid ends up with "Cote d Ivoire" and "Ivory Coast" as two entities, so the
// list goes in the way it arrives — and the answer is a list of what landed, not a toast.
const SPLIT = /\t|\s{2,}|\s*[;|]\s*/;
function whoByName(text) {
  const map = new Map();
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    const m = SPLIT.exec(line);
    if (!m) continue;
    const name = line.slice(0, m.index).trim();
    const who = line.slice(m.index + m[0].length).trim();
    if (name && who) map.set(name.toLowerCase(), who);
  }
  return map;
}

function showImportResult(m, r, groups, who) {
  const grp = (cls, label, names) => (names && names.length ? `<div class="grp ${cls}"><div class="t">${esc(label)} · ${names.length}</div><ol>${names.map((n) => {
    const w = who && who.get(String(n).toLowerCase());
    return `<li>${esc(n)}${w ? ` <span>· ${esc(w)}</span>` : ''}</li>`;
  }).join('')}</ol></div>` : '');
  const any = groups.some((g) => (r[g.key] || []).length);
  m.body.innerHTML = `<div class="dw-res"><p>${esc(r.message || '')}</p>${groups.map((g) => grp(g.cls, g.label, r[g.key])).join('')}${any ? '' : '<p class="small muted">Nothing in that list could be read as a line.</p>'}</div>`;
  const ft = m.root.querySelector('.ft');
  if (ft) ft.innerHTML = '<button class="btn primary" data-close>Done</button>';
}

export function importCompaniesDialog({ teams = [], isAdmin = false } = {}, after = () => {}) {
  const body = el(`<div class="dw-imp">
    <div class="how">One entity per line. To set who is responsible, put their name after a tab, two or more spaces, a semicolon or a pipe — <code>Botswana&#9;Imraan</code>. A heading row is ignored, and an entity already on the grid is never duplicated.</div>
    <textarea id="i-text" spellcheck="false" placeholder="Algeria&#9;Philo&#10;Angola&#9;Otsile&#10;Bahrain&#9;Taher"></textarea>
    ${isAdmin && teams.length ? `<div class="field"><label>Visible to</label><select id="i-team"><option value="">Everyone with the tool</option>${teams.map((t) => `<option value="${t.id}">${esc(t.name)} only</option>`).join('')}</select></div>` : ''}
  </div>`);
  const m = modal({ title: 'Add entities from a list', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="i-ok">Add them</button>' });
  const ok = m.root.querySelector('#i-ok');
  ok.onclick = async () => {
    const text = body.querySelector('#i-text').value;
    if (!text.trim()) { toast('Paste the list first.', 'err'); return; }
    const team = body.querySelector('#i-team');
    const payload = { text };
    if (team && team.value) payload.team_id = +team.value;
    ok.disabled = true; ok.textContent = 'Adding…';
    try {
      const r = await api.post('/api/docwatch/entities/import', payload);
      showImportResult(m, r, [
        { key: 'added', cls: 'added', label: 'Added to the grid' },
        { key: 'updated', cls: 'updated', label: 'Already there — given a responsible person' },
        { key: 'skipped', cls: 'skipped', label: 'Already on the grid, left alone' },
      ], whoByName(text));
      await after(r);
    } catch (e) { ok.disabled = false; ok.textContent = 'Add them'; toast(e.detail || e.message, 'err'); }
  };
}

export function importTypesDialog(after = () => {}) {
  const body = el(`<div class="dw-imp">
    <div class="how">One document type per line, in the order you want the columns. A leading <code>1.</code>, <code>-</code> or bullet is stripped, so the list can go in straight out of the mail it arrived in. A type that is already a column is left alone.</div>
    <textarea id="i-text" spellcheck="false" placeholder="1. Updated bank letters&#10;2. Tax clearance certificate&#10;3. VAT certificate"></textarea>
  </div>`);
  const m = modal({ title: 'Add columns from a list', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="i-ok">Add them</button>' });
  const ok = m.root.querySelector('#i-ok');
  ok.onclick = async () => {
    const text = body.querySelector('#i-text').value;
    if (!text.trim()) { toast('Paste the list first.', 'err'); return; }
    ok.disabled = true; ok.textContent = 'Adding…';
    try {
      const r = await api.post('/api/docwatch/types/import', { text });
      showImportResult(m, r, [
        { key: 'added', cls: 'added', label: 'Added as columns — every entity has a cell for each' },
        { key: 'skipped', cls: 'skipped', label: 'Already a column, left alone' },
      ], null);
      await after(r);
    } catch (e) { ok.disabled = false; ok.textContent = 'Add them'; toast(e.detail || e.message, 'err'); }
  };
}

// ---- the grid page ------------------------------------------------------------------------------
export default {
  title: 'Entity Compliance',
  async render(root, ctx) {
    let data = null;
    let archived = null;   // the archived list, fetched the first time the section is opened — not before
    let archOpen = false;  // kept across redraws so archiving something doesn't shut the list again

    // Column headings run horizontally and wrap over up to three lines (app.css .dw-matrix th.rot),
    // so they show the document type's full name ("Fire Certificate"), not the FIRE/PL/TAX short
    // label the rotated version had to fall back to. Anything up to about thirty characters fits. Longer than that is cut with
    // an ellipsis; the full name is on hover either way.
    const shortLabel = (s) => (s.length > 30 ? s.slice(0, 29) + '…' : s);

    // ---- filtering: a low-profile bar that collapses to one line and stays out of the way.
    // Filter on: text (name/notes), responsible person, team, status, document type, expiry window.
    const FILTERS_KEY = 'dw_filters';
    const loadFilters = () => { try { return JSON.parse(localStorage.getItem(FILTERS_KEY) || '{}'); } catch { return {}; } };
    const saveFilters = (f) => { try { const has = Object.keys(f).some((k) => !k.startsWith('_') && f[k]); has ? localStorage.setItem(FILTERS_KEY, JSON.stringify(f)) : localStorage.removeItem(FILTERS_KEY); } catch {} };
    // What is switched on, counted for the "Filtered" pill. The RESP sort is a sort, not a filter:
    // it reorders the rows but hides nothing, so it must not make the grid claim rows are missing.
    const countFilters = (f) => Object.keys(f).filter((k) => !k.startsWith('_') && k !== 'respSort' && f[k]).length;

    // A row's overall state as the API words it (summary.worst is expired|expiring|missing|ok|empty),
    // against the plain-English choices in the bar.
    const WORST_OF = { expired: 'expired', expiring: 'expiring', outstanding: 'missing', in_date: 'ok', not_required: 'empty' };
    // One cell's state, likewise: the API's "missing" and "needs_date" are both "nothing filed yet".
    const cellState = (c) => {
      const d = c && c.doc;
      if (!d) return c ? 'outstanding' : 'not_required';
      if (d.status === 'ok') return 'in_date';
      if (d.status === 'missing' || d.status === 'needs_date') return 'outstanding';
      return d.status;
    };

    // ?training=1 is the Training grid: the same page over the two practice entities only, so a
    // lesson can file a certificate and archive an entity with nothing real within reach.
    const training = String(ctx.params.training || '') === '1';
    const draw = async () => {
      try { data = await api.get(training ? '/api/docwatch?training=1' : '/api/docwatch'); }
      catch (e) {
        root.innerHTML = `<div class="head"><div><h1>Entity Compliance</h1></div></div><div class="card"><div class="bd empty">Couldn't load the grid: ${esc(e.detail || e.message || 'unknown error')}. <a href="#" id="retry" style="color:var(--accent)">Try again</a></div></div>`;
        root.querySelector('#retry').onclick = (ev) => { ev.preventDefault(); draw(); };
        return;
      }
      const isAdmin = !!(data.me && data.me.is_admin);
      const s = data.summary;
      const types = data.types;
      const headPill = s.expired ? pill(plural(s.expired, 'expired', 'expired'), 'crit') : s.expiring ? pill(plural(s.expiring, 'expiring soon', 'expiring soon'), 'warn') : s.outstanding ? pill(plural(s.outstanding, 'outstanding', 'outstanding'), 'neutral') : s.companies ? pill('All in date', 'good') : '';

      root.innerHTML = `
      <div class="head"><div><h1>Entity Compliance${training ? ' <span class="pill info">Training</span>' : ''}</h1><p>${training ? 'The practice grid: two entities that are not real. File, renew and archive freely — the real grid is untouched.' : 'Click a dot to open that document, or a company name for its page.'}</p></div><div class="row" style="gap:8px">${training ? '<a class="btn" href="#/doc-watch">Back to the real grid</a>' : `${headPill}<button class="btn" id="b-import">Add from a list</button><button class="btn primary" id="b-add">Add entity</button>`}</div></div>
      <div class="grid dw-stats five" style="margin-bottom:16px">
        <div class="card stat"><div class="eyebrow">Entities</div><div class="v">${s.companies}</div><div class="d">${plural(types.length, 'document type', 'document types')}</div></div>
        <div class="card stat"><div class="eyebrow">Expired</div><div class="v" style="color:${s.expired ? 'var(--crit)' : 'inherit'}">${s.expired}</div><div class="d">past their date</div></div>
        <div class="card stat"><div class="eyebrow">Expiring soon</div><div class="v" style="color:${s.expiring ? 'var(--warn)' : 'inherit'}">${s.expiring}</div><div class="d">inside the warning window</div></div>
        <div class="card stat"><div class="eyebrow">Outstanding</div><div class="v" style="color:var(--muted)">${s.outstanding}</div><div class="d">${s.missing ? `${s.missing} nothing filed` : 'nothing filed'}${s.needs_date ? `, ${s.needs_date} no date yet` : ''}</div></div>
        <div class="card stat"><div class="eyebrow">In date</div><div class="v" style="color:${s.in_date ? 'var(--good)' : 'inherit'}">${s.in_date}</div><div class="d">${s.no_expiry ? `${s.no_expiry} never expire` : 'with a date'}</div></div>
      </div>
      <div class="card dw-grid-card" style="margin-bottom:16px">
        <div class="hd"><h2>Compliance grid</h2><div class="dw-legend"><span><i class="dw-dot ok"></i>in date</span><span><i class="dw-dot warn"></i>expiring soon</span><span><i class="dw-dot bad"></i>expired</span><span><i class="dw-dot open"></i>outstanding — nothing filed</span><span><i class="dw-dot grey"></i>not required</span></div></div>
        ${types.length ? filterBar(data) + '<div id="dw-grid-mount"></div>' : `<div class="bd empty">No document types yet.${isAdmin ? ' Add the first column below — for example "Fire Certificate" (FIRE).' : ' Ask an administrator to add them.'}</div>`}
      </div>
      <div class="card" id="add-company" style="margin-bottom:16px">
        <div class="hd"><h2>Add an entity</h2><button class="btn sm" id="b-import2">Add from a list</button></div>
        <div class="bd">
          <form class="dw-form" id="f-company">
            <div class="field"><label>Entity name</label><input id="c-name" type="text" required maxlength="160" placeholder="e.g. Botswana"></div>
            <div class="field sm"><label>Responsible person</label><input id="c-resp" type="text" maxlength="80" placeholder="e.g. Imraan"></div>
            ${isAdmin ? `<div class="field sm"><label>Visible to</label><select id="c-team"><option value="">Everyone with the tool</option>${(data.teams || []).map((t) => `<option value="${t.id}">${esc(t.name)} only</option>`).join('')}</select></div>` : ''}
            <div class="field"><label>Notes (optional)</label><input id="c-notes" type="text" maxlength="300"></div>
            <button class="btn primary" type="submit">Add entity</button>
          </form>
          <p class="small muted" style="margin:10px 0 0">A new entity gets a cell for every document type automatically.${isAdmin ? '' : ' Anyone with this tool can add an entity — the ones you add are visible to your team.'}</p>
        </div>
      </div>
      ${isAdmin ? columnsCard(data.all_types || []) : ''}
      ${archivedCard()}`;

      // Right-click a dot: flip required / not-applicable in one go (the document dialog offers the
      // same switch; this is the fast path for marking documents that don't apply somewhere).
      const flip = async (b) => {
        const id = b.dataset.doc;
        let d;
        try { d = await api.get(`/api/docwatch/${id}`); } catch (e) { toast(e.detail || e.message, 'err'); return; }
        if (!d.can_manage) { toast('Only an administrator (or the team manager) can mark a document not applicable.', 'err'); return; }
        const next = !d.required;
        try {
          await api.patch(`/api/docwatch/${id}`, { required: next });
          toast(next ? `'${d.title}' marked required again.` : `'${d.title}' marked not applicable for ${d.entity || 'this entity'} — grey dot, no reminders.`);
          await draw(); ctx.refreshCounts && ctx.refreshCounts();
        } catch (e) { toast(e.detail || e.message, 'err'); }
      };

      // The grid is repainted in place whenever a filter or the sort changes, so the handlers live on
      // the mount, not on the dots: at 32 entities × 12 columns that is 384 dots, and 384 handlers is
      // 384 closures to rebuild on every repaint.
      const mount = root.querySelector('#dw-grid-mount');
      const paintGrid = () => {
        if (!mount) return;
        const { rows, active } = applyFilters(data);
        mount.innerHTML = matrix(data, rows, active)
          + (active ? `<div class="small muted" style="padding:8px 18px 12px">Showing ${rows.length} of ${data.entities.length} entities — filters are on.</div>` : '')
          + (data.entities.length ? whoNote : '');
        syncBar();
      };
      // The bar is drawn once; a filter set by clicking a name in the grid has to show up in it too.
      const syncBar = () => {
        const f = loadFilters();
        const set = (sel, v) => { const n = root.querySelector(sel); if (n) n.value = v === undefined || v === null ? '' : String(v); };
        set('#dw-f-text', f.text); set('#dw-f-resp', f.resp); set('#dw-f-team', f.team);
        set('#dw-f-status', f.status); set('#dw-f-doc', f.doc); set('#dw-f-docstatus', f.docstatus);
        const t = root.querySelector('#dw-ftoggle');
        if (t) t.innerHTML = countFilters(f) ? pill('Filtered', 'info') : 'Filters';
      };
      const showBar = () => { const b = root.querySelector('#dw-fbody'); if (b) b.style.display = ''; };

      if (mount) {
        mount.addEventListener('click', async (ev) => {
          const b = ev.target.closest('button');
          if (!b) return;
          if (b.dataset.doc) { openDoc(b.dataset.doc, ctx, draw); return; }
          if (b.dataset.make) {
            const [entity_id, doc_type_id] = b.dataset.make.split(':').map(Number);
            try { const c = await api.post('/api/docwatch/cells', { entity_id, doc_type_id }); await draw(); openDoc(c.id, ctx, draw); } catch (e) { toast(e.detail || e.message, 'err'); }
            return;
          }
          if (b.dataset.arch) {
            const r = (data.entities || []).find((x) => String(x.id) === b.dataset.arch);
            if (r) archiveDialog(r, recordCounts(r.cells || [], r.extra || []), afterArchiveChange);
            return;
          }
          // click a responsible person's name to see only their entities; click it again to stop
          if (b.classList.contains('resp-name')) {
            const cur = loadFilters();
            saveFilters(cur.resp === b.dataset.resp ? { ...cur, resp: '' } : { ...cur, resp: b.dataset.resp });
            showBar(); paintGrid();
            return;
          }
          // the RESP heading cycles A→Z → Z→A → off. The third click always clears.
          if (b.classList.contains('dw-resp-sort')) {
            const cur = loadFilters();
            const next = cur.respSort === 'asc' ? 'desc' : cur.respSort === 'desc' ? '' : 'asc';
            saveFilters({ ...cur, respSort: next });
            paintGrid();
          }
        });
        mount.addEventListener('contextmenu', (ev) => {
          const b = ev.target.closest('button.dw-dot[data-doc]');
          if (!b) return;
          ev.preventDefault(); flip(b);
        });
      }
      const ft = root.querySelector('#dw-ftoggle');
      if (ft) ft.onclick = () => { const b = root.querySelector('#dw-fbody'); b.style.display = b.style.display === 'none' ? '' : 'none'; };
      const fApply = root.querySelector('#dw-f-apply');
      if (fApply) fApply.onclick = () => {
        saveFilters({
          ...loadFilters(),   // the RESP sort is not in the bar, so applying must not throw it away
          text: root.querySelector('#dw-f-text').value.trim(),
          resp: root.querySelector('#dw-f-resp').value,
          team: root.querySelector('#dw-f-team')?.value || '',
          status: root.querySelector('#dw-f-status').value,
          doc: root.querySelector('#dw-f-doc').value,
          docstatus: root.querySelector('#dw-f-docstatus').value,
          _t: '1',
        });
        paintGrid();
      };
      const fClear = root.querySelector('#dw-f-clear');
      if (fClear) fClear.onclick = () => { saveFilters({}); draw(); };
      // Enter in the text box applies immediately
      const fText = root.querySelector('#dw-f-text');
      if (fText) fText.onkeydown = (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); fApply.click(); } };
      paintGrid();

      if (training) {
        // The practice grid is for filing, renewing and archiving — the parts a lesson teaches.
        // Adding entities, importing lists, the columns table and the archived list stay on the real page.
        for (const sel of ['#add-company', '#t-name', '#arch']) { const el = root.querySelector(sel); if (el) (el.closest('.card') || el).remove(); }
      }
      const bi = root.querySelector('#b-import'); if (bi) bi.onclick = () => importCompaniesDialog({ teams: data.teams || [], isAdmin }, draw);
      const bi2 = root.querySelector('#b-import2'); if (bi2) bi2.onclick = () => importCompaniesDialog({ teams: data.teams || [], isAdmin }, draw);
      // "Add entity" lives at the foot of a page that is now thirty-two rows tall, so the header
      // button takes you to it and puts the cursor in the field rather than pretending to be a form.
      const bAdd = root.querySelector('#b-add'); if (bAdd) bAdd.onclick = () => {
        const card = root.querySelector('#add-company');
        card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        const name = root.querySelector('#c-name');
        if (name) name.focus({ preventScroll: true });
        // the highlight is worth nothing while the page is still travelling — let it land first
        setTimeout(() => { card.classList.remove('dw-flash'); void card.offsetWidth; card.classList.add('dw-flash'); }, 380);
      };
      wireArchived();
      const fCompany = root.querySelector('#f-company');
      if (fCompany) fCompany.onsubmit = async (ev) => {
        ev.preventDefault();
        const team = root.querySelector('#c-team');
        const body = { name: root.querySelector('#c-name').value, notes: root.querySelector('#c-notes').value, responsible: root.querySelector('#c-resp').value };
        if (team && team.value) body.team_id = +team.value;
        try { const page = await api.post('/api/docwatch/entities', body); toast(`'${page.entity.name}' added with a cell for every document type.`); ctx.go('doc-watch-entity', { id: page.entity.id }); }
        catch (e) { toast(e.detail || e.message, 'err'); }
      };
      if (isAdmin && !training) wireColumns();
    };

    function filterBar(g) {
      const f = loadFilters();
      const people = [...new Set(g.entities.map((r) => r.responsible).filter(Boolean))].sort((a, b) => a.localeCompare(b));
      const teams = [...new Set(g.entities.map((r) => r.team).filter(Boolean))];
      return `<div class="dw-fbar" id="dw-fbar">
        <button class="btn sm ghost" id="dw-ftoggle" title="Show or hide the filters">${countFilters(f) ? pill('Filtered', 'info') : 'Filters'}</button>
        <div class="dw-fbody" id="dw-fbody" style="${countFilters(f) ? '' : 'display:none'}">
          <input id="dw-f-text" type="search" placeholder="Name or notes…" value="${esc(f.text || '')}" style="width:170px">
          <select id="dw-f-resp" title="Responsible person"><option value="">Responsible: all</option>${people.map((p) => `<option ${f.resp === p ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select>
          ${teams.length ? `<select id="dw-f-team" title="Team"><option value="">Team: all</option>${teams.map((t) => `<option ${f.team === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>` : ''}
          <select id="dw-f-status" title="Overall status"><option value="">Status: all</option>${['expired', 'expiring', 'outstanding', 'in_date', 'not_required'].map((k) => `<option value="${k}" ${f.status === k ? 'selected' : ''}>${({ expired: 'Has expired', expiring: 'Expiring soon', outstanding: 'Outstanding', in_date: 'All in date', not_required: 'Nothing required' }[k])}</option>`).join('')}</select>
          <select id="dw-f-doc" title="Document column"><option value="">Document: all</option>${g.types.map((t) => `<option value="${t.id}" ${f.doc == t.id ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select>
          <select id="dw-f-docstatus" title="That document's state"><option value="">Its state: any</option>${['expired', 'expiring', 'outstanding', 'in_date', 'not_required', 'no_expiry'].map((k) => `<option value="${k}" ${f.docstatus === k ? 'selected' : ''}>${({ expired: 'expired', expiring: 'expiring soon', outstanding: 'nothing filed', in_date: 'in date', not_required: 'not required', no_expiry: "doesn't expire" }[k])}</option>`).join('')}</select>
          <button class="btn sm" id="dw-f-apply">Apply</button>
          <button class="btn sm ghost" id="dw-f-clear">Clear</button>
        </div>
      </div>`;
    }

    function applyFilters(g) {
      const f = loadFilters();
      const hasDoc = f.doc && f.docstatus;
      let rows = g.entities.filter((r) => {
        if (f.text && !(`${r.name} ${r.notes || ''}`.toLowerCase().includes(f.text.toLowerCase()))) return false;
        if (f.resp && r.responsible !== f.resp) return false;
        if (f.team && r.team !== f.team) return false;
        if (f.status && r.summary.worst !== WORST_OF[f.status]) return false;
        if (hasDoc) {
          const c = r.cells.find((x) => String(x.type_id) === String(f.doc));
          const d = c && c.doc;
          if (f.docstatus === 'no_expiry') { if (!d || !d.no_expiry) return false; }
          else if (cellState(c) !== f.docstatus) return false;
        }
        return true;
      });
      // RESP click-cycling: 'asc' → A-Z, 'desc' → Z-A, third click clears (undefined = natural order)
      if (f.respSort === 'asc') rows = [...rows].sort((a, b) => (a.responsible || '').localeCompare(b.responsible || '') || a.name.localeCompare(b.name));
      else if (f.respSort === 'desc') rows = [...rows].sort((a, b) => (b.responsible || '').localeCompare(a.responsible || '') || a.name.localeCompare(b.name));
      return { rows, active: countFilters(f) };
    }

    // The responsible person in the grid: the same name-and-tag as everywhere else, but here it is a
    // button, because clicking a name is the quickest filter on the page.
    const respCell = (r) => (r.responsible
      ? `<button class="resp-name" data-resp="${esc(r.responsible)}" title="Responsible: ${esc(r.responsible)} — click to show only ${esc(r.responsible)}'s entities">${whoCell(r)}</button>`
      : '<span class="muted" title="Nobody is responsible for this entity yet">—</span>');

    // The shared row set for both layouts — so the single (filtered) table and the two halves are identical.
    function bodyRows(g, ents, active) {
      const none = active
        ? 'No entity matches those filters — clear them to see the grid again.'
        : 'No entities visible to you yet. Add the first one below.';
      return `<tbody>${ents.length ? ents.map((r) => `<tr>
        <td class="co"><a href="#/doc-watch-entity?id=${r.id}" title="${esc(r.name)}"><span class="co-name">${esc(r.name)}</span></a>${r.team ? `<div class="small muted">${esc(r.team)} only</div>` : ''}</td>
        <td class="who">${respCell(r)}</td>
        ${r.cells.map((c) => `<td class="cell">${c.doc ? `<button class="dw-dot ${esc(c.doc.dot)}" data-doc="${c.doc.id}" title="${esc(c.doc.title)} — ${esc(c.doc.dot_title)} — right-click to mark not applicable" aria-label="${esc(c.doc.title)}: ${esc(c.doc.dot_title)}"></button>` : `<button class="dw-dot open" data-make="${r.id}:${c.type_id}" title="${esc(g.types.find((t) => t.id === c.type_id)?.name || '')} — not set up yet"></button>`}</td>`).join('')}
        <td class="act">${r.can_manage ? `<button class="btn sm ghost" data-arch="${r.id}" title="Archive ${esc(r.name)} — it leaves the grid and keeps everything it has filed">Archive</button>` : ''}</td><td class="fill"></td>
      </tr>`).join('') : `<tr><td colspan="${g.types.length + 4}" class="empty">${none}</td></tr>`}</tbody>`;
    }

    function matrix(g, rows, active) {
      const f = loadFilters();
      const respHead = (first) => `<th class="rot head-resp" title="Responsible person — click to sort A→Z, again for Z→A, a third time to clear"><button type="button" class="dw-resp-sort"${first ? ' id="dw-resp-sort"' : ''}>${f.respSort === 'asc' ? 'RESP ↑' : f.respSort === 'desc' ? 'RESP ↓' : 'RESP'}</button></th>`;
      const head = (first) => `<thead><tr><th class="co">Entity</th>${respHead(first)}${g.types.map((t) => `<th class="rot" title="${esc(t.name)}"><span>${esc(shortLabel(t.name || t.label))}</span></th>`).join('')}<th class="act"></th><th class="fill"></th></tr></thead>`;
      const table = (ents, first) => `<div class="tbl dw-matrix"><table>${head(first)}${bodyRows(g, ents, active)}</table></div>`;
      // One continuous list when filtered (no left/right split); unfiltered, two halves side by side
      // so thirty-two entities fit on one screen instead of scrolling past the fold.
      // Two halves only when they will actually sit side by side (app.css: 2900px and up). Stacked,
      // they are one list with the heading repeated in the middle, which reads as a mistake.
      const sideBySide = window.matchMedia && window.matchMedia('(min-width: 2900px)').matches;
      if (active || rows.length < 2 || !sideBySide) return `<div class="tbl dw-matrix dw-single"><table>${head(true)}${bodyRows(g, rows, active)}</table></div>`;
      const half = Math.ceil(rows.length / 2);
      return `<div class="dw-matrix-wrap">${table(rows.slice(0, half), true)}${table(rows.slice(half), false)}</div>`;
    }

    // ---- archived entities: off the grid, not gone -------------------------------------------------
    // Collapsed, and the list is only asked for when somebody opens it. The count is what came back the
    // last time it was open — before that there is nothing to show a number for without fetching.
    function archivedCard() {
      const n = archived ? archived.length : null;
      return `<details class="card dw-arch" id="arch"${archOpen ? ' open' : ''}>
        <summary><h2>Archived entities</h2><span class="small muted">Entities we no longer service — off the grid, nothing thrown away.</span><span class="sp"></span><span id="arch-count">${n === null ? '<span class="small muted">open to see</span>' : pill(plural(n, 'entity', 'entities'), 'neutral')}</span></summary>
        <div id="arch-body">${archived ? archivedRows(archived) : '<div class="bd small muted">Loading…</div>'}</div>
      </details>`;
    }

    function archivedRows(list) {
      if (!list.length) return '<div class="bd empty">Nothing is archived. An entity you stop servicing goes here — it leaves the grid and keeps everything it filed.</div>';
      return `<div class="tbl"><table><thead><tr><th>Entity</th><th>Responsible person</th><th>Why</th><th>Archived</th><th>Documents kept</th><th></th></tr></thead><tbody>
        ${list.map((e) => `<tr>
          <td><a href="#/doc-watch-entity?id=${e.id}" style="color:var(--ink);font-weight:600;text-decoration:none" title="Open ${esc(e.name)} — everything it filed is still there">${esc(e.name)}</a>${e.team ? `<div class="small muted">${esc(e.team)} only</div>` : ''}</td>
          <td>${whoCell(e)}</td>
          <td class="why">${e.archived_reason ? esc(e.archived_reason) : '<span class="muted">not given</span>'}</td>
          <td>${e.archived_at ? fmtDay(String(e.archived_at).slice(0, 10)) : '<span class="muted">—</span>'}</td>
          <td class="mono">${e.documents || 0}</td>
          <td style="text-align:right">${e.can_manage ? `<button class="btn sm" data-back="${e.id}">Bring back</button>` : ''}</td>
        </tr>`).join('')}
      </tbody></table></div>`;
    }

    async function loadArchived() {
      const body = root.querySelector('#arch-body');
      const count = root.querySelector('#arch-count');
      try { archived = (await api.get('/api/docwatch/entities/archived')).entities || []; }
      catch (e) { if (body) body.innerHTML = `<div class="bd empty">Couldn't load the archived list: ${esc(e.detail || e.message)}</div>`; return; }
      if (body) body.innerHTML = archivedRows(archived);
      if (count) count.innerHTML = pill(plural(archived.length, 'entity', 'entities'), 'neutral');
      wireArchived();
    }

    function wireArchived() {
      const det = root.querySelector('#arch');
      if (!det) return;
      det.ontoggle = () => { archOpen = det.open; if (det.open && archived === null) loadArchived(); };
      if (det.open && archived === null) loadArchived();
      det.querySelectorAll('button[data-back]').forEach((b) => (b.onclick = () => {
        const e = (archived || []).find((x) => String(x.id) === b.dataset.back);
        if (e) bringBack(e, afterArchiveChange);
      }));
    }

    // Archiving or bringing one back changes both lists, so drop the cache and redraw — wireArchived()
    // fetches again only if the section is open.
    const afterArchiveChange = async () => { archived = null; await draw(); };

    function columnsCard(all) {
      const rows = all.map((t) => `<tr data-type="${t.id}">
        <td><input type="text" class="t-name" value="${esc(t.name)}" maxlength="120"></td>
        <td><input type="text" class="t-prefix mono" value="${esc(t.prefix)}" maxlength="12" placeholder="${esc(t.label)}" style="width:90px"></td>
        <td><input type="number" class="t-order" value="${t.order}" style="width:72px"></td>
        <td><input type="number" class="t-warn" value="${t.warn_days}" min="1" max="730" style="width:80px"></td>
        <td><label class="row" style="gap:6px;cursor:pointer;font-weight:400"><input type="checkbox" class="t-active" ${t.is_active ? 'checked' : ''} style="width:auto"> ${t.is_active ? pill('shown', 'good') : pill('hidden', 'neutral')}</label></td>
        <td class="mono small muted">${t.cells}</td>
        <td style="text-align:right;white-space:nowrap"><button class="btn sm" data-save>Save</button> <button class="btn sm ghost" data-remove style="color:var(--crit)">Remove</button></td>
      </tr>`).join('');
      return `<div class="card dw-cols">
        <div class="hd"><h2>The columns (document types)</h2><span class="small muted">Rows come from entities; columns come from here. Adding a column gives every entity a new cell.</span><button class="btn sm" id="t-import">Add from a list</button></div>
        <div class="tbl"><table><thead><tr><th>Name</th><th>Column label</th><th>Order</th><th>Warn days</th><th>Shown</th><th>Cells</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="7" class="empty">No columns yet.</td></tr>'}</tbody></table></div>
        <div class="bd" style="border-top:1px solid var(--line)">
          <div class="eyebrow" style="margin-bottom:10px">Add a column</div>
          <form class="dw-form" id="f-type">
            <div class="field"><label>Document type</label><input id="t-name" type="text" required maxlength="120" placeholder="e.g. Working Permit"></div>
            <div class="field sm"><label>Column label</label><input id="t-prefix" type="text" maxlength="12" placeholder="WP" style="width:110px"></div>
            <div class="field sm"><label>Order</label><input id="t-order" type="number" value="100" style="width:90px"></div>
            <div class="field sm"><label>Warn days</label><input id="t-warn" type="number" value="90" min="1" max="730" style="width:100px"></div>
            <button class="btn primary" type="submit">Add column</button>
          </form>
          <p class="small muted" style="margin:10px 0 0">Lower order numbers sit further left. Removing a column removes its cells for every entity; stored files stay in the warehouse.</p>
        </div>
      </div>`;
    }

    function wireColumns() {
      root.querySelectorAll('.dw-cols tr[data-type]').forEach((tr) => {
        const id = tr.dataset.type;
        tr.querySelector('[data-save]').onclick = async () => {
          const body = { name: tr.querySelector('.t-name').value, prefix: tr.querySelector('.t-prefix').value, order: +tr.querySelector('.t-order').value || 100, warn_days: +tr.querySelector('.t-warn').value || 90, is_active: tr.querySelector('.t-active').checked };
          try { await api.patch(`/api/docwatch/types/${id}`, body); toast('Column updated'); draw(); } catch (e) { toast(e.detail || e.message, 'err'); }
        };
        tr.querySelector('[data-remove]').onclick = async () => {
          const name = tr.querySelector('.t-name').value;
          if (!(await confirmBox(`Remove the "${name}" column and its cells for every entity? Stored files stay in the warehouse.`, 'Remove column'))) return;
          try { const r = await api.del(`/api/docwatch/types/${id}`); toast(`Column '${name}' removed (${plural(r.removed, 'cell', 'cells')}). Files stay in the warehouse.`); draw(); } catch (e) { toast(e.detail || e.message, 'err'); }
        };
      });
      root.querySelector('#t-import').onclick = () => importTypesDialog(draw);
      root.querySelector('#f-type').onsubmit = async (ev) => {
        ev.preventDefault();
        const body = { name: root.querySelector('#t-name').value, prefix: root.querySelector('#t-prefix').value, order: +root.querySelector('#t-order').value || 100, warn_days: +root.querySelector('#t-warn').value || 90 };
        try { const t = await api.post('/api/docwatch/types', body); toast(`Column '${t.name}' added — every entity now has a cell for it.`); draw(); } catch (e) { toast(e.detail || e.message, 'err'); }
      };
    }

    await draw();
  },
};
