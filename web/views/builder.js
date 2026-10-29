// The Builder (1.0.53): one screen. On the left the tool as an outline — its pages (and what is on
// the page you are on), its data, its rules, the tool itself. In the middle the page as people see it,
// with live rows: click a part to change it, or "+ add here" between parts. On the right, whatever is
// selected as plain numbered questions (builder/ask.js). The step bar: Save → Try → Test → Deploy (1.0.82); Test makes a version
// for the tests, the council and the owner's promote. Nothing live changes from here.
import { api } from '../api.js';
import { esc, toast, modal, confirmBox, pill, aiName } from '../ui.js';
import { LANGS } from '../lang.js';
import { renderAsk, typeName, words, humanise, keyName } from '../builder/ask.js';
import { renderLayout, applyTheme } from './tool.js';

const LANG_ORDER = ['en', 'ar', 'pt', 'fr'];
const PAGE_KEEP = ['title', 'intro', 'menu', 'order', 'style', 'access', 'layout'];

// a part's name in the outline: its first words, not a whole paragraph
const short = (t) => { const x = String(t || '').replace(/[*_#`]/g, '').replace(/\s+/g, ' ').trim(); return x.length > 38 ? x.slice(0, 36).trimEnd() + '…' : x; };

export default {
  title: 'Builder',
  async render(root, ctx) {
    if (!ctx.me.can_build) { root.innerHTML = '<div class="head"><div><h1>Builder</h1><p>For the people who build (owners, admins and developers).</p></div></div>'; return undefined; }
    let ov;
    try { ov = await api.get('/api/builder'); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Builder</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return undefined; }
    const B = { ov, F: null, saved: null, slug: '', page: '', sel: null, assets: [], rules: {}, dirty: false, tryMode: false, previewLang: 'en', ctx };
    let hist = [], hpos = -1, htimer = null, stageTimer = null;

    root.classList.add('bx-host');
    root.innerHTML = `<div class="bx">
      <div class="bx-bar"><select id="bx-tool" class="bx-in" aria-label="Tool"></select><button class="btn sm" id="bx-new">New tool</button>
        <span class="bx-state" id="bx-state"></span><span style="flex:1"></span>
        <button class="btn sm ghost" id="bx-undo" title="Undo (Ctrl+Z)">↶</button><button class="btn sm ghost" id="bx-redo" title="Redo (Ctrl+Shift+Z)">↷</button>
        <div class="bx-steps" role="group" aria-label="Save, try, test, deploy">
          <button class="bx-stepbtn" id="bx-save" title="Keep my changes as a draft (Ctrl+S)"><i>1</i><span><b>Save</b><small id="bx-s1"></small></span></button>
          <button class="bx-stepbtn" id="bx-try" title="Use the draft as people will"><i>2</i><span><b>Try</b><small id="bx-s2"></small></span></button>
          <button class="bx-stepbtn" id="bx-test" title="Package my drafts and run every test"><i>3</i><span><b>Test</b><small id="bx-s3"></small></span></button>
          <button class="bx-stepbtn" id="bx-deploy" title="Test area for others, then live"><i>4</i><span><b>Deploy</b><small id="bx-s4"></small></span></button></div></div>
      <div class="bx-cols"><nav class="bx-tree" id="bx-tree" aria-label="The tool"></nav>
        <main class="bx-stage"><div class="bx-stagehd" id="bx-stagehd"></div><div class="bx-page" id="bx-page"></div></main>
        <aside class="bx-ask" id="bx-ask" aria-live="polite"></aside></div></div>`;
    const $ = (q) => root.querySelector(q);

    // ---- history, dirty, save ----------------------------------------------------------------------------
    const snap = () => JSON.stringify(B.F);
    const histReset = () => { hist = B.F ? [snap()] : []; hpos = hist.length - 1; buttons(); };
    const histCommit = () => { clearTimeout(htimer); htimer = null; if (!B.F) return; const x = snap(); if (x === hist[hpos]) return; hist = hist.slice(0, hpos + 1); hist.push(x); if (hist.length > 80) hist.shift(); hpos = hist.length - 1; buttons(); };
    const restore = (i) => { B.F = JSON.parse(hist[i]); B.rules = {}; hpos = i; B.dirty = true; drawAll(); };
    const undo = () => { if (htimer) histCommit(); if (hpos > 0) { restore(hpos - 1); toast('Undone'); } };
    const redo = () => { if (htimer) histCommit(); if (hpos < hist.length - 1) { restore(hpos + 1); toast('Redone'); } };
    function buttons() {
      $('#bx-undo').disabled = hpos <= 0; $('#bx-redo').disabled = hpos >= hist.length - 1;
      steps();
      $('#bx-state').innerHTML = !B.F ? '' : B.dirty ? 'Changed — not saved yet' : B.saved && B.saved.source === 'live' ? 'As it runs now' : `Draft${B.saved && B.saved.job ? ` · <a href="#/agents?job=${B.saved.job.job}">built by ${esc(aiName())}'s team</a>` : ''}`;
    }
    /** Something changed. `all`: redraw the questions too (a click); false: only the outline and the page (typing). */
    B.mark = (all = true) => {
      B.dirty = true; B.tried = false; clearTimeout(htimer); htimer = setTimeout(histCommit, 400); buttons();
      drawTree();
      if (all) { drawStage(); drawAsk(); } else { clearTimeout(stageTimer); stageTimer = setTimeout(drawStage, 450); }
    };
    B.select = (sel) => { B.sel = sel; if (sel.kind === 'page' && sel.page) B.page = sel.page; drawAll(); };
    const onKey = (e) => {
      if (!root.isConnected) { document.removeEventListener('keydown', onKey); return; }
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === 's') { e.preventDefault(); save(); return; }
      if (e.target.closest && e.target.closest('input, textarea, select, [contenteditable]')) return;
      if (document.querySelector('.modal-bg')) return;
      if ((e.ctrlKey || e.metaKey) && k === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
      else if ((e.ctrlKey || e.metaKey) && (k === 'y' || (k === 'z' && e.shiftKey))) { e.preventDefault(); redo(); }
    };
    document.addEventListener('keydown', onKey);
    $('#bx-undo').onclick = undo; $('#bx-redo').onclick = redo; $('#bx-save').onclick = () => save();
    for (const w of ['try', 'test', 'deploy']) $(`#bx-${w}`).onclick = () => { if (B.F) B.select({ kind: 'tool', what: w }); };

    const clean = (F) => JSON.parse(JSON.stringify(F, (k, v) => (k.startsWith('_') || k === 'canvas' ? undefined : v)));
    async function save(takeOver = false) {
      if (!B.F) return false;
      const F = clean(B.F);
      try {
        B.saved = await api.put(`/api/builder/tools/${B.slug}`, { manifest: F.manifest, pages: F.pages, data: F.data, flows: F.flows, languages: F.languages, readme: F.readme || '', take_over: takeOver });
        for (const obj of [...Object.values(B.F.pages), ...Object.values(B.F.data)]) delete obj._new;
        for (const d of Object.values(B.F.data)) for (const c of d.columns || []) delete c._new;
        B.assets = B.saved.assets || B.assets; B.dirty = false; ov = B.ov = await api.get('/api/builder');
        toast(B.saved.errors.length ? `Saved — ${B.saved.errors.length} thing${B.saved.errors.length === 1 ? '' : 's'} to fix before testing (bottom left)` : 'Draft saved', B.saved.errors.length ? 'err' : '');
        drawAll(); return true;
      } catch (e) {
        // 1.0.80: a draft is the person's who first changed it — the owner may take it over on purpose
        if (e.status === 409 && ctx.me.role === 'owner' && !takeOver && await confirmBox(`${e.detail} Take it over? Their changes stay in the draft; they are told by the audit trail.`, 'Take it over')) return save(true);
        toast(e.detail || e.message, 'err'); return false;
      }
    }

    // ---- opening a tool ------------------------------------------------------------------------------------
    function fillPicker() {
      const t = $('#bx-tool');
      t.innerHTML = `<option value="">— open a tool —</option>${(ov.tools || []).map((x) => `<option value="${esc(x.slug)}" ${x.slug === B.slug ? 'selected' : ''}>${esc(x.title)}${x.changed ? ' · draft' : ''}${x.editor && !x.editor.mine ? ` · ${esc(x.editor.name)} is changing it` : ''}${x.ok === false || (x.draft_errors || []).length ? ' · to fix' : ''}</option>`).join('')}${B.slug && !(ov.tools || []).some((x) => x.slug === B.slug) ? `<option value="${esc(B.slug)}" selected>${esc(words(B).text((B.F || { manifest: {} }).manifest.title) || B.slug)} · new</option>` : ''}`;
    }
    $('#bx-tool').onchange = (e) => { if (e.target.value) open(e.target.value); };
    function normalise(F, layouts) {
      F.flows = F.flows || {}; F.data = F.data || {}; F.pages = F.pages || {}; F.languages = F.languages || {}; F.languages.en = F.languages.en || {};
      // 1.0.87: a table saved with the wrong shape (columns as {key: …}) opens as a list instead of breaking the Builder
      for (const d of Object.values(F.data)) {
        if (d && d.columns && !Array.isArray(d.columns)) d.columns = Object.entries(d.columns).map(([k, c]) => ({ key: k, label: `col.${k}`, type: 'text', ...(c && typeof c === 'object' ? c : {}) }));
        if (d && !d.columns) d.columns = [];
      }
      for (const [n, p] of Object.entries(F.pages)) {
        if (!p.layout || !Array.isArray(p.layout.sections)) p.layout = (layouts || {})[n] || { sections: [] };
        for (const k of Object.keys(p)) if (!PAGE_KEEP.includes(k)) delete p[k];
        if (!p.layout.sections.length) p.layout.sections.push({ id: 's1', columns: [{ width: 12, widgets: [] }] });
      }
      return F;
    }
    async function open(slug) {
      if (B.dirty && !(await confirmBox('This tool has changes that are not saved. Leave them?', 'Leave'))) { fillPicker(); return; }
      let f;
      try { f = await api.get(`/api/builder/tools/${encodeURIComponent(slug)}`); } catch (e) { toast(e.detail || e.message, 'err'); return; }
      B.saved = f; B.assets = f.assets || []; B.slug = f.slug; B.rules = {}; B.dirty = false;
      B.F = normalise({ manifest: f.manifest, pages: f.pages, data: f.data, flows: f.flows, languages: f.languages, readme: f.readme }, f.layouts);
      B.page = B.F.manifest.home && B.F.pages[B.F.manifest.home] ? B.F.manifest.home : Object.keys(B.F.pages)[0] || '';
      B.sel = ctx.params.rule && B.F.flows[ctx.params.rule] ? { kind: 'rule', id: ctx.params.rule } : { kind: 'page' };
      histReset(); fillPicker(); drawAll();
    }
    function startNew(title = '', slugHint = '') {
      const pdfJob = (ov.actions || []).find((a) => a.name === 'pdfstudio.read');
      const tpls = pdfJob ? ((pdfJob.inputs.find((x) => x.kind === 'choice') || {}).choices || []) : [];
      const m = modal({ title: 'A new tool', body: `<div class="field"><label>What is it called?</label><input id="n-title" type="text" placeholder="e.g. Utility bills" value="${esc(title)}"></div>
        ${tpls.length ? `<div class="field"><label>It starts from</label><select id="n-from"><option value="">A blank page</option>${tpls.map(([v, l]) => `<option value="${esc(v)}">PDFs read with PDF Studio · ${esc(l)}</option>`).join('')}</select></div>` : ''}
        <p class="small muted">You can change everything else after this.</p>`, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="n-ok">Start</button>' });
      const go = () => {
        const t = m.root.querySelector('#n-title').value.trim();
        const from = (m.root.querySelector('#n-from') || {}).value || '';
        if (!t) { toast('Give it a name first.', 'err'); return; }
        let slug = slugHint || keyName(t).replace(/_/g, '-').replace(/^x-/, 'tool-').slice(0, 40), i = 2;
        if (!/^[a-z][a-z0-9-]{1,40}$/.test(slug)) slug = 'tool-' + slug.replace(/[^a-z0-9-]/g, '').slice(0, 30);
        const base = slug; while ((ov.tools || []).some((x) => x.slug === slug)) slug = `${base}-${i++}`;
        m.close();
        B.slug = slug; B.saved = null; B.assets = []; B.rules = {}; B.dirty = true;
        B.F = normalise({ manifest: { slug, title: 'tool.title', icon: 'tool', menu: { section: 'My tools', order: 100 }, access: { roles: ['owner', 'admin'] }, home: 'main', owner: ctx.me.email || '', version: 1 },
          pages: { main: { title: 'page.main.title', menu: true, style: { width: 'normal' }, layout: { sections: [{ id: 's1', columns: [{ width: 12, widgets: [] }] }] } } }, data: {}, flows: {}, languages: { en: { 'tool.title': t, 'page.main.title': t }, ar: {}, pt: {}, fr: {} }, readme: `# ${t}\n` });
        B.page = 'main'; B.sel = { kind: 'add', sec: 0, col: 0, at: 0 };
        if (from && pdfJob) { fromPdfTemplate(B.F, t, from, pdfJob); B.sel = { kind: 'page' }; }
        histReset(); fillPicker(); drawAll();
      };
      m.root.querySelector('#n-ok').onclick = go;
      m.root.querySelector('#n-title').addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
      m.root.querySelector('#n-title').focus();
    }
    $('#bx-new').onclick = () => startNew();

    // A tool that reads PDFs (1.0.57): a table with the PDF and the template's columns, a form for the PDF, the
    // rule that hands each PDF to PDF Studio, and a button that fetches what PDF Studio learnt since. Plain parts.
    function fromPdfTemplate(F, title, tpl, job) {
      const en = F.languages.en;
      const outs = [...(job.outputs_by || {})[tpl] || []];
      const col = (key, type, label, extra = {}) => { en[`col.${key}`] = label; return { key, label: `col.${key}`, type, ...extra }; };
      const cols = [col('pdf', 'file', 'The PDF', { accept: ['pdf'], max_mb: 25 }),
        ...outs.map((o) => col(o.key, o.kind === 'number' ? 'number' : o.kind === 'date' ? 'date' : 'text', o.label, o.hint === 'money' ? { format: 'money' } : {})),
        col('state', 'text', 'Where it stands'), col('score', 'number', 'How sure (%)'), col('to_check', 'checklist', 'What to check'), col('batch', 'int', 'PDF Studio batch'), col('excel', 'file', 'The results (Excel)', { accept: ['xlsx'], max_mb: 25 })];
      F.data.documents = { kind: 'table', title: 'data.documents.title', scope: 'mine', write: 'everyone', columns: cols };
      en['data.documents.title'] = 'Documents';
      const out = Object.fromEntries([...outs.map((o) => o.key), 'state', 'score', 'to_check', 'batch', 'excel'].map((k) => [k, k]));
      const T = '{{trigger.row.id}}';
      F.flows.read = { title: 'flow.read.title', enabled: true, trigger: { type: 'row_changed', data: 'documents' }, run_as: 'starter', start: 'if1', steps: {
        s1: { type: 'action', action: 'pdfstudio.read', data: 'documents', row: T, in: { pdf: 'pdf', template: tpl }, out },
        if1: { type: 'if', match: 'all', when: [{ left: '{{trigger.row.pdf}}', op: 'not_empty' }, { left: '{{trigger.row.batch}}', op: 'empty' }], yes: 's1' } } };
      F.flows.fetch = { title: 'flow.fetch.title', enabled: true, trigger: { type: 'button', data: 'documents' }, run_as: 'starter', start: 's1', steps: {
        s1: { type: 'action', action: 'pdfstudio.fetch', data: 'documents', row: T, in: { batch: 'batch', template: tpl }, out } } };
      en['flow.read.title'] = 'Read a new PDF with PDF Studio'; en['flow.fetch.title'] = 'Fetch what PDF Studio now knows';
      const shown = [...outs.slice(0, 5).map((o) => ({ key: o.key })), { key: 'to_check' }, { key: 'state' }, { key: 'excel' }];
      F.pages.main.layout = { sections: [{ id: 's1', columns: [
        { width: 3, widgets: [{ id: 'w_new', type: 'form', title: 'w.w_new.title', source: { data: 'documents' }, fields: [{ key: 'pdf' }], submit: 'w.w_new.submit' }] },
        { width: 9, widgets: [{ id: 'w_docs', type: 'table', title: 'w.w_docs.title', source: { data: 'documents' }, add: false, columns: shown, actions: [{ label: 'w.w_docs.a0', flow: 'fetch' }] }] }] }] };
      Object.assign(en, { 'w.w_new.title': 'A new PDF', 'w.w_new.submit': 'Read it', 'w.w_docs.title': 'Documents', 'w.w_docs.a0': 'Fetch the checked values', 'page.main.intro': 'Drop a PDF; PDF Studio reads it into the row. What it is unsure of is answered in PDF Studio, then fetched back.' });
      F.pages.main.intro = 'page.main.intro'; F.pages.main.style = { width: 'wide' };
      F.manifest.access = { roles: ['owner', 'admin'], tool: 'pdf-studio' };
    }

    // ---- helpers the questions use ------------------------------------------------------------------------
    B.rename = (kind, old, words_) => {
      const coll = kind === 'page' ? B.F.pages : B.F.data;
      let n = keyName(words_), i = 2; const base = n;
      while (coll[n] && n !== old) n = `${base}_${i++}`;
      if (n === old) return old;
      coll[n] = coll[old]; delete coll[old];
      if (kind === 'page' && B.F.manifest.home === old) B.F.manifest.home = n;
      if (kind === 'data') for (const p of Object.values(B.F.pages)) for (const s of p.layout.sections) for (const c of s.columns) for (const w of c.widgets) if (w.source && w.source.data === old) w.source.data = n;
      return n;
    };
    B.newRule = (type = 'button') => {
      let n = 'rule', i = 2; while (B.F.flows[n]) n = `rule_${i++}`;
      B.F.flows[n] = { title: `flow.${n}.title`, enabled: false, trigger: { type, ...(type === 'schedule' ? { every: 'weekday', at: '07:00' } : {}) }, run_as: type === 'schedule' ? 'owner' : 'starter', start: '', steps: {} };
      words(B).en[`flow.${n}.title`] = 'New rule';
      return n;
    };
    B.upload = (done, accept = '.png,.jpg,.jpeg,.gif,.webp,.svg,.pdf') => {
      const inp = document.createElement('input'); inp.type = 'file'; inp.accept = accept;
      inp.onchange = async () => {
        if (!inp.files.length) return;
        if (!B.saved || B.dirty) { if (!(await save())) return; }
        try { const r = await api.upload(`/api/builder/tools/${B.slug}/assets`, [...inp.files]); B.assets = r.assets || []; toast(`Added ${r.saved.map((s) => s.name).join(', ')}`); done(r.saved[0] && r.saved[0].name); }
        catch (e) { toast(e.detail || e.message, 'err'); }
      };
      inp.click();
    };
    B.testRule = async (name, out) => {
      const F = clean(B.F), f = F.flows[name];
      out.hidden = false; out.innerHTML = '<span class="spin"></span> Trying it with the live rows…';
      const trig = (f.trigger || {}).type;
      const sample = { id: 1 };
      if ((trig === 'row_added' || trig === 'row_changed') && f.trigger.data) for (const c of (F.data[f.trigger.data] || {}).columns || []) sample[c.key] = c.type === 'choice' ? (c.options || [])[0] : c.type === 'date' ? new Date().toISOString().slice(0, 10) : c.type === 'number' || c.type === 'int' ? 1 : c.type === 'bool' ? true : 'example';
      try {
        const r = await api.post(`/api/builder/tools/${B.slug}/flows/${name}/test`, { flow: f, data: F.data, manifest: F.manifest, sample });
        if ((r.problems || []).length) { out.innerHTML = `<b>Not tried yet — fix these first:</b>${r.problems.map((p) => `<div class="err-box small" style="margin-top:6px">${esc(p)}</div>`).join('')}`; return; }
        out.innerHTML = `<div class="bx-row"><b>${r.status === 'done' ? 'It went to the end' : r.status === 'waiting' ? 'It stopped to wait (for an approval or a wait)' : 'It stopped with a problem'}</b>${pill(r.status, r.status === 'done' ? 'good' : r.status === 'failed' ? 'crit' : 'warn')}</div><div class="bx-hint">${r.steps} step${r.steps === 1 ? '' : 's'} · nothing was written, sent or called${trig && trig.startsWith('row') ? ' · with an example row' : ''}</div>${r.note ? `<p class="small">${esc(r.note)}</p>` : ''}<ol class="run-log">${(r.log || []).map((x) => `<li class="${x.ok ? 'ok' : 'bad'}">${esc(x.note || x.type || '')}</li>`).join('')}</ol>`;
      } catch (e) { out.innerHTML = `<div class="err-box">${esc(e.detail || e.message)}</div>`; }
    };
    // ---- the step bar: Save → Try → Test → Deploy (1.0.82) ---------------------------------------------------
    /** The version this draft was last tested as (null once it changed after that). */
    const myVer = () => { const tag = B.saved && B.saved.released; return tag ? (ov.mine || []).find((v) => v.tag === tag) || null : null; };
    const lastOk = () => myVer() || (ov.mine || []).find((v) => v.ok && v.status !== 'live' && (v.tools || []).includes(B.slug)) || null;
    function steps() {
      const on = !!B.F, draft = on && B.saved && B.saved.source === 'draft', v = myVer(), s = B.sel || {};
      const st = {
        save: [B.dirty ? 'Not saved' : draft ? 'Saved ✓' : 'Nothing changed', B.dirty ? 'now' : draft ? 'done' : ''],
        try: [B.tried && !B.dirty && draft ? 'Tried ✓' : 'Use it first', B.tried && !B.dirty && draft ? 'done' : draft && !B.dirty ? 'now' : ''],
        test: [v ? (v.ok ? `Passed · v${v.tag}` : `Failed · v${v.tag}`) : 'Run the tests', v ? (v.ok ? 'done' : 'bad') : draft && !B.dirty && B.tried ? 'now' : ''],
        deploy: [v && v.status === 'live' ? 'Live ✓' : v && v.in_test ? 'In Test' : 'Test area, then live', v && v.status === 'live' ? 'done' : v && v.ok ? 'now' : ''],
      };
      for (const [w, [sub, cls]] of Object.entries(st)) {
        const b = $(`#bx-${w}`); if (!b) continue;
        b.className = `bx-stepbtn ${cls} ${s.kind === 'tool' && s.what === w ? 'on' : ''}`;
        b.querySelector('small').textContent = on ? sub : '';
        b.disabled = !on || (w === 'save' && !B.dirty);
      }
    }
    const shead = (n, title, sub) => `<div class="bx-qhd"><div><div class="eyebrow">Step ${n} of 4</div><h2>${title}</h2>${sub ? `<p class="bx-hint">${sub}</p>` : ''}</div></div>`;
    const refresh = async () => { ov = B.ov = await api.get('/api/builder'); if (B.slug && B.saved && B.saved.source === 'draft') { try { B.saved = { ...B.saved, released: (await api.get(`/api/builder/tools/${encodeURIComponent(B.slug)}`)).released }; } catch { /* fine */ } } fillPicker(); steps(); };
    const busy = (b, text) => { b.disabled = true; b.dataset.was = b.textContent; b.innerHTML = `<span class="spin"></span> ${esc(text)}`; };
    const unbusy = (b) => { b.disabled = false; b.textContent = b.dataset.was || b.textContent; };

    B.stepPanel = (host, w) => {
      const draft = B.saved && B.saved.source === 'draft', errs = (B.saved && B.saved.errors) || [];
      const notReady = B.dirty ? 'Save first (step 1).' : !draft ? 'Change something and save it first — this is the tool as it runs now.' : errs.length ? 'Fix what is listed at the bottom left first.' : '';
      if (w === 'try') {
        host.innerHTML = shead(2, 'Try it — use it as people will', 'Nothing you do while trying is kept on the live app.')
          + `<div class="bx-q"><div class="bx-ql">Here, in the page</div><span class="bx-hint">Search, sort and click with the live rows. Buttons run dry: every step says what it would do, nothing is written or sent.</span>
              <button class="btn primary" id="st-here" style="justify-self:start">${B.tryMode ? 'Trying it — back to changing' : 'Try it here'}</button></div>
            <div class="bx-q"><div class="bx-ql">In my Dev area — the whole app, with my drafts in it</div><span class="bx-hint">Your own copy, your own sign-in, its own data. Rules really run there.</span>
              <div id="st-dev"><span class="bx-hint"><span class="spin"></span></span></div></div>
            <div class="bx-q"><span class="bx-hint">A rule on its own: open it on the left and press <b>Try it</b>.</span></div>`;
        host.querySelector('#st-here').onclick = () => { B.tryMode = !B.tryMode; if (B.tryMode) B.tried = true; drawStage(); steps(); B.stepPanel(host, 'try'); };
        const dev = host.querySelector('#st-dev');
        const drawDev = (a) => {
          const d = (a && a.dev) || {};
          dev.innerHTML = d.running ? `<div class="row" style="gap:8px"><a class="btn primary" href="${esc(d.door)}" target="_blank" rel="noopener" id="st-open">Open my Dev area</a><button class="btn ghost" id="st-restart">Start again with my latest save</button></div>${(d.login || []).map((l) => `<div class="small" style="margin-top:6px">Sign in there as <b>${esc(l.email)}</b> with <span class="mono">${esc(l.password)}</span></div>`).join('')}`
            : `<button class="btn" id="st-start" ${notReady && !draft ? 'disabled' : ''}>Start my Dev area</button>${B.dirty ? '<span class="bx-hint"> It uses what is saved.</span>' : ''}`;
          const start = async (b) => { busy(b, 'Starting (about a minute)…'); try { const r = await api.post('/api/areas/dev/start', {}); B.tried = true; steps(); drawDev(r.area); } catch (e) { toast(e.detail || e.message, 'err'); unbusy(b); } };
          const sb = dev.querySelector('#st-start'); if (sb) sb.onclick = () => start(sb);
          const rb = dev.querySelector('#st-restart'); if (rb) rb.onclick = () => start(rb);
          const ob = dev.querySelector('#st-open'); if (ob) ob.addEventListener('click', () => { B.tried = true; steps(); });
        };
        api.get('/api/areas').then(drawDev, () => { dev.innerHTML = '<span class="bx-hint">Dev areas are not open to you.</span>'; });
        return;
      }
      if (w === 'test') {
        const drafts = (ov.tools || []).filter((t) => t.draft && (!t.editor || t.editor.mine));
        const runs = (ov.mine || []).slice(0, 3);
        host.innerHTML = shead(3, `Test — package my drafts as v${esc(ov.next_tag)} and run every test`, 'A few minutes. The whole app’s tests run on it, plus a check of each of your tools. Nothing is live.')
          + `<div class="bx-q"><div class="bx-ql">What goes in</div>${drafts.map((t) => `<div class="bx-row"><span style="flex:1"><b>${esc(t.title)}</b> <span class="bx-hint">${(t.draft_errors || []).length ? `${t.draft_errors.length} to fix` : t.changed ? 'changed' : 'same as live'}</span></span>${(t.draft_errors || []).length ? pill('To fix', 'crit') : pill('Ready', 'good')}</div>`).join('') || '<span class="bx-hint">No saved drafts of yours yet.</span>'}</div>
            <div class="bx-q"><div class="bx-ql">What changed, in a line</div><input class="bx-in" id="rl-notes" type="text" placeholder="e.g. the unpaid list and the Monday email"></div>
            <div class="bx-q"><button class="btn primary" id="rl-go" ${!drafts.length || notReady ? 'disabled' : ''} style="justify-self:start">Run the tests</button>${notReady ? `<span class="bx-hint">${esc(notReady)}</span>` : ''}</div>
            <div class="bx-q"><div class="bx-ql">My test runs</div><div id="rl-runs">${runs.map((v) => `<div class="bx-run ${v.ok ? 'ok' : 'bad'}"><div class="bx-row"><span style="flex:1"><b>${v.ok ? '✓' : '✗'} v${esc(v.tag)}</b> · ${esc(String(v.passed))} passed${v.failed ? `, <b>${esc(String(v.failed))} failed</b>` : ''} <span class="bx-hint">${esc((v.tools || []).join(', '))}${v.status === 'live' ? ' · live' : v.in_test ? ' · in Test' : ''}</span></span><button class="btn sm ghost" data-log="${v.id}">See the test</button></div>${(v.failures || []).length ? `<ul class="bx-fails">${v.failures.slice(0, 6).map((f) => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}<div class="bx-log" id="log-${v.id}" hidden></div></div>`).join('') || '<span class="bx-hint">None yet.</span>'}</div></div>`;
        host.querySelectorAll('[data-log]').forEach((b) => (b.onclick = async () => {
          const box = host.querySelector(`#log-${b.dataset.log}`);
          if (!box.hidden) { box.hidden = true; b.textContent = 'See the test'; return; }
          try { const r = await api.get(`/api/builder/released/${b.dataset.log}/log`); box.innerHTML = `<div class="bx-hint">${esc(String(r.passed))} passed · ${esc(String(r.failed))} failed${r.seconds ? ` · ${esc(String(r.seconds))} s` : ''}${r.council ? ` · council: ${esc(r.council.verdict || '')}` : ''}</div><pre class="bx-pre">${esc(r.output || '(no output)')}</pre>`; box.hidden = false; b.textContent = 'Hide the test'; }
          catch (e) { toast(e.detail || e.message, 'err'); }
        }));
        const go = host.querySelector('#rl-go');
        go.onclick = async () => {
          busy(go, 'Packaging and testing… (a few minutes)');
          try {
            const r = await api.post('/api/builder/release', { notes: host.querySelector('#rl-notes').value });
            const t = r.version.tests || {};
            toast(t.ok ? `v${r.tag} passed its tests — step 4 puts it in Test` : `v${r.tag} failed ${t.failed || ''} test(s) — they are listed`, t.ok ? '' : 'err');
            await refresh(); B.stepPanel(host, t.ok ? 'deploy' : 'test'); if (t.ok) B.select({ kind: 'tool', what: 'deploy' });
          } catch (e) { toast(e.detail || e.message, 'err'); unbusy(go); }
        };
        return;
      }
      // deploy
      const v = lastOk();
      if (!v || !v.ok) {
        host.innerHTML = shead(4, 'Deploy', '') + `<div class="bx-q"><span class="bx-hint">${v ? `v${esc(v.tag)} failed its tests — fix them and test again (step 3).` : 'Nothing of yours has passed its tests yet — step 3 first.'}</span></div>`;
        return;
      }
      const live = v.status === 'live';
      host.innerHTML = shead(4, `Deploy v${esc(v.tag)}`, `${esc((v.tools || []).join(', '))} · ${esc(String(v.passed))} tests passed`)
        + (v.stale && !live ? '<div class="bx-q"><span class="bx-hint err-text">Another version went live after this one was made — test again (step 3) so it does not undo that one.</span></div>' : '')
        + `<div class="bx-q"><div class="bx-ql">a. Put it in Test</div><span class="bx-hint">Everyone who tests gets a sign-in to try it — Work areas.</span>
            ${live ? '<span class="bx-hint">Done.</span>' : v.in_test ? '<div class="row" style="gap:8px">✓ In Test <a class="btn sm" href="#/areas">Open Work areas</a></div>' : `<button class="btn primary" id="dp-test" style="justify-self:start" ${v.stale ? 'disabled' : ''}>Put it in Test</button>`}</div>
          <div class="bx-q"><div class="bx-ql">b. Go live</div><span class="bx-hint">Everyone gets 10 minutes’ notice and their typing is kept; a backup is made first.</span>
            ${live ? '✓ Live' : ov.can_promote ? `<div class="row" style="gap:8px"><button class="btn primary" id="dp-live" ${v.in_test && !v.stale ? '' : 'disabled'}>Go live</button>${!v.in_test && !v.stale ? '<button class="btn ghost" id="dp-skip">Go live without Test…</button>' : ''}</div>` : '<span class="bx-hint">The owner puts it live — it is waiting on Versions.</span>'}</div>`;
      const tb = host.querySelector('#dp-test');
      if (tb) tb.onclick = async () => { busy(tb, 'Starting the Test area…'); try { await api.post(`/api/areas/test/${v.id}`, {}); toast(`v${v.tag} is in Test`); await refresh(); B.stepPanel(host, 'deploy'); } catch (e) { toast(e.detail || e.message, 'err'); unbusy(tb); } };
      const promote = async (b, skip) => {
        if (!await confirmBox(`Put v${v.tag} live? Everyone gets 10 minutes' notice.${skip ? ' It has not been in Test.' : ''}`, 'Go live')) return;
        busy(b, 'Announcing…');
        try { await api.post(`/api/versions/${v.id}/promote?minutes=10${skip ? '&skip_test=1' : ''}`, {}); toast(`v${v.tag} goes live in 10 minutes`); await refresh(); B.stepPanel(host, 'deploy'); }
        catch (e) { toast(e.detail || e.message, 'err'); unbusy(b); }
      };
      const lb = host.querySelector('#dp-live'); if (lb) lb.onclick = () => promote(lb, false);
      const sk = host.querySelector('#dp-skip'); if (sk) sk.onclick = () => promote(sk, true);
    };

    // ---- drawing -----------------------------------------------------------------------------------------
    function drawAll() { buttons(); drawTree(); drawStage(); drawAsk(); }

    function drawTree() {
      const t = $('#bx-tree');
      if (!B.F) { t.innerHTML = ''; return; }
      const W = words(B), s = B.sel || {};
      const fold = (key, label, n, inner) => `<details class="bx-fold" data-fold="${key}" ${(B.folds || {})[key] === false ? '' : 'open'}><summary>${label}<span class="n">${n}</span></summary>${inner}</details>`;
      const home = B.F.manifest.home;
      const pages = Object.entries(B.F.pages).sort(([a, pa], [b, pb]) => (a !== home) - (b !== home) || (pa.order || 100) - (pb.order || 100) || a.localeCompare(b)).map(([n, p]) => {
        const here = n === B.page;
        const blocks = here ? p.layout.sections.flatMap((sec) => sec.columns.flatMap((c) => c.widgets)).map((w) => `<button type="button" class="bx-item sub ${s.kind === 'block' && s.id === w.id ? 'on' : ''}" data-block="${esc(w.id)}">${esc(short(W.text(w.title) || W.text(w.text) || W.text(w.label) || typeName(w.type)))}<span class="k">${esc(typeName(w.type))}</span></button>`).join('') : '';
        return `<button type="button" class="bx-item ${s.kind === 'page' && here ? 'on' : here ? 'here' : ''}" data-page="${esc(n)}">${esc(W.text(p.title) || humanise(n))}<span class="k">${p.access && p.access.roles ? esc(p.access.roles.join(', ')) : ''}${B.F.manifest.home === n ? ' · first' : ''}</span></button>${blocks}`;
      }).join('') + '<button type="button" class="bx-add" data-addpage>+ a page</button>';
      const data = Object.entries(B.F.data).map(([n, d]) => `<button type="button" class="bx-item ${s.kind === 'data' && s.id === n ? 'on' : ''}" data-data="${esc(n)}">${esc(W.text(d.title) || humanise(n))}<span class="k">${(d.columns || []).length} columns</span></button>`).join('') + '<button type="button" class="bx-add" data-adddata>+ a table</button>';
      const rules = Object.entries(B.F.flows).map(([n, f]) => `<button type="button" class="bx-item ${s.kind === 'rule' && s.id === n ? 'on' : ''}" data-rule="${esc(n)}"><i class="bx-dot ${f.enabled === false ? 'off' : ''}"></i>${esc(W.text(f.title) || humanise(n))}</button>`).join('') + '<button type="button" class="bx-add" data-addrule>+ a rule</button>';
      const L = B.F.languages, miss = ['ar', 'pt', 'fr'].reduce((a, l) => a + Object.keys(W.en).filter((k) => W.en[k] && !(L[l] || {})[k]).length, 0);
      const tool = [['settings', 'Name, menu and who uses it', ''], ['words', 'Words in 4 languages', miss ? `${miss} missing` : '✓'], ['about', 'About this tool', '']].map(([k, l, x]) => `<button type="button" class="bx-item ${s.kind === 'tool' && s.what === k ? 'on' : ''}" data-tool="${k}">${l}<span class="k">${esc(x)}</span></button>`).join('');
      const errs = (B.saved && B.saved.errors) || [];
      t.innerHTML = fold('pages', 'Pages', Object.keys(B.F.pages).length, pages) + fold('data', 'Data', Object.keys(B.F.data).length, data) + fold('rules', 'Rules · when this, then that', Object.keys(B.F.flows).length, rules) + fold('tool', 'The tool', '', tool)
        + `<div class="bx-checks ${errs.length ? 'bad' : ''}">${B.dirty ? '<span class="bx-hint">Save to check the tool.</span>' : errs.length ? `<b>To fix before testing</b>${errs.slice(0, 12).map((e) => `<div>${esc(e)}</div>`).join('')}${B.saved && B.saved.source === 'draft' && B.saved.live_exists !== false ? '<button type="button" class="btn sm ghost danger" id="bx-discard" style="margin-top:8px">Throw away this draft — back to the tool as it runs</button>' : ''}` : '✓ Sound — nothing to fix'}</div>`;
      t.querySelectorAll('[data-fold]').forEach((d) => d.addEventListener('toggle', () => { B.folds = { ...(B.folds || {}), [d.dataset.fold]: d.open }; }));
      t.querySelectorAll('[data-page]').forEach((b) => (b.onclick = () => B.select({ kind: 'page', page: b.dataset.page })));
      t.querySelectorAll('[data-block]').forEach((b) => (b.onclick = () => B.select({ kind: 'block', id: b.dataset.block })));
      t.querySelectorAll('[data-data]').forEach((b) => (b.onclick = () => B.select({ kind: 'data', id: b.dataset.data })));
      t.querySelectorAll('[data-rule]').forEach((b) => (b.onclick = () => B.select({ kind: 'rule', id: b.dataset.rule })));
      t.querySelectorAll('[data-tool]').forEach((b) => (b.onclick = () => B.select({ kind: 'tool', what: b.dataset.tool })));
      t.querySelector('[data-addpage]').onclick = () => { let n = 'page', i = 2; while (B.F.pages[n]) n = `page_${i++}`; B.F.pages[n] = { title: `page.${n}.title`, menu: true, style: { width: 'normal' }, layout: { sections: [{ id: 's1', columns: [{ width: 12, widgets: [] }] }] }, _new: true }; words(B).en[`page.${n}.title`] = 'New page'; B.page = n; B.dirty = true; B.select({ kind: 'page' }); };
      t.querySelector('[data-adddata]').onclick = () => { let n = 'table', i = 2; while (B.F.data[n]) n = `table_${i++}`; B.F.data[n] = { kind: 'table', title: `data.${n}.title`, write: 'everyone', scope: 'all', columns: [{ key: 'name', type: 'text', label: 'col.name', required: true }], _new: true }; const W2 = words(B); W2.en[`data.${n}.title`] = 'New table'; if (!W2.en['col.name']) W2.en['col.name'] = 'Name'; B.dirty = true; B.select({ kind: 'data', id: n }); };
      t.querySelector('[data-addrule]').onclick = () => { const n = B.newRule('button'); B.dirty = true; B.select({ kind: 'rule', id: n }); };
      const dsc = t.querySelector('#bx-discard');  // 1.0.87: a draft that went wrong can be thrown away here
      if (dsc) dsc.onclick = async () => {
        if (!(await confirmBox('Throw away every change in this draft? The tool as it runs now stays as it is.', 'Throw it away'))) return;
        try { await api.del(`/api/builder/tools/${encodeURIComponent(B.slug)}/draft`); B.dirty = false; toast('Draft thrown away'); ov = B.ov = await api.get('/api/builder'); await open(B.slug); } catch (e) { toast(e.detail || e.message, 'err'); }
      };
    }

    function drawStage() {
      const hd = $('#bx-stagehd'), host = $('#bx-page');
      if (!B.F) { hd.innerHTML = ''; host.innerHTML = '<div class="bx-empty">Open a tool at the top — or press <b>New tool</b>.</div>'; return; }
      const p = B.F.pages[B.page];
      hd.innerHTML = `<span>The page as people see it${B.tryMode ? ' — try it: search, sort, click' : ' — click a part to change it'}</span><span style="flex:1"></span>
        ${seg2('mode', [['edit', 'Change'], ['try', 'Try it']], B.tryMode ? 'try' : 'edit')}
        <select class="bx-in" id="bx-lang" aria-label="Language">${LANG_ORDER.map((l) => `<option value="${l}" ${l === B.previewLang ? 'selected' : ''}>${esc(LANGS[l])}</option>`).join('')}</select>`;
      hd.querySelectorAll('[data-seg="mode"] button').forEach((b) => (b.onclick = () => { B.tryMode = b.dataset.v === 'try'; if (B.tryMode) { B.tried = true; steps(); } drawStage(); }));
      hd.querySelector('#bx-lang').onchange = (e) => { B.previewLang = e.target.value; drawStage(); };
      if (!p) { host.innerHTML = '<div class="bx-empty">This tool has no page yet — <b>+ a page</b> on the left.</div>'; return; }
      const F = clean(B.F), L = B.previewLang;
      const strings = { ...(F.languages.en || {}), ...Object.fromEntries(Object.entries(F.languages[L] || {}).filter(([, v]) => v)) };
      const S = (k) => (k && strings[k]) || (k && !/^[a-z][a-z0-9_.]*$/.test(k) ? k : '') || '';
      const info = {}, lay = JSON.parse(JSON.stringify(p.layout));
      const all = lay.sections.flatMap((s) => s.columns.flatMap((c) => c.widgets));
      for (const w of all) {
        const src = w.source || {};
        if (w.type === 'button') { const f = F.flows[w.flow] || {}; info[w.id] = { flow: { name: w.flow, title: S(f.title) || w.flow || '—', enabled: f.enabled !== false } }; }
        else if (src.provider) info[w.id] = { provider: ov.providers.find((x) => x.name === src.provider) || { columns: [], filters: [] } };
        else if (src.data) info[w.id] = { block: { name: src.data, ...(F.data[src.data] || { columns: [] }) }, can: { add: false } };
        else if (src.sheet) info[w.id] = { sheet: { name: src.sheet, columns: ((ov.sheets || []).find((x) => x.slug === src.sheet) || {}).columns || [] } };
        else if (src.api) info[w.id] = { api: { columns: src.columns || [] } };
        if (w.type === 'metrics') info[w.id] = { metrics: (w.items || []).map((x) => x.metric) };
      }
      host.dir = L === 'ar' ? 'rtl' : 'ltr';
      host.className = 'bx-page eng';
      applyTheme(host, F.manifest.theme);
      const roles = (p.access && p.access.roles) || [];
      host.innerHTML = `<div class="bx-pagehd"><h1>${esc(S(F.manifest.title) || B.slug)} <span class="muted" style="font-weight:400">· ${esc(S(p.title))}</span></h1>${p.intro ? `<p>${esc(S(p.intro))}</p>` : ''}${roles.length ? `<div class="bx-hint">Only for: ${esc(roles.join(', '))}</div>` : ''}</div><div id="bx-lay" class="eng-page w-${esc((p.style || {}).width || 'normal')}"></div>`;
      const env = {
        slug: B.slug, page: B.page, S, strings, me: ctx.me, info,
        go: () => toast('Links open on the live page.'),
        rows: async (w, qs) => { const params = Object.fromEntries(new URLSearchParams(qs || '')); for (const k of ['q', 'sort', 'desc', 'limit', 'offset']) delete params[k]; const wd = all.find((x) => x.id === w.id) || w; return api.post(`/api/builder/tools/${B.slug}/preview-widget`, { page: B.page, widget: wd, data: F.data, params }); },
        assetUrl: (name) => `/api/builder/tools/${B.slug}/assets/${encodeURIComponent(name)}`,
        codeUrl: (w) => `/api/builder/tools/${B.slug}/html/${encodeURIComponent(B.page)}/${encodeURIComponent(w.id)}`,
        metrics: (keys) => api.get('/api/engine/metrics?keys=' + encodeURIComponent(keys.join(','))),
        write: null,
      };
      const layHost = host.querySelector('#bx-lay');
      renderLayout(layHost, lay, env);
      if (B.tryMode) return;
      // change mode: a click on a part selects it; "+ add here" before, between and after the parts
      layHost.querySelectorAll('.eng-sec').forEach((secEl, si) => secEl.querySelectorAll('.eng-col').forEach((colEl, ci) => {
        const ws = [...colEl.querySelectorAll(':scope > .eng-w')];
        const ins = (at) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'bx-ins' + (B.sel && B.sel.kind === 'add' && B.sel.sec === si && B.sel.col === ci && B.sel.at === at ? ' on' : ''); b.textContent = '+ add here'; b.onclick = (e) => { e.stopPropagation(); B.select({ kind: 'add', sec: si, col: ci, at }); }; return b; };
        colEl.insertBefore(ins(0), ws[0] || null);
        ws.forEach((wEl, i) => {
          wEl.after(ins(i + 1));
          const id = wEl.dataset.w, w = all.find((x) => String(x.id) === id);
          wEl.classList.add('bx-part'); if (B.sel && B.sel.kind === 'block' && String(B.sel.id) === id) wEl.classList.add('bx-on');
          const tag = document.createElement('span'); tag.className = 'bx-tag'; tag.textContent = w ? typeName(w.type) : ''; wEl.prepend(tag);
          wEl.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); B.select({ kind: 'block', id }); }, true);
        });
      }));
    }
    const seg2 = (name, opts, cur) => `<div class="bx-seg" data-seg="${name}">${opts.map(([v, l]) => `<button type="button" data-v="${v}" class="${v === cur ? 'on' : ''}">${esc(l)}</button>`).join('')}</div>`;

    function drawAsk() {
      const host = $('#bx-ask');
      const top = host.scrollTop;
      if (!B.F) { host.innerHTML = ''; return; }
      try { renderAsk(host, B); } catch (e) { console.error(e); host.innerHTML = `<div class="err-box">${esc(e.message)}</div>`; }
      host.scrollTop = top;
    }

    // ---- start -------------------------------------------------------------------------------------------------
    fillPicker();
    if (ctx.params.new && /^[a-z][a-z0-9-]{1,40}$/.test(ctx.params.new) && !(ov.tools || []).some((t) => t.slug === ctx.params.new)) startNew(ctx.params.title || '', ctx.params.new);
    else if (ctx.params.t) open(ctx.params.t);
    else if ((ov.tools || []).length === 1) open(ov.tools[0].slug);
    else drawAll();
    return () => { document.removeEventListener('keydown', onKey); root.classList.remove('bx-host'); };
  },
};
