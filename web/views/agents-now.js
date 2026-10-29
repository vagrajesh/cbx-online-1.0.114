// Agents → Now (1.0.62): C-EXA's heartbeat, what her agents are working on and handing each other, what
// waits for you (questions, agents she wants to hire, models, tasks for people), and which model each kind
// of bot runs on. Answering, giving work and models are the owner's; a role with "See C-EXA's teams" reads.
// 1.0.114: it is the owner's board — the goals AXL works from the top (web/views/goals.js), what waits for you, what
// was done today; every single piece of work stays one click down.
import { api } from '../api.js';
import { esc, toast, pill, modal, el, fmtDate } from '../ui.js';
import { voice } from '../voice.js';
import { boardHtml, wireGoals, openFromHash } from './goals.js';

const ago = (iso) => { if (!iso) return ''; const s = (Date.now() - new Date(iso).getTime()) / 1000; return s < 60 ? 'just now' : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago` : fmtDate(iso); };
const until = (iso) => { if (!iso) return ''; const s = (new Date(iso).getTime() - Date.now()) / 1000; return s <= 60 ? 'any moment' : `in ${Math.round(s / 60)} min`; };
const ini = (n) => String(n || '?').split(/[\s-]+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join('') || '?';
const ST = { open: ['Lined up', 'neutral'], working: ['Working', 'info', true], held: ['Waiting on a colleague', 'ai'], waiting: ['Needs you', 'warn'], done: ['Done', 'good'], failed: ['Failed', 'crit'], stopped: ['Stopped', 'neutral'], declined: ['Not now', 'neutral'] };
const KIND = { hire: 'Hire an agent', model: 'A model', person: 'A task for someone', task: 'A question', call: 'A call', api: 'Needs your tap' };
const CALL_ST = { ringing: ['Ringing', 'info', true], live: ['On the call', 'ai', true], done: ['Done', 'good'], ended: ['Hung up', 'neutral'], declined: ['Not now', 'neutral'], missed: ['No answer', 'warn'] };

// part: 'now' (C-EXA → Now), 'asks' (what waits for the owner — shown on Approvals, 1.0.83) or 'setup'
// (her voice and the models — Settings → C-EXA voice & models, 1.0.83)
export function makeNow(box, { canAct, ai, org, part = 'now', onCount = null }) {
  let now = null, models = null, vstat = null, vlist = null, heard = new Set(), first = true, hashSeen = false;

  async function load() {
    try { now = await api.get('/api/cexa/now'); } catch (e) { box.innerHTML = `<div class="card"><div class="bd empty">${esc(e.detail || 'Could not load')}</div></div>`; return false; }
    if (part === 'setup' && canAct && (!models || Object.values(models.pulls || {}).some((p) => p.state === 'downloading'))) { try { models = await api.get('/api/cexa/models'); } catch { models = null; } }
    if (part === 'setup' && canAct) { try { [vstat, vlist] = await Promise.all([api.get('/api/voice/status', { quiet: true }), api.get('/api/voice/voices', { quiet: true })]); } catch { vstat = vstat || null; } }
    if (onCount) onCount(now.waiting.length + goalWaits().length);
    return true;
  }

  function goalWaits() { return ((now && now.goals && now.goals.waiting) || []).filter((w) => w.kind === 'goal'); }

  function speakNew() {
    const fresh = now.waiting.filter((w) => !heard.has(w.id));
    fresh.forEach((w) => heard.add(w.id));
    if (!first && fresh.length && voice.speakEnabled()) voice.speak(`${(fresh[0].kind === 'task' ? fresh[0].to : fresh[0].from) || ai} asks: ${fresh[0].question || fresh[0].title}`);
    first = false;
  }

  function draw() {
    if (!now) { box.innerHTML = '<div class="loading"><span class="spin"></span></div>'; return; }
    if (part === 'asks') {
      const gw = goalWaits();  // 1.0.114: goals AXL could not finish wait here too, with her one recommendation
      const n = now.waiting.length + gw.length;
      box.innerHTML = n ? `<div class="card" style="margin-bottom:12px"><div class="hd"><h2>From ${esc(ai)}</h2><span class="small muted">${n} waiting · only a person can answer these</span></div><div class="bd hb-asks">${gw.map(goalAsk).join('')}${now.waiting.map(ask).join('')}</div></div>` : '';
      wire(); return;
    }
    if (part === 'setup') {
      box.innerHTML = `${vstat ? voiceCard() : ''}${models ? modelsCard() : ''}` || '<div class="card"><div class="bd muted">Voice and models are not available on this server.</div></div>';
      wire(); return;
    }
    const h = now.heartbeat;
    const beat = h.on ? `<span class="hb-dot ${h.counts.working ? 'busy' : ''}" aria-hidden="true"></span>` : '<span class="hb-dot off" aria-hidden="true"></span>';
    const facts = [h.on ? `Last check ${esc(ago(h.last_beat) || 'not yet')} · next ${esc(until(h.next_beat) || 'soon')}` : 'She checks only when asked (the regular check is switched off on this server)', `a deeper check every night at ${esc(h.deep_at)}`,
      `${h.done_today} done today`, `${h.calls_today} of ${h.calls_cap} AI calls used today`, h.web ? 'web search on' : `<span title="${esc(h.web_why)}">web search off</span>`].join(' · ');
    box.innerHTML = `
      <div class="card hb-card"><div class="bd row between" style="gap:16px;align-items:center;flex-wrap:wrap">
        <div class="row" style="gap:14px;align-items:center">${beat}<div><div class="hb-mood">${esc(h.mood)}</div><div class="small muted">${facts}</div>
          ${h.last_signals && h.last_signals.length ? `<div class="small" style="margin-top:4px">Last noticed: ${h.last_signals.slice(0, 3).map(esc).join(' · ')}</div>` : ''}</div></div>
        ${canAct ? '<div class="row" style="gap:8px"><button class="btn sm" id="hb-beat">Check now</button><button class="btn sm ghost" id="hb-give" title="One small piece of work for one agent — a goal is usually better">Give work</button></div>' : ''}</div></div>
      ${boardHtml(now.goals, { ai })}
      <details class="card" style="margin-top:16px"><summary class="hd" style="cursor:pointer"><h2>Every piece of work</h2><span class="small muted">${now.active.length} at work · the agents hand each other work; one thinks at a time</span></summary>
      <div class="grid g2 hb-grid" style="gap:16px;align-items:start;padding:0 12px 12px">
        <div><div class="eyebrow">At work</div><ul class="list hb-list">${now.active.map(item).join('') || '<li class="empty">Nothing lined up.</li>'}</ul></div>
        <div><div class="eyebrow">Done lately</div><ul class="list hb-list">${now.recent.map(item).join('') || '<li class="empty">Nothing yet.</li>'}</ul></div>
      </div></details>
`;
    wire();
    if (!hashSeen) { hashSeen = true; openFromHash({ after: refresh, ai, canAct }); }
  }

  function goalAsk(w) {
    return `<div class="hb-ask"><div class="row between" style="gap:8px"><span class="small"><b>${esc(ai)}</b> <span class="muted">· A goal she could not finish · ${esc(ago(w.at))}</span></span><a href="#" class="small" data-goal="${w.id}">details</a></div>
      <div class="hb-q"><b>${esc(w.title)}</b> — ${esc(w.text)}</div>${w.recommend ? `<div class="small"><b>She recommends:</b> ${esc(w.recommend)}</div>` : ''}
      ${canAct ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">${w.options.map((o) => `<button class="btn sm ${/^stop/i.test(o) ? 'ghost' : ''}" data-ganswer="${w.id}" data-text="${esc(o)}">${esc(o)}</button>`).join('')}</div>` : ''}</div>`;
  }

  function ask(w) {
    const p = w.proposal || {};
    const extra = w.kind === 'hire' ? `<div class="small muted">${esc(p.role)} on ${esc(p.team)} · ${esc(p.focus || '')}${p.model ? ' · ' + esc(p.model) : ''}</div>`
      : w.kind === 'model' ? `<div class="small muted">${p.downloading ? 'Downloading… ' + esc(dl(p.model)) : p.installed ? 'installed' : 'not installed yet'}</div>`
      : w.kind === 'person' ? `<div class="small muted">to ${esc(p.to)}${p.due ? ' · due ' + esc(p.due) : ''}</div>`
      : w.kind === 'call' ? `<div class="small muted">${esc(ai)} rings ${esc(p.to)} in CBX and asks: ${(p.questions || []).map(esc).join(' · ')}</div>`
      : w.kind === 'api' ? `<div class="small muted mono" title="What runs, as you, when you approve">${esc(p.method)} ${esc(p.path)}${p.body ? ' ' + esc(JSON.stringify(p.body)).slice(0, 220) : ''}</div>` : '';
    const who = w.kind === 'task' ? (w.to || ai) : (w.from || ai);
    return `<div class="hb-ask" data-w="${w.id}"><div class="row between" style="gap:8px"><span class="small"><b>${esc(who)}</b> <span class="muted">· ${esc(KIND[w.kind] || '')} · ${esc(ago(w.created_at))}</span></span><a href="#" class="small" data-open="${w.id}">details</a></div>
      <div class="hb-q">${esc(w.question || w.title)}</div>${extra}
      ${canAct ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px">${(w.options || []).map((o) => `<button class="btn sm ${/^(not now|stop|no\b)/i.test(o) ? 'ghost' : ''}" data-answer="${w.id}" data-text="${esc(o)}">${esc(o)}</button>`).join('')}
        ${w.kind === 'task' ? `<input type="text" class="hb-other" data-other="${w.id}" placeholder="Or say something else…" maxlength="500"><button class="btn sm" data-say="${w.id}">Send</button>` : ''}</div>` : ''}</div>`;
  }

  function dl(name) { const p = models && models.pulls && models.pulls[name]; return p ? (p.state === 'downloading' ? `${p.pct}%` : p.state) : ''; }

  function item(w) {
    const [t, k, r] = ST[w.status] || [w.status, 'neutral'];
    const done = (w.plan || []).filter((p) => p.done).length;
    return `<li><a href="#" class="hb-item" data-open="${w.id}"><span class="ag-face sm r-lead">${esc(ini(w.kind === 'task' ? w.to : w.from))}</span>
      <div class="t"><b>${esc(w.title)}</b><span>${esc(w.from || '')}${w.kind === 'task' ? ' → ' + esc(w.to) : ''}${w.plan && w.plan.length && !['done', 'failed', 'stopped', 'declined'].includes(w.status) ? ` · ${done}/${w.plan.length} steps` : ''}${w.result && ['done', 'failed'].includes(w.status) ? ' · ' + esc(w.result.slice(0, 120)) : ''}</span></div>${pill(t, k, r)}</a></li>`;
  }

  function modelsCard() {
    const m = models;
    const opts = (need) => m.installed.filter((x) => !x.caps.length || (x.caps.includes(need) && !x.caps.includes('embedding')));
    const row = (t) => {
      const sel = t.tier === 'brain' ? `<span class="mono">${esc(t.model)}</span> <span class="small muted">(set on the server)</span>`
        : `<select data-pick="${t.tier}"><option value="">${esc(t.picked ? '— back to the default —' : t.model + ' (default)')}</option>${opts(t.needs).map((x) => `<option value="${esc(x.name)}" ${t.picked && x.name === t.model ? 'selected' : ''}>${esc(x.name)} · ${x.gb} GB</option>`).join('')}</select>`;
      const state = !t.installed ? pill('Not installed', 'crit') : !t.able ? pill(`Can't do ${t.needs}`, 'warn') : pill('OK', 'good');
      const sug = t.suggest ? `<button class="btn sm" data-use="${t.tier}" data-model="${esc(t.suggest)}" title="${esc(t.why)}">Use ${esc(t.suggest)}</button>` : '';
      const down = t.download ? `<button class="btn sm ghost" data-pull="${esc(t.download.name)}" title="${esc(t.download.why)}">Download ${esc(t.download.name)} (${t.download.gb} GB)</button> <span class="small muted">${esc(dl(t.download.name))}</span>` : '';
      return `<tr><td><b>${esc(t.who)}</b><div class="small muted">${esc(t.what)}</div></td><td>${sel}</td><td>${state}${t.gb ? ` <span class="small muted">${t.gb} GB</span>` : ''}</td><td>${sug} ${down}</td></tr>`;
    };
    return `<div class="card" style="margin-top:16px"><div class="hd"><h2>Models</h2><span class="small muted">graphics card ${m.gpu_gb ? m.gpu_gb + ' GB' : 'unknown'} · ${m.installed.length} installed${m.ollama_up ? '' : ' · Ollama not answering'}</span></div>
      <div class="bd"><p class="small muted" style="margin-top:0">Not every bot needs the strongest model: small jobs run on a small one, in seconds. ${esc(ai)} checks at night and asks you here when something would fit better.</p>
      <div class="tbl"><table><thead><tr><th>Who</th><th>Model</th><th></th><th></th></tr></thead><tbody>${m.tiers.map(row).join('')}</tbody></table></div>
      ${m.note ? `<p class="small muted">${esc(m.note)}</p>` : ''}
      <div class="row" style="gap:8px;margin-top:8px;align-items:center"><input type="text" id="hb-pull-name" class="mono" placeholder="e.g. qwen3:8b" style="max-width:240px"><button class="btn sm" id="hb-pull">Download</button><span class="small muted">from Ollama's library</span></div></div></div>`;
  }

  function voiceCard() {
    const v = vstat, ok = (x) => (x.ready ? pill('Ready', 'good') : pill('Not set up', 'warn'));
    const running = v.setup && v.setup.running;
    const calls = (v.calls || []).map((c) => { const [t, k, r] = CALL_ST[c.status] || [c.status, 'neutral']; return `<li><div class="t"><b>${esc(c.to)}</b><span>${esc(c.purpose)} · ${esc(ago(c.created_at))}${c.by ? ' · asked by ' + esc(c.by) : ''}</span>${c.summary && ['done', 'ended'].includes(c.status) ? `<details><summary class="small muted">What was said</summary><pre class="small" style="white-space:pre-wrap">${esc(c.summary)}</pre></details>` : ''}</div>${pill(t, k, r)}</li>`; }).join('');
    return `<div class="card vc-card" style="margin-top:16px"><div class="hd"><h2>Voice</h2><span class="small muted">on this server — no audio leaves it, none is kept</span></div>
      <div class="bd">
        <div class="vc-row"><b>Ears</b>${ok(v.ears)}<span class="small muted">${esc(v.ears.engine)} · ${esc(v.ears.model)}${v.ears.why ? ' · ' + esc(v.ears.why) : ''}</span></div>
        <div class="vc-row"><b>Voice</b>${ok(v.voice)}<span class="small muted">${esc(v.voice.engine)} · ${esc(v.voice.voice)}${v.voice.why ? ' · ' + esc(v.voice.why) : ''} · Arabic: the browser speaks it</span></div>
        <div class="row" style="gap:8px;margin-top:10px;flex-wrap:wrap">
          ${!v.on || running ? `<button class="btn sm primary" id="vc-setup" ${running ? 'disabled' : ''}>${running ? '<span class="spin"></span> Setting up…' : 'Set up voice'}</button>` : ''}
          <button class="btn sm" id="vc-test">Hear her</button>
          <span class="small muted">${v.on ? 'Talk hands-free from the C-EXA drawer; she can ring people when you or an admin ask, or when you press Call on an agent\'s suggestion.' : 'Installs Whisper and Kokoro into this Python and fetches Kokoro\'s files (about 340 MB). Whisper downloads its model the first time she listens.'}</span>
        </div>
        ${v.setup_log ? `<details style="margin-top:8px"><summary class="small muted">Set-up log${v.setup && v.setup.ok === false ? ' — it did not finish' : ''}</summary><pre class="small" style="white-space:pre-wrap">${esc(v.setup_log)}</pre></details>` : ''}
        ${vlist ? voicesHtml() : ''}
        ${calls ? `<div class="eyebrow" style="margin-top:12px">Recent calls</div><ul class="list">${calls}</ul>` : ''}
      </div></div>`;
  }

  function voicesHtml() {
    const st = vlist.studio, busy = st.setup && st.setup.running;
    const sPill = busy ? pill('Setting up…', 'info', true) : !st.installed ? pill('Not set up', 'neutral') : !st.running ? pill('Stopped', 'warn') : !st.loaded ? (st.error ? pill('Did not load', 'crit') : pill('Loading the model…', 'info', true)) : pill(`Ready · ${st.gpu || st.device}`, 'good');
    const row = (x) => `<div class="vc-v ${x.id === vlist.choice ? 'on' : ''}"><span class="n">${esc(x.name)}</span><span class="d">${esc(x.accent)} ${esc(x.gender)} · ${esc(x.blurb)}</span>
      <button class="btn sm ghost" data-hear="${esc(x.id)}" title="Hear ${esc(x.name)}">▶ Hear</button>${x.id === vlist.choice ? pill('Her voice', 'good') : `<button class="btn sm" data-vuse="${esc(x.id)}" ${x.engine === 'studio' && !x.ready ? 'title="Works once the studio voice is set up — until then her quick twin speaks"' : ''}>Use</button>`}</div>`;
    const group = (title, f) => { const xs = vlist.voices.filter(f); return xs.length ? `<div class="eyebrow" style="margin-top:10px">${esc(title)}</div><div class="vc-voices">${xs.map(row).join('')}</div>` : ''; };
    return `<div class="eyebrow" style="margin-top:16px">Her voice</div>
      <div class="vc-row"><b>Studio</b>${sPill}<span class="small muted">Chatterbox on the graphics card — fuller and more natural, English. Made-up British voices, no real person's.</span>
        ${!st.installed && !busy ? '<button class="btn sm primary" id="vc-studio">Set up the studio voice</button>' : st.installed && !st.running && !busy ? '<button class="btn sm" id="vc-studio-start">Start</button>' : ''}</div>
      ${busy ? `<p class="small muted">${esc(st.setup.step || '')} — this takes a while (about 4 GB the first time).</p>` : ''}
      ${st.setup_log ? `<details><summary class="small muted">Studio set-up log${st.setup.ok === false ? ' — it did not finish' : ''}</summary><pre class="small" style="white-space:pre-wrap">${esc(st.setup_log)}</pre></details>` : ''}
      ${st.error ? `<p class="small" style="color:var(--crit)">${esc(st.error)}</p>` : ''}
      ${group('Studio — British women', (x) => x.engine === 'studio')}
      ${group('Quick — British women', (x) => x.engine === 'quick' && x.accent === 'British' && x.gender === 'woman')}
      ${group('Quick — British men', (x) => x.engine === 'quick' && x.accent === 'British' && x.gender === 'man')}
      ${group('Quick — American', (x) => x.engine === 'quick' && x.accent === 'American')}
      <p class="small muted" style="margin-top:8px">Everyone hears the voice you pick — in Talk, in the drawer when Speak is on, and on calls. Portuguese and French use Kokoro's own voices; Arabic the browser's.</p>`;
  }

  async function answer(id, text, btn) {
    if (btn) btn.disabled = true;
    try { await api.post(`/api/cexa/work/${id}/answer`, { text }); toast('Thanks — passed on'); models = null; await refresh(); }
    catch (e) { toast(e.detail || 'Could not answer', 'err'); if (btn) btn.disabled = false; }
  }

  function wire() {
    wireGoals(box, { after: refresh, ai, canAct });
    box.querySelectorAll('[data-answer]').forEach((b) => (b.onclick = () => answer(+b.dataset.answer, b.dataset.text, b)));
    box.querySelectorAll('[data-say]').forEach((b) => (b.onclick = () => { const i = box.querySelector(`[data-other="${b.dataset.say}"]`); if (i && i.value.trim()) answer(+b.dataset.say, i.value.trim(), b); }));
    box.querySelectorAll('[data-open]').forEach((a) => (a.onclick = (e) => { e.preventDefault(); openWork(+a.dataset.open); }));
    const bt = box.querySelector('#hb-beat');
    if (bt) bt.onclick = async () => { bt.disabled = true; try { const r = await api.post('/api/cexa/heartbeat/beat', {}); toast(r.made.length ? `Noticed ${r.signals.length}; handed out ${r.made.length}` : r.signals.length ? 'Nothing new — already on it' : 'All quiet'); await refresh(); } catch (e) { toast(e.detail || 'Could not beat', 'err'); bt.disabled = false; } };
    const gv = box.querySelector('#hb-give'); if (gv) gv.onclick = giveDialog;
    box.querySelectorAll('[data-pick]').forEach((s) => (s.onchange = () => pick(s.dataset.pick, s.value)));
    box.querySelectorAll('[data-use]').forEach((b) => (b.onclick = () => pick(b.dataset.use, b.dataset.model)));
    box.querySelectorAll('[data-pull]').forEach((b) => (b.onclick = () => pull(b.dataset.pull)));
    const vs = box.querySelector('#vc-setup');
    if (vs) vs.onclick = async () => { vs.disabled = true; try { await api.post('/api/voice/setup', {}); toast('Setting up voice — it can take a few minutes'); vstat = { ...vstat, setup: { running: true } }; draw(); } catch (e) { toast(e.detail || 'Could not start it', 'err'); vs.disabled = false; } };
    box.querySelectorAll('[data-hear]').forEach((b) => (b.onclick = async () => { b.disabled = true; await voice.say('', { voiceId: b.dataset.hear }); b.disabled = false; }));
    box.querySelectorAll('[data-vuse]').forEach((b) => (b.onclick = async () => { try { await api.post('/api/voice/choice', { id: b.dataset.vuse }); voice.refresh(); toast('Her voice is changed'); vlist = await api.get('/api/voice/voices'); draw(); } catch (e) { toast(e.detail || 'Could not change it', 'err'); } }));
    const vss = box.querySelector('#vc-studio');
    if (vss) vss.onclick = async () => { vss.disabled = true; try { await api.post('/api/voice/studio/setup', {}); toast('Setting up the studio voice — it takes a while'); vlist = await api.get('/api/voice/voices'); draw(); } catch (e) { toast(e.detail || 'Could not start it', 'err'); vss.disabled = false; } };
    const vsg = box.querySelector('#vc-studio-start');
    if (vsg) vsg.onclick = async () => { vsg.disabled = true; try { await api.post('/api/voice/studio/start', {}); toast('Starting'); setTimeout(refresh, 4000); } catch (e) { toast(e.detail || 'Could not start it', 'err'); vsg.disabled = false; } };
    const vt = box.querySelector('#vc-test');
    if (vt) vt.onclick = async () => { vt.disabled = true; voice.refresh(); await voice.say(`Hello, I'm ${ai}. This is my voice.`); vt.disabled = false; };
    const pb = box.querySelector('#hb-pull'); if (pb) pb.onclick = () => { const v = box.querySelector('#hb-pull-name').value.trim(); if (v) pull(v); };
  }

  async function pick(tier, model) {
    try { models = await api.post('/api/cexa/models/pick', { tier, model }); toast(model ? `Now on ${model}` : 'Back to the default model'); draw(); } catch (e) { toast(e.detail || 'Could not change it', 'err'); draw(); }
  }
  async function pull(name) {
    try { models = await api.post('/api/cexa/models/pull', { name }); toast(`Downloading ${name} — it can take a while`); draw(); } catch (e) { toast(e.detail || 'Could not download', 'err'); }
  }

  function giveDialog() {
    const people = [[ '', `${ai} (she decides who)` ]].concat(((org() || {}).teams || []).filter((t) => t.enabled).flatMap((t) => t.members.filter((m) => m.enabled).map((m) => [m.name, `${m.name} — ${m.role}${m.lead ? ', lead' : ''} · ${t.name}`])));
    const b = el(`<div style="display:flex;flex-direction:column;gap:10px">
      <div class="field"><label>Who</label><select id="gw-to">${people.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select></div>
      <div class="field"><label>What</label><input id="gw-title" type="text" maxlength="300" placeholder="e.g. Find out why last night's harvest was slow"></div>
      <div class="field"><label>Anything they should know <span class="muted">(optional)</span></label><textarea id="gw-detail" rows="4"></textarea></div>
      <p class="small muted" style="margin:0">They work it with their own tools, hand parts to colleagues and ask you when they are not sure. They never release, promote or touch live rows.</p></div>`);
    const d = modal({ title: 'Give work', body: b, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="gw-ok">Give work</button>' });
    d.root.querySelector('#gw-ok').onclick = async () => {
      try { await api.post('/api/cexa/work', { to: b.querySelector('#gw-to').value, title: b.querySelector('#gw-title').value, detail: b.querySelector('#gw-detail').value }); d.close(); toast('Given — they start when the GPU is free'); await refresh(); }
      catch (e) { toast(e.detail || 'Could not give it', 'err'); }
    };
  }

  async function openWork(id) {
    let w;
    try { w = await api.get(`/api/cexa/work/${id}`); } catch (e) { toast(e.detail || 'Could not open it', 'err'); return; }
    const [t, k] = ST[w.status] || [w.status, 'neutral'];
    const steps = (w.transcript || []).slice(1).map((m) => {
      if (m.role === 'assistant') return (m.tool_calls || []).map((c) => `<li class="hb-step"><span class="mono">${esc(c.function.name)}</span> <span class="small muted">${esc(JSON.stringify(c.function.arguments || {})).slice(0, 220)}</span></li>`).join('') + (m.content && !(m.tool_calls || []).length ? `<li class="hb-step"><span class="small">${esc(m.content.slice(0, 400))}</span></li>` : '');
      if (m.role === 'tool') return `<li class="hb-res"><details><summary class="small muted">${esc((m.content || '').split('\n')[0].slice(0, 140))}</summary><pre class="small">${esc(m.content || '')}</pre></details></li>`;
      if (m.role === 'user') return `<li class="hb-user small"><b>${esc((m.content || '').slice(0, 600))}</b></li>`;
      return '';
    }).join('');
    const body = el(`<div>
      <div class="row between" style="gap:8px"><span class="small muted">${esc(w.from || '')}${w.kind === 'task' ? ' → ' + esc(w.to) : ''} · #${w.id} · ${esc(ago(w.created_at))}</span>${pill(t, k)}</div>
      ${w.detail ? `<div class="ag-brief small">${esc(w.detail)}</div>` : ''}
      ${w.plan && w.plan.length ? `<div class="eyebrow" style="margin-top:12px">Plan</div><ol class="small">${w.plan.map((p) => `<li>${p.done ? '✓ ' : ''}${esc(p.text)}</li>`).join('')}</ol>` : ''}
      ${w.result ? `<div class="ag-summary" style="margin-top:10px">${esc(w.result)}${w.confidence ? ` <span class="small muted">· ${w.confidence}% sure</span>` : ''}</div>` : ''}
      ${w.tainted ? '<p class="small muted">It read the web, so its changing tools were put away for the rest of this work.</p>' : ''}
      ${(w.children || []).length ? `<div class="eyebrow" style="margin-top:12px">Handed on</div><ul class="list hb-list">${w.children.map(item).join('')}</ul>` : ''}
      ${steps ? `<div class="eyebrow" style="margin-top:12px">Every step</div><ol class="hb-steps">${steps}</ol>` : ''}</div>`);
    const live = ['open', 'working', 'held', 'waiting'].includes(w.status);
    const d = modal({ title: w.title, body, footer: `${canAct && live ? '<button class="btn ghost danger" id="w-stop">Stop it</button>' : ''}<button class="btn" data-close>Close</button>` });
    body.querySelectorAll('[data-open]').forEach((a) => (a.onclick = (e) => { e.preventDefault(); d.close(); openWork(+a.dataset.open); }));
    const s = d.root.querySelector('#w-stop');
    if (s) s.onclick = async () => { try { await api.post(`/api/cexa/work/${id}/stop`, {}); d.close(); toast('Stopped'); await refresh(); } catch (e) { toast(e.detail || 'Could not stop it', 'err'); } };
  }

  async function refresh() {
    if (!(await load())) return;
    speakNew();
    const busy = box.contains(document.activeElement) && ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    if (!busy && !document.querySelector('.modal-bg')) draw();
  }

  return { refresh, busy: () => !!(now && (now.heartbeat.counts.working || now.heartbeat.counts.open || Object.values((models || {}).pulls || {}).some((p) => p.state === 'downloading') || (vstat && vstat.setup && vstat.setup.running) || (vlist && vlist.studio && ((vlist.studio.setup && vlist.studio.setup.running) || (vlist.studio.running && !vlist.studio.loaded))))) };
}
