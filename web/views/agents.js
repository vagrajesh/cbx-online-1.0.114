// Agents & teams (owner; read-only for a role with "See C-EXA's teams" — the board's view). Teams: C-EXA as
// CEO and her standing teams, each with a lead and members, what each is on, when it runs, what it found.
// Jobs: every job with its tasks, events and what came out. Standing agents: the always-on workers.
// (1.0.49) C-EXA runs a team. Give her a job; she asks what she needs, writes a plan and waits for
// your Go; then her agents — developers, a designer, a tester, a helper, a core developer when the job
// needs core code — take the tasks and build it as a Builder draft. Everything they do is on this page:
// who is on what, every task and its small steps, what they wrote and tested, the screenshots the designer
// took, and behind every model call the prompt and the reply. Below, the standing agents (harvester,
// developer, watcher) as before.
import { api } from '../api.js';
import { esc, toast, pill, modal, el, fmtDate, confirmBox, md, aiName } from '../ui.js';
import { makeNow } from './agents-now.js';
import { drawChart } from './agents-chart.js';
import { drawOffice } from './agents-office.js';

const ST = {
  reading: ['Reading it', 'ai', true],
  asking: ['Questions for you', 'warn'], planning: ['Planning', 'ai', true], plan: ['Plan — waiting for Go', 'warn'], working: ['Working', 'info', true],
  review: ['Ready for review', 'good'], done: ['Done', 'neutral'], stopped: ['Stopped', 'neutral'], failed: ['Needs a retry', 'crit'],
};
const STEPS = [['asking', 'Questions'], ['plan', 'Plan'], ['working', 'Working'], ['review', 'Review'], ['done', 'Done']];
const STEP_OF = { reading: 0, asking: 0, planning: 1, plan: 1, working: 2, review: 3, done: 4, stopped: 4, failed: 1 };
const STEPS_BY_KIND = {
  advice: [[['reading', 'asking'], 'Questions'], [['working'], 'Writing'], [['review'], 'Read it'], [['done', 'stopped'], 'Done']],
  report: [[['reading'], 'Starting'], [['working'], 'Working'], [['done', 'stopped', 'review'], 'Done']],
  review: [[['reading'], 'Starting'], [['working'], 'Reviewing'], [['done', 'stopped', 'review'], 'Verdict']],
};
const SEV = { high: 'crit', medium: 'warn', low: 'neutral', info: 'neutral' };
const EVERY = [['', 'Only when given work'], ['hour', 'Every hour'], ['day', 'Every day'], ['weekday', 'Weekdays'], ['week', 'Every week'], ['month', 'Every month']];
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const DAY_NAME = { mon: 'Mondays', tue: 'Tuesdays', wed: 'Wednesdays', thu: 'Thursdays', fri: 'Fridays', sat: 'Saturdays', sun: 'Sundays' };
const TASK_COLS = [['todo', 'To do'], ['working', 'Working'], ['done', 'Done'], ['problem', 'Needs you']];
const KIND_ICON = { said: '›', owner: '•', plan: '▤', write: '✎', test: '✓', look: '◉', fix: '↻', problem: '!', done: '✓', model: '…' };
const ini = (n) => String(n || '?').split(/[\s-]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('') || '?';
const ago = (iso) => { if (!iso) return ''; const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : fmtDate(iso); };
// A report or a document: headings (#, ##, ###), bullets, numbered lines, **bold**, *italic*, `code`, paragraphs.
function docMd(text) {
  const inl = (x) => esc(x).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*([^*]+)\*/g, '$1<i>$2</i>').replace(/`(.+?)`/g, '<code>$1</code>');
  const out = [];
  let list = null;
  const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trimEnd();
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { close(); out.push(`<h${m[1].length + 1}>${inl(m[2])}</h${m[1].length + 1}>`); }
    else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { if (list !== 'ul') { close(); out.push('<ul>'); list = 'ul'; } out.push(`<li>${inl(m[1])}</li>`); }
    else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { if (list !== 'ol') { close(); out.push('<ol>'); list = 'ol'; } out.push(`<li>${inl(m[1])}</li>`); }
    else if (!line.trim()) close();
    else { close(); out.push(`<p>${inl(line)}</p>`); }
  }
  close();
  return `<div class="md">${out.join('')}</div>`;
}
const LIVE = new Set(['reading', 'planning', 'working']);

export default {
  title: 'Agents',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner' && !ctx.me.can_see_teams) { root.innerHTML = '<div class="head"><div><h1>Agents &amp; teams</h1><p>This page is for the owner.</p></div></div>'; return; }
    const ai = aiName();
    const canAct = ctx.me.role === 'owner';
    let list = { missions: [], screenshots: {}, roles: {} }, job = null, sel = +(ctx.params.job || 0) || null, taskFilter = null, showModel = false, timer = null, standing = null;
    let findingsOpen = null;
    const AG_TABS = ['now', 'teams', 'jobs', 'standing'];
    let tab = sel ? 'jobs' : (AG_TABS.includes(ctx.tab) ? ctx.tab : AG_TABS.includes(ctx.params.tab) ? ctx.params.tab : 'now'), org = null, teamFilter = ctx.params.team || '', newTeam = '';
    const dispose = () => { if (timer) clearTimeout(timer); timer = null; };

    root.innerHTML = `
      <div class="head"><div><h1 id="ag-h1"></h1><p id="ag-lead"></p></div>${canAct ? '' : pill('Read-only', 'neutral')}</div>
      <div id="ag-tab-now"></div>
      <div id="ag-tab-teams"></div>
      <div id="ag-tab-jobs"><div class="ag-layout"><aside class="card ag-jobs"><div class="hd"><h2>Jobs</h2><span class="small muted" id="ag-shots"></span></div><div class="ag-jobfilter"><select id="ag-tf"></select></div><div id="ag-list"></div></aside><section id="ag-detail"></section></div></div>
      <div id="ag-tab-standing"><div class="card"><div class="hd"><h2>Standing agents</h2><span class="small muted">the harvester, the developer and the watcher, always on</span></div><div id="ag-standing-body" class="bd"><div class="loading"><span class="spin"></span></div></div></div></div>`;
    const $ = (s) => root.querySelector(s);
    const nowTab = makeNow($('#ag-tab-now'), { canAct, ai, org: () => org });
    const HEADS = {
      now: ['Now', `What ${ai} and her teams are doing this minute, and what waits for ${canAct ? 'you' : 'the owner'}.`],
      teams: ['Teams', `${ai} is the CEO. Her teams each own one kind of work, each with a lead; she hands every job to the right one.`],
      jobs: ['Jobs', `Every job a team took on: the questions, the plan, the work and what came out. ${canAct ? 'You approve' : 'The owner approves'}, release and promote.`],
      standing: ['Standing agents', 'The workers that are always on: the harvester, the developer and the watcher.'],
    };
    const showTab = () => {
      for (const t of AG_TABS) { $(`#ag-tab-${t}`).hidden = t !== tab; }
      $('#ag-h1').textContent = HEADS[tab][0]; $('#ag-lead').textContent = HEADS[tab][1];
      // the C-EXA tab row above follows when a button here opens another part (a team's job → Jobs)
      document.querySelectorAll('.hub-bar [data-tab]').forEach((b) => { const on = b.dataset.tab === tab; b.classList.toggle('on', on); b.setAttribute('aria-selected', String(on)); });
      if (tab === 'standing') loadStanding();
      if (tab === 'teams') drawTeams();
      if (tab === 'now') nowTab.refresh();
    };

    let nowData = null;
    async function loadNow() { try { nowData = await api.get('/api/cexa/now'); } catch { nowData = null; } }
    async function loadOrg() { try { org = await api.get('/api/cexa/teams'); } catch (e) { toast(e.detail || 'Could not load the teams', 'err'); } }
    async function loadList() { try { list = await api.get('/api/cexa/missions' + (teamFilter ? `?team=${encodeURIComponent(teamFilter)}` : '')); } catch (e) { toast(e.detail || 'Could not load the jobs', 'err'); } }

    // ---- the teams -------------------------------------------------------------------------------------------
    function drawTeams() {
      const box = $('#ag-tab-teams');
      if (!org) { box.innerHTML = '<div class="loading"><span class="spin"></span></div>'; return; }
      const face = (n, role, busy) => `<span class="ag-face r-${esc(role)} ${busy ? 'busy' : ''}" title="${esc(n)} · ${esc((org.roles[role] || {}).label || role)}">${esc(ini(n))}</span>`;
      const card = (t) => {
        const on = t.enabled;
        const status = !on ? pill('Off', 'neutral') : t.working ? pill('Working', 'info', true) : t.needs_you ? pill(`${t.needs_you} for you`, 'warn') : pill('Ready', 'good');
        const last = t.working ? `On: <a href="#" data-openjob="${t.working.job}">${esc(t.working.title)}</a>` : t.last ? `Last: <a href="#" data-openjob="${t.last.job}">${esc(t.last.summary || t.last.title)}</a> <span class="muted">· ${esc(ago(t.last.at))}</span>` : '<span class="muted">Nothing yet.</span>';
        const act = canAct ? (['report', 'review'].includes(t.kind) ? `<button class="btn sm" data-run="${esc(t.key)}" ${on ? '' : 'disabled'}>Run now</button>` : t.kind === 'work' ? `<button class="btn sm" data-work="${esc(t.key)}" ${on && t.lead ? '' : 'disabled'}>Give work</button>` : `<button class="btn sm" data-give="${esc(t.key)}" ${on ? '' : 'disabled'}>Give work</button>`) : '';
        return `<div class="card ag-tcard ${on ? '' : 'off'}"><div class="bd">
          <div class="row between" style="gap:8px;align-items:flex-start"><div>${t.word && t.word.toLowerCase() !== t.name.toLowerCase() ? `<div class="eyebrow">${esc(t.word)}</div>` : '<div class="eyebrow">&nbsp;</div>'}<h3 class="ag-tname">${esc(t.name)}</h3></div>${status}</div>
          <p class="small ag-tpurpose">${esc(t.purpose)}</p>
          <div class="row" style="gap:8px;align-items:center">${t.lead ? `${face(t.lead.name, 'teamlead', !!t.working)}<span class="small"><b>${esc(t.lead.name)}</b> <span class="muted">leads</span></span>` : ''}</div>
          <div class="ag-faces">${t.members.filter((m) => !m.lead && m.enabled).map((m) => face(m.name, m.role, false)).join('')}</div>
          <div class="small ag-tlast">${last}</div>
          <div class="row between small muted" style="margin-top:8px;align-items:center"><span>${esc(t.when)} · owner ${esc(t.owner ? t.owner.name : '—')}</span><span class="row" style="gap:6px">${act}<button class="btn sm ghost" data-team="${esc(t.key)}">Team</button></span></div></div></div>`;
      };
      let view = 'office';
      try { view = localStorage.getItem('cbx_ag_view') || 'office'; } catch { /* per-browser only */ }
      const toggle = `<div class="row between" style="align-items:center;margin-bottom:12px;flex-wrap:wrap;row-gap:8px"><div class="segctl" role="tablist"><button data-view="office" aria-selected="${view === 'office'}">Office</button><button data-view="chart" aria-selected="${view === 'chart'}">Chart</button><button data-view="cards" aria-selected="${view === 'cards'}">Cards</button></div>
        <span class="row" style="gap:10px;align-items:center;flex-wrap:wrap"><span class="small muted">${org.teams.length} teams · ${org.teams.reduce((n, t) => n + t.members.filter((m) => m.enabled).length, 0)} agents</span>${canAct ? '<button class="btn sm primary" id="ag-newteam">New agent team</button>' : ''}</span></div>`;
      const contextCard = `<div class="card" style="margin-top:16px"><div class="hd"><h2>What every team knows</h2><span class="small muted">the rules every agent's prompt carries</span></div><div class="bd">
          ${canAct ? `<textarea id="ag-context" rows="7">${esc(org.context)}</textarea><div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn" id="ag-context-save">Save</button></div>` : `<div class="ag-brief">${esc(org.context)}</div>`}</div></div>`;
      const wireNew = () => { const nb = box.querySelector('#ag-newteam'); if (nb) nb.onclick = newTeamDialog; };
      if (view === 'office') {
        box.innerHTML = toggle + '<div id="ag-office"></div>' + contextCard;
        wireNew();
        drawOffice(box.querySelector('#ag-office'), { org, now: nowData, me: ctx.me, canAct, ai, onTeam: (k) => teamDialog(k), onChanged: async () => { await loadNow(); drawTeams(); } });
        box.querySelectorAll('[data-view]').forEach((b) => (b.onclick = () => { try { localStorage.setItem('cbx_ag_view', b.dataset.view); } catch { /* fine */ } drawTeams(); }));
        const cs1 = $('#ag-context-save'); if (cs1) cs1.onclick = async () => { try { await api.put('/api/cexa/teams-context', { text: $('#ag-context').value }); toast('Saved — every team has it from the next step'); } catch (e) { toast(e.detail || 'Could not save', 'err'); } };
        return;
      }
      if (view === 'chart') {
        box.innerHTML = toggle + '<div id="ag-chart"></div>' + contextCard;
        wireNew();
        drawChart(box.querySelector('#ag-chart'), { org, now: nowData, me: ctx.me, canAct, ai, onTeam: (k) => teamDialog(k), onChanged: async () => { await loadNow(); drawTeams(); } });
        box.querySelectorAll('[data-view]').forEach((b) => (b.onclick = () => { try { localStorage.setItem('cbx_ag_view', b.dataset.view); } catch { /* fine */ } drawTeams(); }));
        const cs0 = $('#ag-context-save'); if (cs0) cs0.onclick = async () => { try { await api.put('/api/cexa/teams-context', { text: $('#ag-context').value }); toast('Saved — every team has it from the next step'); } catch (e) { toast(e.detail || 'Could not save', 'err'); } };
        return;
      }
      box.innerHTML = toggle + `
        <div class="card ag-ceo"><div class="bd row between" style="gap:16px;align-items:center">
          <div class="row" style="gap:12px;align-items:center"><span class="ag-face big r-lead">${esc(ini(org.ceo.name))}</span><div><b>${esc(org.ceo.name)}</b> <span class="muted">· CEO</span><div class="small muted">Hands every job to the right team, checks the work, reports to ${canAct ? 'you' : 'the owner'}. <span class="mono">${esc(org.ceo.model)}</span></div></div></div>
          <div class="small muted">${org.teams.length} teams · ${org.teams.reduce((n, t) => n + t.members.filter((m) => m.enabled).length, 0)} agents${org.auto ? '' : ' · teams run only when started here (their own schedules are switched off on this server)'}</div></div></div>
        <div class="ag-tgrid">${org.teams.map(card).join('')}</div>` + contextCard;
      box.querySelectorAll('[data-view]').forEach((b) => (b.onclick = () => { try { localStorage.setItem('cbx_ag_view', b.dataset.view); } catch { /* fine */ } drawTeams(); }));
      box.querySelectorAll('[data-openjob]').forEach((a) => (a.onclick = async (e) => { e.preventDefault(); sel = +a.dataset.openjob; tab = 'jobs'; history.replaceState(null, '', `#/cexa?tab=jobs&job=${sel}`); await loadJob(); showTab(); drawList(); drawDetail(); }));
      box.querySelectorAll('[data-team]').forEach((b) => (b.onclick = () => teamDialog(b.dataset.team)));
      box.querySelectorAll('[data-run]').forEach((b) => (b.onclick = async () => {
        b.disabled = true;
        try { job = await api.post(`/api/cexa/teams/${b.dataset.run}/run`, {}); sel = job.id; tab = 'jobs'; history.replaceState(null, '', `#/cexa?tab=jobs&job=${sel}`); await Promise.all([loadList(), loadOrg()]); showTab(); drawList(); drawDetail(); toast('The team is on it'); }
        catch (e) { toast(e.detail || 'Could not start it', 'err'); b.disabled = false; }
      }));
      box.querySelectorAll('[data-give]').forEach((b) => (b.onclick = () => { newTeam = b.dataset.give; sel = null; job = null; tab = 'jobs'; showTab(); drawList(); drawDetail(); }));
      box.querySelectorAll('[data-work]').forEach((b) => (b.onclick = () => giveWork(org.teams.find((x) => x.key === b.dataset.work))));
      wireNew();
      const cs = $('#ag-context-save'); if (cs) cs.onclick = async () => { try { await api.put('/api/cexa/teams-context', { text: $('#ag-context').value }); toast('Saved — every team has it from the next step'); } catch (e) { toast(e.detail || 'Could not save', 'err'); } };
    }

    // 1.0.82: a team of your own — a working team does the work in the app with its hands; or one that builds, changes or advises
    function newTeamDialog() {
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Name</label><input id="nt-name" type="text" maxlength="60" placeholder="e.g. Utility bills"></div>
        <div class="field"><label>What it is for</label><textarea id="nt-purpose" rows="3" maxlength="400" placeholder="e.g. Every morning: read the new bills in the harvest, file them, chase what is missing and send the list to the team."></textarea></div>
        <div class="field"><label>Kind</label><select id="nt-kind"><option value="work">Does work in the app (people, files, rows, tickets…)</option><option value="build">Builds new tools</option><option value="change">Changes and fixes tools</option><option value="advice">Advises — lists, documents</option></select></div>
        <div class="field"><label>On its own</label><select id="nt-every"><option value="">Only when given work</option><option value="hour">Every hour</option><option value="day">Every day</option><option value="weekday">Every weekday</option><option value="week">Every week</option></select></div>
        <div class="field" id="nt-at-f" hidden><label>At</label><input id="nt-at" type="time" value="07:00" style="max-width:140px"></div>
        <p class="small muted" style="margin:0">It starts with a lead, an operator and a helper; change them under Team. What it may do alone and what waits for your tap is the same as for ${esc(ai)}.</p></div>`);
      const m = modal({ title: 'New agent team', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="nt-ok">Make the team</button>' });
      const ntEvery = body.querySelector('#nt-every');
      ntEvery.onchange = () => { body.querySelector('#nt-at-f').hidden = !['day', 'weekday', 'week'].includes(ntEvery.value); };
      m.root.querySelector('#nt-ok').onclick = async () => {
        const every = body.querySelector('#nt-every').value;
        const v = { name: body.querySelector('#nt-name').value, purpose: body.querySelector('#nt-purpose').value, kind: body.querySelector('#nt-kind').value };
        if (every) v.schedule = { every, at: body.querySelector('#nt-at').value || '07:00', day: every === 'week' ? 'mon' : undefined };
        try { await api.post('/api/cexa/teams', v); toast(`${v.name} is ready`); m.close(); await loadOrg(); drawTeams(); } catch (e) { toast(e.detail || 'Could not make it', 'err'); }
      };
    }
    function giveWork(t) {
      if (!t || !t.lead) return;
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px"><div class="field"><label>What to do</label><input id="gw-title" type="text" maxlength="300"></div>
        <div class="field"><label>Anything they should know</label><textarea id="gw-detail" rows="4" maxlength="4000"></textarea></div></div>`);
      const m = modal({ title: `Work for ${t.name}`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="gw-ok">Give work</button>' });
      m.root.querySelector('#gw-ok').onclick = async () => {
        try { await api.post('/api/cexa/work', { to: t.lead.name, title: body.querySelector('#gw-title').value, detail: body.querySelector('#gw-detail').value }); toast(`${t.lead.name} has it — follow it under Now`); m.close(); await loadNow(); } catch (e) { toast(e.detail || 'Could not give it', 'err'); }
      };
    }

    async function teamDialog(key) {
      let t;
      try { t = await api.get(`/api/cexa/teams/${key}`); } catch (e) { toast(e.detail || 'Could not open the team', 'err'); return; }
      const roles = Object.entries(org.roles);
      const draw = () => {
        const s = t.schedule || {};
        const row = (m) => `<tr data-mid="${m.id}" class="${m.enabled ? '' : 'muted'}"><td><span class="ag-face sm r-${esc(m.lead ? 'teamlead' : m.role)}">${esc(ini(m.name))}</span> <b>${esc(m.name)}</b>${m.lead ? ' ' + pill('Lead', 'ai') : ''}</td><td>${esc(m.role_label)}</td><td class="small">${esc(m.focus)}</td><td class="mono small">${esc(m.model)}</td>
          ${canAct ? `<td class="act"><button class="btn sm ghost" data-edit="${m.id}">Edit</button></td>` : ''}</tr>`;
        body.innerHTML = `<p class="small">${esc(t.purpose)}</p>
          <div class="tbl"><table><thead><tr><th>Agent</th><th>Role</th><th>Focus</th><th>Model</th>${canAct ? '<th></th>' : ''}</tr></thead><tbody>${t.members.map(row).join('')}</tbody></table></div>
          ${canAct ? `<div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn sm" id="m-add">Add an agent</button></div>
          <div class="grid g2" style="gap:12px;margin-top:12px">
            <div class="field"><label>Runs</label><div class="row" style="gap:6px"><select id="t-every" ${['build', 'change', 'advice'].includes(t.kind) ? 'disabled' : ''}>${EVERY.map(([v, l]) => `<option value="${v}" ${(s.every || '') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
              <select id="t-day" style="width:auto" title="Which day">${DAYS.map((d) => `<option value="${d}" ${String(s.day || 'mon') === d ? 'selected' : ''}>${DAY_NAME[d]}</option>`).join('')}</select><input id="t-at" type="time" value="${esc(s.at || '07:00')}" style="width:auto" title="At"></div></div>
            <div class="field"><label>Owner (accountable person)</label><select id="t-owner"><option value="">—</option>${(org.people || []).map((p) => `<option value="${p.id}" ${t.owner && t.owner.id === p.id ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select></div>
          </div>
          <label class="row small" style="gap:8px;align-items:center;margin-top:8px"><input type="checkbox" id="t-on" ${t.enabled ? 'checked' : ''}> The team is on</label>` : `<p class="small muted">Runs: ${esc(t.when)} · owner ${esc(t.owner ? t.owner.name : '—')}</p>`}
          <div class="eyebrow" style="margin-top:14px">Recent jobs</div><ul class="list small">${(t.jobs || []).map((j) => `<li><a href="#/agents?job=${j.id}">${esc(j.title)}</a> <span class="muted">· ${esc((ST[j.status] || [j.status])[0])}${j.summary ? ' · ' + esc(j.summary) : ''}</span></li>`).join('') || '<li class="empty">None yet.</li>'}</ul>`;
        if (!canAct) return;
        const tEvery = body.querySelector('#t-every');
        const runsFit = () => { const e = tEvery.value; body.querySelector('#t-day').hidden = e !== 'week'; body.querySelector('#t-at').hidden = !['day', 'weekday', 'week', 'month'].includes(e); };
        tEvery.onchange = runsFit; runsFit();
        body.querySelector('#m-add').onclick = () => memberDialog(null);
        body.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => memberDialog(t.members.find((m) => m.id === +b.dataset.edit))));
      };
      const memberDialog = (m) => {
        const b2 = el(`<div style="display:flex;flex-direction:column;gap:10px">
          <div class="field"><label>Name</label><input id="mm-name" type="text" value="${esc(m ? m.name : '')}" maxlength="30"></div>
          <div class="field"><label>Role</label><select id="mm-role">${roles.map(([k, r]) => `<option value="${k}" ${m && m.role === k ? 'selected' : ''}>${esc(r.label)} — ${esc(r.what)}</option>`).join('')}</select></div>
          <div class="field"><label>Focus</label><input id="mm-focus" type="text" value="${esc(m ? m.focus : '')}" maxlength="200"></div>
          <div class="field"><label>Model <span class="muted">(empty: the role's model)</span></label><input id="mm-model" type="text" class="mono" value="${esc(m ? m.model_set : '')}" placeholder="e.g. qwen3.8:27b"></div>
          <label class="row small" style="gap:8px"><input type="checkbox" id="mm-lead" ${m && m.lead ? 'checked' : ''}> Leads the team</label>
          ${m ? `<label class="row small" style="gap:8px"><input type="checkbox" id="mm-on" ${m.enabled ? 'checked' : ''}> On the team</label>` : ''}</div>`);
        const md2 = modal({ title: m ? `Edit ${m.name}` : 'Add an agent', body: b2, footer: `${m && !m.lead ? '<button class="btn ghost danger" id="mm-del">Remove</button>' : ''}<button class="btn" data-close>Cancel</button><button class="btn primary" id="mm-ok">Save</button>` });
        md2.root.querySelector('#mm-ok').onclick = async () => {
          const v = { name: b2.querySelector('#mm-name').value, role: b2.querySelector('#mm-role').value, focus: b2.querySelector('#mm-focus').value, model: b2.querySelector('#mm-model').value, lead: b2.querySelector('#mm-lead').checked };
          if (m) v.enabled = b2.querySelector('#mm-on').checked;
          try { t = await (m ? api.patch(`/api/cexa/teams/${key}/members/${m.id}`, v) : api.post(`/api/cexa/teams/${key}/members`, v)); md2.close(); draw(); } catch (e) { toast(e.detail || 'Could not save', 'err'); }
        };
        const del = md2.root.querySelector('#mm-del');
        if (del) del.onclick = async () => { try { t = await api.del(`/api/cexa/teams/${key}/members/${m.id}`); md2.close(); draw(); } catch (e) { toast(e.detail || 'Could not remove', 'err'); } };
      };
      const body = el('<div></div>');
      const dlg = modal({ title: t.name, body, footer: canAct ? `${t.custom ? '<button class="btn ghost danger" id="t-del" style="margin-right:auto">Remove the team</button>' : ''}<button class="btn" data-close>Close</button><button class="btn primary" id="t-save">Save the team</button>` : '<button class="btn" data-close>Close</button>', onClose: async () => { await loadOrg(); if (tab === 'teams') drawTeams(); } });
      draw();
      const tdel = dlg.root.querySelector('#t-del');
      if (tdel) tdel.onclick = async () => { if (!(await confirmBox(`Remove ${t.name} and its agents? What they did stays in the history.`, 'Remove'))) return; try { await api.del(`/api/cexa/teams/${key}`); toast('Removed'); dlg.close(); } catch (e) { toast(e.detail || 'Could not remove it', 'err'); } };
      const sv = dlg.root.querySelector('#t-save');
      if (sv) sv.onclick = async () => {
        const every = body.querySelector('#t-every').value;
        const sch = every ? { every, at: ['day', 'weekday', 'week', 'month'].includes(every) ? body.querySelector('#t-at').value || undefined : undefined, day: every === 'week' ? body.querySelector('#t-day').value : every === 'month' ? ((t.schedule || {}).day || 1) : undefined } : {};
        const v = { enabled: body.querySelector('#t-on').checked, owner_id: +body.querySelector('#t-owner').value || 0 };
        if (!['build', 'change', 'advice'].includes(t.kind)) v.schedule = sch;
        try { t = await api.patch(`/api/cexa/teams/${key}`, v); toast('Saved'); dlg.close(); } catch (e) { toast(e.detail || 'Could not save', 'err'); }
      };
    }
    async function loadJob() { if (!sel) { job = null; return; } try { job = await api.get(`/api/cexa/missions/${sel}`); } catch (e) { job = null; sel = null; } }

    function drawList() {
      const s = list.screenshots || {};
      $('#ag-shots').innerHTML = s.on ? 'Screenshots on' : `<a href="#" id="shots-on" title="${esc(s.why || '')}">Screenshots off</a>`;
      const on = $('#shots-on'); if (on) on.onclick = (e) => { e.preventDefault(); screenshotsDialog(); };
      const tf = $('#ag-tf');
      if (org && tf && !tf.options.length) { tf.innerHTML = `<option value="">Every team</option>${org.teams.map((t) => `<option value="${esc(t.key)}">${esc(t.name)}</option>`).join('')}`; tf.value = teamFilter; tf.onchange = async () => { teamFilter = tf.value; await loadList(); drawList(); }; }
      $('#ag-list').innerHTML = (list.missions || []).map((m) => {
        const [t, k, r] = ST[m.status] || [m.status, 'neutral'];
        const p = m.counts.tasks ? Math.round((m.counts.done / m.counts.tasks) * 100) : 0;
        return `<button class="ag-job ${m.id === sel ? 'on' : ''}" data-job="${m.id}"><div class="row between" style="gap:8px"><b>${esc(m.title)}</b>${pill(m.paused ? 'Paused' : t, m.paused ? 'warn' : k, !m.paused && r)}</div>
          <div class="small muted">${m.team_name ? esc(m.team_name) + ' · ' : ''}${esc(ago(m.created_at))}${m.counts.tasks ? ` · ${m.counts.done}/${m.counts.tasks} tasks` : ''}${m.counts.problems ? ` · ${m.counts.problems} need you` : ''}</div>
          ${m.counts.tasks ? `<div class="progress" style="margin-top:6px"><i style="width:${p}%"></i></div>` : ''}
          <div class="ag-faces">${(m.team || []).map((a) => `<span class="ag-face r-${esc(a.role)} ${a.status === 'working' ? 'busy' : ''}" title="${esc(a.name)} · ${esc(a.role)}">${esc(ini(a.name))}</span>`).join('')}</div></button>`;
      }).join('') || `<div class="empty">No jobs yet.${canAct ? ` Give ${esc(ai)} one.` : ''}</div>`;
      root.querySelectorAll('[data-job]').forEach((b) => (b.onclick = async () => { sel = +b.dataset.job; taskFilter = null; history.replaceState(null, '', `#/cexa?tab=jobs&job=${sel}`); await loadJob(); drawList(); drawDetail(); }));
    }

    // ---- a new job ---------------------------------------------------------------------------------------
    function drawNew() {
      if (!canAct) { $('#ag-detail').innerHTML = '<div class="card"><div class="bd empty">Pick a job on the left.</div></div>'; return; }
      const opts = (org ? org.teams.filter((t) => t.enabled) : []).map((t) => `<option value="${esc(t.key)}" ${newTeam === t.key ? 'selected' : ''}>${esc(t.name)} — ${esc(t.purpose)}</option>`).join('');
      $('#ag-detail').innerHTML = `<div class="card"><div class="hd"><h2>A new job for ${esc(ai)}</h2></div><div class="bd">
        <div class="field"><label for="ag-team-pick">Which team</label><select id="ag-team-pick"><option value="">Let ${esc(ai)} choose</option>${opts}</select></div>
        <div class="field" style="margin-top:10px"><label for="brief">What should her team do, and for whom?</label><textarea id="brief" rows="6" placeholder="e.g. Build a Project Manager tool: projects and their tasks, who has each task and when it is due. Remind people when a task is late, and tell me every morning what slipped."></textarea></div>
        <div class="row between" style="margin-top:10px;align-items:flex-start;gap:16px"><p class="small muted" style="max-width:560px">She asks her questions first. For a tool (Build, Change) she shows you the plan and nothing is built until you press Go; a list or a document (Advice) is written straight away; a standing team's check runs at once. Her teams never release, promote, grant access or see a real row.</p>
          <button class="btn primary" id="send">Give work</button></div></div></div>
        ${rolesCard(list.roles)}`;
      $('#send').onclick = async () => {
        const brief = $('#brief').value.trim();
        if (!brief) { toast('Say what the job is', 'err'); return; }
        $('#send').disabled = true;
        try { job = await api.post('/api/cexa/missions', { brief, team: $('#ag-team-pick').value }); sel = job.id; history.replaceState(null, '', `#/cexa?tab=jobs&job=${sel}`); await loadList(); drawList(); drawDetail(); toast(`${ai} has it`); }
        catch (e) { toast(e.detail || 'Could not send it', 'err'); $('#send').disabled = false; }
      };
    }

    function rolesCard(roles) {
      const rows = Object.entries(roles || {}).map(([k, r]) => `<li><span class="ag-face r-${esc(k)}">${esc(ini(r.label))}</span><div class="t"><b>${esc(r.label)}</b><span>${esc(r.what)}</span></div></li>`).join('');
      return `<div class="card" style="margin-top:16px"><div class="hd"><h2>The roles</h2><span class="small muted">which AI model each runs on: Settings → ${esc(ai)} voice &amp; models</span></div><ul class="list ag-roles">${rows}</ul></div>`;
    }

    // ---- one job -----------------------------------------------------------------------------------------
    function drawDetail() {
      if (!job) { drawNew(); return; }
      const j = job;
      const [t, k, r] = ST[j.status] || [j.status, 'neutral'];
      const byKind = STEPS_BY_KIND[j.kind];
      const step = byKind ? Math.max(0, byKind.findIndex(([sts]) => sts.includes(j.status))) : (STEP_OF[j.status] ?? 0);
      const stepper = `<div class="ag-steps">${(byKind ? byKind.map(([, l]) => l) : STEPS.map(([, l]) => l)).map((label, i) => `<span class="${i < step ? 'done' : i === step ? 'on' : ''}">${esc(label)}</span>`).join('')}</div>`;
      $('#ag-detail').innerHTML = `
        <div class="card ag-head"><div class="bd">
          <div class="row between" style="gap:12px;align-items:flex-start"><div><div class="eyebrow">${j.team ? esc(j.team.name) + ' · ' : ''}Job ${j.id}${j.round > 1 ? ` · round ${j.round}` : ''}${j.tool_slug ? ` · <span class="mono">${esc(j.tool_slug)}</span>` : ''}</div><h2 style="margin:2px 0 4px">${esc(j.title)}</h2></div>
            <div class="row" style="gap:8px;align-items:center">${pill(j.paused ? 'Paused' : t, j.paused ? 'warn' : k, !j.paused && r)}<span class="small muted num" title="Model calls this job has spent, and the most it may">${j.calls}/${j.max_calls} calls</span></div></div>
          ${stepper}
          <p class="ag-state">${esc(j.state || '')}</p>
          <details class="small"><summary class="muted">The job as you gave it</summary><div class="ag-brief">${esc(j.brief)}</div></details>
          <div id="ag-act"></div></div></div>
        ${j.document || (j.findings || []).length ? `<div class="card ag-doc" id="ag-doc"></div>` : ''}
        ${j.agents.length > 1 || j.status === 'working' ? `<div class="ag-team" id="ag-team"></div>` : ''}
        ${j.tasks.length ? `<div class="card"><div class="hd"><h2>Tasks</h2><span class="small muted">${j.counts.done}/${j.counts.tasks} done${taskFilter ? ` · <a href="#" id="tf-clear">show every task's activity</a>` : ' · click a task to see only its activity'}</span></div><div class="ag-board" id="ag-board"></div></div>` : ''}
        <div class="grid ag-bottom"><div class="card"><div class="hd"><h2>What's happening</h2><label class="small muted row" style="gap:6px;align-items:center"><input type="checkbox" id="show-model" ${showModel ? 'checked' : ''}> model calls</label></div><ul class="ag-feed" id="ag-feed"></ul></div>
          <div class="card"><div class="hd"><h2>What came out</h2></div><div class="bd" id="ag-out"></div></div></div>`;
      if (canAct) drawActions(); else $('#ag-act').innerHTML = '';
      if ($('#ag-doc')) drawDoc();
      if ($('#ag-team')) drawTeam();
      if ($('#ag-board')) drawBoard();
      drawFeed();
      drawOut();
      const tc = $('#tf-clear'); if (tc) tc.onclick = (e) => { e.preventDefault(); taskFilter = null; drawDetail(); };
      $('#show-model').onchange = (e) => { showModel = e.target.checked; drawFeed(); };
    }

    function drawActions() {
      const j = job, box = $('#ag-act');
      const post = async (path, body, msg) => { try { job = await api.post(`/api/cexa/missions/${j.id}/${path}`, body || {}); await loadList(); drawList(); drawDetail(); if (msg) toast(msg); } catch (e) { toast(e.detail || 'That did not work', 'err'); } };
      const textDialog = (title, label, ph, path, ok, msg) => {
        const body = el(`<div class="field"><label>${esc(label)}</label><textarea id="t" rows="4" placeholder="${esc(ph)}"></textarea></div>`);
        const m = modal({ title, body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">${esc(ok)}</button>` });
        m.root.querySelector('#ok').onclick = async () => { const text = body.querySelector('#t').value.trim(); if (!text) return; m.close(); await post(path, { text }, msg); };
      };
      if (j.status === 'asking') {
        box.innerHTML = `<div class="ag-qs">${j.questions.map((q, i) => `<div class="field"><label for="q${i}">${esc(q.q)}</label><textarea id="q${i}" rows="2">${esc(q.a || '')}</textarea></div>`).join('')}</div>
          <div class="row" style="gap:8px;justify-content:flex-end"><button class="btn" id="skip">Let her decide</button><button class="btn primary" id="ans">Send the answers</button></div>`;
        $('#ans').onclick = () => post('answers', { answers: j.questions.map((_, i) => $(`#q${i}`).value) }, 'She is writing the plan');
        $('#skip').onclick = () => post('answers', { answers: j.questions.map((_, i) => $(`#q${i}`).value), skip: true }, 'She is writing the plan');
      } else if (j.status === 'plan') {
        box.innerHTML = planHtml(j.plan) + `<div class="row" style="gap:8px;justify-content:flex-end;margin-top:12px"><button class="btn ghost danger" id="stop">Stop</button><button class="btn" id="change">Change the plan</button><button class="btn primary" id="go">Go</button></div>`;
        $('#go').onclick = () => post('go', {}, 'The team is on it');
        $('#change').onclick = () => textDialog('Change the plan', 'What should she change?', 'e.g. No morning check — a weekly one on Mondays. Call the second page My tasks.', 'replan', 'Change it', 'She is changing the plan');
        $('#stop').onclick = async () => { if (await confirmBox('Stop this job? Nothing has been built yet.', 'Stop')) post('stop'); };
      } else if (j.status === 'working') {
        box.innerHTML = `<div class="row" style="gap:8px;align-items:center"><input type="text" id="note" placeholder="Tell the team something — e.g. use “Owner” not “Assignee”" style="flex:1"><button class="btn" id="send-note">Tell them</button>
          ${j.paused ? '<button class="btn primary" id="resume">Carry on</button>' : '<button class="btn" id="pause">Pause</button>'}<button class="btn ghost danger" id="stop">Stop</button></div>`;
        $('#send-note').onclick = () => { const text = $('#note').value.trim(); if (text) post('note', { text }, 'The team has it'); };
        const p = $('#pause'); if (p) p.onclick = () => post('pause', {}, 'Paused after this step');
        const rs = $('#resume'); if (rs) rs.onclick = () => post('resume', {}, 'Carrying on');
        $('#stop').onclick = async () => { if (await confirmBox('Stop the team? The draft stays in the Builder as it is.', 'Stop')) post('stop'); };
      } else if (j.status === 'review' && j.kind === 'advice') {
        box.innerHTML = `<div class="row" style="gap:8px;justify-content:flex-end;margin-top:4px"><button class="btn" id="changes">Ask for changes</button><button class="btn" id="close">Close the job</button></div>`;
        $('#changes').onclick = () => textDialog('Ask for changes', 'What should the team change in the document?', 'e.g. Add who owns each item, and keep it to one page.', 'revise', 'Send', 'Round ' + (j.round + 1));
        $('#close').onclick = () => post('close', {}, 'Closed');
      } else if (j.status === 'review') {
        box.innerHTML = `${j.summary ? `<div class="ag-summary">${md(j.summary)}</div>` : ''}<div class="row" style="gap:8px;justify-content:flex-end;margin-top:10px"><button class="btn" id="changes">Ask for changes</button><button class="btn" id="close">Close the job</button><a class="btn primary" href="#/builder?t=${encodeURIComponent(j.tool_slug)}">Open it in the Builder</a></div>`;
        $('#changes').onclick = () => textDialog('Ask for changes', 'What should the team change?', 'e.g. Put the form on its own page, and add a Status filter to the tasks table.', 'revise', 'Send', 'Round ' + (j.round + 1));
        $('#close').onclick = () => post('close', {}, 'Closed');
      } else if (j.status === 'failed') {
        box.innerHTML = `<div class="row" style="justify-content:flex-end"><button class="btn primary" id="retry">Try again</button></div>`;
        $('#retry').onclick = () => post('retry', {}, 'Trying again');
      } else if ((j.status === 'done' || j.status === 'stopped') && j.draft && j.draft.exists) {
        box.innerHTML = `<div class="row" style="justify-content:flex-end"><a class="btn" href="#/builder?t=${encodeURIComponent(j.tool_slug)}">Open the draft in the Builder</a></div>`;
      } else box.innerHTML = '';
    }

    function planHtml(p) {
      if (!p || !p.title) return '';
      if (p.changes) {
        return `<div class="ag-plan"><div class="eyebrow">Change a live tool, in a copy draft</div><b>${esc(p.change_of || p.slug)}</b> <span class="mono small muted">${esc(p.slug)}</span>${p.summary ? `<p>${esc(p.summary)}</p>` : ''}
          <div class="eyebrow">The changes</div><ul class="ag-plan-list">${p.changes.map((c) => `<li><b>${esc(c.kind)} ${esc(c.name)}</b> — ${esc(c.instruction)}</li>`).join('') || '<li class="muted">None.</li>'}</ul>
          <div class="eyebrow" style="margin-top:12px">The team</div><div class="ag-faces big">${(p.team || []).map((a) => `<span class="ag-chip"><span class="ag-face r-${esc(a.role)}">${esc(ini(a.name))}</span><b>${esc(a.name)}</b> <span class="small muted">${esc(a.focus || '')}</span></span>`).join('')}</div>
          ${p.left_out ? `<div class="small muted" style="margin-top:10px"><b>Left out:</b> ${esc(p.left_out)}</div>` : ''}</div>`;
      }
      const ul = (items, f) => (items && items.length ? `<ul class="ag-plan-list">${items.map(f).join('')}</ul>` : '<div class="small muted">None.</div>');
      return `<div class="ag-plan">
        <div class="ag-plan-top"><div><div class="eyebrow">The tool</div><b>${esc(p.title)}</b> <span class="mono small muted">${esc(p.slug)}</span><div class="small muted">${esc(p.section)} · for ${esc((p.roles || []).join(', '))} · icon ${esc(p.icon)}</div></div></div>
        ${p.summary ? `<p>${esc(p.summary)}</p>` : ''}
        <div class="grid g2" style="gap:12px">
          <div><div class="eyebrow">Tables</div>${ul(p.data, (d) => `<li><b class="mono">${esc(d.name)}</b> — ${esc(d.purpose)}<div class="small muted mono">${esc((d.columns || []).join(' · '))}</div></li>`)}</div>
          <div><div class="eyebrow">Pages</div>${ul(p.pages, (x) => `<li><b>${esc(x.title)}</b> — ${esc(x.purpose)}<div class="small muted">${esc(x.blocks)}</div></li>`)}</div>
          <div><div class="eyebrow">Flows <span class="muted">(they ship switched off)</span></div>${ul(p.flows, (f) => `<li><b>${esc(f.title)}</b> — ${esc(f.purpose)} <span class="small muted">· ${esc(f.trigger)}</span></li>`)}</div>
          <div><div class="eyebrow">Core code <span class="muted">(built in a sandbox; you ship it)</span></div>${ul(p.code, (c) => `<li><b>${esc(c.title)}</b> — ${esc(c.purpose)}${c.why ? `<div class="small muted">Why: ${esc(c.why)}</div>` : ''}</li>`)}</div>
        </div>
        <div class="eyebrow" style="margin-top:12px">The team</div><div class="ag-faces big">${[{ name: aiName(), role: 'lead', focus: 'the plan and the team' }, ...(p.team || [])].map((a) => `<span class="ag-chip"><span class="ag-face r-${esc(a.role)}">${esc(ini(a.name))}</span><b>${esc(a.name)}</b> <span class="small muted">${esc((list_roles()[a.role] || {}).label || a.role)}${a.focus ? ' · ' + esc(a.focus) : ''}</span></span>`).join('')}</div>
        ${p.left_out ? `<div class="small muted" style="margin-top:10px"><b>Left out:</b> ${esc(p.left_out)}</div>` : ''}</div>`;
    }
    const list_roles = () => list.roles || (job && job.roles) || {};

    function drawDoc() {
      const j = job, f = j.findings || [];
      const handTo = (i) => canAct ? `<select class="ag-hand" data-hand="${i}"><option value="">Hand it to…</option><option value="change">Change — improve a live tool</option><option value="build">Build — a new tool</option><option value="advice">Advice — a document</option><option value="core">Core code — a proposal</option></select>` : '';
      $('#ag-doc').innerHTML = `<div class="hd"><h2>${j.kind === 'review' ? 'The council' : j.kind === 'advice' ? 'The document' : 'The report'}</h2>${j.document ? `<a class="btn sm" href="/api/cexa/missions/${j.id}/document.md">Download</a>` : ''}</div>
        <div class="bd">${j.document ? `<div class="ag-docbody">${docMd(j.document)}</div>` : ''}
        ${f.length ? `<details class="ag-fdet" ${(findingsOpen ?? (f.length <= 3 || !j.document)) ? 'open' : ''}><summary class="eyebrow" style="margin-top:12px;cursor:pointer">Findings (${f.length})${canAct ? ' — hand one to a team' : ''}</summary><ul class="ag-findings">${f.map((x, i) => `<li>${pill(x.severity, SEV[x.severity] || 'neutral')}<div class="t"><b>${esc(x.title)}</b>${x.evidence ? `<div class="small muted mono">${esc(x.evidence)}</div>` : ''}${x.suggestion ? `<div class="small">${esc(x.suggestion)}</div>` : ''}
          ${x.handed ? `<div class="small">Handed to ${esc(x.handed.to)}${x.handed.job ? ` — <a href="#/agents?job=${x.handed.job}">job ${x.handed.job}</a>` : x.handed.proposal ? ` — <a href="#/cexa-dev">proposal ${x.handed.proposal}</a>` : ''}</div>` : handTo(i)}</div></li>`).join('')}</ul></details>` : ''}</div>`;
      const fd = root.querySelector('.ag-fdet'); if (fd) fd.addEventListener('toggle', () => { findingsOpen = fd.open; });
      root.querySelectorAll('select[data-hand]').forEach((b) => (b.onchange = async () => {
        if (!b.value) return;
        b.dataset.to = b.value;
        try { const r = await api.post(`/api/cexa/missions/${j.id}/findings/${b.dataset.hand}/handoff`, { to: b.dataset.to }); job = r.mission; drawDetail(); toast(r.job ? `Job ${r.job} for the ${b.dataset.to} team` : 'On the board for C-EXA as developer'); await loadList(); drawList(); }
        catch (e) { toast(e.detail || 'Could not hand it on', 'err'); b.value = ''; }
      }));
    }

    function drawTeam() {
      $('#ag-team').innerHTML = job.agents.map((a) => {
        const busy = a.status === 'working';
        const st = busy ? pill('Working', 'info', true) : a.status === 'done' ? pill('Done', 'neutral') : a.status === 'problem' ? pill('Problem', 'crit') : pill(a.tasks && a.done < a.tasks ? 'Waiting' : 'Idle', 'neutral');
        return `<div class="card ag-agent ${busy ? 'busy' : ''}"><div class="row" style="gap:10px;align-items:center"><span class="ag-face big r-${esc(a.role)} ${busy ? 'busy' : ''}">${esc(ini(a.name))}</span>
          <div class="t" style="flex:1;min-width:0"><b>${esc(a.name)}</b><div class="small muted">${esc(a.role_label)}${a.focus ? ' · ' + esc(a.focus) : ''}</div></div>${st}</div>
          <div class="ag-doing small">${busy && a.doing ? esc(a.doing) : a.role === 'lead' && a.doing ? esc(a.doing) : '&nbsp;'}</div>
          <div class="row between small muted"><span class="mono" title="The model this agent uses">${esc(a.model)}</span><span class="num">${a.role === 'lead' ? '' : `${a.done}/${a.tasks} tasks · `}${a.calls} calls</span></div></div>`;
      }).join('');
    }

    function taskCard(t) {
      const out = t.output || {};
      if ((t.status === 'done' || t.status === 'skipped') && taskFilter !== t.id) {
        const sc = t.kind === 'look' && out.score !== undefined ? ` <span class="small ${out.score >= 75 ? 'muted' : 'ag-prob'}">${out.score}/100</span>` : '';
        return `<button class="ag-task done ${t.parent_id ? 'child' : ''}" data-task="${t.id}" title="${esc(t.title)}"><span class="ag-face sm r-${esc((job.agents.find((a) => a.id === t.agent_id) || {}).role || 'lead')}">${esc(ini(t.agent || aiName()))}</span><span class="ag-tt">${t.parent_id ? '↳ ' : ''}${esc(t.title)}</span>${sc}${t.status === 'skipped' ? ' <span class="small muted">skipped</span>' : ''}</button>`;
      }
      const badge = t.kind === 'look' && out.score !== undefined ? pill(`${out.score}/100`, out.score >= 75 ? 'good' : 'warn') : t.kind === 'code' && t.status === 'testing' ? pill('Sandbox tests', 'info', true) : '';
      const steps = (t.steps || []).length && (t.status === 'working' || t.status === 'testing' || taskFilter === t.id) ? `<ul class="ag-substeps">${t.steps.map((s) => `<li class="${s.done ? 'done' : ''}">${s.done ? '✓' : '○'} ${esc(s.text)}</li>`).join('')}</ul>` : '';
      return `<button class="ag-task k-${esc(t.kind)} ${taskFilter === t.id ? 'on' : ''} ${t.parent_id ? 'child' : ''}" data-task="${t.id}"><div class="row between" style="gap:6px"><span class="ag-tt">${t.parent_id ? '↳ ' : ''}${esc(t.title)}</span>${badge}</div>
        <div class="row" style="gap:6px;align-items:center;margin-top:4px"><span class="ag-face sm r-${esc((job.agents.find((a) => a.id === t.agent_id) || {}).role || 'lead')}">${esc(ini(t.agent || aiName()))}</span><span class="small muted">${esc(t.agent || aiName())}${t.attempts > 1 ? ` · try ${t.attempts}` : ''}</span></div>
        ${steps}${out.problem ? `<div class="small ag-prob">${esc(out.problem)}</div>` : ''}</button>`;
    }

    function drawBoard() {
      const col = (key) => job.tasks.filter((t) => (key === 'working' ? ['working', 'testing'].includes(t.status) : key === 'done' ? ['done', 'skipped'].includes(t.status) : t.status === key));
      const shown = TASK_COLS.filter(([k]) => col(k).length || k === 'done' || (k !== 'problem' && ['working', 'plan'].includes(job.status)));
      $('#ag-board').style.gridTemplateColumns = `repeat(${shown.length}, minmax(0, 1fr))`;
      $('#ag-board').innerHTML = shown.map(([k, label]) => { const ts = col(k); return `<div class="ag-col c-${k}"><div class="ag-col-h">${esc(label)} <span class="muted">${ts.length}</span></div>${ts.map(taskCard).join('') || '<div class="small muted ag-none">—</div>'}</div>`; }).join('');
      root.querySelectorAll('[data-task]').forEach((b) => (b.onclick = () => { const id = +b.dataset.task; taskFilter = taskFilter === id ? null : id; drawDetail(); }));
    }

    function drawFeed() {
      const evs = job.events.filter((e) => (showModel || e.kind !== 'model') && (!taskFilter || e.task_id === taskFilter));
      $('#ag-feed').innerHTML = evs.map((e) => {
        const who = e.kind === 'owner' ? 'You' : e.agent || 'Team';
        const shot = e.detail && e.detail.shot ? `<a href="#" class="ag-shot" data-shot="${esc(e.detail.shot)}"><img src="/api/cexa/missions/${job.id}/files/${encodeURIComponent(e.detail.shot)}" alt="Screenshot" loading="lazy"></a>` : '';
        const probs = e.detail && e.detail.problems && e.detail.problems.length ? `<ul class="small ag-probs">${e.detail.problems.slice(0, 6).map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : '';
        const qs = e.detail && e.detail.questions ? `<ol class="small">${e.detail.questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ol>` : '';
        return `<li class="ag-ev k-${esc(e.kind)}"><span class="ag-ico" aria-hidden="true">${KIND_ICON[e.kind] || '•'}</span><div class="t"><div class="row between" style="gap:8px"><b>${esc(who)}</b><span class="small muted" title="${esc(fmtDate(e.at, { time: true }))}">${esc(ago(e.at))}</span></div>
          <div class="ag-txt">${esc(e.text)}</div>${qs}${probs}${shot}${e.has_prompt ? `<button class="btn sm ghost" data-ev="${e.id}">Prompt &amp; reply</button>` : ''}</div></li>`;
      }).join('') || '<li class="empty">Nothing yet.</li>';
      root.querySelectorAll('[data-ev]').forEach((b) => (b.onclick = () => showEvent(+b.dataset.ev)));
      root.querySelectorAll('[data-shot]').forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); showShot(a.dataset.shot); }));
    }

    function showShot(name) {
      const m = modal({ title: 'What the designer saw (made-up rows)', body: `<img src="/api/cexa/missions/${job.id}/files/${encodeURIComponent(name)}" alt="Screenshot" style="width:100%;border:1px solid var(--line);border-radius:8px">` });
      const box = m.root.querySelector('.modal'); if (box) { box.style.maxWidth = 'min(1200px, 94vw)'; box.style.width = 'min(1200px, 94vw)'; }
    }

    async function showEvent(id) {
      try {
        const e = await api.get(`/api/cexa/missions/${job.id}/events/${id}`);
        const d = e.detail || {};
        modal({ title: 'Prompt and reply', body: `<p class="small muted">${esc(e.text)}${d.images ? ` · ${d.images} picture(s) sent` : ''}</p>
          <div class="eyebrow">System</div><pre class="ag-pre">${esc(d.system || '')}</pre><div class="eyebrow">Prompt</div><pre class="ag-pre">${esc(d.user || '')}</pre><div class="eyebrow">Reply</div><pre class="ag-pre">${esc(d.reply || '')}</pre>` });
      } catch (err) { toast(err.detail || 'Could not open it', 'err'); }
    }

    function drawOut() {
      if (!['build', 'change', ''].includes(job.kind || '')) {
        $('#ag-out').innerHTML = job.document ? `<div class="small">${esc(job.summary || '')}</div><div class="small muted" style="margin-top:6px">The ${job.kind === 'advice' ? 'document' : 'report'} is above.</div>` : '<div class="small muted">It appears here when the team is done.</div>';
        return;
      }
      const d = job.draft || {};
      const looks = job.tasks.filter((t) => t.kind === 'look' && t.output && t.output.shot);
      const code = job.tasks.filter((t) => t.kind === 'code');
      $('#ag-out').innerHTML = `${d.exists ? `<div class="row between" style="gap:8px"><div><div class="eyebrow">Builder draft</div><b class="mono">${esc(d.slug)}</b> ${d.errors && d.errors.length ? pill(`${d.errors.length} problems`, 'crit') : pill('Loads clean', 'good')}${d.released ? ' ' + pill('Released', 'info') : ''}</div><a class="btn sm" href="#/builder?t=${encodeURIComponent(d.slug)}">Open</a></div>
          <ul class="ag-files mono small">${(d.files || []).map((f) => `<li>${esc(f)}</li>`).join('')}</ul>${d.errors && d.errors.length ? `<ul class="small ag-probs">${d.errors.slice(0, 8).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}${(d.warnings || []).length ? `<div class="small muted">${d.warnings.map(esc).join(' · ')}</div>` : ''}`
        : `<div class="small muted">${job.tool_slug ? `The draft <span class="mono">${esc(job.tool_slug)}</span> appears here once the team starts.` : 'The plan names the draft.'}</div>`}
        ${looks.length ? `<div class="eyebrow" style="margin-top:12px">Screenshots</div><div class="ag-shots">${looks.map((t) => `<a href="#" data-shot="${esc(t.output.shot)}" title="${esc(t.title)} · ${t.output.score}/100"><img src="/api/cexa/missions/${job.id}/files/${encodeURIComponent(t.output.shot)}" alt="" loading="lazy"><span class="small">${t.output.score}/100</span></a>`).join('')}</div>` : ''}
        ${code.length ? `<div class="eyebrow" style="margin-top:12px">Core code (sandbox)</div>${code.map((t) => `<details class="small"><summary>${esc(t.title)} — ${esc(t.status)}${t.output.tests ? ` · ${t.output.tests.passed || 0} passed, ${t.output.tests.failed || 0} failed` : ''}</summary>${t.output.diff ? `<pre class="ag-pre">${esc(t.output.diff)}</pre>` : '<div class="muted">No diff yet.</div>'}</details>`).join('')}<div class="small muted">Ship it from <a href="#/cexa-dev">${esc(aiName())} as developer</a> when it is green.</div>` : ''}`;
      root.querySelectorAll('#ag-out [data-shot]').forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); showShot(a.dataset.shot); }));
    }

    function screenshotsDialog() {
      const s = list.screenshots || {};
      const body = el(`<div><p>The designer looks at every page the team builds. With screenshots on she sees it as a picture (the page drawn with made-up rows) — the closest check against CBX-Connect's look. Off, she reads the layout as text.</p>
        <p class="small muted">Now: ${esc(s.why || 'off')}. Turning it on installs Playwright and its Chromium into this server's Python (about 150 MB, once, from PyPI and Microsoft's download site).</p>${s.setup_log ? `<pre class="ag-pre">${esc(s.setup_log)}</pre>` : ''}</div>`);
      const m = modal({ title: 'Screenshots for the designer', body, footer: `<button class="btn" data-close>Not now</button><button class="btn primary" id="ok" ${s.setup && s.setup.running ? 'disabled' : ''}>${s.setup && s.setup.running ? 'Installing…' : 'Turn on screenshots'}</button>` });
      m.root.querySelector('#ok').onclick = async () => { try { await api.post('/api/cexa/screenshots/setup', {}); m.close(); toast('Installing — a few minutes'); } catch (e) { toast(e.detail || 'Could not start it', 'err'); } };
    }

    async function loadStanding() {
      try { standing = await api.get('/api/cexa/agents'); } catch (e) { $('#ag-standing-body').innerHTML = `<div class="empty">${esc(e.detail || e.message)}</div>`; return; }
      // 1.0.83: "harvester-01" reads as "Harvester"
      const friendly = (n) => { const b = String(n || '').replace(/-\d+$/, ''); return ({ dev: 'Developer' })[b] || b.replace(/^./, (c) => c.toUpperCase()); };
      const row = (a) => `<li><span class="ag-face r-helper ${a.state === 'busy' ? 'busy' : ''}">${esc(friendly(a.name).slice(0, 1))}</span><div class="t"><b>${esc(friendly(a.name))}</b><span>${a.state === 'busy' ? esc(a.task) + (a.note ? ' · ' + esc(a.note) : '') : 'Idle' + (a.last_task ? ' · last: ' + esc(a.last_task) : '')}</span></div>${a.state === 'busy' ? pill('Busy', 'info', true) : pill('Idle', 'neutral')}</li>`;
      $('#ag-standing-body').innerHTML = `<div class="grid g2" style="align-items:start"><div><div class="eyebrow">The agents</div><ul class="list">${standing.agents.map(row).join('') || '<li class="empty">No standing agents have run since the server started.</li>'}</ul></div>
        <div><div class="eyebrow">What they noted</div><ul class="list">${standing.notes.map((n) => `<li><div class="t"><b>${esc(friendly(n.agent))}</b><span>${esc(n.text)}</span></div></li>`).join('') || '<li class="empty">No notes.</li>'}</ul></div></div>
        <div class="tbl" style="margin-top:12px"><table><thead><tr><th>Agent</th><th>Task</th><th>Result</th><th class="num">Took</th></tr></thead><tbody>${standing.history.map((h) => `<tr><td>${esc(friendly(h.agent))}</td><td>${esc(h.task)}</td><td>${h.ok ? pill('OK', 'good') : pill('Failed', 'crit')}</td><td class="num">${h.seconds} s</td></tr>`).join('') || '<tr><td colspan="4" class="empty">Nothing yet.</td></tr>'}</tbody></table></div>`;
    }

    async function tick() {
      if (!root.isConnected) { dispose(); return; }
      if (!document.querySelector('.modal-bg') && !(document.activeElement && ['TEXTAREA', 'INPUT'].includes(document.activeElement.tagName) && root.contains(document.activeElement))) {
        await loadList();
        if (sel) await loadJob();
        if (tab === 'teams') { await Promise.all([loadOrg(), loadNow()]); drawTeams(); }
        if (tab === 'now') await nowTab.refresh();
        drawList();
        if (job) drawDetail();
      }
      const live = (job && (LIVE.has(job.status) || job.running)) || (list.missions || []).some((m) => LIVE.has(m.status) || m.status === 'reading');
      timer = setTimeout(tick, live || (tab === 'now' && nowTab.busy()) ? 3000 : (tab === 'now' ? 8000 : 15000));
    }

    await Promise.all([loadList(), loadOrg(), loadNow()]);
    if (sel) await loadJob();
    showTab();
    drawList();
    drawDetail();
    timer = setTimeout(tick, 3000);
    return dispose;
  },
};
