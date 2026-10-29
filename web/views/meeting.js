import { api } from '../api.js';
import { esc, toast, confirmBox, pill, el } from '../ui.js';

export default {
  title: 'Interview',
  navRoute: 'req-queue',
  async render(root, ctx) {
    let iv, req;
    try { ({ interview: iv, request: req } = await api.get(`/api/interviews/${ctx.params.id}`)); }
    catch (e) { root.innerHTML = `<div class="head"><div><h1>Interview</h1><p>${esc(e.detail || 'That interview could not be opened.')}</p></div><button class="btn sm" id="back">Back</button></div>`; root.querySelector('#back').onclick = () => ctx.go('req-queue'); return; }
    const first = req.requester.name.split(' ')[0];
    const lead = iv.lead ? iv.lead.name.split(' ')[0] : 'You';
    let suggestion = null;
    let newSource = 'user';
    const done = iv.status === 'done';
    const closed = ['cancelled', 'declined'].includes(req.status);

    root.innerHTML = `
    <div class="head"><div><h1>${esc(req.title)}</h1><p>Request #${req.number} · interview with ${esc(req.requester.name)} · ${esc(lead)} leading · C-EXA listening. Type what ${esc(first)} says as rules; C-EXA will tell you what to ask next.</p></div>
      <div class="row" style="gap:8px">${closed ? pill(req.status === 'declined' ? 'Declined' : 'Cancelled', 'neutral') : done ? pill('Done · ticket created', 'good') : pill('C-EXA listening', 'ai', true)}<button class="btn sm" id="back">Back</button></div></div>
    <div class="meet">
      <div class="card">
        <div class="hd"><h2>Rules so far</h2><span class="small muted"><span style="color:var(--good);font-weight:600">Green</span> = came from ${esc(first)}, or ${esc(first)} changed it</span></div>
        <div class="bd rules" id="rules"></div>
        <div class="bd" style="border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;gap:12px">
          <span class="small muted">Rules stay editable after today. Anything ${esc(first)} adds or changes later turns green too, and the build ticket shows the change.</span>
          ${done ? (iv.ticket_id ? `<button class="btn primary sm" id="open-flow">Open build</button>` : `<span class="small muted">${closed ? `Request ${esc(req.status)} — no ticket.` : 'No ticket.'}</span>`) : `<button class="btn primary sm" id="finish">Finish &amp; create ticket</button>`}
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card"><div class="hd"><h2>C-EXA suggests you ask</h2><button class="btn sm ghost" id="refresh">Refresh</button></div><div class="bd listen" id="listen"><div class="small muted">Thinking…</div></div></div>
        <div class="card"><div class="hd"><h2>My confidence</h2></div><div class="bd" id="conf"></div></div>
        <div class="card"><div class="hd"><h2>What ${esc(first)} wrote</h2></div><div class="bd small" style="display:grid;gap:10px;color:var(--ink-2)"><div><div class="eyebrow">Problem</div>${esc(req.problem)}</div><div><div class="eyebrow">Impact</div>${esc(req.impact) || '—'}</div><div><div class="eyebrow">Wants</div>${esc(req.wish) || '—'}</div></div></div>
      </div>
    </div>`;
    root.querySelector('#back').onclick = () => ctx.go('req-queue');
    const of = root.querySelector('#open-flow'); if (of) of.onclick = () => ctx.go('build', { id: iv.ticket_id });
    root.querySelector('#refresh').onclick = () => suggest(true);

    const rulesBox = root.querySelector('#rules');
    function drawRules() {
      const rules = iv.rules || [];
      rulesBox.innerHTML = rules.map((x) => `<div class="rule ${x.green ? 'user' : ''}" data-id="${x.id}"><span class="k">R${x.ordinal}</span><span class="t" contenteditable="${done ? 'false' : 'true'}" spellcheck="false">${esc(x.text)}</span>${x.green ? `<span class="tag">${esc(first)}${x.source !== 'user' && x.changed_by_user ? ' · changed' : ''}</span>` : ''}${done ? '' : `<span class="x" data-del="${x.id}" title="Remove" style="cursor:pointer">×</span>`}</div>`).join('')
        + (done ? '' : `<div class="rule add" id="add"><span class="k">R${rules.length + 1}</span><span class="t">Add what ${esc(first)} says next…</span></div>`);
      rulesBox.querySelectorAll('.t[contenteditable="true"]').forEach((t) => {
        const id = t.closest('.rule').dataset.id; const orig = t.textContent;
        t.onblur = async () => { const v = t.textContent.trim(); if (v === orig.trim() || !v) { t.textContent = orig; return; } try { await api.patch(`/api/requests/${req.id}/rules/${id}`, { text: v }); toast('Rule updated · logged'); await reload(); } catch (e) { toast(e.detail, 'err'); } };
        t.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); t.blur(); } };
      });
      rulesBox.querySelectorAll('[data-del]').forEach((x) => (x.onclick = async () => { if (!(await confirmBox('Remove this rule?', 'Remove'))) return; try { await api.del(`/api/requests/${req.id}/rules/${x.dataset.del}`); toast('Rule removed · logged'); await reload(); } catch (e) { toast(e.detail || 'Could not remove', 'err'); } }));
      const add = rulesBox.querySelector('#add'); if (add) add.onclick = openAdd;
    }
    function openAdd() {
      const add = rulesBox.querySelector('#add'); if (!add) return;
      const row = el(`<div class="rule" style="flex-wrap:wrap"><span class="k">R${(iv.rules || []).length + 1}</span><input type="text" id="new-rule" placeholder="What ${esc(first)} just said, as a rule" style="flex:1;min-width:200px">
        <span class="row" style="gap:4px"><button class="btn sm ${newSource === 'user' ? 'primary' : ''}" data-src="user">${esc(first)} said it</button><button class="btn sm ${newSource === 'admin' ? 'primary' : ''}" data-src="admin">${esc(lead === 'You' ? 'I' : lead)} added it</button></span>
        <button class="btn sm primary" id="save-rule">Add</button></div>`);
      add.replaceWith(row);
      const inp = row.querySelector('#new-rule'); inp.focus();
      row.querySelectorAll('[data-src]').forEach((b) => (b.onclick = () => { newSource = b.dataset.src; row.querySelectorAll('[data-src]').forEach((o) => o.classList.toggle('primary', o.dataset.src === newSource)); inp.focus(); }));
      const save = async () => { const v = inp.value.trim(); if (!v) return; try { await api.post(`/api/requests/${req.id}/rules`, { text: v, source: newSource }); toast(`R${(iv.rules || []).length + 1} added · logged`); await reload(); suggest(true); } catch (e) { toast(e.detail, 'err'); } };
      row.querySelector('#save-rule').onclick = save; inp.onkeydown = (e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') drawRules(); };
    }
    async function reload() { ({ interview: iv, request: req } = await api.get(`/api/interviews/${iv.id}`)); drawRules(); }

    async function suggest(force) {
      const box = root.querySelector('#listen');
      if (!force && suggestion) return drawSuggest();
      box.innerHTML = '<div class="small muted"><span class="spin"></span> C-EXA is thinking…</div>';
      try { suggestion = await api.get(`/api/interviews/${iv.id}/suggest`); } catch (e) { box.innerHTML = `<div class="small muted">${esc(e.detail)}</div>`; return; }
      drawSuggest();
    }
    function drawSuggest() {
      const box = root.querySelector('#listen');
      box.innerHTML = suggestion.questions.map((q, i) => `<div class="q" data-i="${i}"><b>${esc(q.why)}</b>“${esc(q.text)}”<div class="acts"><button class="btn sm" data-asked="${i}">Asked</button><button class="btn sm ghost" data-skip="${i}">Skip</button></div></div>`).join('') || '<div class="small muted">Nothing to add — I think you have what you need.</div>';
      box.querySelectorAll('[data-asked],[data-skip]').forEach((b) => (b.onclick = async () => {
        const i = +(b.dataset.asked ?? b.dataset.skip); const skipped = b.dataset.skip !== undefined;
        try { await api.post(`/api/interviews/${iv.id}/asked`, { text: suggestion.questions[i].text, skipped }); } catch (e) { toast(e.detail || 'Could not save that', 'err'); return; }
        b.closest('.q').remove(); if (!skipped) { toast('Marked asked — add the answer as a rule'); openAdd(); }
      }));
      const c = Math.round((suggestion.confidence || 0) * 100); const was = suggestion.was === null || suggestion.was === undefined ? null : Math.round(suggestion.was * 100);
      root.querySelector('#conf').innerHTML = `<div class="gauge"><div class="ring ${c < 60 ? 'low' : ''}" style="--p:${c}"><i>${c}%</i></div><div style="font-size:13px"><b>${c >= 75 ? 'I can build this now.' : c > (was ?? 0) ? 'Rising as we talk.' : 'Not enough yet.'}</b><div class="small muted">${was !== null ? `Was ${was}% from the written request. ` : ''}${esc(suggestion.gaps)}</div></div></div>`;
    }

    const fin = root.querySelector('#finish');
    if (fin) fin.onclick = async () => {
      if (!(iv.rules || []).length) { toast('Capture at least one rule first.', 'err'); return; }
      if (!(await confirmBox(`Create the build ticket from ${(iv.rules || []).length} rule${iv.rules.length === 1 ? '' : 's'}? ${first} gets a summary from C-EXA and the request moves to Working.`, 'Create ticket'))) return;
      try { const r = await api.post(`/api/interviews/${iv.id}/finish`); toast(`Ticket #${req.number} created from ${iv.rules.length} rules · C-EXA attached the meeting notes · logged`); ctx.refreshCounts && ctx.refreshCounts(); ctx.go('build', { id: r.ticket.id }); }
      catch (e) { toast(e.detail, 'err'); }
    };

    drawRules();
    if (done) root.querySelector('#listen').innerHTML = '<div class="small muted">The interview is finished — nothing more to ask.</div>';
    else suggest(false);
  },
};
