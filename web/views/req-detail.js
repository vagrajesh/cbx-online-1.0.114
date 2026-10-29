import { api } from '../api.js';
import { esc, toast, modal, statusPill, fmtDate, el, cancelRequestDialog } from '../ui.js';
import { impBar } from './req-queue.js';

const heShe = (name) => name.split(' ')[0];

export default {
  title: 'Request',
  navRoute: 'req-queue',
  async render(root, ctx) {
    let r;
    try { r = await api.get(`/api/requests/${ctx.params.id}`); }
    catch (e) { root.innerHTML = `<div class="head"><div><h1>Request</h1><p>${esc(e.detail || 'That request could not be opened.')}</p></div><button class="btn sm" id="back">Back to requests</button></div>`; root.querySelector('#back').onclick = () => ctx.go('req-queue'); return; }
    const first = heShe(r.requester.name);
    const conf = r.confidence === null || r.confidence === undefined ? null : Math.round(r.confidence * 100);
    let mode = r.recommend || 'axl';
    let assignee = (r.admins || []).find((a) => a.id === ctx.me.id) ? ctx.me.id : ((r.admins || [])[0] || {}).id;
    const decided = r.status !== 'new';

    const related = (r.related || []).map((x) => `<li><div class="t"><b>#${x.number} ${esc(x.title)}</b><span>shares: ${esc(x.shared.join(', '))}</span></div>${statusPill(x.status)}</li>`).join('') || '<li class="empty">Nothing similar among live requests.</li>';
    const verdict = conf === null ? '' : conf >= 75 ? "Yes — I'm confident." : conf >= 60 ? 'Probably, with a person checking.' : 'Not on my own — a person should lead.';

    root.innerHTML = `
    <div class="head"><div><h1>${esc(r.title)}</h1><p>Request #${r.number} · ${esc(r.requester.name)}${r.requester.team ? ' · ' + esc(r.requester.team) : ''} · sent ${fmtDate(r.created_at, { time: true })}</p></div><div class="row" style="gap:8px">${statusPill(r.status)}${!['declined', 'cancelled'].includes(r.status) ? '<button class="btn sm ghost" id="cancel-req" style="color:var(--crit)">Cancel request</button>' : ''}<button class="btn sm" id="back">Back to requests</button></div></div>
    <div class="grid g21">
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card"><div class="hd"><h2>Part 1 · what ${esc(first)} wrote</h2><span class="small muted">unedited</span></div>
          <div class="bd" style="display:grid;gap:14px;font-size:13.5px">
            <div><div class="eyebrow" style="margin-bottom:4px">The problem</div>${esc(r.problem)}</div>
            <div><div class="eyebrow" style="margin-bottom:4px">The impact</div>${esc(r.impact) || '<span class="muted">—</span>'}</div>
            <div><div class="eyebrow" style="margin-bottom:4px">How they'd like it fixed</div>${esc(r.wish) || '<span class="muted">—</span>'}</div>
            ${r.attachment_id ? `<div><a class="btn sm" href="/api/warehouse/files/${r.attachment_id}/download">Open the example they attached</a></div>` : ''}
          </div></div>
        <div class="card"><div class="hd"><h2>C-EXA's assessment</h2><span class="small mono muted">${r.assessed_at ? 'read ' + fmtDate(r.assessed_at, { time: true }) : 'not read yet'}</span></div>
          <div class="bd assess">
            <div><div class="eyebrow" style="margin-bottom:8px">Importance</div><div class="row" style="gap:10px"><span style="display:inline-block;transform:scale(1.3);margin:0 6px">${impBar(r.importance).replace(/ <span class="small">.*$/, '')}</span><b>${r.importance ? r.importance[0].toUpperCase() + r.importance.slice(1) : '—'}</b></div>
              <p class="small" style="margin-top:10px;color:var(--ink-2)">${esc(r.importance_reason)}</p></div>
            <div><div class="eyebrow" style="margin-bottom:8px">Can I do it myself?</div><div class="gauge"><div class="ring ${conf !== null && conf < 60 ? 'low' : ''}" style="--p:${conf ?? 0}"><i>${conf === null ? '—' : conf + '%'}</i></div><div style="font-size:13px"><b>${verdict}</b><div class="small muted">${esc(r.confidence_reason)}</div></div></div>
              <p class="small" style="margin-top:10px;color:var(--ink-2)"><b>What I'd still need to ask ${esc(first)}:</b> ${esc(r.would_ask)} <b>What I won't guess:</b> ${esc(r.wont_guess)}</p></div>
          </div></div>
        ${r.rules && r.rules.length ? `<div class="card"><div class="hd"><h2>Rules so far</h2><span class="small muted"><span style="color:var(--good);font-weight:600">Green</span> = from ${esc(first)}</span></div><div class="bd rules">${r.rules.map((x) => `<div class="rule ${x.green ? 'user' : ''}"><span class="k">R${x.ordinal}</span><span class="t">${esc(x.text)}</span>${x.green ? `<span class="tag">${esc(first)}${x.changed_by_user && x.source !== 'user' ? ' · changed' : ''}</span>` : ''}</div>`).join('')}</div></div>` : ''}
      </div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card" ${decided ? '' : 'style="border-color:var(--accent)"'}><div class="hd"><h2>${decided ? 'Decision' : 'Approve & choose the interview'}</h2></div>
          <div class="bd" style="display:flex;flex-direction:column;gap:10px" id="decide">
          ${decided ? `<p class="small" style="color:var(--ink-2)">${r.status === 'declined' ? `Declined${r.decline_note ? ': ' + esc(r.decline_note) : ''}.` : r.status === 'cancelled' ? `Cancelled${r.decline_note ? ': ' + esc(r.decline_note) : ''}.` : r.status === 'sent' ? 'C-EXA is still reading this request — it can be approved once it shows as New. You can decline it now if needed.' : `Approved ${fmtDate(r.approved_at, { time: true })} · ${r.interview_mode === 'axl' ? 'C-EXA is interviewing' : 'a person is interviewing'}${r.assignee ? ' · assigned to ' + esc(r.assignee.name) : ''}.`}</p>
            <div class="row" style="gap:8px;flex-wrap:wrap">${r.interview_id && r.interview_mode === 'human' && r.status === 'interview' ? `<button class="btn sm primary" data-go="meeting" data-id="${r.interview_id}">Join meeting</button>` : ''}${r.interview_id ? `<button class="btn sm" data-go="messages" data-key="interview" data-id="${r.interview_id}">Open conversation</button>` : ''}${r.ticket_id ? `<button class="btn sm primary" data-go="build" data-id="${r.ticket_id}">Open build</button>` : ''}${r.status === 'sent' ? '<button class="btn ghost sm" id="decline">Decline with a note</button>' : ''}</div>`
          : `<button class="opt" data-mode="axl" aria-pressed="${mode === 'axl'}" style="width:100%"><b>C-EXA interviews ${esc(first)}</b><span>Opens a message thread in-app. C-EXA asks until ${esc(first)} says they're happy, then writes the ticket. You get the summary.${conf !== null && conf >= 75 ? ` Recommended at ${conf}%.` : ''}</span></button>
            <button class="opt" data-mode="human" aria-pressed="${mode === 'human'}" style="width:100%"><b>A person interviews ${esc(first)}</b><span>Book a meeting. The admin types the rules; C-EXA listens and suggests questions to ask.${conf !== null && conf < 75 ? ' Recommended here.' : ''}</span></button>
            <div class="field"><label>Assign to</label><select id="assignee">${(r.admins || []).map((a) => `<option value="${a.id}" ${a.id === assignee ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></div>
            <button class="btn primary" style="justify-content:center" id="approve">Approve</button>
            <button class="btn ghost sm" style="justify-content:center" id="decline">Decline with a note</button>`}
          </div></div>
        <div class="card"><div class="hd"><h2>Related</h2></div><ul class="list">${related}</ul></div>
      </div>
    </div>`;
    root.querySelector('#back').onclick = () => ctx.go('req-queue');
    root.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => ctx.go(b.dataset.go, { [b.dataset.key || 'id']: b.dataset.id })));
    const cr = root.querySelector('#cancel-req'); if (cr) cr.onclick = async () => { if (await cancelRequestDialog(api, r)) { ctx.refreshCounts && ctx.refreshCounts(); ctx.go('req-queue'); } };
    const dcl = root.querySelector('#decline'); if (dcl) dcl.onclick = () => {
      const body = el(`<div class="field"><label>Note to ${esc(first)}</label><textarea id="note" style="min-height:90px" placeholder="Plain words — this goes to ${esc(first)} as a message from you."></textarea></div>`);
      const m = modal({ title: 'Decline this request', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Decline & send note</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        try { await api.post(`/api/requests/${r.id}/decline`, { note: body.querySelector('#note').value }); m.close(); toast(`Declined with a note to ${first} · logged`); ctx.refreshCounts && ctx.refreshCounts(); ctx.go('req-queue'); }
        catch (e) { toast(e.detail, 'err'); }
      };
    };
    if (decided) return;
    root.querySelectorAll('.opt').forEach((b) => (b.onclick = () => { mode = b.dataset.mode; root.querySelectorAll('.opt').forEach((o) => o.setAttribute('aria-pressed', o.dataset.mode === mode)); }));
    root.querySelector('#approve').onclick = async () => {
      const btn = root.querySelector('#approve'); btn.disabled = true;
      try {
        const out = await api.post(`/api/requests/${r.id}/approve`, { mode, assignee_id: +root.querySelector('#assignee').value });
        toast(mode === 'axl' ? `Approved · C-EXA has messaged ${first} · status → Interview · logged` : `Approved · ${first} will be interviewed in a meeting · logged`);
        ctx.refreshCounts && ctx.refreshCounts();
        if (mode === 'human') ctx.go('meeting', { id: out.interview.id }); else ctx.go('req-queue');
      } catch (e) { toast(e.detail, 'err'); btn.disabled = false; }
    };
  },
};
