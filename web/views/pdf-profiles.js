// PDF Studio · profiles & models (admin/owner). What C-EXA learned, how sure she is, which model earns its keep.
import { api } from '../api.js';
import { esc, toast, pill, statusPill, fmtDate, pct, modal } from '../ui.js';

const ROLE_PILL = { first_pass: ['First pass, always', 'neutral'], scanned: ['Scanned pages', 'neutral'], misses: ['Misses', 'neutral'], profiles: ['Profiles & repair', 'ai'], challenger: ['Challenger', 'neutral'] };
const WHERE = { right: 'value to the right', below: 'value below', left: 'value to the left' };

// A profile's accuracy is what it scored on bills it was NOT built from. Never show a training
// score as if it were the real one — that is how 52.7% wrong was once reported as 90% right.
function honestShort(p) {
  const h = p.honest || {};
  if (h.basis === 'held_out') return `${(h.accuracy * 100).toFixed(1)}% unseen`;
  if (h.basis === 'training_only') return 'never checked unseen';
  return 'too few to check';
}
function honestLine(h) {
  if (!h) return '';
  const train = h.training_accuracy != null ? ` Training score on the ${h.trained_on} it was built from: ${(h.training_accuracy * 100).toFixed(1)}%.` : '';
  if (h.basis === 'held_out') return `<b>${(h.accuracy * 100).toFixed(1)}%</b> on ${h.held_out} bill${h.held_out === 1 ? '' : 's'} it had not seen.${train}`;
  if (h.basis === 'training_only') return `Only ever scored on the bills it was built from (${(h.training_accuracy * 100).toFixed(1)}%). That number says the rules fire consistently, not that they are right — it will be re-measured on the next batch.`;
  return `Too few bills to check.${train} A profile scored on the bills it was built from is marking its own homework, so no accuracy is claimed until there are some to hold back.`;
}

export default {
  title: 'PDF Studio · profiles & models',
  navRoute: 'pdf-studio',
  async render(root, ctx) {
    if (ctx.me.role === 'user') { root.innerHTML = '<div class="head"><div><h1>Not for you</h1><p>This page is for admins.</p></div></div>'; return; }
    const isOwner = ctx.me.role === 'owner';
    let data, spend, templates, lex = { learned: 0, from_corrections: 0, groups: 0, layouts: 0, seeds: 0, top: [] };
    try { [data, spend, templates] = await Promise.all([api.get('/api/pdfstudio/profiles'), api.get('/api/pdfstudio/spend'), api.get('/api/pdfstudio/templates')]); }
    catch (e) { root.innerHTML = `<div class="head"><div><h1>Profiles</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }
    try { lex = await api.get('/api/pdfstudio/lexicon'); } catch { /* the lexicon is memory, not the page */ }
    let selected = Number(ctx.params.profile) || (data.profiles.find((p) => p.status === 'patch') || data.profiles[0] || {}).id || null;
    let prof = null;
    let benchTid = Number(ctx.params.template) || (templates.templates[0] || {}).id || null;
    let bench = { rows: [] };

    async function loadProfile() { prof = selected ? await api.get(`/api/pdfstudio/profiles/${selected}`) : null; }
    async function loadBench() { try { bench = benchTid ? await api.get(`/api/pdfstudio/bench/${benchTid}`) : { rows: [] }; } catch { bench = { rows: [] }; } }
    await Promise.all([loadProfile(), loadBench()]);

    function draw() {
      const ps = data.profiles;
      const layouts = new Set(ps.map((p) => p.key.split('.').pop()));
      const lastBatch = spend.batches[0];
      root.innerHTML = `
      <div class="head"><div><h1>PDF Studio · profiles &amp; models</h1><p>What C-EXA has learned, how sure she is, and which model earns its keep. Users never see this page.</p></div><button class="btn sm" id="back">Back to PDF Studio</button></div>
      <div class="grid g3" style="margin-bottom:16px">
        <div class="card stat"><div class="eyebrow">Profiles</div><div class="v num">${ps.length}</div><div class="d">covering ${layouts.size} layout${layouts.size === 1 ? '' : 's'}${data.pending ? ` · <span style="color:var(--warn)">${data.pending} awaiting approval</span>` : ''}</div></div>
        <div class="card stat"><div class="eyebrow">Read without AI</div><div class="v num">${spend.docs ? pct(spend.rules_only_share, 1) : '—'}</div><div class="d">${spend.docs ? `of ${spend.docs} document${spend.docs === 1 ? '' : 's'} — rules only, no model call` : 'Nothing read yet'}</div></div>
        <div class="card stat"><div class="eyebrow">Model spend</div><div class="row" style="gap:10px;margin-top:6px"><div class="meter" style="flex:1"><i style="width:${lastBatch ? Math.min(100, Math.round((lastBatch.spend_zar / (lastBatch.ceiling_zar || 1)) * 100)) : 0}%"></i></div><span class="num small">Last batch <b>R ${(lastBatch ? lastBatch.spend_zar : 0).toFixed(2)}</b> of R ${lastBatch ? lastBatch.ceiling_zar : spend.ceiling_zar} allowed</span></div><div class="d">${lastBatch ? `Batch #${lastBatch.id} · ${esc(lastBatch.name)}` : 'No batches yet'}</div><div class="d">All batches this month: R ${spend.month_zar.toFixed(2)} · ${spend.mode === 'stub' ? 'no model connected' : 'models are local'}</div></div>
      </div>
      ${knowsHtml()}
      <div class="grid g12" style="margin-bottom:16px">
        <div class="card"><div class="hd"><h2>Profiles</h2><div class="row" style="gap:8px">${isOwner && belowGate().length ? `<button class="btn sm ghost" id="cleanup" style="color:var(--crit);white-space:nowrap">Clean up ${belowGate().length} below gate</button>` : '<span class="small muted">one per layout · the % is on bills it had not seen</span>'}</div></div>
          <ul class="list">${ps.length ? ps.map((p) => `<li style="cursor:pointer;${p.id === selected ? 'background:var(--accent-soft)' : ''}" data-p="${p.id}"><div class="t"><b>${esc(p.label)}</b><span>v${p.version || '–'}${p.pending ? ` → v${p.version + p.pending} pending` : ''} · ${esc(honestShort(p))} · ${p.docs_seen} doc${p.docs_seen === 1 ? '' : 's'}</span></div>${statusPill(p.status)}</li>`).join('') : '<li class="empty">No profiles yet. Run a batch in PDF Studio and C-EXA will draft the first one.</li>'}</ul></div>
        <div style="display:flex;flex-direction:column;gap:16px" id="detail">${detailHtml()}</div>
      </div>
      <div class="card"><div class="hd"><h2>Model bake-off${benchTid ? ' · ' + esc((templates.templates.find((t) => t.id === benchTid) || {}).name || '') : ''}</h2><div class="row">${templates.templates.length ? `<select id="bench-tpl" style="width:auto">${templates.templates.map((t) => `<option value="${t.id}" ${t.id === benchTid ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select><button class="btn sm" id="run-bench">Run bake-off</button>` : '<span class="small muted">Make a template in PDF Studio first</span>'}</div></div>
        <div class="tbl"><table>
          <thead><tr><th>Model</th><th>Runs on</th><th>Precision</th><th>Recall</th><th>Made-up values</th><th>Cost / 1 000 pages</th><th>Speed</th><th></th></tr></thead>
          <tbody>${bench.rows.length ? bench.rows.map((r) => `<tr class="${r.recommended ? 'rec' : ''}"><td>${esc(r.model)}</td><td>${esc(r.runs_on)}</td><td class="num">${r.docs ? pct(r.precision) : '—'}</td><td class="num">${r.docs ? pct(r.recall) : '—'}</td><td class="num">${r.docs ? pct(r.hallucination_rate) + (r.role !== 'first_pass' ? '*' : '') : '—'}</td><td class="num">R ${r.cost_per_1000.toFixed(2)}</td><td class="num">${r.latency_s} s</td><td>${r.recommended ? pill('Recommended · ' + (ROLE_PILL[r.role] || [r.role])[0].toLowerCase(), 'good') : pill(...(ROLE_PILL[r.role] || [r.role, 'neutral']))}</td></tr>`).join('') : (templates.templates.length ? '' : '<tr><td colspan="8" class="empty">No templates yet.</td></tr>') || '<tr><td colspan="8" class="empty">Not run yet for this template. Click "Run bake-off" — the rules-only pass runs now; model rows fill in when a model is connected.</td></tr>'}</tbody>
        </table></div>
        <div class="bd small muted" style="border-top:1px solid var(--line)">* A model answer is discarded unless its quoted text is found verbatim on the page — so made-up values reach the sheet 0% of the time by construction, not by luck. "Recommended" is computed: highest recall among rows with ≤0.1% made-up values and cost within 2× of the cheapest such row.</div>
      </div>`;
      root.querySelector('#back').onclick = () => ctx.go('pdf-studio');
      root.querySelectorAll('[data-p]').forEach((li) => (li.onclick = async () => { selected = Number(li.dataset.p); await loadProfile(); draw(); }));
      if (root.querySelector('#bench-tpl')) root.querySelector('#bench-tpl').onchange = async (e) => { benchTid = Number(e.target.value); await loadBench(); draw(); };
      if (root.querySelector('#run-bench')) root.querySelector('#run-bench').onclick = async () => {
        if (!benchTid) return;
        try { bench = await api.post(`/api/pdfstudio/bench/${benchTid}`); toast(`Bake-off run on ${bench.gold ? bench.gold.gold_cells : 0} cells (${bench.gold ? bench.gold.verified_cells : 0} hand-checked)`); draw(); } catch (e) { toast(e.detail || 'Could not run', 'err'); }
      };
      root.querySelectorAll('[data-reauthor]').forEach((b) => (b.onclick = async () => {
        const f = (prof.fields || []).find((x) => String(x.id) === b.dataset.reauthor);
        if (!f || !prof.template_id) return;
        b.disabled = true;
        try {
          const r = await api.post(`/api/pdfstudio/templates/${prof.template_id}/columns/${f.id}/reauthor`);
          toast(r.message); await loadProfile(); data = await api.get('/api/pdfstudio/profiles'); draw();
        } catch (e) { b.disabled = false; toast(e.detail || 'Could not re-author', 'err'); }
      }));
      root.querySelectorAll('[data-approve]').forEach((b) => (b.onclick = async () => {
        try { prof = await api.post(`/api/pdfstudio/profiles/${selected}/versions/${b.dataset.approve}/approve`); data = await api.get('/api/pdfstudio/profiles'); toast(`v${b.dataset.n} approved · profile patched · logged`); draw(); } catch (e) { toast(e.detail || 'Could not approve', 'err'); }
      }));
      const del = root.querySelector('#del-profile'); if (del) del.onclick = () => deleteOne();
      const cu = root.querySelector('#cleanup'); if (cu) cu.onclick = () => cleanUp();
    }

    // Deleting a profile is a production decision, so the confirm says what it costs rather than
    // asking "are you sure?". The readings never go — only the link to the rules that made them.
    function belowGate() { return data.profiles.filter((p) => p.status === 'below_gate'); }

    async function deleteOne() {
      let cost;
      try { cost = await api.get(`/api/pdfstudio/profiles/${selected}/deletable`); } catch (e) { return toast(e.detail || 'Could not check', 'err'); }
      const m = modal({
        title: `Delete “${cost.label}”?`,
        body: `<p>This removes the profile and its <b>${cost.versions}</b> version${cost.versions === 1 ? '' : 's'} — the rules C-EXA wrote for this layout.</p>
          ${cost.status === 'live' ? '<p class="small" style="color:var(--crit)"><b>This one is live.</b> Documents matching this layout have nothing to be read with until C-EXA authors it again on the next batch.</p>' : ''}
          <ul class="list">
            <li><div class="t"><b>${cost.readings} reading${cost.readings === 1 ? '' : 's'} stay</b><span>the values already pulled into your sheets are untouched — only the note of which rules produced them goes</span></div></li>
            <li><div class="t"><b>${cost.documents} document${cost.documents === 1 ? '' : 's'} become unassigned</b><span>the next batch reads them as if they were new</span></div></li>
            <li><div class="t"><b>A copy is saved first</b><span>every rule is written to your Results folder as JSON before anything is deleted</span></div></li>
          </ul>
          <p class="small muted">C-EXA cannot do this — only you, and it is written to the audit log.</p>`,
        footer: '<button class="btn" data-close>Keep it</button><button class="btn primary" id="d-ok" style="background:var(--crit);border-color:var(--crit)">Delete profile</button>',
      });
      m.root.querySelector('#d-ok').onclick = async (e) => {
        e.target.disabled = true;
        try {
          const r = await api.del(`/api/pdfstudio/profiles/${selected}`);
          m.close(); toast(r.message);
          data = await api.get('/api/pdfstudio/profiles');
          selected = (data.profiles[0] || {}).id || null; await loadProfile(); draw();
        } catch (err) { e.target.disabled = false; toast(err.detail || 'Could not delete', 'err'); }
      };
    }

    async function cleanUp() {
      const rows = belowGate();
      const m = modal({
        title: `Delete ${rows.length} profile${rows.length === 1 ? '' : 's'} that never passed the gate?`,
        body: `<p>These are below the accuracy gate, so nothing they read is trusted anyway. Deleting them lets C-EXA author the layout again from scratch — with what she knows now, not what she knew then.</p>
          <ul class="list">${rows.map((p) => `<li><div class="t"><b>${esc(p.label)}</b><span>${esc(p.key)} · ${esc(honestShort(p))} · ${p.docs_seen} doc${p.docs_seen === 1 ? '' : 's'}</span></div>${statusPill(p.status)}</li>`).join('')}</ul>
          <p class="small muted">Every reading stays. Each profile is exported to your Results folder first. Profiles that are live or still a draft are not touched.</p>`,
        footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="p-ok" style="background:var(--crit);border-color:var(--crit)">Delete all ' + rows.length + '</button>',
      });
      m.root.querySelector('#p-ok').onclick = async (e) => {
        e.target.disabled = true; e.target.textContent = 'Deleting…';
        try {
          const r = await api.post('/api/pdfstudio/profiles/purge', { ids: rows.map((p) => p.id) });
          m.close(); toast(r.message);
          data = await api.get('/api/pdfstudio/profiles');
          selected = (data.profiles[0] || {}).id || null; await loadProfile(); draw();
        } catch (err) { e.target.disabled = false; e.target.textContent = `Delete all ${rows.length}`; toast(err.detail || 'Could not delete', 'err'); }
      };
    }

    function knowsHtml() {
      const top = lex.top || [];
      const learned = lex.learned || 0;
      return `<div class="card lexicon" style="margin-bottom:16px"><div class="hd"><h2>What C-EXA knows</h2><span class="small muted">label text that has worked, carried from every layout to the next</span></div>
        <div class="bd small muted" style="border-bottom:1px solid var(--line)"><b class="mono">${learned}</b> label${learned === 1 ? '' : 's'} learned from real documents${lex.from_corrections ? ` · <b class="mono">${lex.from_corrections}</b> taught by someone fixing a cell` : ''}${lex.groups ? ` · ${lex.groups} kind${lex.groups === 1 ? '' : 's'} of column` : ''}${lex.layouts ? ` · across ${lex.layouts} layout${lex.layouts === 1 ? '' : 's'}` : ''} · on top of <b class="mono">${lex.seeds || 0}</b> shipped as a starting vocabulary.</div>
        ${top.length ? `<ul class="list">${top.map((r) => `<li><div class="t"><b>${esc(r.label)} <span class="small muted" style="font-weight:400">${esc(WHERE[r.placement] || r.placement)} · ${esc(r.group.replace(/_/g, ' '))}</span></b><span>confirmed ${r.confirmed}${r.failed ? ` · failed ${r.failed}` : ''}${r.layouts ? ` · ${r.layouts} layout${r.layouts === 1 ? '' : 's'}` : ''}${r.example ? ` · e.g. “${esc(r.example)}”` : ''}</span></div>${r.corrections ? pill(`${r.corrections} from a person's fix`, 'good') : ''}</li>`).join('')}</ul>`
          : '<div class="empty">Nothing learned yet. C-EXA starts from the shipped vocabulary; every layout she reads, and every cell a person fixes, adds to this.</div>'}
        <div class="bd small muted" style="border-top:1px solid var(--line)">When a person corrects a cell and the value they typed sits next to a printed heading, that heading is written down here — so the fix helps on the next municipality, not just on this bill. Labels that turned out wrong are marked failed and stop being offered.</div></div>`;
    }

    function detailHtml() {
      if (!prof) return '<div class="card"><div class="empty">Pick a profile to see C-EXA\'s notes.</div></div>';
      const sc = prof.scoring;
      const nRules = prof.versions.length ? prof.versions[0].rules : 0;
      const vers = prof.versions.map((v) => `<li style="padding:8px 0"><span class="vtag">v${v.number}</span><div class="vt"><b>${esc(v.reason || (v.number === 1 ? 'Drafted from samples' : 'Update'))}</b><span>${esc(v.author_name)} · ${fmtDate(v.created_at, { time: true })}${v.backtest && v.backtest.docs ? ` · ${v.backtest.holdout_accuracy != null ? `${(v.backtest.holdout_accuracy * 100).toFixed(1)}% on ${v.backtest.held_out} it hadn't seen` : `${v.backtest.docs} docs, too few held back`}` : ''}${v.pending ? ' · awaiting approval' : v.id === prof.live_version_id ? ' · live' : ''}</span></div>${v.pending ? `<button class="btn sm primary" data-approve="${v.id}" data-n="${v.number}">Approve</button>` : ''}</li>`).join('');
      const seed = prof.seeded_from;
      return `<div class="card"><div class="hd"><h2>${esc(prof.label)} · C-EXA's notes on this layout</h2><div class="row" style="gap:8px">${seed ? pill(`seeded from ${seed.profile}`, 'ai') : ''}<span class="small mono muted">profile v${prof.version || '–'} · ${Object.keys(prof.ruleset.fields || {}).length} fields · ${nRules} rules</span>${isOwner ? '<button class="btn sm ghost" id="del-profile" style="color:var(--crit)">Delete</button>' : ''}</div></div>
        <div class="bd">
          <p class="small" style="margin:0 0 12px">${honestLine(prof.honest)}</p>
          <div class="notes">${esc(prof.notes || 'No notes yet.').replace(/\n/g, '<br>')}</div>
          <div class="grid g2" style="margin-top:14px">
            <div><div class="eyebrow" style="margin-bottom:8px">How a value earns a score</div>
              <div class="score"><span>Exact label match</span><div class="bar"><i style="width:100%"></i></div><span class="pct">${sc.base.exact.toFixed(2)}</span></div>
              <div class="score"><span>Derived (adds up)</span><div class="bar"><i style="width:${sc.base.derived * 100}%"></i></div><span class="pct">${sc.base.derived.toFixed(2)}</span></div>
              <div class="score"><span>Fuzzy label</span><div class="bar"><i class="mid" style="width:${sc.base.fuzzy * 100}%"></i></div><span class="pct">${sc.base.fuzzy.toFixed(2)}</span></div>
              <div class="score"><span>Position only</span><div class="bar"><i class="mid" style="width:${sc.base.positional * 100}%"></i></div><span class="pct">${sc.base.positional.toFixed(2)}</span></div>
              <div class="score"><span>Not verbatim on the PDF</span><div class="bar"><i class="low" style="width:2%"></i></div><span class="pct">0</span></div>
              <p class="small muted" style="margin-top:8px">× ${sc.ocr} if OCR · × ${sc.arith} if the arithmetic fails · × ${sc.parse} if it won't parse. Gates: ${Math.round(sc.gates.batch * 100)}% master · ${Math.round(sc.gates.profile * 100)}% per profile · ${(sc.gates.critical * 100).toFixed(1)}% for money fields. Under 0.9 the cell goes to the user.</p></div>
            <div><div class="eyebrow" style="margin-bottom:8px">Version history</div><ul class="list vers" style="font-size:13px">${vers}</ul></div>
          </div>
        </div></div>
        ${fieldsHtml()}`;
    }
    function fieldsHtml() {
      const fs = prof.fields || [];
      if (!fs.length) return '';
      const RULE = { label_right: 'right of', label_below: 'below', label_left: 'left of', line_after: 'line after', regex: 'pattern', const: 'fixed', derived: 'derived', between: 'between', table: 'table' };
      const ruleTag = (r) => {
        if (r.type === 'derived') return `<span title="worked out from other columns">= ${esc((r.args || []).join(` ${r.op === 'add' ? '+' : r.op === 'sub' ? '−' : r.op === 'mul' ? '×' : '÷'} `))}</span>`;
        if (r.type === 'const') return `<span>fixed “${esc(r.value)}”</span>`;
        if (r.type === 'regex') return `<span title="${esc(r.pattern)}">pattern</span>`;
        if (r.type === 'between') return `<span>between “${esc(r.after)}” and “${esc(r.before)}”</span>`;
        return `<span title="${esc(r.id || '')}${r.hits != null ? ` · works on ${r.hits} of ${r.of} samples` : ''}">${RULE[r.type] || esc(r.type)} “${esc(r.label)}”${r.pick === 'last' ? ' (last)' : ''}${r.page ? ` p${r.page}` : ''}</span>`;
      };
      const canRe = !!prof.template_id;
      const kindPill = (f) => {
        if (f.ctype === 'filename') return pill('file name', 'neutral');
        if (f.ctype === 'formula') return pill('calculated', 'info');
        return f.required === false ? `<span class="pill neutral" title="${esc(f.optional_reason || 'Blank when the document has no such figure — not a miss')}">optional</span>` : pill('required', 'good');
      };
      const noRule = (f) => f.ctype === 'filename' ? '<span class="muted">the PDF\'s own file name — nothing to read</span>' : f.ctype === 'formula' ? `<span title="worked out from other columns">= ${esc(f.expr || '')}</span>` : f.required === false ? '<span class="muted">no rule yet — blank unless a fix teaches one</span>' : '<span class="muted" style="background:var(--warn-soft);color:var(--warn)">no rule yet — cells go to the user</span>';
      const nOpt = fs.filter((f) => f.required === false && f.needs_rules !== false).length, nCalc = fs.filter((f) => f.ctype === 'formula').length, nBorrowed = fs.filter((f) => f.seeded_from).length;
      return `<div class="card"><div class="hd"><h2>Rules per column</h2><span class="small muted">${fs.length} columns${nOpt ? ` · ${nOpt} optional` : ''}${nCalc ? ` · ${nCalc} calculated` : ''}${nBorrowed ? ` · ${nBorrowed} borrowed` : ''} · tried in order · fix one column without touching the rest</span></div>
        <ul class="list hv-rules">${fs.map((f) => `<li><div class="t"><b>${esc(f.label)} <span class="small muted" style="font-weight:400">${esc(f.ctype === 'formula' ? 'calculated' : f.ctype === 'filename' ? 'file name' : f.ctype || '')}${f.hint ? ` · “${esc(f.hint)}”` : ''}</span> ${kindPill(f)}</b>
            <div class="rl">${f.seeded_from ? `<span class="seed" title="borrowed from another profile: ${esc(f.seeded_from)}">from ${esc(f.seeded_from)}</span>` : ''}${f.rules.length ? f.rules.map(ruleTag).join('') : noRule(f)}</div></div>
            ${canRe && f.id && f.needs_rules !== false ? `<button class="btn sm" data-reauthor="${f.id}" title="Write this column's rules again from the sample pages; every other column keeps its rules">Re-author this column</button>` : ''}</li>`).join('')}</ul>
        <div class="bd small muted" style="border-top:1px solid var(--line)">Re-authoring makes a new version whose other fields are copied unchanged (reason: “Re-authored column …”). When a template gains a column, only that column is authored the same way. <b>from …</b> marks a rule borrowed from another profile — the same layout under another template, or another vendor's layout under this template, tested on this layout's samples first. Optional columns are blank when the figure is not on the document; that counts as right, not as a miss. Change required/optional in the template editor in PDF Studio.</div></div>`;
    }
    draw();
  },
};
