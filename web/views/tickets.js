// Ticket request (1.0.66): report a problem — with CBX-Connect, Chase, or any system the owner adds.
// 1. what it's about  2. your details (filled in from your account)  3. the problem (its own questions)
// C-EXA reads it, asks if anything is missing, then hands it to the support team. You follow it here.
import { api } from '../api.js';
import { esc, toast, pill, fmtDate, fileDrop } from '../ui.js';
import { drawThread, editor, fmtSize, openPreview, sendRich } from './support-ui.js';

const PILL = { new: 'info', pending: 'warn', ready: 'ai', assigned: 'info', discussing: 'ai', closed: 'good' };

export default {
  title: 'My tickets',
  async render(root, ctx) {
    const open = ctx.params.t ? +ctx.params.t : null;
    if (open) return ticket(root, ctx, open);
    if (ctx.params.new) return wizard(root, ctx);
    let d;
    try { d = await api.get('/api/support/tickets/mine'); } catch (e) { root.innerHTML = `<div class="head"><div><h1>My tickets</h1><p>${esc(e.detail || 'Could not load.')}</p></div></div>`; return; }
    const rows = d.tickets.map((t) => `<tr data-t="${t.id}" style="cursor:pointer"><td><b>${esc(t.title)}</b><div class="small muted mono">${esc(t.ref)}</div></td><td class="muted nowrap">${esc(t.system || '—')}</td>
      <td class="muted nowrap">${fmtDate(t.updated_at, { rel: true })}</td><td>${pill(t.word, PILL[t.status] || 'neutral')}</td></tr>`).join('');
    root.innerHTML = `<div class="head"><div><h1>My tickets</h1><p>Something not working? Tell us here — or mail the support mailbox.</p></div><button class="btn primary" id="new">New ticket</button></div>
      <div class="card"><div class="tbl"><table><thead><tr><th>Ticket</th><th>About</th><th>Last change</th><th>Status</th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="empty">No tickets yet.</td></tr>'}</tbody></table></div></div>`;
    root.querySelector('#new').onclick = () => ctx.go('tickets', { new: 1 });
    root.querySelectorAll('[data-t]').forEach((r) => (r.onclick = () => ctx.go('tickets', { t: r.dataset.t })));
  },
};

async function wizard(root, ctx) {
  let systems = [], me = {};
  try { [{ systems }, me] = await Promise.all([api.get('/api/support/systems'), api.get('/api/support/me')]); } catch (e) { toast(e.detail || 'Could not load', 'err'); return; }
  const st = { step: 1, system: null, problem: null, answers: {}, description: '', where: '', outcome: '', location: me.location || '', phone: me.phone || '', department: me.department || '', files: [] };
  const sys = () => systems.find((s) => s.key === st.system);
  const prob = () => (sys() ? (sys().problems || []).find((p) => p.key === st.problem) : null);

  function draw() {
    const steps = ['What is it about?', 'Your details', 'The problem'];
    const bar = `<ol class="tk-steps">${steps.map((s, i) => `<li class="${i + 1 === st.step ? 'on' : i + 1 < st.step ? 'done' : ''}">${i + 1}. ${esc(s)}</li>`).join('')}</ol>`;
    let body = '';
    if (st.step === 1) {
      body = `<div class="tk-cards">${systems.map((s) => `<button class="tk-card ${st.system === s.key ? 'on' : ''}" data-sys="${esc(s.key)}"><b>${esc(s.name)}</b><span>${esc(s.blurb || '')}</span></button>`).join('')}</div>`;
    } else if (st.step === 2) {
      body = `<div class="grid g2" style="gap:12px">
        <div class="field"><label>Name</label><input type="text" value="${esc(me.first)}" disabled></div>
        <div class="field"><label>Surname</label><input type="text" value="${esc(me.last)}" disabled></div>
        <div class="field"><label>E-mail</label><input type="text" value="${esc(me.email)}" disabled></div>
        <div class="field"><label>Department</label><input type="text" id="tk-dep" value="${esc(st.department)}" maxlength="160" ${me.department ? 'disabled' : ''}></div>
        <div class="field"><label>Where are you? <span class="muted">(site or office)</span></label><input type="text" id="tk-loc" value="${esc(st.location)}" maxlength="160" placeholder="e.g. Hyde Park"></div>
        <div class="field"><label>Phone <span class="muted">(optional)</span></label><input type="text" id="tk-ph" value="${esc(st.phone)}" maxlength="40"></div></div>
        <p class="small muted">From your account. We remember where you are for next time.</p>`;
    } else {
      const s = sys();
      body = `<div class="field"><label>What kind of problem?</label><div class="chips">${(s.problems || []).map((p) => `<button class="chip tk-chip ${st.problem === p.key ? 'on' : ''}" data-prob="${esc(p.key)}">${esc(p.name)}</button>`).join('')}</div></div>
        ${prob() ? `${(prob().fields || []).map((f) => fieldHtml(f, st.answers[f.key] || '')).join('')}
        <div class="field"><label>Where is the problem? <span class="muted">— the site (country, city, building), or the page in the system</span></label><input type="text" id="tk-where" value="${esc(st.where)}" maxlength="300" placeholder="e.g. Egypt, Alexandria, Smouha site — or the Invoices page"></div>
        <div class="field"><label>What happened? <span class="muted">— step by step, and what you expected</span></label><textarea id="tk-desc" rows="5" maxlength="8000">${esc(st.description)}</textarea></div>
        <div class="field"><label>What do you need us to do?</label><textarea id="tk-out" rows="2" maxlength="2000">${esc(st.outcome)}</textarea></div>
        <div id="tk-files"></div>` : ''}`;
    }
    const canNext = st.step === 1 ? !!st.system : st.step === 2 ? true : !!prob();
    root.innerHTML = `<div class="head"><div><h1>New ticket</h1><p>${esc(sys() ? sys().name : 'Report a problem')}</p></div><button class="btn" id="tk-back-list">My tickets</button></div>
      <div class="card"><div class="bd">${bar}${body}
      <div class="row between" style="margin-top:16px"><button class="btn ghost" id="tk-prev" ${st.step === 1 ? 'style="visibility:hidden"' : ''}>Back</button>
      <button class="btn primary" id="tk-next" ${canNext ? '' : 'disabled'}>${st.step === 3 ? 'Send ticket' : 'Next'}</button></div></div></div>`;
    root.querySelector('#tk-back-list').onclick = () => ctx.go('tickets');
    root.querySelectorAll('[data-sys]').forEach((b) => (b.onclick = () => { if (st.system !== b.dataset.sys) { st.system = b.dataset.sys; st.problem = null; st.answers = {}; } draw(); }));
    root.querySelectorAll('[data-prob]').forEach((b) => (b.onclick = () => { keep(); st.problem = b.dataset.prob; draw(); }));
    const fb = root.querySelector('#tk-files');
    if (fb) { const drop = fileDrop({ label: 'Add screenshots or files', hint: ' — optional, up to 10 MB each', onFiles: (f) => { st.files = f; } }); fb.append(drop); }
    root.querySelector('#tk-prev').onclick = () => { keep(); st.step -= 1; draw(); };
    root.querySelector('#tk-next').onclick = async () => {
      keep();
      if (st.step < 3) { st.step += 1; draw(); return; }
      const missing = (prob().fields || []).find((f) => f.required && !String(st.answers[f.key] || '').trim());
      if (missing) { toast(`Please answer: ${missing.label}`, 'err'); return; }
      if (st.description.trim().split(/\s+/).length < 3) { toast('Tell us a little more about what happened.', 'err'); return; }
      if (!st.where.trim()) { toast('Where is the problem?', 'err'); return; }
      if (!st.outcome.trim()) { toast('What do you need us to do?', 'err'); return; }
      const btn = root.querySelector('#tk-next'); btn.disabled = true; btn.innerHTML = '<span class="spin"></span> Sending…';
      try {
        const form = { system: st.system, problem: st.problem, description: st.description, answers: st.answers, where: st.where, outcome: st.outcome, location: st.location, phone: st.phone, department: st.department };
        // 1.0.98: the ticket and its files go together, so the pictures are read before the ticket is summed up
        const r = st.files.length ? await api.upload('/api/support/tickets/upload', st.files, { ticket: JSON.stringify(form) }) : await api.post('/api/support/tickets', form);
        toast(`Sent — your ticket is ${r.ticket.ref}`);
        ctx.go('tickets', { t: r.ticket.id });
      } catch (e) { toast(e.detail || 'Could not send', 'err'); btn.disabled = false; btn.textContent = 'Send ticket'; }
    };
  }
  function keep() {
    const v = (id) => { const e = root.querySelector(id); return e ? e.value : null; };
    if (v('#tk-loc') !== null) st.location = v('#tk-loc').trim();
    if (v('#tk-ph') !== null) st.phone = v('#tk-ph').trim();
    if (v('#tk-dep') !== null && !me.department) st.department = v('#tk-dep').trim();
    if (v('#tk-desc') !== null) st.description = v('#tk-desc');
    if (v('#tk-where') !== null) st.where = v('#tk-where');
    if (v('#tk-out') !== null) st.outcome = v('#tk-out');
    root.querySelectorAll('[data-q]').forEach((e) => { st.answers[e.dataset.q] = e.value; });
  }
  draw();
}

function fieldHtml(f, v) {
  const req = f.required ? '' : ' <span class="muted">(optional)</span>';
  if (f.kind === 'multiline') return `<div class="field"><label>${esc(f.label)}${req}</label><textarea data-q="${esc(f.key)}" rows="3" maxlength="2000">${esc(v)}</textarea></div>`;
  if (f.kind === 'choice') return `<div class="field"><label>${esc(f.label)}${req}</label><select data-q="${esc(f.key)}"><option value=""></option>${(f.choices || []).map((c) => `<option ${c === v ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>`;
  return `<div class="field"><label>${esc(f.label)}${req}</label><input type="text" data-q="${esc(f.key)}" value="${esc(v)}" maxlength="2000"></div>`;
}

export async function ticket(root, ctx, id, { staff = false, back, admin = false } = {}) {
  let t;
  try { t = await api.get(`/api/support/tickets/${id}`); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Ticket</h1><p>${esc(e.detail || 'Not found.')}</p></div></div>`; return; }
  // 1.0.109 (round 2): the conversation, calm — messages as cards (the requester on the left, the desk on the right, AXL quieter),
  // the desk's bookkeeping one grey line, pictures as a neat grid under their message (click → the preview, next/prev), files as
  // chips (click → the preview), the quoted chain of a mail folded away, the newest at the bottom and the reply box always in reach
  const known = (t.where ? kv('Where the problem is', t.where) : '') + (t.outcome ? kv('What is needed', t.outcome) : '') + (t.sap_said ? kv('What you sent', t.sap_said) : '');  // 1.0.98
  const answers = known + (t.answers || []).map((a) => kv(a.label, a.value)).join('');
  const loose = (t.files || []).filter((f) => !f.msg);  // 1.0.109: a mail's own are under its message
  const canReply = !!t.mine && !staff && !t.removed; // staff answer in their own box below (1.0.83: one box, not two)
  // 1.0.111: the requester's Details card as before; the desk's is drawn by support.js (one calm card: status, people, ticket,
  // actions) and places the form answers, the loose files and what was done itself (`t.answers_html`, `t.files_html`)
  const filesHtml = loose.length ? `<div class="tk-files" style="margin-top:6px">${loose.map((f) => `<button type="button" class="tk-file" data-prev="${f.n}" title="Preview ${esc(f.name)}"><span class="tk-fn">${esc(f.name)}</span><span class="tk-fs">${esc(fmtSize(f.size))}</span></button>`).join('')}</div>` : '';
  const detailsHtml = `${answers || '<span class="small muted">No form answers (it came by e-mail).</span>'}
        ${filesHtml}
        ${t.resolution ? `<div class="ag-summary" style="margin-top:8px"><b>What was done</b><br>${esc(t.resolution)}</div>` : ''}`;
  if (staff) { t.answers_html = answers; t.files_html = filesHtml; }
  root.innerHTML = `<div class="head tk-head"><div><h1>${esc(t.title)}</h1><p><span class="mono">${esc(t.ref)}</span> · ${esc(t.system || 'not sorted yet')}${t.problem ? ' · ' + esc(t.problem) : ''} · opened ${fmtDate(t.created_at, { rel: true })}</p></div>
      <div class="row tk-head-r">${staff ? '<span id="tk-find"></span>' : ''}${pill(t.word, PILL[t.status] || 'neutral')}${staff ? '<button class="btn" id="tk-axl" type="button">Ask AXL</button>' : ''}<button class="btn" id="tk-back">${staff ? (t.can_assign ? 'All tickets' : 'Your tickets') : 'My tickets'}</button></div></div>
    <div class="grid g21 tk-layout" style="align-items:start">
      <div class="card tk-conv"><div class="hd"><h2>Conversation</h2></div><div class="bd"><div class="tk-thread" id="tk-thread"></div></div>
        ${canReply ? '<div class="tk-compose" id="tk-compose"></div>' : ''}
        <div id="tk-staff"></div></div>
      <div class="card tk-details"><div class="hd"><h2>Details</h2></div><div class="bd" style="display:flex;flex-direction:column;gap:6px">
        ${staff ? '' : detailsHtml}
        <div id="tk-side"></div></div></div></div>`;
  root.querySelector('#tk-back').onclick = () => (back ? back() : ctx.go('tickets'));
  const all = [...(t.files || [])].sort((a, b) => a.n - b.n);
  const preview = (n) => openPreview(t.id, all, n);
  t.preview = preview;
  drawThread(root.querySelector('#tk-thread'), t, { admin, onPreview: preview });
  root.querySelectorAll('.tk-files [data-prev]').forEach((b) => (b.onclick = () => preview(+b.dataset.prev)));
  const box = root.querySelector('#tk-compose');
  if (box && canReply) {
    const ed = editor({ placeholder: t.status === 'pending' ? 'Your answer — paste a screenshot if it helps' : 'Add to the ticket — paste a screenshot if it helps', label: 'Your answer' });
    box.innerHTML = `<div class="row tk-send"><span class="small muted">${t.status === 'pending' ? 'Your answer' : 'Add to the ticket'}</span><button class="btn primary" id="tk-send" type="button">Send</button></div>`;
    box.prepend(ed.node);
    const send = box.querySelector('#tk-send');
    send.onclick = async () => {
      if (ed.empty()) { ed.focus(); return; }
      send.disabled = true;
      try { await sendRich(`/api/support/tickets/${t.id}/reply-rich`, ed); ed.clear(); toast('Added'); ticket(root, ctx, id, { staff, back, admin }); } catch (e) { toast(e.detail || 'Could not send', 'err'); send.disabled = false; }
    };
  }
  newestInView(root);
  return t;
}

// 1.0.83: a label column and a value column, so the values line up
const kv = (label, value) => `<div class="tk-kv"><span class="muted small">${esc(label)}</span><span class="small">${esc(value)}</span></div>`;

/** The newest message in view when a long conversation opens (the reply box is right under it). */
export function newestInView(root) {
  const th = root.querySelector('#tk-thread');
  const last = th && th.lastElementChild;
  if (!last) return;
  requestAnimationFrame(() => { const r = last.getBoundingClientRect(); if (r.bottom > window.innerHeight) last.scrollIntoView({ block: 'center' }); });
}
