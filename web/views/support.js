// Support (1.0.66): the dashboard, the tickets, and (owner/admins) the settings — which systems people can
// report problems with, each one's problem areas and questions, and who its support leads are.
import { api } from '../api.js';
import { esc, toast, pill, fmtDate, el, modal, confirmBox, busyChipHtml, aiName } from '../ui.js';
import { ticket } from './tickets.js';
import { askAxl, editor, placeByTime, quickFind, sendRich } from './support-ui.js';

const PILL = { new: 'info', pending: 'warn', ready: 'ai', assigned: 'info', discussing: 'ai', closed: 'good' };
const REMOVED_OPT = ['removed', 'Removed'];  // 1.0.109: the owner and admins only
// 1.0.98: the SLA — waiting to accept / accepted / done, with times
const SLA_WORD = { waiting: 'Waiting to accept', accepted: 'Accepted', done: 'Done' };
const SLA_PILL = { waiting: 'warn', accepted: 'info', done: 'good' };
const slaPill = (x) => (x && x.state ? pill(SLA_WORD[x.state] + (x.state === 'waiting' && x.waiting_for ? ' · ' + x.waiting_for : ''), SLA_PILL[x.state] || 'neutral') : '');

const stat = (label, v, d = '') => `<div class="card stat"><div class="eyebrow">${esc(label)}</div><div class="v num">${v}</div>${d ? `<div class="d">${esc(d)}</div>` : ''}</div>`;

export const supportDashboard = {
  title: 'Support',
  async render(root, ctx) {
    // 1.0.98: tabs — the overview, the team (live), My settings, what the desk has learned (owner/admins)
    if (ctx.params.t) return ctx.go('support-tickets', { t: ctx.params.t }); // a ticket link from a notice opens the ticket
    let me = { person: false };
    try { me = await api.get('/api/support/my-settings'); } catch { /* not on the desk */ }
    const assigner = ['owner', 'admin'].includes(ctx.me.role);
    const tabs = [['overview', 'Overview'], ['team', 'The team'], ...(me.person ? [['me', 'My settings']] : []), ...(assigner ? [['learned', 'What the desk has learned']] : [])];
    const tab = tabs.some(([k]) => k === ctx.params.tab) ? ctx.params.tab : 'overview';
    root.innerHTML = `<div class="head"><div><h1>Support</h1><p>Tickets people opened in CBX-Connect or mailed to the support mailbox.</p></div><div id="sp-find"></div></div>
      <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${k === tab}">${esc(l)}</button>`).join('')}</div><div id="sp-pane"></div>`;
    root.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => ctx.go('support', b.dataset.tab === 'overview' ? {} : { tab: b.dataset.tab })));
    const pane = root.querySelector('#sp-pane');
    quickFind(root.querySelector('#sp-find'), ctx);  // 1.0.109 (round 2): "28" jumps to T-0028; words search (press / )
    if (tab === 'team') return teamTab(pane);
    if (tab === 'me') return mySettings(pane, me);
    if (tab === 'learned') return learnedTab(pane);
    return overview(pane, ctx);
  },
};

async function overview(root, ctx) {
    let d;
    try { d = await api.get('/api/support/dashboard'); } catch (e) { root.innerHTML = `<p class="muted">${esc(e.detail || 'Could not load.')}</p>`; return; }
    if (d.own_only) return ownOverview(root, ctx, d);  // 1.0.106: the support team sees its own queue (the desk's workings are for the owner and admins)
    const t = d.totals;
    const sysRows = d.systems.map((s) => `<tr data-sys="${s.id ?? ''}" style="cursor:pointer"><td class="nowrap"><b>${esc(s.name)}</b></td><td class="num">${s.open}</td><td class="num">${s.pending}</td><td class="num">${s.counts.ready}</td><td class="num">${s.discussing}</td><td class="num">${s.counts.closed}</td><td class="num muted">${s.closed_30d}</td></tr>`).join('');
    const leadRows = d.leads.map((l) => `<tr data-lead="${l.id}" style="cursor:pointer"><td><b>${esc(l.name)}</b></td><td class="num">${l.open}</td><td class="num">${l.discussing}</td><td class="num">${l.closed}</td><td class="num muted">${l.closed_30d}</td><td class="num muted">${l.avg_days === null ? '—' : l.avg_days + ' d'}</td></tr>`).join('');
    const ready = d.ready.map((x) => `<li><a href="#" data-open="${x.id}" class="row between" style="gap:8px"><span><b>${esc(x.title)}</b> <span class="small muted mono">${esc(x.ref)}</span><br><span class="small muted">${esc(x.system || 'not sorted')} · ${esc(x.requester)} · ${fmtDate(x.updated_at, { rel: true })}</span></span>${pill('Assign', 'ai')}</a></li>`).join('');
    const m = d.mail;
    const n = d.nudge || {};
    root.innerHTML = `<div class="grid sp-tiles">${stat('Open', t.open, `${t.new} new · ${t.assigned} assigned`)}${stat('Pending', t.pending, 'waiting for the requester')}${stat('Ready to assign', t.ready)}${stat('Waiting to accept', n.waiting ?? 0, `${n.over48 ?? 0} accepted, open over 48 h`)}${stat('Being discussed', t.discussing)}${stat('Closed', t.closed, `${t.closed_30d} in the last 30 days`)}</div>
      ${ready && d.can_assign ? `<div class="card" style="margin-top:16px"><div class="hd"><h2>Ready to assign</h2><span class="small muted">C-EXA has everything — pick a lead</span></div><ul class="list">${ready}</ul></div>` : ''}
      <div class="grid g2" style="margin-top:16px;align-items:start">
        <div class="card"><div class="hd"><h2>By system</h2></div><div class="tbl"><table><thead><tr><th>System</th><th>Open</th><th>Pending</th><th>Ready</th><th>Discussed</th><th>Closed</th><th>30 days</th></tr></thead><tbody>${sysRows || '<tr><td colspan="7" class="empty">No tickets yet.</td></tr>'}</tbody></table></div></div>
        <div class="card"><div class="hd"><h2>By lead</h2></div><div class="tbl"><table><thead><tr><th>Lead</th><th>Open</th><th>Discussed</th><th>Done</th><th>30 days</th><th>Avg to close</th></tr></thead><tbody>${leadRows || '<tr><td colspan="6" class="empty">Nothing assigned yet.</td></tr>'}</tbody></table></div></div></div>
      ${d.automatic ? '<div class="card" style="margin-top:16px" id="sp-auto-card"></div>' : ''}
      ${m ? `<p class="small muted" style="margin-top:12px">Support mail: ${m.on ? `on · ${esc(m.mailbox || '')} · ${m.reachable ? 'Outlook reachable' : esc(m.detail)}${m.last_at ? ' · last read ' + fmtDate(m.last_at, { rel: true }) + (m.last && !m.last.startsWith('ok') ? ' — ' + esc(m.last) : '') : ''}` : 'off'} · mail goes out by ${esc(m.transport === 'outlook' ? 'Outlook' : 'SMTP')}</p>` : ''}`;
    root.querySelectorAll('[data-sys]').forEach((r) => (r.onclick = () => ctx.go('support-tickets', r.dataset.sys ? { system: r.dataset.sys } : {})));
    root.querySelectorAll('[data-lead]').forEach((r) => (r.onclick = () => ctx.go('support-tickets', { lead: r.dataset.lead })));
    root.querySelectorAll('[data-open]').forEach((a) => (a.onclick = (e) => { e.preventDefault(); ctx.go('support-tickets', { t: a.dataset.open }); }));
    const ac = root.querySelector('#sp-auto-card');
    if (ac) automaticMails(ac, ctx);
}

// 1.0.109: Automatic mails (owner/admins) — auto-replies and other helpdesks' acknowledgements: never a ticket, never mailed.
// One line each: Open (read it; make it a ticket after all) or Dismiss.
async function automaticMails(box, ctx) {
  let d;
  try { d = await api.get('/api/support/automatic'); } catch (e) { box.innerHTML = `<div class="bd small muted">${esc(e.detail || 'Could not load.')}</div>`; return; }
  const draw = (mails) => {
    if (!mails.length) { box.remove(); return; }
    box.innerHTML = `<div class="hd"><h2>Automatic mails</h2><span class="small muted">never a ticket, never answered</span></div>
      <ul class="list sp-auto">${mails.map((x) => `<li data-a="${x.id}"><span class="small"><b>${esc(x.sender)}</b> · ${esc(x.subject || '(no subject)')}<br><span class="muted">${esc(x.why)} · ${fmtDate(x.at, { rel: true })}${x.ticket ? ' · ' + esc(x.ticket.ref) + (x.ticket.removed ? ' (removed)' : '') : ''}</span></span>
        <span class="row" style="gap:6px;flex:none"><button class="btn sm ghost" data-aopen>Open</button><button class="btn sm ghost" data-adis>Dismiss</button></span></li>`).join('')}</ul>`;
    box.querySelectorAll('li[data-a]').forEach((li) => {
      const x = mails.find((y) => y.id === +li.dataset.a);
      li.querySelector('[data-adis]').onclick = async (e) => { e.target.disabled = true; try { const r = await api.post(`/api/support/automatic/${x.id}/dismiss`, {}); draw(r.mails); toast('Dismissed'); } catch (er) { toast(er.detail || 'Could not dismiss it', 'err'); e.target.disabled = false; } };
      li.querySelector('[data-aopen]').onclick = () => {
        const body = el(`<div><p class="small muted">${esc(x.sender)} · ${esc(x.why)}</p><div class="tk-text" style="max-height:50vh;overflow:auto">${esc(x.text || '(no words kept)').replace(/\n/g, '<br>')}</div></div>`);
        const dlg = modal({ title: x.subject || 'Automatic mail', body, footer: `<button class="btn" data-close>Close</button>${x.ticket ? `<button class="btn" id="am-see">Open ${esc(x.ticket.ref)}</button>` : ''}<button class="btn primary" id="am-make">${x.ticket && x.ticket.removed ? 'Restore it as a ticket' : 'Make it a ticket'}</button>` });
        const see = dlg.root.querySelector('#am-see');
        if (see) see.onclick = () => { dlg.close(); ctx.go('support-tickets', { t: x.ticket.id }); };
        dlg.root.querySelector('#am-make').onclick = async (e) => {
          e.target.disabled = true;
          try { const r = await api.post(`/api/support/automatic/${x.id}/ticket`, {}); dlg.close(); toast(`${r.ticket.ref} is a ticket`); ctx.go('support-tickets', { t: r.ticket.id }); } catch (er) { toast(er.detail || 'Could not make it', 'err'); e.target.disabled = false; }
        };
      };
    });
  };
  draw(d.mails);
}

// 1.0.106: the support team's overview — their tickets, then the free ones by age; nothing of the desk's workings
function ownOverview(root, ctx, d) {
  const c = d.mine_counts || {}, n = d.nudge || {};
  const item = (x) => `<li><a href="#" data-open="${x.id}" class="row between" style="gap:8px"><span><b>${esc(x.title)}</b> <span class="small muted mono">${esc(x.ref)}</span><br><span class="small muted">${esc(x.system || 'not sorted')} · ${esc(x.requester)} · opened ${fmtDate(x.created_at, { rel: true })}</span></span>${x.assigned_to_id ? pill(x.word, PILL[x.status] || 'neutral') : pill('Take it', 'ai')}</a></li>`;
  root.innerHTML = `<div class="grid sp-tiles">${stat('Yours, open', c.open ?? 0, n.waiting ? `${n.waiting} waiting for you to accept` : '')}${stat('Free to take', c.free ?? 0, 'the oldest first')}${stat('Done', c.done_30d ?? 0, 'in the last 30 days')}</div>
    <div class="grid g2" style="margin-top:16px;align-items:start">
      <div class="card"><div class="hd"><h2>Yours</h2></div>${d.mine.length ? `<ul class="list">${d.mine.map(item).join('')}</ul>` : '<p class="small muted" style="padding:12px 16px">Nothing with you now.</p>'}</div>
      <div class="card"><div class="hd"><h2>Free to take</h2><span class="small muted">the oldest first</span></div>${d.free.length ? `<ul class="list">${d.free.map(item).join('')}</ul>` : '<p class="small muted" style="padding:12px 16px">No free tickets.</p>'}</div></div>`;
  root.querySelectorAll('[data-open]').forEach((a) => (a.onclick = (e) => { e.preventDefault(); ctx.go('support-tickets', { t: a.dataset.open }); }));
}

// 1.0.98: The team, live — Mo, Lu and Kit read the mail, Ada checks, Iris reads the pictures, Nudge reminds; C-EXA only
// for what Ada marks. Polled every few seconds while the tab is open.
async function teamTab(root) {
  let timer = null, gone = false;
  async function draw() {
    let d;
    try { d = await api.get('/api/support/team'); } catch (e) { root.innerHTML = `<p class="muted">${esc(e.detail || 'Could not load.')}</p>`; return; }
    if (gone) return;
    const bot = (b) => `<div class="card sp-bot"><div class="bd"><div class="row between" style="gap:8px;align-items:center"><b>${esc(b.name)}</b>${b.status === 'working' ? pill(b.reader ? 'Reading' : 'Working', 'ai', true) : pill('Idle', 'neutral')}</div>
      <div class="small muted">${esc(b.role)}</div>
      <div class="small" style="margin-top:6px;min-height:2.6em">${b.status === 'working' ? (b.ticket ? `<a href="#/support-tickets?t=${b.ticket.id}">${esc(b.task)}</a>` : esc(b.task)) + (b.ticket && b.ticket.busy ? `<div>${busyChipHtml(b.ticket.busy, true)}</div>` : '') : (b.last ? '<span class="muted">Last: </span>' + esc(b.last) : '<span class="muted">Nothing yet today.</span>')}</div>
      <div class="small muted">${b.done_today} done today${b.last_at ? ' · ' + esc(b.last_at) : ''}</div></div></div>`;
    const lines = (xs, empty) => (xs.length ? `<ul class="list">${xs.map((x) => `<li class="small"><span class="muted mono">${esc(x.at)}</span> ${esc(x.text)}</li>`).join('')}</ul>` : `<p class="small muted">${esc(empty)}</p>`);
    const nd = d.nudge || {};
    root.innerHTML = `<div class="sp-bots">${d.bots.map(bot).join('')}</div>
      <div class="grid g2" style="margin-top:16px;align-items:start">
        <div class="card"><div class="hd"><h2>Ada's checks</h2><span class="small muted">${d.ai ? '' : 'by rules (no AI box)'}</span></div><div class="bd">${lines(d.ada, 'No checks yet.')}</div></div>
        <div class="card"><div class="hd"><h2>${esc(d.axl_name)}: what Ada couldn't settle</h2><span class="small muted">${d.axl_today} today</span></div><div class="bd">${lines(d.axl, 'Nothing escalated.')}</div></div></div>
      <div class="card" style="margin-top:16px"><div class="hd"><h2>Nudge</h2><span class="small muted">${nd.last_run_shown ? 'last run ' + esc(nd.last_run_shown) : 'not run yet'}${nd.note ? ' · ' + esc(nd.note) : ''}</span></div>
        <div class="bd"><p class="small">${d.counts.waiting} waiting to be accepted · ${d.counts.over48} accepted and open over 48 hours</p>${lines(nd.lines || [], 'No reminders or digests sent yet.')}</div></div>`;
  }
  await draw();
  timer = setInterval(() => { if (document.hidden) return; draw(); }, 4000);
  return () => { gone = true; clearInterval(timer); };
}

const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function settingsForm(s) {
  return `<div class="grid g2" style="gap:12px">
      <div class="field"><label>Your digest comes</label><select class="sp-kind"><option value="daily" ${s.schedule === 'daily' ? 'selected' : ''}>Once a day</option><option value="every" ${s.schedule === 'every' ? 'selected' : ''}>Every few days</option><option value="weekly" ${s.schedule === 'weekly' ? 'selected' : ''}>Once a week</option></select></div>
      <div class="field"><label>At</label><input type="time" class="sp-at" value="${esc(s.at_time)}"></div>
      <div class="field sp-every" ${s.schedule === 'every' ? '' : 'hidden'}><label>Every how many days</label><input type="number" class="sp-n" min="2" max="14" value="${s.every_days}"></div>
      <div class="field sp-week" ${s.schedule === 'weekly' ? '' : 'hidden'}><label>On</label><select class="sp-day">${DAYS.map((x, i) => `<option value="${i}" ${i === s.weekday ? 'selected' : ''}>${x}</option>`).join('')}</select></div></div>
    <label class="row small" style="gap:6px;align-items:center;margin-top:8px"><input type="checkbox" class="sp-inv" ${s.invisible ? 'checked' : ''}> Invisible — requesters see "the responsible person", never my name, address or signature</label>`;
}

function readForm(box) {
  return { schedule: box.querySelector('.sp-kind').value, at_time: box.querySelector('.sp-at').value, every_days: +box.querySelector('.sp-n').value || 3,
    weekday: +box.querySelector('.sp-day').value || 0, invisible: box.querySelector('.sp-inv').checked };
}

function bindForm(box) {
  box.querySelector('.sp-kind').onchange = (e) => { box.querySelector('.sp-every').hidden = e.target.value !== 'every'; box.querySelector('.sp-week').hidden = e.target.value !== 'weekly'; };
}

// 1.0.98: My settings — when my digest comes; Invisible. Tickets waiting to be accepted are mailed at once anyway.
function mySettings(root, me) {
  const s = me.settings;
  root.innerHTML = `<div class="card" style="max-width:640px"><div class="hd"><h2>My support settings</h2><span class="small muted">${esc(s.says)}</span></div><div class="bd">
      <p class="small muted">One mail on your schedule with what is outstanding. A ticket waiting for you to accept is mailed at once.</p>${settingsForm(s)}
      <div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn primary" id="sp-me-save">Save</button></div></div></div>`;
  bindForm(root);
  root.querySelector('#sp-me-save').onclick = async (e) => {
    e.target.disabled = true;
    try { const r = await api.post('/api/support/my-settings', readForm(root)); toast('Saved'); mySettings(root, r); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
  };
}

// 1.0.98: what the desk has learned — the questions the leads had to ask, and where tickets went (owner/admins)
async function learnedTab(root, page = 1) {
  let d;
  try { d = await api.get(`/api/support/lessons?page=${page}`); } catch (e) { root.innerHTML = `<p class="muted">${esc(e.detail || 'Could not load.')}</p>`; return; }
  const what = (x) => (x.kind === 'route' ? `Went to <b>${esc(x.system || 'any system')}</b>${x.lead ? ' · ' + esc(x.lead) : ''}${x.tcode ? ' · ' + esc(x.tcode) : ''}` : esc(x.question));
  const rows = d.lessons.map((x) => `<tr data-l="${x.id}"><td>${pill(x.kind === 'route' ? 'Route' : 'Question', x.kind === 'route' ? 'info' : 'ai')}</td>
      <td>${what(x)}<div class="small muted">${esc((x.words || []).join(', '))}${x.why ? ' · ' + esc(x.why) : ''}</div></td>
      <td class="small muted">${esc(x.system || 'any')}</td><td class="small muted">${esc(x.example || '')}<br>${esc(x.learned_from || '')} · ${esc(x.learned)}</td>
      <td class="num">${x.used}${x.overridden ? `<div class="small muted">${x.overridden} against</div>` : ''}</td>
      <td><label class="row small" style="gap:4px;align-items:center"><input type="checkbox" data-on ${x.active ? 'checked' : ''}> on</label></td>
      <td style="text-align:right;white-space:nowrap">${x.kind === 'route' ? '' : '<button class="btn sm ghost" data-edit>Edit</button>'}<button class="btn sm ghost" data-del>Delete</button></td></tr>`).join('');
  root.innerHTML = `<div class="card"><div class="hd"><h2>What the desk has learned</h2><span class="small muted">${d.active} on · ${d.total} in all</span></div>
      <div class="tbl"><table><thead><tr><th>Kind</th><th>What it asks / where it goes</th><th>System</th><th>Learned from</th><th>Uses</th><th></th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="7" class="empty">Nothing learned yet. When a lead asks the requester for something more, or a person moves a ticket, the desk keeps it here.</td></tr>'}</tbody></table></div>
      ${d.pages > 1 ? `<div class="row" style="gap:8px;justify-content:flex-end;padding:10px">${d.page > 1 ? '<button class="btn sm" id="pg-prev">Previous</button>' : ''}<span class="small muted">page ${d.page} of ${d.pages}</span>${d.page < d.pages ? '<button class="btn sm" id="pg-next">Next</button>' : ''}</div>` : ''}</div>`;
  const again = () => learnedTab(root, d.page);
  const pg = (id, n) => { const b = root.querySelector(id); if (b) b.onclick = () => learnedTab(root, n); };
  pg('#pg-prev', d.page - 1); pg('#pg-next', d.page + 1);
  root.querySelectorAll('tr[data-l]').forEach((tr) => {
    const id = +tr.dataset.l, x = d.lessons.find((y) => y.id === id);
    tr.querySelector('[data-on]').onchange = async (e) => { try { await api.post(`/api/support/lessons/${id}`, { active: e.target.checked }); again(); } catch (er) { toast(er.detail || 'Could not change it', 'err'); } };
    tr.querySelector('[data-del]').onclick = async () => { if (!(await confirmBox('Delete this lesson? The desk forgets it.', 'Delete'))) return; try { await api.del(`/api/support/lessons/${id}`); again(); } catch (er) { toast(er.detail || 'Could not delete', 'err'); } };
    const ed = tr.querySelector('[data-edit]');
    if (ed) ed.onclick = () => {
      const b = el(`<div><div class="field"><label>The question the desk asks</label><input type="text" id="ls-q" maxlength="300" value="${esc(x.question)}"></div>
        <div class="field"><label>Words that mark tickets like it</label><input type="text" id="ls-w" value="${esc((x.words || []).join(', '))}"></div></div>`);
      const dlg = modal({ title: 'Edit what the desk asks', body: b, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="ls-ok">Save</button>' });
      dlg.root.querySelector('#ls-ok').onclick = async () => {
        try { await api.post(`/api/support/lessons/${id}`, { question: b.querySelector('#ls-q').value, words: b.querySelector('#ls-w').value.split(',').map((w) => w.trim()).filter(Boolean) }); dlg.close(); again(); } catch (er) { toast(er.detail || 'Could not save', 'err'); }
      };
    };
  });
}

// 1.0.109: the ticket list's filters — one compact row (status, the person it is with), "More filters" for the system, grouping and
// "Assigned to me". They combine, live in the address (a filtered view can be bookmarked or shared) and are remembered per person.
// 1.0.109 (round 2): + by age; the statuses in the desk's words (Waiting on the requester, With someone); an older remembered status
// (Accepted, Ready, New) still works — it is shown as an extra choice
// 1.0.111 (round 3): status, person and system are multi-select — a compact dropdown with checkboxes each ("Status: Done, Not
// relevant" / "Status: 4 chosen"), Clear per filter and Clear all. A ticket shows when it matches any ticked value of a filter;
// different filters together. In the address as comma lists (?status=done,not_relevant&person=12,15); the server keeps only the
// values it knows (the whitelist), and so does the page (cleanStatus, cleanIds).
const FILTER_KEYS = ['status', 'person', 'system', 'group', 'mine', 'age'];
const LIST_STATUS = [['open', 'Open (not closed)'], ['free', 'Free to take'], ['not_accepted', 'Not accepted yet'], ['requester', 'Waiting on the requester'],
  ['with', 'With someone'], ['escalated', 'Escalated'], ['done', 'Done'], ['not_relevant', 'Not relevant'], ['all', 'Everything']];
const OLD_STATUS = { accepted: 'Accepted — being worked on', pending: 'Pending — AXL asked the requester', ready: 'Ready to assign', new: 'New — being read', waiting: 'Not accepted yet' };
const SHORT_STATUS = { open: 'Open', free: 'Free', not_accepted: 'Not accepted', requester: 'Waiting on the requester', with: 'With someone', all: 'Everything', removed: 'Removed', accepted: 'Accepted', pending: 'Pending', ready: 'Ready', new: 'New', waiting: 'Not accepted' };
const AGES = [['', 'Any age'], ['1', 'Older than a day'], ['3', 'Older than 3 days'], ['7', 'Older than a week'], ['30', 'Older than a month']];
const GROUPS = [['', 'No grouping'], ['lead', 'Group by person'], ['status', 'Group by status']];

// 1.0.109 (review): only a status the list knows
function cleanStatus(st) {
  return LIST_STATUS.some(([v]) => v === st) || Object.prototype.hasOwnProperty.call(OLD_STATUS, st) || st === REMOVED_OPT[0] ? st : 'open';
}  // cleanStatus end

/** 1.0.111: a status list (a comma list from the address, or what was remembered): only known values, each once; none → open. */
function cleanStatuses(v, admin) {
  const xs = (Array.isArray(v) ? v : String(v || '').split(',')).map((x) => String(x).trim()).filter(Boolean);
  const out = [...new Set(xs.filter((x) => cleanStatus(x) === x && (admin || x !== REMOVED_OPT[0])))];
  return out.length ? out : ['open'];
}  // cleanStatuses end

/** 1.0.111: an id list (people, systems): whole numbers only, each once (system 0 is "not sorted yet"). */
function cleanIds(v) {
  const xs = (Array.isArray(v) ? v : String(v ?? '').split(',')).map((x) => String(x).trim()).filter((x) => /^\d{1,9}$/.test(x));
  return [...new Set(xs.map((x) => String(+x)))];
}  // cleanIds end

function rememberKey(ctx) { return `cbx_tk_filters_${ctx.me && ctx.me.id}`; }
function remembered(ctx) { try { return JSON.parse(localStorage.getItem(rememberKey(ctx)) || '{}') || {}; } catch { return {}; } }
function keepFilters(ctx, f) {
  const q = new URLSearchParams();
  if (!(f.status.length === 1 && f.status[0] === 'open')) q.set('status', f.status.join(','));
  if (f.person.length) q.set('person', f.person.join(','));
  if (f.system.length) q.set('system', f.system.join(','));
  ['group', 'age'].forEach((k) => { if (f[k]) q.set(k, f[k]); });
  if (f.mine) q.set('mine', '1');
  try { localStorage.setItem(rememberKey(ctx), JSON.stringify(f)); } catch { /* only a convenience */ }
  try { history.replaceState(null, '', '#/support-tickets' + (q.toString() ? '?' + q.toString().replace(/%2C/g, ',') : '')); } catch { /* the address stays as it was */ }
}

/** "Status: Done, Not relevant" — up to two names, then "4 chosen". */
function msLabel(what, names, none) {
  if (!names.length) return `${what}: ${none}`;
  return `${what}: ${names.length > 2 ? names.length + ' chosen' : names.join(', ')}`;
}

/** A compact dropdown with a checkbox per value, and Clear. `on`: the filter is narrowed (shown in the accent colour). */
function msHtml(key, label, opts, chosen, on) {
  return `<details class="tkf${on ? ' on' : ''}" data-tkf="${key}"><summary class="btn sm" aria-label="${esc(label)}"><span class="tkf-v">${esc(label)}</span> <span aria-hidden="true">▾</span></summary>
    <div class="tkf-p" role="group">${opts.map(([v, l]) => `<label class="tkf-o"><input type="checkbox" value="${esc(v)}" ${chosen.includes(String(v)) ? 'checked' : ''}><span>${esc(l)}</span></label>`).join('')}
    <div class="tkf-f"><button type="button" class="btn sm ghost" data-msclear>Clear</button></div></div></details>`;
}

// one listener per page root: an open dropdown or menu closes on a click outside it, or on Escape
function closeOutside(root) {
  if (root.__closeOutside) return;
  root.__closeOutside = true;
  document.addEventListener('click', (e) => root.querySelectorAll('details.tkf[open], details.tk-moremenu[open]').forEach((d) => { if (!d.contains(e.target)) d.open = false; }), true);
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    root.querySelectorAll('details.tkf[open], details.tk-moremenu[open]').forEach((d) => { d.open = false; const s = d.querySelector('summary'); if (s) s.focus(); });
  });
}

function groupOf(t, by) {
  if (by === 'lead') return t.assigned_to || 'Nobody yet — free';
  if (t.removed) return 'Removed';
  if (t.not_relevant) return 'Not relevant';
  if (t.status === 'closed') return 'Done';
  if (/^Escalated/.test(t.word || '')) return 'Escalated';
  if (['assigned', 'discussing'].includes(t.status)) return t.sla && t.sla.state === 'waiting' ? 'Not accepted yet' : 'Being worked on';
  return { new: 'New — being read', pending: 'Waiting on the requester', ready: 'Ready to assign' }[t.status] || t.word;
}

export const supportTickets = {
  title: 'Support tickets',
  async render(root, ctx) {
    if (ctx.params.t) return staffTicket(root, ctx, +ctx.params.t);
    const admin = ['owner', 'admin'].includes(ctx.me.role);
    const fromUrl = [...FILTER_KEYS, 'lead'].some((k) => ctx.params[k] !== undefined);
    const src = fromUrl ? ctx.params : remembered(ctx);
    // 1.0.111: lists; an older address or memory (one status, `lead=`) still reads
    const f = { status: cleanStatuses(src.status, admin), person: cleanIds(src.person ?? src.lead), system: cleanIds(src.system), group: GROUPS.some(([v]) => v === src.group) ? src.group : '',
      mine: src.mine === '1' || src.mine === true, age: AGES.some(([v]) => v === String(src.age ?? '')) ? String(src.age ?? '') : '' };
    let more = !!(f.group || f.mine || f.age);
    let reopen = '';  // the dropdown that was open stays open while its boxes are ticked
    closeOutside(root);
    let seq = 0;
    async function load() {
      const nth = ++seq;  // quick ticks: only the last answer is drawn
      const q = new URLSearchParams({ status: f.status.join(','), mine: f.mine ? 'true' : 'false' });
      if (f.system.length) q.set('system', f.system.join(','));
      if (f.person.length) q.set('lead', f.person.join(','));
      if (f.age) q.set('age', f.age);
      let d;
      try { d = await api.get('/api/support/queue?' + q); } catch (e) { if (nth === seq) root.innerHTML = `<div class="head"><div><h1>All tickets</h1><p>${esc(e.detail || 'Could not load.')}</p></div></div>`; return; }
      if (nth !== seq) return;
      keepFilters(ctx, f);
      const statuses = [...LIST_STATUS, ...(admin ? [REMOVED_OPT] : [])];
      f.status.filter((s) => !statuses.some(([v]) => v === s)).forEach((s) => statuses.push([s, OLD_STATUS[s] || s]));
      const sysOpts = [['0', 'Not sorted yet'], ...d.systems.map((s) => [String(s.id), s.name])];
      const personOpts = d.leads.map((u) => [String(u.id), u.name]);
      const stNames = f.status.map((s) => SHORT_STATUS[s] || (statuses.find(([v]) => v === s) || [s, s])[1]);
      const personNames = f.person.map((id) => (personOpts.find(([v]) => v === id) || [id, 'Someone'])[1].split(' ')[0]);
      const sysNames = f.system.map((id) => (sysOpts.find(([v]) => v === id) || [id, '?'])[1]);
      const narrowed = !(f.status.length === 1 && f.status[0] === 'open') || f.person.length || f.system.length || f.group || f.mine || f.age;
      const row = (t) => `<tr data-t="${t.id}" style="cursor:pointer"><td><b>${esc(t.title)}</b><div class="small muted"><span class="mono">${esc(t.ref)}</span> · ${esc(t.requester)}${t.source === 'mail' ? ' · by e-mail' : ''}</div></td>
        <td class="muted nowrap">${esc(t.system || '—')}</td><td class="muted nowrap">${d.own_only && !t.assigned_to_id && t.status !== 'closed' ? pill('Free to take', 'ai') : esc(t.assigned_to || '—')}</td><td class="muted">${fmtDate(d.own_only ? t.created_at : t.updated_at, { rel: true })}</td><td>${pill(d.own_only && t.status === 'ready' ? 'Ready' : t.word, t.removed ? 'neutral' : PILL[t.status] || 'neutral')} ${slaPill(t.sla)} <span class="busyslot" data-ticket="${t.id}"></span>${t.not_support && admin ? ` <button class="btn sm ghost" data-nr="${t.id}" title="C-EXA thinks this isn't a support request">Not relevant</button>` : ''}</td></tr>`;  // 1.0.103: a bot reading it now
      const head = `<thead><tr><th>Ticket</th><th>System</th><th>Lead</th><th>${d.own_only ? 'Opened' : 'Last change'}</th><th>Status</th></tr></thead>`;
      let body;
      if (!d.tickets.length) body = `<div class="card"><div class="tbl"><table>${head}<tbody><tr><td colspan="5" class="empty">Nothing here.</td></tr></tbody></table></div></div>`;
      else if (f.group) {  // 1.0.109: group headings with their counts, folding
        const groups = new Map();
        d.tickets.forEach((t) => { const g = groupOf(t, f.group); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(t); });
        body = [...groups].map(([g, ts]) => `<details class="card sp-group" open><summary class="sp-sum"><span class="sp-chev" aria-hidden="true">▸</span><b>${esc(g)}</b> <span class="small muted">${ts.length}</span></summary><div class="tbl"><table>${head}<tbody>${ts.map(row).join('')}</tbody></table></div></details>`).join('');
      } else body = `<div class="card"><div class="tbl"><table>${head}<tbody>${d.tickets.map(row).join('')}</tbody></table></div></div>`;
      // what the filter shows, counted from the same rows
      const tally = new Map();
      d.tickets.forEach((t) => { const g = groupOf(t, 'status'); tally.set(g, (tally.get(g) || 0) + 1); });
      const counts = [...tally].map(([g, n]) => `${n} ${g.toLowerCase()}`).join(' · ');
      // 1.0.106: the support team sees theirs and the free ones (in the order to work them) — not "All tickets"
      root.innerHTML = `<div class="head"><div><h1>${d.own_only ? 'Your tickets and the free ones' : 'All tickets'}</h1><p>${d.tickets.length} shown${counts ? ' · ' + esc(counts) : ''}${d.own_only ? ' · yours first, then the free ones, the oldest first' : ''}</p></div><div id="sp-find"></div></div>
        <div class="row sp-filters" style="gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center">
          ${msHtml('status', msLabel('Status', stNames, 'Open'), statuses, f.status, !(f.status.length === 1 && f.status[0] === 'open'))}
          ${d.own_only ? '' : msHtml('person', msLabel('Person', personNames, 'Anyone'), personOpts, f.person, f.person.length > 0)}
          ${msHtml('system', msLabel('System', sysNames, 'Every system'), sysOpts, f.system, f.system.length > 0)}
          <button type="button" class="btn sm ghost" id="f-more" aria-expanded="${more}">${more ? 'Fewer filters' : 'More filters'}</button>
          ${narrowed ? '<button type="button" class="btn sm ghost" id="f-clear">Clear all</button>' : ''}</div>
        <div class="row sp-filters" id="f-extra" style="gap:8px;flex-wrap:wrap;margin:-4px 0 12px;align-items:center" ${more ? '' : 'hidden'}>
          <select id="f-age" aria-label="Age">${AGES.map(([v, l]) => `<option value="${esc(v)}" ${v === String(f.age) ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
          <select id="f-group" aria-label="Grouping">${GROUPS.map(([v, l]) => `<option value="${esc(v)}" ${v === f.group ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
          <label class="row small" style="gap:6px;align-items:center"><input type="checkbox" id="f-mine" ${f.mine ? 'checked' : ''}> Assigned to me</label></div>
        ${body}`;
      const on = (id, k, fn) => { const e = root.querySelector(id); if (e) e.onchange = (ev) => { f[k] = fn ? fn(ev.target) : ev.target.value; reopen = ''; load(); }; };
      on('#f-mine', 'mine', (x) => x.checked); on('#f-group', 'group'); on('#f-age', 'age');
      // the dropdowns: each tick reloads at once; the dropdown stays open (and the box keeps the focus) until a click outside
      root.querySelectorAll('details.tkf').forEach((dd) => {
        const k = dd.dataset.tkf;
        const set = (vals, keep = k) => { f[k] = k === 'status' ? cleanStatuses(vals, admin) : cleanIds(vals); reopen = keep; load(); };
        dd.querySelectorAll('input[type=checkbox]').forEach((c) => (c.onchange = () => set([...dd.querySelectorAll('input:checked')].map((x) => x.value), k + ':' + c.value)));
        dd.querySelector('[data-msclear]').onclick = () => set([]);
        dd.addEventListener('toggle', () => { if (dd.open) root.querySelectorAll('details.tkf[open]').forEach((o) => { if (o !== dd) o.open = false; }); });
        if (reopen && reopen.split(':')[0] === k) {
          dd.open = true;
          const v = reopen.split(':').slice(1).join(':');
          const box = v ? [...dd.querySelectorAll('input[type=checkbox]')].find((x) => x.value === v) : null;
          (box || dd.querySelector('summary')).focus();
        }
      });
      reopen = '';
      quickFind(root.querySelector('#sp-find'), ctx);  // 1.0.109 (round 2)
      root.querySelector('#f-more').onclick = () => { more = !more; const x = root.querySelector('#f-extra'); x.hidden = !more; root.querySelector('#f-more').textContent = more ? 'Fewer filters' : 'More filters'; root.querySelector('#f-more').setAttribute('aria-expanded', String(more)); };
      const clr = root.querySelector('#f-clear');
      if (clr) clr.onclick = () => { Object.assign(f, { system: [], status: ['open'], person: [], group: '', mine: false, age: '' }); load(); };
      root.querySelectorAll('[data-t]').forEach((r) => (r.onclick = () => ctx.go('support-tickets', { t: r.dataset.t })));
      // 1.0.103: one click (and a yes) closes C-EXA's "probably not for support" as not relevant — the requester isn't told
      root.querySelectorAll('[data-nr]').forEach((b) => (b.onclick = async (e) => {
        e.stopPropagation();
        if (!(await confirmBox('Close this ticket as not relevant? The requester isn\'t told.', 'Close it'))) return;
        try { await api.post(`/api/support/tickets/${b.dataset.nr}/not-relevant`, { reason: 'not a support request', tell: false }); toast('Closed as not relevant'); load(); } catch (er) { toast(er.detail || 'Could not close it', 'err'); }
      }));
      pollDesk(root);
    }
    load();
  },
};

// 1.0.103: which bot is reading which ticket now ("● Mo is reading — 0:12"): a light poll every 5 s while one is
async function pollDesk(root) {
  clearTimeout(root.__busyT);
  if (!root.isConnected || !root.querySelector('.busyslot[data-ticket]')) return;
  let r;
  try { r = await api.get('/api/support/busy', { quiet: true }); } catch { return; }
  root.querySelectorAll('.busyslot[data-ticket]').forEach((s) => {
    const m = r.tickets[s.dataset.ticket], key = m ? [m.bot, m.step, m.since, m.stuck ? 1 : 0].join('|') : '';
    if (s.dataset.key === key) return;
    s.dataset.key = key;
    s.innerHTML = m ? busyChipHtml(m) : '';
  });
  if (r.any) root.__busyT = setTimeout(() => pollDesk(root), 5000);
}

// 1.0.111: the ticket's Details card, calm — one card: the status in one line with what happens next; People (label left, value
// right, click the value to change it); the Ticket; what it is about; and at the bottom one button for the obvious next step with
// a "More" menu for the rest. Same rights as before (the server's can_* flags); nothing removed, only quieter.
const firstName = (n) => String(n || '').trim().split(/[\s@]+/)[0] || '';
const clipQ = (s, n = 90) => { const x = String(s || '').replace(/\s+/g, ' ').trim(); return x.length > n ? x.slice(0, n - 1).replace(/\s+\S*$/, '') + '…' : x; };

/** The status as one line — [what, what happens next] — from the ticket as GET /tickets/{id} gives it to the desk. */
function nowLine(t) {
  const who = firstName(t.requester) || 'the requester';
  const fu = t.followup;
  if (t.removed) {
    const r = t.removal || {};
    return ['Removed', [r.by ? 'by ' + r.by : '', r.at ? fmtDate(r.at, { rel: true }) : '', { tell: 'the requester was told', silent: 'silently', automatic: 'an automatic mail' }[r.how] || '', r.reason || ''].filter(Boolean).join(' · ')];
  }
  if (t.not_relevant) return ['Closed as not relevant', t.resolution && t.resolution !== 'Not relevant' ? t.resolution.replace(/^Not relevant: /, '') : ''];
  if (t.status === 'closed') return [`Done${t.assigned_to ? ' by ' + firstName(t.assigned_to) : ''}`, t.closed_at ? fmtDate(t.closed_at, { rel: true }) : ''];
  if (fu && fu.state === 'escalated') return [`Escalated to ${fu.escalated_to || 'the responsible person'}`, `no answer from ${who}`];
  if (t.status === 'pending' || fu) {
    const qs = (t.missing || []).filter(Boolean);
    const asked = fu && fu.kind === 'person' && fu.asked ? fu.asked : qs[0] || (fu && fu.asked) || '';
    return [`Waiting for ${who}`, asked ? `asked: ${clipQ(asked)}${!(fu && fu.kind === 'person') && qs.length > 1 ? ` (+${qs.length - 1} more)` : ''}` : 'waiting for their answer'];
  }
  if (!t.assigned_to_id) {
    if (t.status === 'new') return [t.word, ''];
    return [t.free ? 'Free — nobody has it yet' : t.word, t.can_assign ? 'assign it, or take it yourself' : t.can_take ? 'take it to work it' : ''];
  }
  return [t.word, ''];
}

function slaWords(t) {
  const x = t.sla || {}, fu = t.followup;
  if (fu) {
    return fu.state === 'escalated' ? `escalated ${fmtDate(fu.escalated_at, { rel: true })}`
      : fu.state === 'followed' ? `followed up ${fmtDate(fu.sent_at, { rel: true })} · escalates ${fmtDate(fu.escalate_at)}`
        : `waiting since ${fmtDate(fu.since, { rel: true })} · follow-up ${fmtDate(fu.next_at)}`;
  }
  if (x.state === 'waiting') return `${x.waiting_for || 'just now'} without an accept`;
  if (x.state === 'accepted') return `open ${x.open_for} since accepted`;
  if (x.state === 'done') return x.open_for ? `took ${x.open_for}` : `done ${x.done}`;
  return '';
}

const row = (label, value, extra = '') => `<div class="tk-row"><span class="tk-lb">${esc(label)}</span><div class="tk-v">${value}${extra}</div></div>`;

async function staffTicket(root, ctx, id) {
  const admin = ['owner', 'admin'].includes(ctx.me.role);
  const t = await ticket(root, ctx, id, { staff: true, back: () => ctx.go('support-tickets'), admin });
  if (!t) return;
  const again = () => staffTicket(root, ctx, id);
  const fin = root.querySelector('#tk-find');
  if (fin) quickFind(fin, ctx);  // 1.0.109 (round 2)
  const side = root.querySelector('#tk-side'), area = root.querySelector('#tk-staff');
  const open = t.status !== 'closed' && !t.removed;
  const [now, next] = nowLine(t);
  const own = t.owner;
  // People — Assigned to (1.0.112: owner/admins get the dropdown and Assign; AXL's suggestions under it when nobody has it),
  // Accepted, Responsible
  const sugg = (t.suggestions || []).filter((s) => t.leads.some((u) => u.id === s.id));
  const canPick = !!(t.can_assign && open && t.leads.length);
  let roles = new Map();  // who is an owner/admin — "(admin)" in the dropdown (the assign pool's roles, for those who may assign)
  if (canPick) { try { roles = new Map((await api.get('/api/support/system-people', { quiet: true })).pool.map((p) => [p.id, p.role])); } catch { /* no labels */ } }
  const pickGroups = canPick ? assignGroups(t, sugg, roles) : [];
  const preset = t.assigned_to_id || (sugg[0] && sugg[0].id) || '';
  const nPeople = pickGroups.reduce((n, g) => n + g.people.length, 0);
  const whoVal = canPick ? `<div class="tk-assign">${nPeople > 8 ? '<input type="search" id="sp-afind" class="tk-afind" placeholder="Filter people…" aria-label="Filter the people" autocomplete="off">' : ''}
      <div class="tk-arow"><select id="sp-lead" aria-label="Assign to">${assignOptions(pickGroups, preset, '')}</select>
      <button type="button" class="btn sm primary" id="sp-assign" ${!preset || preset === t.assigned_to_id ? 'disabled' : ''}>${t.assigned_to_id ? 'Reassign' : 'Assign'}</button></div></div>`
    : `<span>${esc(t.assigned_to || (t.free ? 'Nobody — free to take' : 'Nobody'))}</span>`;
  let under = '';
  if (t.auto_assigned) under += `<div class="tk-sub">Assigned by AXL: ${esc(t.auto_assigned.why)}${t.auto_assigned.can_undo ? ' · <button class="linkbtn" id="sp-undo-auto" type="button">Undo</button>' : ''}</div>`;
  if (t.can_assign && open && !t.assigned_to_id) {
    // 1.0.111 (review): an old route lesson's lead only when that person is set for the system (never an owner/admin by history)
    const routeOk = t.suggested_lead && t.leads.some((u) => u.id === t.suggested_lead.id && u.covers);
    const list = sugg.length ? sugg : routeOk ? [{ id: t.suggested_lead.id, name: t.suggested_lead.name, why: t.suggested_why }] : [];
    if (list.length) {
      under += `<div class="tk-sub tk-sugl">AXL suggests: ${list.map((s) => `<button type="button" class="tk-sg" data-sugg="${s.id}" data-tip="${esc(s.why || '')}" title="${esc(s.why || '')}">${esc(firstName(s.name))}</button>`).join(' · ')}
        <button type="button" class="tk-whyb" aria-expanded="false">why?</button></div>
        <ul class="tk-whys" hidden>${list.map((s) => `<li><b>${esc(s.name)}</b> — ${esc(s.why || '')}</li>`).join('')}</ul>`;
    } else if (t.suggest_note) under += `<div class="tk-sub">${esc(t.suggest_note)}</div>`;
    else if (!t.system_id) under += '<div class="tk-sub">AXL suggests once the system is known</div>';
  }
  if (!t.leads.length && t.can_assign && open) under += '<div class="tk-sub">Nobody can be given tickets yet — set who works each system in Support set-up.</div>';
  under += '<div id="sp-whynot"></div>';
  const x = t.sla || {};
  const accepted = !t.assigned_to_id && !x.state ? '' : x.state === 'waiting' ? '<span class="muted">not yet</span>'
    : x.accepted ? `<span>✓ ${esc(x.accepted)}${x.to_accept ? ` <span class="muted">(${esc(x.to_accept)} after assignment)</span>` : ''}</span>` : '<span class="muted">—</span>';
  const ownVal = 'owner' in t ? `<button type="button" class="tk-val" id="sp-owner">${esc(own ? own.name || own.email : 'Nobody set')}</button>${own && own.kind === 'other' ? ' <span class="muted">(not on CBX)</span>' : ''}${own && own.from && own.from !== 'ticket' ? ` <span class="muted">· the ${own.from === 'system' ? "system's" : own.from === 'desk' ? "desk's" : 'escalation'} default</span>` : ''}` : '';
  // 1.0.112: with the dropdown the row is the card's full width (the label above), so the chosen name and its short why read closed
  const whoRow = canPick ? `<div class="tk-row tk-row-wide"><span class="tk-lb">Assigned to</span><div class="tk-v">${whoVal}${under}</div></div>` : row('Assigned to', whoVal, under);
  const people = whoRow + (accepted ? row('Accepted', accepted) : '') + (ownVal ? row('Responsible', ownVal, '<div id="sp-owner-form" hidden></div>') : '');
  // Ticket — System (click to change), Area, Requester, Opened, the timer
  const canSys = (t.can_work || t.free) && open;
  const sysVal = canSys ? `<button type="button" class="tk-val" id="sp-sysv" title="${t.system_set_by ? 'set by ' + (t.system_set_by === 'person' ? 'a person' : 'the desk') : ''}">${esc(t.system || 'Not sorted yet')}</button>` : `<span>${esc(t.system || 'Not sorted yet')}</span>`;
  const sysPick = canSys ? `<div class="tk-pick" id="sp-sysbox" hidden><select id="sp-sys" aria-label="System"><option value="">Not sure</option>${(t.systems || []).map((s) => `<option value="${s.id}" ${s.id === t.system_id ? 'selected' : ''}>${esc(s.name)}</option>`).join('')}</select><button class="btn sm" id="sp-sys-set" type="button">Set</button></div>` : '';
  const reqVal = `<span><b>${esc(t.requester)}</b>${t.requester_email && t.requester_email !== t.requester ? `<br><span class="small">${esc(t.requester_email)}</span>` : ''}</span>
    <div class="tk-sub">${[t.requester_department, t.requester_location, t.requester_phone].filter(Boolean).map(esc).join(' · ')}${[t.requester_department, t.requester_location, t.requester_phone].some(Boolean) ? '<br>' : ''}${t.requester_has_account ? 'Has a CBX account' : 'No CBX account — by e-mail'}</div>`;
  const timer = slaWords(t);
  const tick = row('System', sysVal, sysPick) + (t.problem ? row('Area', `<span>${esc(t.problem)}</span>`) : '') + row('Requester', reqVal)
    + row('Opened', `<span>${esc(fmtDate(t.created_at))}</span>`) + (timer ? row(t.followup ? 'Follow-up' : 'SLA', `<span class="${t.followup && t.followup.state === 'escalated' ? 'tk-bad' : ''}">${esc(timer)}</span>`) : '');
  // What it is about — the summary, what was asked, the form answers and files, the SAP block, what the pictures show, what was done
  const qs = (t.missing || []).filter(Boolean);
  const about = `${t.summary ? `<div class="tk-sum">${esc(t.summary).replace(/\n/g, '<br>')}</div>` : ''}
    ${t.status === 'pending' && qs.length > 1 ? `<div class="tk-sub">Asked for (${t.asks}×): ${qs.map(esc).join(' · ')}</div>` : ''}
    ${t.answers_html || ''}${t.files_html || ''}
    ${t.sap && t.sap.length ? `<details class="tk-fold"><summary>SAP (${t.sap.length})</summary>${t.sap.map((r) => `<div class="tk-kv"><span class="muted small">${esc(r.label)}</span><span class="small">${esc(r.value)}</span></div>`).join('')}</details>` : ''}
    ${t.seen && t.seen.length ? `<details class="tk-fold"><summary>What the pictures show (${t.seen.length})${t.pictures ? ' · ' + esc(t.pictures) : ''}</summary><ul class="small sp-seen">${t.seen.map((s) => `<li><b>${esc(s.file)}</b>: ${esc(s.skipped || s.what || '')}${s.system ? ' · ' + esc(s.system) : ''}${s.error ? ' · ' + esc(s.error) : ''}${s.sap ? '<br><span class="muted">' + esc(s.sap) + '</span>' : ''}${s.text ? '<br><span class="muted">' + esc(s.text.slice(0, 240)) + '</span>' : ''}</li>`).join('')}</ul></details>` : ''}
    ${t.resolution && !t.not_relevant ? `<div class="ag-summary"><b>What was done</b><br>${esc(t.resolution)}</div>` : ''}`;
  // Actions — the obvious next step, and More for the rest (admin-only items only when the server says so)
  const primary = t.can_restore ? ['sp-restore', 'Restore'] : t.can_accept ? ['sp-accept', 'Accept'] : t.can_take ? ['sp-take', 'Take it']
    : t.can_work && open && t.assigned_to_id ? ['sp-done', 'Mark done…'] : null;
  const newProbs = (t.thread || []).filter((m) => m.new_problem);
  const more = [
    t.can_work && t.status === 'assigned' ? ['sp-disc', 'Mark as being discussed'] : null,
    t.can_give_back ? ['sp-giveback', 'Give back'] : null,
    t.can_assign && ['new', 'pending', 'ready'].includes(t.status) && !t.removed ? ['sp-reread', 'Let AXL read it again'] : null,
    ...newProbs.map((m) => [`np-${m.new_problem}`, `Make it a new ticket (the mail of ${fmtDate(m.at, { rel: true })})`]),
    t.can_not_relevant ? ['sp-notrel', 'Not relevant — close…'] : null,
    t.can_remove ? ['sp-remove', 'Remove ticket…'] : null,
  ].filter(Boolean);
  const acts = primary || more.length ? `<div class="tk-acts">${primary ? `<button type="button" class="btn sm primary" id="${primary[0]}">${primary[1]}</button>` : ''}
    ${more.length ? `<details class="tk-moremenu"><summary class="btn sm ghost">More ▾</summary><div class="tk-menu" role="menu">${more.map(([k, l]) => `<button type="button" role="menuitem" id="${k}">${esc(l)}</button>`).join('')}</div></details>` : ''}</div>` : '';
  side.className = 'tk-det';
  side.innerHTML = `<div class="tk-now"><b>${esc(now)}</b>${next ? ` <span class="muted">— ${esc(next)}</span>` : ''}</div>
    <section class="tk-sec"><div class="eyebrow">People</div>${people}</section>
    <section class="tk-sec"><div class="eyebrow">Ticket</div>${tick}</section>
    ${about.trim() ? `<section class="tk-sec tk-about"><div class="eyebrow">What it is about</div>${about}</section>` : ''}
    ${acts}`;
  side.querySelectorAll('.tk-files [data-prev]').forEach((b) => (b.onclick = () => t.preview(+b.dataset.prev)));
  const sm = side.querySelector('.tk-sum');
  if (sm) sm.onclick = () => sm.classList.toggle('all');  // a long summary shows its first lines; a click shows it all
  // the menu closes when a choice is made, or on a click elsewhere / Escape
  closeOutside(root);
  const menu = side.querySelector('.tk-moremenu');
  if (menu) menu.querySelectorAll('[role=menuitem]').forEach((b) => b.addEventListener('click', () => { menu.open = false; }));
  // 1.0.109 (round 2): the reply box — a small editor (pictures where they are pasted), sticky at the bottom of the conversation
  // 1.0.111: Mark done and Mark as being discussed moved to the Details card (one button / More)
  const ed = editor({ placeholder: t.can_work ? 'Write to the requester, or a note for the team — paste a screenshot with Ctrl+V' : 'A note for the team — paste a screenshot with Ctrl+V', label: t.can_work ? 'Write to the requester, or add a note for the team' : 'Add a note for the team' });
  area.className = 'tk-compose';
  area.innerHTML = `<div class="row tk-send">
      ${t.can_work && open ? '<label class="small row tk-wait" title="The desk follows up if they don\'t answer"><input type="checkbox" id="sp-wait"> Waiting for their answer</label>' : ''}<span style="flex:1"></span>
      <button class="btn sm ghost" id="sp-note" type="button">Add note</button>
      ${t.can_work && open ? '<button class="btn sm" id="sp-msg" type="button">Send to requester</button>' : ''}
    </div>`;
  area.prepend(ed.node);
  const words = () => ed.value().text;
  const act = async (url, body, okMsg, b) => { if (b) b.disabled = true; try { const r = await api.post(url, body); toast(okMsg); return r; } catch (e) { toast(e.detail || 'Did not work', 'err'); if (b) b.disabled = false; return null; } };
  const actAgain = async (...a) => { if (await act(...a)) again(); };
  const bind = (sel, fn) => { const b = root.querySelector(sel); if (b) b.onclick = () => fn(b); };
  const rich = async (toReq, okMsg, b) => {
    if (ed.empty()) { ed.focus(); return; }
    b.disabled = true;
    const w = root.querySelector('#sp-wait');
    try { await sendRich(`/api/support/tickets/${t.id}/message-rich`, ed, { to_requester: toReq ? 'true' : 'false', wait: toReq && w && w.checked ? 'true' : 'false' }); ed.clear(); toast(okMsg); again(); } catch (e) { toast(e.detail || 'Did not work', 'err'); b.disabled = false; }
  };
  const assign = async (uid, b) => {
    const r = await act(`/api/support/tickets/${t.id}/assign`, { user_id: uid }, 'Assigned — they have the summary by e-mail', b);
    if (!r) return;
    if (r.ask_why) return whyNot(root.querySelector('#sp-whynot'), t, r.ask_why, again);  // 1.0.109 (round 2): ask once, optional
    again();
  };
  // 1.0.112: Assigned to — picking in the dropdown never assigns (no accidental mails); only Assign / Reassign does
  const sel = root.querySelector('#sp-lead'), abtn = root.querySelector('#sp-assign'), afind = root.querySelector('#sp-afind');
  if (sel) {
    const ready = () => { abtn.disabled = !sel.value || +sel.value === t.assigned_to_id; };
    sel.onchange = ready;
    if (afind) afind.oninput = () => { const keep = sel.value; sel.innerHTML = assignOptions(pickGroups, keep, afind.value); ready(); };
    abtn.onclick = () => { if (sel.value) assign(+sel.value, abtn); };
    // the "AXL suggests" names choose that person in the dropdown (they never assign by themselves)
    root.querySelectorAll('[data-sugg]').forEach((b) => (b.onclick = () => {
      if (afind && afind.value) { afind.value = ''; sel.innerHTML = assignOptions(pickGroups, b.dataset.sugg, ''); }
      sel.value = b.dataset.sugg; ready(); sel.focus();
    }));
  }
  bind('.tk-whyb', (b) => { const l = root.querySelector('.tk-whys'); l.hidden = !l.hidden; b.setAttribute('aria-expanded', String(!l.hidden)); });
  bind('#sp-sysv', () => { const s = root.querySelector('#sp-sysbox'); s.hidden = !s.hidden; if (!s.hidden) s.querySelector('select').focus(); });
  bind('#sp-undo-auto', (b) => actAgain(`/api/support/tickets/${t.id}/undo-auto`, {}, 'Undone — it is free again', b));
  bind('#sp-owner', () => ownerForm(root.querySelector('#sp-owner-form'), t, again));
  bind('#sp-accept', (b) => actAgain(`/api/support/tickets/${t.id}/accept`, {}, 'Accepted — it is on your To-do', b));
  // 1.0.103: a free ticket is taken first (then it is theirs to finish); their own can go back to the desk
  // 1.0.106: lost the race (someone was first, or it closed): say so in place of the ticket, with the way back
  bind('#sp-take', async (b) => {
    b.disabled = true;
    try { await api.post(`/api/support/tickets/${t.id}/take`, {}); toast('It is yours — on your To-do; the time it takes counts from now'); again(); } catch (e) {
      root.innerHTML = `<div class="head"><div><h1>${esc(t.title)}</h1><p>${esc(e.detail || 'This ticket isn\'t free any more.')} It is no longer on your list.</p></div><button class="btn" id="tk-back2">Your tickets</button></div>`;
      root.querySelector('#tk-back2').onclick = () => ctx.go('support-tickets');
    }
  });
  bind('#sp-done', () => markDone(t, ed, again));
  bind('#sp-notrel', () => notRelevant(t, again));  // 1.0.103
  bind('#sp-remove', () => removeTicket(t, again));  // 1.0.109
  const makeNew = async (mid, b) => {  // 1.0.109: a new problem sent in this thread → its own ticket
    b.disabled = true;
    try { const r = await api.post(`/api/support/new-problem/${mid}`, {}); toast(`Made ${r.ticket.ref} — nobody was told`); ctx.go('support-tickets', { t: r.ticket.id }); } catch (e) { toast(e.detail || 'Could not make it', 'err'); b.disabled = false; }
  };
  root.querySelectorAll('[data-newprob]').forEach((b) => (b.onclick = () => makeNew(b.dataset.newprob, b)));
  newProbs.forEach((m) => bind(`#np-${m.new_problem}`, (b) => makeNew(m.new_problem, b)));
  // 1.0.111: "It belongs here" (owner/admins) — the reverse: the mail joins this ticket as the requester's reply; nobody is told
  root.querySelectorAll('[data-belongs]').forEach((b) => (b.onclick = async () => {
    b.disabled = true;
    try { await api.post(`/api/support/new-problem/${b.dataset.belongs}/belongs`, {}); toast('Added to the ticket — nobody was told'); again(); } catch (e) { toast(e.detail || 'Could not add it', 'err'); b.disabled = false; }
  }));
  bind('#sp-restore', (b) => actAgain(`/api/support/tickets/${t.id}/restore`, {}, 'Restored', b));
  bind('#sp-giveback', async (b) => { if (await confirmBox('Give this ticket back? It goes back to the desk for anyone to take.', 'Give back')) actAgain(`/api/support/tickets/${t.id}/give-back`, {}, 'Given back — free for anyone to take', b); });
  bind('#sp-sys-set', (b) => actAgain(`/api/support/tickets/${t.id}/system`, { system_id: +root.querySelector('#sp-sys').value || null }, 'System set', b));
  bind('#sp-reread', (b) => actAgain(`/api/support/tickets/${t.id}/reread`, {}, 'Read again', b));
  bind('#sp-note', (b) => rich(false, 'Note added', b));
  bind('#sp-msg', (b) => rich(true, 'Sent to the requester', b));
  // pictures in the reply box go with Send to requester or Add note — never dropped by a status change (1.0.111 review)
  const noPics = () => { if (ed.value().files.length) { toast('Pictures go with Send to requester or Add note — send them first.', 'err'); return false; } return true; };
  bind('#sp-disc', (b) => { if (noPics()) actAgain(`/api/support/tickets/${t.id}/status`, { status: 'discussing', note: words() }, 'Marked as being discussed', b); });
  // 1.0.109 (round 2): Ask AXL — a panel beside the ticket; its chat is one folded line in the conversation (never the requester's)
  const name = aiName() || 'AXL';
  const axlBtn = root.querySelector('#tk-axl');
  axlBtn.textContent = `Ask ${name}`;
  const openAxl = () => askAxl(t, { aiName: name, onChanged: (onlyChat) => (onlyChat ? axlLine(root, t, name, openAxl) : again()) });
  axlBtn.onclick = openAxl;
  axlLine(root, t, name, openAxl);
}

// 1.0.112: the Assign dropdown's groups — "AXL suggests" (the top suggestions, "Name — why" short), "Works <system>" (the people
// set for the system not listed above), "Everyone else on the desk" (the rest of the assign pool); owners/admins say "(admin)"
function assignGroups(t, sugg, roles) {
  const tag = (id) => (['owner', 'admin'].includes(roles.get(id)) ? ' (admin)' : '');
  const has = (id) => (id === t.assigned_to_id ? ' — has it' : '');
  const seen = new Set();
  const take = (us, label) => us.filter((u) => !seen.has(u.id)).map((u) => { seen.add(u.id); return { id: u.id, name: u.name, text: label(u) }; });
  const short = (w) => clipQ(String(w).replace(/\s*\([^)]*\)/g, ''), 48);  // "works Chase · 13 done · 2 open now"
  const groups = [
    { label: 'AXL suggests', people: take(sugg, (u) => `${u.name}${tag(u.id)}${u.why ? ' — ' + short(u.why) : ''}${has(u.id)}`) },
    { label: `Works ${t.system || 'this system'}`, people: take(t.leads.filter((u) => u.covers), (u) => `${u.name}${tag(u.id)}${has(u.id)}`) },
    { label: 'Everyone else on the desk', people: take(t.leads, (u) => `${u.name}${tag(u.id)}${has(u.id)}`) },
  ];
  if (t.assigned_to_id && !seen.has(t.assigned_to_id)) groups.unshift({ label: 'Has it now', people: [{ id: t.assigned_to_id, name: t.assigned_to, text: `${t.assigned_to} — has it` }] });
  return groups.filter((g) => g.people.length);
}  // assignGroups end

/** The dropdown's options: "Choose a person…" and the groups; `q` filters by the name (and AXL's why), `chosen` stays chosen. */
function assignOptions(groups, chosen, q) {
  const words = String(q || '').toLowerCase().trim().split(/\s+/).filter(Boolean);
  const hit = (p) => words.every((w) => p.text.toLowerCase().includes(w));
  const gs = groups.map((g) => ({ label: g.label, people: g.people.filter(hit) })).filter((g) => g.people.length);
  const ids = gs.flatMap((g) => g.people.map((p) => String(p.id)));
  const pick = ids.includes(String(chosen)) ? String(chosen) : words.length && ids.length ? ids[0] : '';
  return `<option value="" ${pick ? '' : 'selected'}>${gs.length ? 'Choose a person…' : 'Nobody matches'}</option>`
    + gs.map((g) => `<optgroup label="${esc(g.label)}">${g.people.map((p) => `<option value="${p.id}" ${String(p.id) === pick ? 'selected' : ''}>${esc(p.text)}</option>`).join('')}</optgroup>`).join('');
}  // assignOptions end

// 1.0.111: Mark done… — what was done (the requester is told), from the reply box's words if any were written there
function markDone(t, ed, again) {
  const pics = ed.value().files.length;
  const body = el(`<div><div class="field"><label for="md-what">What was done <span class="muted">— the requester is told</span></label><textarea id="md-what" rows="4" maxlength="4000"></textarea></div>
    ${pics ? '<p class="small muted">The pictures in the reply box go with Send to requester or Add note — send them first.</p>' : ''}</div>`);
  const what = body.querySelector('#md-what');
  what.value = ed.value().text || '';
  const m = modal({ title: `Mark ${t.ref} done`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>Mark done</button>' });
  m.root.querySelector('[data-ok]').onclick = async (e) => {
    if (what.value.trim().length < 5) { toast('Write what was done — the requester is told.', 'err'); what.focus(); return; }
    e.target.disabled = true;
    try { await api.post(`/api/support/tickets/${t.id}/status`, { status: 'closed', note: what.value.trim() }); m.close(); if (!pics) ed.clear(); toast('Done — the requester is told'); again(); } catch (er) { toast(er.detail || 'Could not close it', 'err'); e.target.disabled = false; }
  };
  what.focus();
}

// 1.0.109 (round 2): the AXL chat as one folded line in the conversation ("AXL chat (3)"), where it happened
async function axlLine(root, t, name, openAxl) {
  let d;
  try { d = await api.get(`/api/support/tickets/${t.id}/axl`, { quiet: true }); } catch { return; }
  const th = root.querySelector('#tk-thread');
  if (!th) return;
  th.querySelectorAll('.tk-axlchat').forEach((x) => x.remove());
  const people = d.chat.filter((c) => c.role === 'person');
  if (!people.length) return;
  const last = d.chat[d.chat.length - 1];
  const node = document.createElement('details');
  node.className = 'tk-axlchat';
  node.innerHTML = `<summary><span class="sp-chev" aria-hidden="true">▸</span> ${esc(name)} chat (${people.length}) · ${esc(fmtDate(last.at, { rel: true }))} <span class="muted">— the desk only</span></summary>
    <div class="tk-axlchat-bd">${d.chat.map((c) => `<div class="small"><b>${esc(c.role === 'axl' ? name : c.name)}:</b> ${esc(c.text)}${c.card && c.card.kind ? ` <span class="muted">[${esc({ draft: 'a draft mail', reassign: 'move to ' + (c.card.name || ''), system: 'system ' + (c.card.name || ''), not_relevant: 'not relevant' }[c.card.kind] || '')} — ${esc(c.card.state)}]</span>` : ''}</div>`).join('')}
    <button type="button" class="btn sm ghost" data-open-axl>Open the chat</button></div>`;
  node.querySelector('[data-open-axl]').onclick = openAxl;
  placeByTime(th, node, last.at);
}

// 1.0.109 (round 2): "Why not <name>?" — asked once, optional, one line of chips
function whyNot(box, t, sug, again) {
  if (!box) { again(); return; }
  box.innerHTML = `<div class="sp-whynot small"><span>Why not ${esc(sug.name)}?</span>
    ${[['wrong_area', 'Wrong area'], ['too_busy', 'Too busy'], ['on_leave', 'On leave'], ['other', 'Other']].map(([k, l]) => `<button type="button" class="chip" data-why="${k}">${esc(l)}</button>`).join('')}
    <input type="text" id="wn-text" maxlength="300" placeholder="a few words (optional)" hidden><button type="button" class="btn sm" id="wn-ok" hidden>Save</button><button type="button" class="btn sm ghost" id="wn-skip">Skip</button></div>`;
  const save = async (reason) => {
    try { await api.post(`/api/support/tickets/${t.id}/why-not`, { reason, text: box.querySelector('#wn-text').value }); toast('Thanks — the suggestions learn from it'); } catch (e) { toast(e.detail || 'Could not save it', 'err'); }
    again();
  };
  box.querySelectorAll('[data-why]').forEach((b) => (b.onclick = () => {
    if (b.dataset.why !== 'other') { save(b.dataset.why); return; }
    const x = box.querySelector('#wn-text'); x.hidden = false; box.querySelector('#wn-ok').hidden = false; x.focus();
    box.querySelector('#wn-ok').onclick = () => save('other');
  }));
  box.querySelector('#wn-skip').onclick = again;
}

// 1.0.109 (round 2): the responsible person — someone on CBX, or someone not on CBX (name and e-mail)
async function ownerForm(box, t, again) {
  if (!box) return;
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = '<span class="spin"></span>';
  let d;
  try { d = await api.get('/api/support/owner-people'); } catch (e) { box.innerHTML = `<span class="small muted">${esc(e.detail || 'Could not load.')}</span>`; return; }
  const o = t.owner_own ? t.owner : null;
  box.innerHTML = `<div class="sp-ownerf"><select id="ow-who" aria-label="Responsible person"><option value="">The default${t.owner && !t.owner_own ? ' (' + esc(t.owner.name) + ')' : ''}</option>${d.people.map((p) => `<option value="${p.id}" ${o && o.kind === 'user' && o.id === p.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}<option value="other" ${o && o.kind === 'other' ? 'selected' : ''}>Someone not on CBX…</option></select>
    <div class="row" id="ow-other" style="gap:6px" ${o && o.kind === 'other' ? '' : 'hidden'}><input type="text" id="ow-name" placeholder="Name" maxlength="160" value="${esc(o && o.kind === 'other' ? o.name : '')}"><input type="email" id="ow-email" placeholder="E-mail" maxlength="255" value="${esc(o && o.kind === 'other' ? o.email : '')}"></div>
    <div class="row" style="gap:6px"><button type="button" class="btn sm primary" id="ow-save">Save</button><button type="button" class="btn sm ghost" id="ow-cancel">Cancel</button></div></div>`;
  const who = box.querySelector('#ow-who');
  who.onchange = () => { box.querySelector('#ow-other').hidden = who.value !== 'other'; };
  box.querySelector('#ow-cancel').onclick = () => { box.hidden = true; };
  box.querySelector('#ow-save').onclick = async (e) => {
    const body = who.value === 'other' ? { kind: 'other', name: box.querySelector('#ow-name').value.trim(), email: box.querySelector('#ow-email').value.trim() } : who.value ? { kind: 'user', id: +who.value } : { kind: 'none' };
    e.target.disabled = true;
    try { await api.post(`/api/support/tickets/${t.id}/owner`, body); toast('Saved'); again(); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
  };
}

// 1.0.103: Not relevant — close: a reason (optional, one line) and "Tell the requester" (off unless ticked)
function notRelevant(t, again) {
  const body = el(`<div><div class="field"><label for="nr-why">Reason (optional)</label><input id="nr-why" maxlength="200" placeholder="spam, not a support request, duplicate of T-0123"></div>
    <label class="row small" style="gap:6px;align-items:center;margin-top:8px"><input type="checkbox" id="nr-tell"> Tell the requester</label>
    <p class="small muted">${esc(t.ref)} is closed without being assigned. It never counts as a solved ticket.</p></div>`);
  const m = modal({ title: 'Not relevant — close', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>Close it</button>' });
  m.root.querySelector('[data-ok]').onclick = async (e) => {
    e.target.disabled = true;
    try {
      await api.post(`/api/support/tickets/${t.id}/not-relevant`, { reason: body.querySelector('#nr-why').value, tell: body.querySelector('#nr-tell').checked });
      m.root.remove(); toast('Closed as not relevant'); again();
    } catch (er) { toast(er.detail || 'Could not close it', 'err'); e.target.disabled = false; }
  };
  body.querySelector('#nr-why').focus();
}

// 1.0.109: Remove ticket… (owner/admins): let the requester know (a short message, editable — sent once) or remove it silently
function removeTicket(t, again) {
  const body = el(`<div><div class="field"><label for="rm-why">Reason (optional — kept in the audit)</label><input id="rm-why" maxlength="200" placeholder="a duplicate of T-0123, not for this desk, logged elsewhere"></div>
    <div class="field" style="margin-top:8px"><label class="row small" style="gap:6px;align-items:center"><input type="radio" name="rm-how" value="tell" checked> Let the requester know</label>
      <textarea id="rm-msg" rows="3" maxlength="2000">${esc(t.remove_notice || '')}</textarea>
      <label class="row small" style="gap:6px;align-items:center;margin-top:6px"><input type="radio" name="rm-how" value="silent"> Remove silently — no mail</label></div>
    <p class="small muted">${esc(t.ref)} disappears from every list and count. You find it under Removed, to restore it.</p></div>`);
  const why = body.querySelector('#rm-why'), msg = body.querySelector('#rm-msg');
  let edited = false;
  msg.oninput = () => { edited = true; };
  why.oninput = () => { if (!edited) msg.value = (t.remove_notice || '').replace(/\.$/, '') + (why.value.trim() ? ': ' + why.value.trim().replace(/\.$/, '') + '.' : '.'); };
  body.querySelectorAll('[name=rm-how]').forEach((r) => (r.onchange = () => { msg.disabled = body.querySelector('[name=rm-how]:checked').value !== 'tell'; }));
  const m = modal({ title: `Remove ${t.ref}`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" data-ok>Remove</button>' });
  m.root.querySelector('[data-ok]').onclick = async (e) => {
    const tell = body.querySelector('[name=rm-how]:checked').value === 'tell';
    e.target.disabled = true;
    try {
      await api.post(`/api/support/tickets/${t.id}/remove`, { tell, reason: why.value, message: tell ? msg.value : '' });
      m.close(); toast(tell ? 'Removed — the requester is told' : 'Removed'); again();
    } catch (er) { toast(er.detail || 'Could not remove it', 'err'); e.target.disabled = false; }
  };
  why.focus();
}

export const supportSettings = {
  title: 'Support settings',
  async render(root, ctx) {
    let d;
    try { d = await api.get('/api/support/settings'); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Support set-up</h1><p>${esc(e.detail || 'Could not load.')}</p></div></div>`; return; }
    const m = d.mail;
    const reload = () => this.render(root, ctx);
    root.innerHTML = `<div class="head"><div><h1>Support settings</h1><p>Who works tickets, what people can report problems with, and the questions for each kind of problem.</p></div></div>
      <div class="card" id="sp-work-card"><div class="hd"><h2>Who works each system</h2><span class="small muted">AXL suggests only them for that system</span></div><div class="bd" id="sp-work"></div></div>
      <div class="card" id="sp-leads-card" style="margin-top:16px"><div class="hd"><h2>Support leads</h2><span class="small muted">they see the Support menu and can be given tickets — so can people with the Support desk tool</span></div><div class="bd" id="sp-leads"></div></div>
      <div class="card" style="margin-top:16px"><div class="hd"><h2>Reminders and each person's settings</h2><span class="small muted">Nudge mails a lead at 6 hours and 2 days when a ticket isn't accepted</span></div><div class="bd" id="sp-people"></div></div>
      <div class="card" style="margin-top:16px"><div class="hd"><h2>Follow-ups and the responsible person</h2><span class="small muted">when the requester doesn't answer</span></div><div class="bd" id="sp-follow"></div></div>
      <div class="card" style="margin-top:16px"><div class="hd"><h2>Who gets a ticket</h2><span class="small muted">AXL's suggestions</span></div><div class="bd" id="sp-picks"></div></div>
      <div class="row between" style="margin:20px 0 8px;align-items:center"><h2 style="margin:0;font-size:16px">Ticket types</h2><button class="btn sm" id="sp-add">Add a ticket type</button></div>
      <div id="sp-systems"></div>
      <p class="small muted" style="margin-top:16px">Support mailboxes: ${m.on ? `reading <b>${esc(m.mailbox || '—')}</b>${m.reachable ? '' : ' (not reachable)'}` : 'none read yet'} — chosen in <a href="#/mailer?box=settings">Mailer → Mail set-up</a>.</p>`;
    drawSystemPeople(root.querySelector('#sp-work'), reload);  // 1.0.111
    drawLeads(root.querySelector('#sp-leads'), d, reload);
    drawPeople(root.querySelector('#sp-people'));
    drawFollowups(root.querySelector('#sp-follow'));  // 1.0.109 (round 2)
    drawPicks(root.querySelector('#sp-picks'));
    const box = root.querySelector('#sp-systems');
    const open = (() => { try { return JSON.parse(sessionStorage.getItem('cbx_sp_open') || '[]'); } catch { return []; } })();
    d.systems.forEach((s) => box.append(systemCard(s, d.leads, reload, open.includes(s.id))));
    root.querySelector('#sp-add').onclick = () => {
      const b = el('<div class="field"><label>What people will report problems with</label><input id="sp-new" type="text" maxlength="80" placeholder="e.g. SAP"></div>');
      const dlg = modal({ title: 'Add a ticket type', body: b, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="sp-new-ok">Add</button>' });
      dlg.root.querySelector('#sp-new-ok').onclick = async () => {
        const name = b.querySelector('#sp-new').value.trim(); if (!name) return;
        try { const s = await api.post('/api/support/systems', { name, problems: [{ name: 'Something else', fields: [] }] }); dlg.close(); remember(s.id, true); toast('Added — now give it its problem areas'); reload(); } catch (e) { toast(e.detail || 'Could not add', 'err'); }
      };
    };
  },
};

// 1.0.111: Who works each system — per system the people chosen from everyone who can work tickets (the support leads and the
// people with the Support desk tool). AXL suggests only them for that system. Nobody set → AXL guesses from past tickets, and
// "Use AXL's guess" proposes who finished most of them (dashed until saved — remove any wrong one, then Save). Audited.
async function drawSystemPeople(box, reload) {
  let d;
  try { d = await api.get('/api/support/system-people'); } catch (e) { box.innerHTML = `<p class="small muted">${esc(e.detail || 'Could not load.')}</p>`; return; }
  const pool = new Map(d.pool.map((p) => [p.id, p]));
  const saved = new Map(d.systems.map((s) => [s.id, s.people.map((p) => p.id)]));
  const work = new Map(d.systems.map((s) => [s.id, [...saved.get(s.id)]]));
  const guessed = new Map();  // system → {id: done} of the people the guess added (not saved yet)
  const nameOf = (id) => (pool.get(id) || {}).name || 'Someone';
  const changed = () => d.systems.filter((s) => { const a = saved.get(s.id), b = work.get(s.id); return a.length !== b.length || a.some((x) => !b.includes(x)); });
  function draw() {
    box.innerHTML = `${d.systems.map((s) => {
      const ids = work.get(s.id), g = guessed.get(s.id) || {};
      const chips = ids.map((id) => `<span class="sw-chip${id in g ? ' new' : ''}" title="${id in g ? esc(`AXL's guess — finished ${g[id]} ${s.name} ticket${g[id] === 1 ? '' : 's'}`) : ''}">${esc(nameOf(id))}<button type="button" data-rm="${id}" aria-label="Remove ${esc(nameOf(id))} from ${esc(s.name)}">×</button></span>`).join('');
      const left = d.pool.filter((p) => !ids.includes(p.id));
      const add = left.length ? `<select class="sw-add" data-add aria-label="Add a person to ${esc(s.name)}"><option value="">Add person…</option>${left.map((p) => `<option value="${p.id}">${esc(p.name)}${['owner', 'admin'].includes(p.role) ? ' · ' + esc(p.role) : ''}</option>`).join('')}</select>` : '';
      const none = ids.length ? '' : `<span class="sw-none">Nobody set — AXL guesses from past tickets</span>${(s.guess || []).length || saved.get(s.id).length ? ' <button type="button" class="btn sm ghost" data-guess>Use AXL\'s guess</button>' : ''}`;
      return `<div class="sw-row" data-s="${s.id}"><div><b>${esc(s.name)}</b>${s.active ? '' : ' ' + pill('Hidden', 'neutral')}</div><div class="sw-chips">${chips}${none}${add}</div></div>`;
    }).join('')}
      <div class="row" style="justify-content:flex-end;gap:10px;margin-top:10px;align-items:center"><span class="small muted">${changed().length ? 'Not saved yet' : ''}</span><button class="btn primary" id="sw-save" type="button" ${changed().length ? '' : 'disabled'}>Save</button></div>`;
    box.querySelectorAll('.sw-row').forEach((r) => {
      const sid = +r.dataset.s, s = d.systems.find((x) => x.id === sid);
      r.querySelectorAll('[data-rm]').forEach((b) => (b.onclick = () => { work.set(sid, work.get(sid).filter((x) => x !== +b.dataset.rm)); draw(); }));
      const sel = r.querySelector('[data-add]');
      if (sel) sel.onchange = () => { if (sel.value) { work.set(sid, [...work.get(sid), +sel.value]); draw(); } };
      const gb = r.querySelector('[data-guess]');
      if (gb) gb.onclick = async () => {
        gb.disabled = true;
        let ppl = s.guess || [];
        if (!ppl.length) { try { ppl = (await api.get(`/api/support/system-people/${sid}/guess`)).people; } catch (e) { toast(e.detail || 'Could not ask AXL', 'err'); gb.disabled = false; return; } }
        if (!ppl.length) { toast(`Nobody has finished a ${s.name} ticket yet — add the people by hand.`); gb.disabled = false; return; }
        guessed.set(sid, Object.fromEntries(ppl.map((p) => [p.id, p.done])));
        work.set(sid, ppl.map((p) => p.id).filter((id) => pool.has(id)));
        draw();
      };
    });
    box.querySelector('#sw-save').onclick = async (e) => {
      const body = { systems: changed().map((s) => ({ id: s.id, people: work.get(s.id) })) };
      e.target.disabled = true;
      try { await api.post('/api/support/system-people', body); toast('Saved — AXL suggests only these people for each system'); reload(); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
    };
  }
  draw();
}

// 1.0.109 (round 2): Follow-ups and the responsible person (cbx/support/followup.py) — after N working days without an answer one
// follow-up mail; M more and it goes to the ticket's responsible person. Defaults per system and for the whole desk.
async function drawFollowups(box) {
  let d, people;
  try { [d, people] = await Promise.all([api.get('/api/support/followup-settings'), api.get('/api/support/owner-people')]); } catch (e) { box.closest('.card').remove(); return; }
  const pick = (o, key) => `<div class="row sp-op" data-op="${key}" style="gap:6px;flex-wrap:wrap"><select class="op-who" aria-label="Responsible person"><option value="">Nobody</option>${people.people.map((p) => `<option value="${p.id}" ${o && o.kind === 'user' && o.id === p.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}<option value="other" ${o && o.kind === 'other' ? 'selected' : ''}>Someone not on CBX…</option></select>
    <input type="text" class="op-name" placeholder="Name" maxlength="160" value="${esc(o && o.kind === 'other' ? o.name : '')}" ${o && o.kind === 'other' ? '' : 'hidden'}><input type="email" class="op-email" placeholder="E-mail" maxlength="255" value="${esc(o && o.kind === 'other' ? o.email : '')}" ${o && o.kind === 'other' ? '' : 'hidden'}></div>`;
  box.innerHTML = `<div class="row" style="gap:10px;align-items:center;flex-wrap:wrap"><label class="small" for="fu-n">Follow up after</label><input type="number" id="fu-n" min="1" max="20" value="${d.follow_days}" style="width:70px"><span class="small">working days without an answer, then escalate after</span>
      <input type="number" id="fu-m" min="1" max="20" value="${d.escalate_days}" style="width:70px"><span class="small">more</span></div>
    <p class="small muted">Weekends and South African public holidays don't count${d.holidays && d.holidays[0] ? ` (next: ${esc(d.holidays[0].name)}, ${esc(d.holidays[0].date)})` : ''}.</p>
    <div class="field" style="margin-top:8px"><label>Responsible person for the whole desk</label>${pick(d.desk_owner, 'desk')}</div>
    <div class="tbl" style="margin-top:8px"><table><thead><tr><th>System</th><th>Responsible person (else the desk's)</th></tr></thead><tbody>${d.systems.map((s) => `<tr><td class="nowrap"><b>${esc(s.name)}</b></td><td>${pick(s.owner, s.id)}</td></tr>`).join('')}</tbody></table></div>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn primary" id="fu-save" type="button">Save</button></div>`;
  box.querySelectorAll('.sp-op').forEach((r) => (r.querySelector('.op-who').onchange = (e) => { const o = e.target.value === 'other'; r.querySelector('.op-name').hidden = !o; r.querySelector('.op-email').hidden = !o; }));
  const read = (r) => { const v = r.querySelector('.op-who').value; return v === 'other' ? { kind: 'other', name: r.querySelector('.op-name').value.trim(), email: r.querySelector('.op-email').value.trim() } : v ? { kind: 'user', id: +v } : { kind: 'none' }; };
  box.querySelector('#fu-save').onclick = async (e) => {
    const rows = [...box.querySelectorAll('.sp-op')];
    const body = { follow_days: +box.querySelector('#fu-n').value, escalate_days: +box.querySelector('#fu-m').value, desk_owner: read(rows.find((r) => r.dataset.op === 'desk')),
      systems: rows.filter((r) => r.dataset.op !== 'desk').map((r) => ({ id: +r.dataset.op, owner: read(r) })) };
    e.target.disabled = true;
    try { await api.post('/api/support/followup-settings', body); toast('Saved'); drawFollowups(box); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
  };
}

// 1.0.109 (round 2): how AXL suggests who gets a ticket, auto-assign per system (off unless ticked), and how often it was right
async function drawPicks(box) {
  let d;
  try { d = await api.get('/api/support/assign-settings'); } catch { box.closest('.card').remove(); return; }
  const st = d.stats || {};
  box.innerHTML = `<div class="sp-how"><div class="eyebrow">How AXL picks</div><p class="small">AXL scores the people who solved tickets like it (the system, the area, the requester's company, the words), how many they have open, and who is on leave — and learns from every pick and every "why not". Up to 3 suggestions, each with its reason.</p>
      ${st.rate !== null && st.rate !== undefined ? `<div class="row" style="gap:16px;align-items:baseline"><div class="v num sp-rate">${st.rate}%</div><div class="small muted">first suggestion picked, last ${st.days} days (${st.top} of ${st.suggested}; one of the three ${st.top3})</div></div>` : ''}
      ${(st.lines || []).map((l) => `<p class="small muted">${esc(l)}</p>`).join('')}</div>
    <div class="eyebrow" style="margin-top:10px">Assign by itself</div><p class="small muted">When AXL's first suggestion is clearly ahead, it assigns the ticket and says why; an admin can undo it with one click.</p>
    <div class="chips">${d.systems.map((s) => `<label class="chip ${s.auto ? 'on' : ''}"><input type="checkbox" data-auto="${s.id}" ${s.auto ? 'checked' : ''} hidden>${esc(s.name)}</label>`).join('')}</div>
    <div class="row" style="gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px"><label class="small" for="pk-m">Clearly ahead by</label><input type="number" id="pk-m" min="0.5" max="50" step="0.5" value="${d.margin}" style="width:80px"><span class="small">points, with at least</span><input type="number" id="pk-h" min="1" max="100" value="${d.min_history}" style="width:70px"><span class="small">finished tickets to go on</span></div>
    <div class="row" style="justify-content:flex-end;margin-top:10px"><button class="btn primary" id="pk-save" type="button">Save</button></div>`;
  box.querySelectorAll('[data-auto]').forEach((c) => (c.onchange = () => c.closest('.chip').classList.toggle('on', c.checked)));
  box.querySelector('#pk-save').onclick = async (e) => {
    e.target.disabled = true;
    try { await api.post('/api/support/assign-settings', { margin: +box.querySelector('#pk-m').value, min_history: +box.querySelector('#pk-h').value, systems: [...box.querySelectorAll('[data-auto]')].map((c) => ({ id: +c.dataset.auto, auto: c.checked })) }); toast('Saved'); drawPicks(box); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
  };
}

// 1.0.98: who is told when a ticket isn't accepted in 12 hours (an admin), and every support person's digest and Invisible
async function drawPeople(box) {
  let d;
  try { d = await api.get('/api/support/people'); } catch (e) { box.innerHTML = `<p class="small muted">${esc(e.detail || 'Could not load.')}</p>`; return; }
  const rows = d.people.map((p) => `<tr data-u="${p.user_id}"><td><b>${esc(p.name)}</b><div class="small muted">${esc(p.email)}</div></td><td class="small">${esc(p.says)}</td>
      <td>${p.invisible ? pill('Invisible', 'neutral') : '<span class="small muted">visible</span>'}</td><td class="small muted">${esc(p.digest_sent || '—')}</td>
      <td style="text-align:right"><button class="btn sm ghost" data-ed>Change</button></td></tr>`).join('');
  box.innerHTML = `<div class="row" style="gap:8px;align-items:center;flex-wrap:wrap"><label class="small" for="sp-esc">If a ticket isn't accepted in 12 hours, tell</label>
      <select id="sp-esc"><option value="0">nobody</option>${d.admins.map((a) => `<option value="${a.id}" ${a.id === d.escalate_to ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select>
      <span class="small muted">they reassign it</span></div>
    <div class="tbl" style="margin-top:12px"><table><thead><tr><th>Person</th><th>Digest</th><th>To requesters</th><th>Last digest</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="5" class="empty">No support people yet.</td></tr>'}</tbody></table></div>`;
  box.querySelector('#sp-esc').onchange = async (e) => { try { await api.post('/api/support/escalation', { user_id: +e.target.value || null }); toast('Saved'); } catch (er) { toast(er.detail || 'Could not save', 'err'); } };
  box.querySelectorAll('[data-ed]').forEach((b) => (b.onclick = () => {
    const uid = +b.closest('tr').dataset.u, p = d.people.find((x) => x.user_id === uid);
    const body = el(`<div>${settingsForm(p)}</div>`);
    bindForm(body);
    const dlg = modal({ title: `${p.name} — support settings`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="pp-ok">Save</button>' });
    dlg.root.querySelector('#pp-ok').onclick = async () => {
      try { await api.post('/api/support/people', { user_id: uid, ...readForm(body) }); dlg.close(); toast('Saved'); drawPeople(box); } catch (er) { toast(er.detail || 'Could not save', 'err'); }
    };
  }));
}

function remember(id, on) {
  try {
    const set = new Set(JSON.parse(sessionStorage.getItem('cbx_sp_open') || '[]'));
    on ? set.add(id) : set.delete(id);
    sessionStorage.setItem('cbx_sp_open', JSON.stringify([...set]));
  } catch { /* only remembers which are open */ }
}

// The support-lead list: add a person, tick the ticket types they cover, remove them.
function drawLeads(box, d, reload) {
  const sys = d.systems;
  const rows = d.leads.map((u) => `<tr data-u="${u.id}"><td><b>${esc(u.name)}</b><div class="small muted">${esc(u.team || u.email)}</div></td>
    <td class="small">${esc(sys.filter((s) => u.systems.includes(s.id)).map((s) => s.name).join(', ')) || '<span class="muted">—</span>'}</td>
    <td class="num muted">${u.open}</td><td style="text-align:right"><button class="btn sm ghost" data-rm="${u.id}">Remove</button></td></tr>`).join('');
  box.innerHTML = `<div class="tbl"><table><thead><tr><th>Lead</th><th>Works</th><th>Open</th><th></th></tr></thead>
      <tbody>${rows || '<tr><td colspan="4" class="empty">No support leads yet — add the first below.</td></tr>'}</tbody></table></div>
    <div class="row" style="gap:8px;margin-top:12px;align-items:center;flex-wrap:wrap"><select id="sp-person" style="flex:1 1 260px;width:auto;max-width:min(420px,100%)"><option value="">Add a person as a support lead…</option>${d.people.map((p) => `<option value="${p.id}">${esc(p.name)}${p.team ? ' · ' + esc(p.team) : ''}</option>`).join('')}</select>
      <button class="btn sm primary" id="sp-person-add">Add</button><span class="small muted">Which systems they work is set in Who works each system above.</span></div>`;
  box.querySelector('#sp-person-add').onclick = async () => {
    const id = +box.querySelector('#sp-person').value;
    if (!id) { toast('Pick the person first.', 'err'); box.querySelector('#sp-person').focus(); return; }  // 1.0.106: Add did nothing at all
    try { await api.post('/api/support/leads', { user_id: id, system_ids: [] }); toast('Added — set what they work in Who works each system'); reload(); } catch (e) { toast(e.detail || 'Could not add', 'err'); }
  };
  box.querySelectorAll('[data-rm]').forEach((b) => (b.onclick = async () => {
    const u = d.leads.find((x) => x.id === +b.dataset.rm);
    if (!(await confirmBox(`Take ${u.name} off the support-lead list?${u.open ? ` They still have ${u.open} open ticket(s) — those stay with them until reassigned.` : ''}`, 'Remove'))) return;
    try { await api.del(`/api/support/leads/${u.id}`); toast('Removed'); reload(); } catch (e) { toast(e.detail || 'Could not remove', 'err'); }
  }));
}

// One ticket type (CBX-Connect, Chase, Vendor portal …): folds open to edit its problem areas and questions.
function systemCard(s, leads, reload, startOpen) {
  const st = JSON.parse(JSON.stringify(s));
  const card = el('<details class="card sp-sys"></details>');
  if (startOpen) card.open = true;
  card.addEventListener('toggle', () => remember(s.id, card.open));
  const covers = (s.leads || []).map((u) => u.name);  // 1.0.111: who works it (support leads and the Support desk)
  function draw() {
    const qn = st.problems.reduce((n, p) => n + (p.fields || []).length, 0);
    card.innerHTML = `<summary class="sp-sum"><span class="sp-chev" aria-hidden="true">▸</span><b>${esc(st.name)}</b>${st.active ? '' : ' ' + pill('Hidden', 'neutral')}
        <span class="small muted">${st.problems.length} problem area${st.problems.length === 1 ? '' : 's'} · ${qn} question${qn === 1 ? '' : 's'} · ${covers.length ? 'worked by ' + esc(covers.join(', ')) : 'nobody set to work it yet'}</span></summary>
      <div class="bd"><div class="grid g2" style="gap:12px">
        <div class="field"><label>Name</label><input type="text" class="sp-name" value="${esc(st.name)}" maxlength="80"></div>
        <div class="field"><label>One line under its name</label><input type="text" class="sp-blurb" value="${esc(st.blurb || '')}" maxlength="200"></div></div>
        <div class="row" style="gap:16px;align-items:center"><label class="row small" style="gap:6px;align-items:center"><input type="checkbox" class="sp-active" ${st.active ? 'checked' : ''}> shown on My tickets</label>
          <span class="small muted">order <input type="number" class="sp-sort" value="${st.sort}" style="width:64px"></span></div>
        <div class="eyebrow" style="margin-top:12px">Problem areas and their questions</div>
        ${st.problems.map((p, i) => `<div class="sp-prob" data-i="${i}"><div class="row" style="gap:6px"><input type="text" class="sp-pname" value="${esc(p.name)}" maxlength="80" placeholder="e.g. Signing in"><button class="btn sm ghost" data-delp="${i}" title="Remove this problem area">Remove</button></div>
          <table class="sp-fields"><tbody>${(p.fields || []).map((f, j) => `<tr data-j="${j}"><td><input type="text" class="sp-flabel" value="${esc(f.label)}" maxlength="120" placeholder="The question"></td>
            <td><select class="sp-fkind">${[['text', 'Short answer'], ['multiline', 'Long answer'], ['choice', 'Pick one']].map(([v, l]) => `<option value="${v}" ${f.kind === v ? 'selected' : ''}>${l}</option>`).join('')}</select></td>
            <td><input type="text" class="sp-fchoices" value="${esc((f.choices || []).join(', '))}" placeholder="choices, comma separated" ${f.kind === 'choice' ? '' : 'style="display:none"'}></td>
            <td><label class="small row" style="gap:4px;align-items:center"><input type="checkbox" class="sp-freq" ${f.required ? 'checked' : ''}> required</label></td><td><button class="btn sm ghost" data-delf="${i}:${j}">×</button></td></tr>`).join('')}</tbody></table>
          <button class="btn sm ghost" data-addf="${i}">+ a question</button></div>`).join('')}
        <button class="btn sm" data-addp="1" style="margin-top:6px">+ a problem area</button>
        <p class="small muted" style="margin-top:10px">Who works ${esc(st.name)} is set in Who works each system above.</p>
        <div class="row" style="justify-content:flex-end;margin-top:8px;gap:8px"><button class="btn primary sp-save">Save ${esc(st.name)}</button></div></div>`;
    card.querySelectorAll('[data-addp]').forEach((b) => (b.onclick = () => { keep(); st.problems.push({ name: '', fields: [] }); draw(); }));
    card.querySelectorAll('[data-delp]').forEach((b) => (b.onclick = () => { keep(); st.problems.splice(+b.dataset.delp, 1); draw(); }));
    card.querySelectorAll('[data-addf]').forEach((b) => (b.onclick = () => { keep(); st.problems[+b.dataset.addf].fields.push({ label: '', kind: 'text', required: false, choices: [] }); draw(); }));
    card.querySelectorAll('[data-delf]').forEach((b) => (b.onclick = () => { keep(); const [i, j] = b.dataset.delf.split(':').map(Number); st.problems[i].fields.splice(j, 1); draw(); }));
    card.querySelectorAll('.sp-fkind').forEach((s2) => (s2.onchange = () => { keep(); draw(); }));
    card.querySelector('.sp-save').onclick = async (e) => {
      keep();
      e.target.disabled = true;
      try { await api.put(`/api/support/systems/${st.id}`, { name: st.name, blurb: st.blurb, active: st.active, sort: st.sort, problems: st.problems }); toast(`Saved ${st.name}`); reload(); }
      catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
    };
  }
  function keep() {
    st.name = card.querySelector('.sp-name').value; st.blurb = card.querySelector('.sp-blurb').value; st.active = card.querySelector('.sp-active').checked; st.sort = +card.querySelector('.sp-sort').value || 100;
    card.querySelectorAll('.sp-prob').forEach((pd) => {
      const p = st.problems[+pd.dataset.i]; if (!p) return;
      p.name = pd.querySelector('.sp-pname').value;
      pd.querySelectorAll('tr[data-j]').forEach((tr) => {
        const f = p.fields[+tr.dataset.j]; if (!f) return;
        f.label = tr.querySelector('.sp-flabel').value; f.kind = tr.querySelector('.sp-fkind').value; f.required = tr.querySelector('.sp-freq').checked;
        f.choices = tr.querySelector('.sp-fchoices').value.split(',').map((x) => x.trim()).filter(Boolean);
      });
    });
  }
  draw();
  return card;
}
