// A build ticket: one request and the tool folder that answers it. Working → Testing → Live, mirrored
// onto the request so the requester sees the same word. The building happens in the Builder (pages
// and flows); this page links the two and moves the status on. (Replaced the Node-RED workflow page.)
import { api } from '../api.js';
import { esc, toast, modal, pill, fmtDate, el, md } from '../ui.js';

const STEPS = [['working', 'Working'], ['testing', 'Testing'], ['ready', 'Live']];

export default {
  title: 'Build',
  navHighlight: 'req-queue',
  navRoute: 'req-queue',
  async render(root, ctx) {
    const id = +ctx.params.id;
    let t;
    const load = async () => { t = await api.get(`/api/tickets/${id}`); };
    try { await load(); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Build</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }

    const draw = () => {
      const r = t.request || {};
      const closed = ['cancelled', 'declined'].includes(r.status);
      const at = STEPS.findIndex(([k]) => k === t.status);
      const slug = t.tool_slug || '';
      const live = t.tool;
      root.innerHTML = `
        <div class="head"><div><h1>#${esc(t.number)} ${esc(t.title)}</h1><p>From ${esc((t.requester || {}).name || '')} · ${fmtDate(t.created_at, { rel: true })}${t.cadence ? ' · ' + esc(t.cadence) : ''}</p></div>
          <div class="row" style="gap:8px"><button class="btn sm" data-go="req-detail" data-id="${t.request_id}">Full request</button>${t.interview_id ? `<button class="btn sm" data-go="messages" data-key="interview" data-id="${t.interview_id}">Conversation</button>` : ''}</div></div>
        ${closed ? `<div class="card" style="margin-bottom:14px"><div class="bd err-box">This request was ${esc(r.status)} — the build is closed.</div></div>` : ''}
        <div class="bld-steps">${STEPS.map(([k, l], i) => `<div class="${i < at ? 'done' : i === at ? 'on' : ''}"><b>${i < at ? '✓' : i + 1}. ${l}</b><span class="small muted">${k === 'working' ? 'building it in the Builder' : k === 'testing' ? 'in a sandbox, or being tried' : live ? 'the requester has been told' : 'released and promoted'}</span></div>`).join('')}</div>
        <div class="grid g2">
          <div class="card"><div class="hd"><h2>The tool</h2>${live ? pill('Live', 'good') : slug ? pill(t.draft ? 'Draft in the Builder' : 'Not built yet', t.draft ? 'info' : 'neutral') : ''}</div><div class="bd bld-form">
            <div class="field"><label>Tool folder that answers this request</label>
              <div class="row" style="gap:8px"><input id="slug" type="text" list="tool-list" value="${esc(slug)}" placeholder="e.g. absa-month-end" pattern="[a-z][a-z0-9-]{1,40}" ${closed ? 'disabled' : ''}><datalist id="tool-list">${(t.tools || []).map((x) => `<option value="${esc(x.slug)}">${esc(x.title)}</option>`).join('')}</datalist><button class="btn sm" id="save-slug" ${closed ? 'disabled' : ''}>Save</button></div>
              <span class="small muted">An existing tool, or a new folder name — then build it in the Builder: its pages, and its flows (schedules, approvals, emails).</span></div>
            <div class="row" style="gap:8px;flex-wrap:wrap">
              ${slug ? `<button class="btn primary" id="open-builder">${t.draft || live ? 'Open in the Builder' : 'Start it in the Builder'}</button>` : ''}
              ${live ? `<a class="btn" href="#/tool?t=${esc(live.slug)}&p=${esc(live.home)}">Open the live tool</a>` : ''}
            </div>
          </div></div>
          <div class="card"><div class="hd"><h2>Where it is</h2>${pill(STEPS[Math.max(0, at)][1], t.status === 'ready' ? 'good' : t.status === 'testing' ? 'warn' : 'info')}</div><div class="bd">
            <div class="row" style="gap:8px;flex-wrap:wrap">
              ${!closed && t.status === 'working' ? `<button class="btn" id="to-test" ${slug ? '' : 'disabled title="Say which tool first"'}>Mark as testing</button>` : ''}
              ${!closed && t.status !== 'ready' ? `<button class="btn primary" id="to-live" ${live ? '' : 'disabled title="The tool has to be live first: Builder → Release → Versions → Promote"'}>Mark as live and tell ${esc(((t.requester || {}).name || 'them').split(' ')[0])}</button>` : ''}
              ${!closed && t.status !== 'working' ? '<button class="btn ghost" id="reopen">Reopen</button>' : ''}
            </div>
            ${!live && slug && t.status !== 'ready' ? '<p class="small muted" style="margin-top:8px">Live means released from the Builder and promoted on Versions &amp; sandboxes. Then press Mark as live: the requester gets a message with a link to it.</p>' : ''}
            <div class="small muted" style="margin-top:12px">Working on it: ${(t.workers || []).map((w) => esc(w.name)).join(', ') || 'nobody yet'}${(t.workers || []).some((w) => w.id === ctx.me.id) ? '' : ' · <a href="#" id="join" style="color:var(--accent)">join</a>'}</div>
          </div></div>
        </div>
        <div class="card" style="margin-top:16px"><div class="hd"><h2>What was asked</h2><span class="small muted">${esc(t.wants_back || '')}</span></div><div class="bd">${md(t.summary || '')}</div></div>`;
      root.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => ctx.go(b.dataset.go, b.dataset.key ? { [b.dataset.key]: b.dataset.id } : { id: b.dataset.id })));
      const q = (s) => root.querySelector(s);
      if (q('#save-slug')) q('#save-slug').onclick = async () => { try { t = (await api.patch(`/api/tickets/${id}`, { tool_slug: q('#slug').value.trim() })).ticket; await load(); toast('Saved'); draw(); } catch (e) { toast(e.detail, 'err'); } };
      if (q('#open-builder')) q('#open-builder').onclick = () => ctx.go('builder', t.draft || live ? { t: slug } : { new: slug, title: t.title });
      if (q('#to-test')) q('#to-test').onclick = () => noteThen('Mark as testing', 'What is being tested (optional)', async (note) => { await api.post(`/api/tickets/${id}/test`, { note }); });
      if (q('#to-live')) q('#to-live').onclick = async () => { try { await api.post(`/api/tickets/${id}/publish`); await load(); toast('Live — the requester has been told'); ctx.refreshCounts && ctx.refreshCounts(); draw(); } catch (e) { toast(e.detail, 'err'); } };
      if (q('#reopen')) q('#reopen').onclick = async () => { try { await api.post(`/api/tickets/${id}/reopen`); await load(); draw(); } catch (e) { toast(e.detail, 'err'); } };
      if (q('#join')) q('#join').onclick = async (e) => { e.preventDefault(); await api.post(`/api/tickets/${id}/join`); await load(); draw(); };
    };
    const noteThen = (title, label, fn) => {
      const body = el(`<div class="field"><label>${esc(label)}</label><input id="n" type="text"></div>`);
      const m = modal({ title, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">OK</button>' });
      m.root.querySelector('#ok').onclick = async () => { try { await fn(body.querySelector('#n').value); m.close(); await load(); draw(); } catch (e) { toast(e.detail, 'err'); } };
    };
    draw();
  },
};
