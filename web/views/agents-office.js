// The office (1.0.65): C-EXA's agents at their desks, live.
//   working  — seated at the computer, typing; the screen scrolls with code
//   waiting  — stands up and looks at you, a "!" over the head (it needs an answer on Now)
//   idle     — keeps busy: looks around, plays a quick game, files, waters the plant
//   off      — the team is switched off: lights out, empty chair
// On every desk: a computer, a plant (grows with the work done in the last 30 days, droops after two idle
// weeks; idle agents water it — click it and they will), and an open filing rack with a folder for every
// piece of work they have had (more work, more and fatter folders; a pile on top when the rack is full).
// Click a desk to see what the agent is doing and give it work.
import { api } from '../api.js';
import { esc, busyChipHtml } from '../ui.js';
import { openAgentCard } from './agents-chart.js';

const ROLE_C = { lead: '#0f7b5f', developer: '#2f6fd6', core: '#4b4fc4', designer: '#8a4fc4', tester: '#c4784f', helper: '#c45a8a', analyst: '#1f8f99',
  reviewer: '#6b7c3a', writer: '#9a6b1f', operator: '#4f6b84', checker: '#7a8a4f', triager: '#c4564f', security: '#a33b3b', correctness: '#3b6ea3', people: '#a35f9b',
  translator: '#5f8a3b', scout: '#c49a1f', ceo: '#0f7b5f' };
const FOLDER_C = ['#e9c46a', '#7fb3d5', '#8fc9a3', '#e8a598', '#c3b1e1', '#f2d388'];
const IDLE = ['look', 'game', 'file', 'water', 'look', 'game'];
const IDLE_WORD = { look: 'Looking around', game: 'Playing a quick game', file: 'Filing', water: 'Watering the plant' };

const hash = (s) => { let h = 7; for (const c of String(s)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; };

function plant(st) {
  const leaves = 2 + Math.min(7, (st && st.done_30d) || 0);
  const h = 16 + leaves * 6;
  let g = `<path class="pl-stem" d="M17 133 Q15 ${133 - h / 2} 18 ${133 - h}" />`;
  for (let i = 0; i < leaves; i++) {
    const y = 128 - i * ((h - 6) / leaves), left = i % 2 === 0;
    g += `<ellipse class="pl-leaf" cx="${left ? 11 : 23}" cy="${y.toFixed(1)}" rx="7" ry="3.4" transform="rotate(${left ? 32 : -32} ${left ? 11 : 23} ${y.toFixed(1)})" />`;
  }
  return `<g class="plant" data-plant="1"><g class="pl-leaves">${g}</g>
    <path class="pl-pot" d="M5 132 H29 L26 158 H8 Z" /><rect class="pl-rim" x="4" y="130" width="26" height="5" rx="1.5" /></g>`;
}

function rack(st, seed) {
  const n = (st && st.total) || 0, open = (st && st.open) || 0;
  const shown = Math.min(n, 18), w = n > 36 ? 6 : n > 18 ? 5 : 4;
  let f = '';
  for (let i = 0; i < shown; i++) {
    const shelf = Math.floor(i / 6), slot = i % 6, base = [91, 123, 155][shelf];
    const ht = 15 + ((seed >> (i % 13)) & 7), x = 256 + slot * (w + 1.2);
    const live = i >= shown - Math.min(open, shown);
    f += `<rect class="fd${live ? ' fd-live' : ''}" x="${x.toFixed(1)}" y="${base - ht}" width="${w}" height="${ht}" rx="0.8" fill="${FOLDER_C[(seed + i) % FOLDER_C.length]}" />`;
  }
  const extra = Math.max(0, n - 18), pile = Math.min(5, Math.ceil(extra / 6));
  for (let i = 0; i < pile; i++) f += `<rect class="fd-pile" x="${257 + (i % 2)}" y="${57 - i * 3.2}" width="34" height="3" rx="0.6" />`;
  return `<g class="rack"><rect class="rk" x="251" y="60" width="3" height="98" /><rect class="rk" x="294" y="60" width="3" height="98" />
    <rect class="rk" x="251" y="91" width="46" height="3" /><rect class="rk" x="251" y="123" width="46" height="3" /><rect class="rk" x="251" y="155" width="46" height="3" />${f}</g>`;
}

function head(x, y, s, c) { // a friendly robot head: antenna, visor, two eyes
  return `<g class="hd"><line x1="${x}" y1="${y}" x2="${x}" y2="${y - 6 * s}" class="ant" /><circle cx="${x}" cy="${y - 7 * s}" r="${2.2 * s}" fill="${c}" />
    <rect class="hd-b" x="${x - 12 * s}" y="${y}" width="${24 * s}" height="${22 * s}" rx="${8 * s}" />
    <rect class="visor" x="${x - 9 * s}" y="${y + 7 * s}" width="${18 * s}" height="${8 * s}" rx="${4 * s}" />
    <g class="eyes"><circle cx="${x - 4 * s}" cy="${y + 11 * s}" r="${1.7 * s}" /><circle cx="${x + 4 * s}" cy="${y + 11 * s}" r="${1.7 * s}" /></g></g>`;
}

function desk(m, st, c, ceo) {
  const seed = hash(m.name);
  return `<svg viewBox="0 0 300 170" class="of-svg" role="img" aria-label="${esc(m.name)}'s desk">
    <ellipse class="sh" cx="150" cy="160" rx="146" ry="7" />
    ${plant(st)}
    ${rack(st, seed)}
    <rect class="chair" x="158" y="64" width="40" height="42" rx="9" />
    <g class="fig fig-sit"><rect x="162" y="78" width="32" height="28" rx="10" fill="${c}" />${head(178, 50, 1, c)}
      <path class="arm arm-l" d="M167 86 L154 98" stroke="${c}" /><path class="arm arm-r" d="M189 86 L174 98" stroke="${c}" /></g>
    <g class="fig fig-stand"><rect x="162" y="58" width="32" height="44" rx="10" fill="${c}" />${head(178, 30, 1, c)}
      <path class="arm" d="M165 66 L160 88" stroke="${c}" /><path class="arm arm-wave" d="M191 66 L203 49" stroke="${c}" />
      <g class="bubble"><rect x="204" y="6" width="22" height="18" rx="6" /><path d="M208 23 L205 29 L213 23 Z" /><text x="215" y="19.5" text-anchor="middle">!</text></g></g>
    <rect class="dk-top" x="76" y="100" width="140" height="8" rx="2" />
    <rect class="dk-ped" x="80" y="108" width="38" height="50" rx="1.5" /><line class="dk-ln" x1="84" y1="124" x2="114" y2="124" /><line class="dk-ln" x1="84" y1="140" x2="114" y2="140" />
    <rect class="dk-pan" x="118" y="108" width="90" height="26" /><rect class="dk-ped" x="206" y="108" width="7" height="50" rx="1.5" />
    ${ceo ? '<rect class="plaque" x="128" y="112" width="48" height="12" rx="2" /><text class="plaque-t" x="152" y="121" text-anchor="middle">CEO</text>' : ''}
    <rect class="mon" x="90" y="58" width="56" height="38" rx="3" /><rect class="mon" x="115" y="95" width="6" height="5" /><rect class="mon" x="107" y="98.5" width="22" height="2" rx="1" />
    <svg x="93" y="61" width="50" height="31" viewBox="0 0 50 31" class="scr">
      <rect class="scr-bg" width="50" height="31" />
      <g class="scr-code"><g class="code-roll">${[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((i) => `<rect x="${4 + ((seed >> i) & 3) * 3}" y="${3 + i * 5}" width="${10 + ((seed >> (i + 3)) & 15) * 1.8}" height="2" rx="1" class="cl${i % 3}" />`).join('')}</g></g>
      <g class="scr-game"><rect class="pad pad-l" x="3" y="11" width="2" height="9" /><rect class="pad pad-r" x="45" y="11" width="2" height="9" /><circle class="ball" cx="25" cy="15" r="1.8" /><line x1="25" y1="2" x2="25" y2="29" class="net" /></g>
      <g class="scr-idle"><circle cx="25" cy="15.5" r="6" class="logo" /></g>
    </svg>
    <rect class="kb" x="150" y="97" width="26" height="3" rx="1" />
    <rect class="mug" x="196" y="91" width="8" height="9" rx="1.5" /><path class="mug-h" d="M204 93 q4 2 0 5" />
    <g class="fig fig-file"><rect x="226" y="134" width="6" height="24" rx="2" class="leg" /><rect x="236" y="134" width="6" height="24" rx="2" class="leg" />
      <rect x="222" y="100" width="24" height="36" rx="8" fill="${c}" />${head(234, 77, 0.85, c)}
      <g class="file-arm"><path class="arm" d="M243 108 L255 100" stroke="${c}" /><rect class="fd-held" x="254" y="86" width="5" height="15" rx="0.8" /></g></g>
    <g class="fig fig-water"><rect x="42" y="134" width="6" height="24" rx="2" class="leg" /><rect x="52" y="134" width="6" height="24" rx="2" class="leg" />
      <rect x="38" y="100" width="24" height="36" rx="8" fill="${c}" />${head(50, 77, 0.85, c)}
      <g class="can"><path class="arm" d="M40 107 L34 96" stroke="${c}" /><rect class="can-b" x="22" y="86" width="16" height="12" rx="2.5" /><path class="can-h" d="M37 88 q5 3 0 8" /><path class="can-s" d="M23 89 L12 82" />
        <g class="drops"><circle cx="12" cy="86" r="1.4" /><circle cx="14" cy="92" r="1.4" /><circle cx="11" cy="98" r="1.4" /></g></g></g>
  </svg>`;
}

export function drawOffice(box, { org, now, me, canAct, ai, onTeam, onChanged }) {
  let data = now;
  const stats = () => (data && data.desks) || {};
  // laid out like the company chart: C-EXA on top looking down at her teams; each team's lead under its
  // team, the agents in the column below, looking up
  const tile = (m, ceo, off, lead) => `<button class="of-desk${ceo ? ' of-ceo' : ''}${lead ? ' of-lead' : ''}" data-agent="${esc(m.name)}" data-look="${ceo ? 'down' : 'up'}" data-s="${off ? 'off' : 'idle'}" data-act="${IDLE[hash(m.name) % IDLE.length]}">
      ${desk(m, stats()[m.name], ROLE_C[ceo ? 'ceo' : m.role] || '#6b7a72', ceo)}
      <span class="of-name"><b>${esc(m.name)}</b> <span class="muted">${esc(m.role_label || '')}${lead && m.role !== 'lead' ? ' · lead' : ''}</span></span>
      <span class="of-doing small"></span></button>`;
  const col = (t) => {
    const ms = t.members.filter((m) => m.enabled);
    const lead = ms.find((m) => m.lead), rest = ms.filter((m) => !m.lead), off = t.members.length - ms.length;
    const chain = [lead ? tile(lead, false, !t.enabled, true) : '', ...rest.map((m) => tile(m, false, !t.enabled, false))].filter(Boolean).join('<i class="oc-v"></i>');
    return `<div class="oc-col ${t.enabled ? '' : 'of-dark'}"><button class="oc-box oc-team" data-team="${esc(t.key)}"><b>${esc(t.name)}</b><span>${esc(t.word)}${t.enabled ? '' : ' · off'}</span></button>
      ${chain ? `<i class="oc-v"></i>${chain}` : ''}${off ? `<span class="small muted oc-more">+${off} switched off</span>` : ''}</div>`;
  };
  const head = me && me.role === 'owner' ? me.name : 'The owner';
  box.innerHTML = `<div class="card"><div class="bd oc-wrap"><div class="oc of-oc">
      <button class="oc-box oc-top" disabled><b>${esc(head)}</b><span>Owner · decides, releases, promotes</span></button><i class="oc-v"></i>
      ${tile({ name: ai, role: 'ceo', role_label: 'CEO · hands out the work' }, true, false, false)}<i class="oc-v"></i>
      <div class="oc-row">${org.teams.map(col).join('')}</div></div>
    <p class="small muted oc-key">At the computer: working · standing up: needs you (answer on Now) · otherwise keeping busy · the plant grows with the work done this month · a folder in the rack for every job · click anyone</p></div></div>`;

  const desks = [...box.querySelectorAll('.of-desk')];
  function paint() {
    const active = (data && data.active) || [], waiting = (data && data.waiting) || [];
    for (const d of desks) {
      const name = d.dataset.agent;
      if (d.dataset.s === 'off') continue;
      const working = active.find((w) => w.to === name && w.status === 'working');
      const asks = waiting.find((w) => (w.kind === 'task' ? w.to : w.from) === name);
      const next = active.find((w) => w.to === name && w.status === 'open');
      const held = active.find((w) => w.to === name && w.status === 'held');
      const s = asks ? 'waiting' : working ? 'working' : 'idle';
      d.dataset.s = s;
      const st = stats()[name];
      const wilt = st && st.total && (!st.last_done || Date.now() - new Date(st.last_done).getTime() > 14 * 864e5) && s !== 'working';
      d.classList.toggle('wilt', !!wilt);
      // 1.0.103: an AP or Support bot on a document shows it, with how long ("Working on: INV-2758.pdf · reading 0:42")
      const doc = s === 'working' && data && data.docs ? data.docs[name] : null, doing = d.querySelector('.of-doing');
      const key = doc ? [doc.label, doc.step, doc.since, doc.stuck ? 1 : 0].join('|') : '';
      if (doc) {
        if (doing.dataset.key !== key) { doing.dataset.key = key; doing.innerHTML = `Working on: ${esc(doc.label)} ${busyChipHtml(doc, true)}`; }
        d.title = `${name} — ${doc.label}`;
        continue;
      }
      doing.dataset.key = '';
      doing.textContent = s === 'working' ? `Working on: ${working.title}` : s === 'waiting' ? `Needs you: ${asks.question || asks.title}`
        : held ? 'Waiting on a colleague' : next ? `Next up: ${next.title}` : IDLE_WORD[d.dataset.act] || '';
      d.title = doing.textContent;
    }
  }
  paint();

  // the idle ones keep busy: every few seconds one of them moves on to something else
  const idleTimer = setInterval(() => {
    if (!box.isConnected) { clearInterval(idleTimer); return; }
    if (document.visibilityState !== 'visible') return;
    const idle = desks.filter((d) => d.dataset.s === 'idle');
    if (!idle.length) return;
    const d = idle[Math.floor(Math.random() * idle.length)];
    const choices = IDLE.filter((a) => a !== d.dataset.act);
    d.dataset.act = choices[Math.floor(Math.random() * choices.length)];
    paint();
  }, 3500);
  // live: who works, who waits — without redrawing the room
  const liveTimer = setInterval(async () => {
    if (!box.isConnected) { clearInterval(liveTimer); return; }
    if (document.visibilityState !== 'visible') return;
    try { data = await api.get('/api/cexa/now', { quiet: true }); paint(); } catch { /* next time */ }
  }, 15000);

  box.querySelectorAll('[data-team]').forEach((b) => (b.onclick = () => onTeam(b.dataset.team)));
  desks.forEach((d) => (d.onclick = (e) => {
    if (e.target.closest('[data-plant]') && d.dataset.s === 'idle') { d.dataset.act = 'water'; paint(); return; } // "water your plant"
    openAgentCard(d.dataset.agent, { org, now: data, canAct, ai, onTeam, onChanged });
  }));
}
