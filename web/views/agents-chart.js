// The company chart of C-EXA's agents (1.0.63): the owner at the top, C-EXA under him, her teams across,
// each team's lead and agents down. Live: drawn from the real teams, so a hired agent appears and a team
// switched off fades; whoever is working right now pulses, whoever waits for you has a dot. Click an agent
// to see what it is doing (and give it work); click a team to open it.
import { api } from '../api.js';
import { esc, toast, modal, el, pill, fmtDate, busyChipHtml } from '../ui.js';

const ago = (iso) => { if (!iso) return ''; const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : fmtDate(iso); };

export function drawChart(box, { org, now, me, canAct, ai, onTeam, onChanged }) {
  const active = (now && now.active) || [];
  const waiting = (now && now.waiting) || [];
  const busy = new Set(active.filter((w) => w.status === 'working').map((w) => w.to));
  const asks = new Set(waiting.map((w) => (w.kind === 'task' ? w.to : w.from)));
  const flag = (name) => `${busy.has(name) ? ' busy' : ''}`;
  const dot = (name) => (asks.has(name) ? '<i class="oc-ask" title="Waiting for you"></i>' : '');
  const agent = (m, lead) => `<button class="oc-box ${lead ? 'oc-lead' : 'oc-agent'}${flag(m.name)}" data-agent="${esc(m.name)}" title="${esc((m.focus ? m.focus + ' · ' : '') + m.model)}">${dot(m.name)}<b>${esc(m.name)}</b><span>${esc(m.role_label)}${lead && m.role !== 'lead' ? ' · lead' : ''}</span></button>`;
  const team = (t) => {
    const ms = t.members.filter((m) => m.enabled);
    const lead = ms.find((m) => m.lead);
    const rest = ms.filter((m) => !m.lead);
    const off = t.members.length - ms.length;
    const chain = [lead ? agent(lead, true) : '', ...rest.map((m) => agent(m, false))].filter(Boolean).join('<i class="oc-v"></i>');
    return `<div class="oc-col ${t.enabled ? '' : 'oc-off'}"><button class="oc-box oc-team" data-team="${esc(t.key)}"><b>${esc(t.name)}</b><span>${esc(t.word)}${t.enabled ? '' : ' · off'}</span></button>
      ${chain ? `<i class="oc-v"></i>${chain}` : ''}${off ? `<span class="small muted oc-more">+${off} switched off</span>` : ''}</div>`;
  };
  const head = me && me.role === 'owner' ? me.name : 'The owner';
  box.innerHTML = `<div class="card"><div class="bd oc-wrap"><div class="oc">
      <button class="oc-box oc-top" disabled><b>${esc(head)}</b><span>Owner · decides, releases, promotes</span></button><i class="oc-v"></i>
      <button class="oc-box oc-ceo${busy.has(ai) ? ' busy' : ''}" data-agent="${esc(ai)}">${dot(ai)}<b>${esc(ai)}</b><span>CEO · hands out the work</span></button><i class="oc-v"></i>
      <div class="oc-row">${org.teams.map(team).join('')}</div></div>
    <p class="small muted oc-key"><span class="oc-box oc-agent busy oc-mini"></span> working now <i class="oc-ask oc-mini-ask"></i> waiting for you <span class="oc-mini-off">faded</span> team switched off · click anyone</p></div></div>`;

  box.querySelectorAll('[data-team]').forEach((b) => (b.onclick = () => onTeam(b.dataset.team)));
  box.querySelectorAll('[data-agent]').forEach((b) => (b.onclick = () => openAgentCard(b.dataset.agent, { org, now, canAct, ai, onTeam, onChanged })));
}

// One agent: what it is doing, waiting for, has done — and give it work (the owner). Used by the chart and the office.
export function openAgentCard(name, { org, now, canAct, ai, onTeam, onChanged }) {
  const active = (now && now.active) || [];
  const waiting = (now && now.waiting) || [];
  {
    const isCeo = name === ai;
    let m = null, t = null;
    for (const tt of org.teams) { const x = tt.members.find((y) => y.name === name); if (x) { m = x; t = tt; break; } }
    const mine = active.filter((w) => w.to === name);
    const asking = waiting.filter((w) => (w.kind === 'task' ? w.to : w.from) === name);
    const done = ((now && now.recent) || []).filter((w) => w.to === name).slice(0, 5);
    const doc = now && now.docs ? now.docs[name] : null;  // 1.0.103: the document an AP or Support bot is on now; a click opens it
    const li = (w) => `<li><div class="t"><b>${esc(w.title)}</b><span>${esc(w.status)}${w.result ? ' · ' + esc(w.result.slice(0, 140)) : ''} · ${esc(ago(w.updated_at || w.created_at))}</span></div></li>`;
    const body = el(`<div>
      <p class="small" style="margin-top:0">${isCeo ? `CEO of the agents · <span class="mono">${esc(org.ceo.model)}</span>` : `${esc(m.role_label)}${m.lead ? ' and lead' : ''} of <b>${esc(t.name)}</b> · <span class="mono">${esc(m.model)}</span>`}</p>
      ${!isCeo && m.focus ? `<p class="small muted">Focus: ${esc(m.focus)}</p>` : ''}
      ${!isCeo && !t.enabled ? `<p class="small">${pill('Team switched off', 'neutral')} Its work goes to ${esc(ai)} until you switch it on.</p>` : ''}
      <div class="eyebrow" style="margin-top:12px">Now</div>${doc ? `<p class="small" style="margin:4px 0">${esc(name)} — <a href="${esc(doc.link)}" data-close>${esc(doc.label)}</a>, ${busyChipHtml(doc, true)}</p>` : ''}<ul class="list small">${mine.map(li).join('') || '<li class="empty">Nothing on.</li>'}</ul>
      ${asking.length ? `<div class="eyebrow" style="margin-top:12px">Waiting for you</div><ul class="list small">${asking.map((w) => `<li><div class="t"><b>${esc(w.question || w.title)}</b><span>answer it on the Now tab</span></div></li>`).join('')}</ul>` : ''}
      <div class="eyebrow" style="margin-top:12px">Done lately</div><ul class="list small">${done.map(li).join('') || '<li class="empty">Nothing yet.</li>'}</ul>
      ${canAct && (isCeo || t.enabled) ? `<div class="field" style="margin-top:12px"><label>Give ${esc(name)} work</label><div class="row" style="gap:6px"><input id="oc-w" type="text" maxlength="300" placeholder="e.g. Check why last night's harvest was slow"><button class="btn primary" id="oc-go">Give it</button></div></div>` : ''}</div>`);
    const d = modal({ title: name, body, footer: `${!isCeo ? '<button class="btn ghost" id="oc-team">Open the team</button>' : ''}<button class="btn" data-close>Close</button>` });
    const ot = d.root.querySelector('#oc-team'); if (ot) ot.onclick = () => { d.close(); onTeam(t.key); };
    const go = d.root.querySelector('#oc-go');
    if (go) go.onclick = async () => {
      const title = body.querySelector('#oc-w').value.trim();
      if (!title) return;
      go.disabled = true;
      try { await api.post('/api/cexa/work', { to: isCeo ? '' : name, title, detail: '' }); d.close(); toast(`Given to ${name} — it shows on the Now tab`); onChanged && onChanged(); }
      catch (e) { toast(e.detail || 'Could not give it', 'err'); go.disabled = false; }
    };
  }
}
