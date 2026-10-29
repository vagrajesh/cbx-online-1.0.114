// C-EXA as developer: Noticed → Building → For your sign-off → Shipped. Owner only; shipping needs a person.
import { api } from '../api.js';
import { esc, toast, pill, modal, confirmBox, fmtDate, md } from '../ui.js';

const POLL_MS = 10000;
const SRC = { requests: 'From requests', audit: 'From the audit trail', errors: 'From errors', corrections: 'From corrections', owner: 'From you' };

export const diffHtml = (text, limit = 40) => {
  const lines = String(text || '').split('\n');
  const shown = lines.slice(0, limit).map((l) => (l.startsWith('+') && !l.startsWith('+++') ? `<span class="add">${esc(l)}</span>` : l.startsWith('-') && !l.startsWith('---') ? `<span class="del">${esc(l)}</span>` : esc(l))).join('\n');
  return `<div class="diff">${shown}${lines.length > limit ? `\n… ${lines.length - limit} more lines` : ''}</div>`;
};
const testsPill = (p) => {
  const t = p.tests || {};
  const rounds = (t.rounds || []).length;
  if (t.ok) return pill(`${t.passed} tests pass${rounds > 1 ? ` · green after ${rounds} rounds` : ''}`, 'good');
  if (t.failed) return pill(`${t.failed} failed${rounds > 1 ? ` after ${rounds} rounds` : ''}`, 'crit') + ((t.failures || []).length ? `<span class="small mono" style="color:var(--crit);display:block;margin-top:4px;max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc((t.failures || []).join('\n'))}">${esc(t.failures[0])}</span>` : '');
  return pill('Building', 'info', true);
};

export default {
  title: 'C-EXA as developer',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner') { root.innerHTML = '<div class="head"><div><h1>C-EXA as developer</h1><p>This page is for the owner.</p></div></div>'; return; }
    let data;
    let timer = null;
    const dispose = () => { if (timer) clearInterval(timer); timer = null; };
    async function load() { data = await api.get('/api/cexa/proposals'); }
    try { await load(); } catch (e) { root.innerHTML = `<div class="head"><div><h1>C-EXA as developer</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return dispose; }

    const body = (p) => `<span class="small" style="color:var(--ink-2)">${esc(p.body.length > 220 ? p.body.slice(0, 220) + '…' : p.body)}</span>`;

    const card = (p) => {
      const src = `<span class="src">${esc(SRC[p.evidence_kind] || 'Noticed')}${p.status === 'building' || p.status === 'signoff' ? ` · you approved ${fmtDate(p.updated_at)}` : ''}</span>`;
      if (p.status === 'noticed') return `<div class="tcard">${src}<b>${esc(p.title)}</b>${body(p)}<div class="acts"><button class="btn sm primary" data-go="${p.id}">Go ahead</button><button class="btn sm ghost" data-no="${p.id}">Not now</button><button class="btn sm ghost" data-why="${p.id}">Why?</button><button class="btn sm ghost" data-del="${p.id}" style="color:var(--crit)" title="Remove this card">Delete</button></div></div>`;
      if (p.status === 'building') return `<div class="tcard">${src}<b>${esc(p.title)}</b>${body(p)}${p.diff ? diffHtml(p.diff, 6) : ''}<div class="m">${testsPill(p)}${p.tests && p.tests.new ? `<span>${p.tests.new} new tests</span>` : ''}<span>${esc(p.sandbox || '')}</span></div><div class="acts">${p.diff ? `<button class="btn sm" data-diff="${p.id}">Open diff</button>` : ''}<button class="btn sm ghost" data-no="${p.id}">Stop</button></div></div>`;
      if (p.status === 'signoff') return `<div class="tcard" style="border-color:var(--accent)">${src}<b>${esc(p.title)}</b>${body(p)}${p.diff ? diffHtml(p.diff, 6) : ''}<div class="m">${testsPill(p)}${p.tests && p.tests.new ? `<span>${p.tests.new} new tests</span>` : ''}<span>${esc(p.sandbox || '')}</span>${p.version ? pill(`v${p.version.tag} · ${p.version.status}`, p.version.status === 'sandbox' ? 'good' : p.version.status === 'failed' ? 'crit' : 'neutral') : ''}</div><div class="acts"><button class="btn sm primary" data-ship="${p.id}">Promote to production</button><button class="btn sm" data-diff="${p.id}">Open diff</button><button class="btn sm ghost" data-why="${p.id}">Ask C-EXA why</button><button class="btn sm ghost" data-no="${p.id}">Not now</button></div></div>`;
      return `<div class="tcard"><b>${esc(p.title)}</b><div class="m"><span>${fmtDate(p.updated_at)}</span>${p.version ? `<span class="mono">v${esc(p.version.tag)}</span>` : ''}<span>${esc(SRC[p.evidence_kind] || '').toLowerCase()}</span></div></div>`;
    };

    async function drawFixes() {
      const el = root.querySelector('#fixes-body'); if (!el) return;
      let d; try { d = await api.get('/api/cexa/fixes'); } catch (e) { el.innerHTML = `<div class="bd err-box">${esc(e.detail || e.message)}</div>`; return; }
      const FP = { open: ['Open', 'crit'], investigating: ['Investigating', 'info', true], fixed: ['Fixed', 'good'], code_pending: ['Proposed · needs your go', 'warn'], shipped: ['Shipped', 'good'], wont_fix: ["Won't fix", 'neutral'] };
      const PS = { building: ['Building in sandbox', 'info', true], signoff: ['Tested · needs sign-off', 'good'], shipped: ['Shipped', 'good'], declined: ['Not now', 'neutral'] };
      const fixPill = (f) => { const [t, k, r] = (f.status === 'code_pending' && PS[f.proposal_status]) || FP[f.status] || [f.status, 'neutral']; return pill(t, k, r); };
      if (!d.fixes.length) { el.innerHTML = '<div class="bd small muted">Nothing reported yet. Tell C-EXA in the drawer when something is wrong — she investigates, fixes what she can, and it shows up here.</div>'; return; }
      el.innerHTML = `<div class="tbl"><table><thead><tr><th>Reported</th><th>Area</th><th>Status</th><th>What C-EXA did</th><th>Your verdict</th></tr></thead><tbody>${d.fixes.map((f) => {
        const acts = (f.actions || []).map((a) => `<div class="small"><span class="mono" style="color:var(--ai)">${esc(a.tool)}</span> ${esc(Object.values(a.args || {}).filter(Boolean).join(', '))}<div class="muted" style="white-space:pre-wrap">${esc((a.result || '').split('\n').slice(-1)[0].slice(0, 160))}</div></div>`).join('');
        return `<tr><td style="max-width:280px"><b>${esc(f.title.slice(0, 90))}${f.title.length > 90 ? '…' : ''}</b><div class="small muted">${esc(f.reported_by)} · ${fmtDate(f.created_at, { rel: true })}</div></td><td>${esc(f.area)}</td><td>${fixPill(f)}${f.proposal_id ? `<div class="small muted">proposal #${f.proposal_id}</div>` : ''}</td><td style="max-width:420px">${acts || '<span class="small muted">—</span>'}${f.resolution ? `<div class="small" style="margin-top:4px"><b>Result:</b> ${esc(f.resolution.slice(0, 200))}</div>` : ''}</td><td>${f.feedback ? `<span class="small">${esc(f.feedback)}</span><br>` : ''}<div class="row" style="gap:4px;margin-top:4px"><button class="btn sm" data-fb="${f.id}" data-v="works">Works</button><button class="btn sm ghost" data-fb="${f.id}" data-v="still_broken">Still broken</button></div></td></tr>`;
      }).join('')}</tbody></table></div>`;
      el.querySelectorAll('[data-fb]').forEach((b) => (b.onclick = async () => {
        let note = '';
        if (b.dataset.v === 'still_broken') { note = prompt('What is still wrong? (C-EXA reads this)') || ''; }
        await api.post(`/api/cexa/fixes/${b.dataset.fb}/feedback`, { verdict: b.dataset.v, note });
        toast(b.dataset.v === 'works' ? 'Thanks — marked as working' : 'Reopened for C-EXA'); drawFixes();
      }));
    }

    function draw() {
      const cols = [['noticed', 'Noticed'], ['building', 'Building'], ['signoff', 'For your sign-off'], ['shipped', 'Shipped this month']];
      root.innerHTML = `
      <div class="head"><div><h1>C-EXA as developer</h1><p>She proposes, builds in a sandbox, runs the tests and brings you the result. Nothing ships without you.</p></div>
        <div class="row">${pill(data.sandbox_label, 'ai', !!data.branch)}<button class="btn sm" id="look" title="She looks for problems and ideas right away instead of waiting for her next round">Check now</button></div></div>
      <div class="board">${cols.map(([k, l]) => `<div class="col"><h3>${l} <span>${k === 'shipped' ? data.shipped_total : data[k].length}</span></h3>${data[k].map(card).join('') || `<div class="tcard muted" style="text-align:center;border-style:dashed">${{ noticed: 'Nothing noticed', building: 'Nothing building', signoff: 'Nothing waiting on you', shipped: 'Nothing shipped yet' }[k]}</div>`}${k === 'shipped' && data.shipped_total > data.shipped.length ? `<div class="tcard muted" style="text-align:center;border-style:dashed">+ ${data.shipped_total - data.shipped.length} earlier</div>` : ''}${k === 'noticed' && data.declined ? `<div class="small muted" style="padding:4px">${data.declined} said not now · not re-proposed for 30 days</div>` : ''}</div>`).join('')}</div>
      <div class="card" style="margin-top:16px" id="fixes-card"><div class="hd"><h2>Bugs &amp; fixes</h2><span class="small muted">what people reported, what C-EXA did about it — tell her if it worked</span></div><div id="fixes-body"><div class="loading"><span class="spin"></span></div></div></div>
      <div class="card" style="margin-top:16px"><div class="hd"><h2>How this works</h2><span class="small muted">she works on a copy, never on the live app</span></div>
        <div class="bd howto">
          <div><div class="eyebrow">1 · She watches</div>Requests, the audit trail, failed runs, user corrections. Read-only. She never edits the live app.</div>
          <div><div class="eyebrow">2 · She proposes</div>Every idea lands in <b>Noticed</b> with the evidence. You say go or not now. Silence means not now.</div>
          <div><div class="eyebrow">3 · She builds in a sandbox</div>A copy of the app on its own branch. The tests must pass before you see it.</div>
          <div><div class="eyebrow">4 · You ship</div>One button. Rollback is one button too, on Versions. Everything she did is in the audit trail under her name.</div>
        </div></div>`;
      drawFixes();
      root.querySelector('#look').onclick = async () => { try { const r = await api.post('/api/cexa/proposals/notice'); toast(r.created.length ? `C-EXA noticed ${r.created.length} new thing${r.created.length === 1 ? '' : 's'}` : 'Nothing new to notice'); await load(); draw(); } catch (e) { toast(e.detail, 'err'); } };
      // the board polls every 10 s, so a card can be gone by the time its button is clicked
      const find = (id) => ['noticed', 'building', 'signoff', 'shipped'].flatMap((k) => data[k] || []).find((p) => p.id === +id);
      const withCard = (id, fn) => { const p = find(id); if (!p) { toast('That card has moved on — refreshing.'); refresh(); return; } return fn(p); };
      root.querySelectorAll('[data-go]').forEach((b) => (b.onclick = async () => { b.disabled = true; try { await api.post(`/api/cexa/proposals/${b.dataset.go}/go`); toast('C-EXA is building it in a sandbox · logged'); await load(); draw(); ctx.refreshCounts && ctx.refreshCounts(); } catch (e) { toast(e.detail || 'Could not start the build', 'err'); b.disabled = false; } }));
      root.querySelectorAll('[data-no]').forEach((b) => (b.onclick = () => withCard(b.dataset.no, async (p) => { if (!(await confirmBox(`Not now for “${p.title}”? C-EXA won't propose it again for 30 days.`, 'Not now'))) return; try { await api.post(`/api/cexa/proposals/${p.id}/not-now`); toast('Parked · logged'); await load(); draw(); ctx.refreshCounts && ctx.refreshCounts(); } catch (e) { toast(e.detail || 'Could not park it', 'err'); } })));
      root.querySelectorAll('[data-diff]').forEach((b) => (b.onclick = () => withCard(b.dataset.diff, openDiff)));
      root.querySelectorAll('[data-why]').forEach((b) => (b.onclick = () => withCard(b.dataset.why, askWhy)));
      root.querySelectorAll('[data-del]').forEach((b) => (b.onclick = () => withCard(b.dataset.del, (p) => delCard(p))));
      root.querySelectorAll('[data-ship]').forEach((b) => (b.onclick = () => withCard(b.dataset.ship, (p) => ship(p, b))));
    }

    async function delCard(p) {
      if (!(await confirmBox(`Delete “${p.title}” from the board? C-EXA can propose it again later if the evidence returns. This is logged.`, 'Delete'))) return;
      try { await api.del(`/api/cexa/proposals/${p.id}`); toast('Deleted · logged'); await load(); draw(); } catch (e) { toast(e.detail || e.message, 'err'); }
    }

    function openDiff(p) {
      const t = p.tests || {};
      modal({ title: p.title, body: `<div class="row" style="gap:8px">${testsPill(p)}${t.new ? `<span class="small muted">${t.new} new tests</span>` : ''}${t.seconds ? `<span class="small muted">${t.seconds} s</span>` : ''}<span class="small muted mono">${esc(p.branch || '')}</span></div>
        <div><div class="eyebrow">Evidence</div><p class="small" style="white-space:pre-wrap">${esc(p.evidence)}</p></div>
        <div><div class="eyebrow">What C-EXA did</div>${md(p.body)}</div>
        <div><div class="eyebrow">Diff</div>${p.diff ? diffHtml(p.diff, 400) : '<p class="small muted">No diff yet.</p>'}</div>
        ${p.test_output ? `<div><div class="eyebrow">Test output</div><div class="diff">${esc(p.test_output)}</div></div>` : ''}`, footer: '<button class="btn" data-close>Close</button>' });
    }

    async function askWhy(p) {
      const m = modal({ title: 'Ask C-EXA why', body: `<p class="small muted">Why did you propose “${esc(p.title)}”?</p><div class="msg ai thinking-msg"><span class="spin"></span></div>`, footer: '<button class="btn" data-close>Close</button>' });
      try {
        const r = await api.post('/api/cexa/ask', { question: `Why did you propose “${p.title}”, and what's the risk in shipping it?`, route: 'cexa-dev', params: { id: p.id } });
        m.body.innerHTML = `<p class="small muted">Why did you propose “${esc(p.title)}”?</p>${r.thinking ? `<div class="thinking"><div class="eyebrow">Thinking</div>${esc(r.thinking)}</div>` : ''}<div class="msg ai" style="max-width:100%">${md(r.answer)}</div><div><div class="eyebrow">Evidence on the card</div><p class="small" style="white-space:pre-wrap">${esc(p.evidence)}</p></div>`;
      } catch (e) { m.body.innerHTML = `<div class="err-box">${esc(e.detail || 'C-EXA could not answer')}</div>`; }
    }

    async function ship(p, btn) {
      if (!(await confirmBox(`Promote “${p.title}” to production? C-EXA packages the sandbox as a version, the tests run once more, and the live app is replaced (with a backup). The server restarts. This is logged under your name.`, 'Promote'))) return;
      btn.disabled = true; btn.textContent = 'Packaging…';
      try {
        let v = p.version && p.version.status === 'sandbox' ? p.version : null;
        if (!v) { const r = await api.post(`/api/cexa/proposals/${p.id}/ship`); v = r.version; }
        if (v.status !== 'sandbox') { toast(`Version ${v.tag} is ${v.status} — see Versions & sandboxes before promoting.`, 'err'); await load(); draw(); return; }
        const out = await api.post(`/api/versions/${v.id}/promote`);
        toast(`${v.tag} promoted · ${out.restart ? 'restarting' : 'live'} · logged`);
        await load(); draw(); ctx.refreshCounts && ctx.refreshCounts();
      } catch (e) { toast(e.detail || 'Could not promote', 'err'); await load(); draw(); }
    }

    async function refresh() { if (document.querySelector('.modal-bg')) return; try { await load(); draw(); } catch { /* keep last */ } }
    draw();
    timer = setInterval(refresh, POLL_MS);
    return dispose;
  },
};
