// IFRS 16 (1.0.92; 1.0.93 the master report, the ledger check as the desktop): the same page as CBX-Connect 2 — the source files kept, a month worked out (the Lease
// Accelerator import workbooks, the monthly report, the working papers), the ledger check, LA vs RR06 (ABSA),
// Sequentra and the Rates table. Ported from CBX-Connect 2 (templates/ifrs16.html and static/js/ifrs16.js); the
// server side is cbx/api/ifrs16_page_api.py over cbx/ifrs16/page.py (2.x's service, line for line).
import { api as API } from '../api.js';
import { toast as TOAST } from '../ui.js';

function el(tag, attrs) {
  const n = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    const v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') { for (const s in v) if (v[s] !== null && v[s] !== undefined) n.style[s] = v[s]; }
    else if (k === 'class') n.className = v;
    else if (v === true) n.setAttribute(k, '');
    else n.setAttribute(k, v);
  }
  for (let i = 2; i < arguments.length; i++) add(n, arguments[i]);
  return n;
}
function add(n, c) {
  if (c === null || c === undefined || c === false) return;
  if (Array.isArray(c)) { c.forEach((x) => add(n, x)); return; }
  n.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
}
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const toast = (m, kind) => TOAST(m, kind === 'err' ? 'err' : '');
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.detail || e.message, 'err'); throw e; } }
function api(path, o) {
  o = o || {};
  if (o.form) return API.post(path, o.form);
  if (o.body !== undefined) return API.post(path, o.body);
  if (o.method === 'POST') return API.post(path, {});
  if (o.method === 'DELETE') return API.del(path);
  return API.get(path);
}
const B = '';

const k = (t) => el('span', { class: 'k' }, t);
const lab = (title, input, hint) => el('label', { class: 'stack', style: { gap: '4px' } }, k(title), hint ? el('span', { class: 'small muted' }, hint) : null, input);
const sec = (title, kk, ...body) => el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, title), kk ? el('span', { class: 'k', id: kk.id || null }, kk.text || kk) : null), ...body);
const fileIn = (name, accept, required) => el('input', { type: 'file', name, accept, required: required || null });

function skeleton() {
  return el('div', { class: 'ifv', id: 'ifrs' },
    el('div', { class: 'head' }, el('div', null, el('h1', null, 'IFRS 16'),
      el('p', null, "The month's Lease Accelerator imports from the movements and the LA register, or from Sequentra, with the reconciliation. The ledger check, and LA vs RR06."))),
    el('div', { class: 'tabs', role: 'tablist' }, [['months', 'Monthly workflow'], ['ledger', 'Ledger check'], ['recon', 'LA vs RR06'], ['sequentra', 'Sequentra'], ['rates', 'Rates']]
      .map(([t, l]) => el('button', { type: 'button', role: 'tab', 'data-tab': t, 'aria-selected': 'false' }, l))),
    // monthly workflow
    el('div', { id: 'tab-months', class: 'grid21' },
      el('div', { class: 'stack' },
        el('section', { class: 'sec', id: 'upform' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, '1 · The source files'), k('kept, so a month can be run again')),
          el('form', { id: 'upf', class: 'stack', style: { gap: '10px' } },
            lab('The LA register *', fileIn('la', '.xlsx,.xls,.xlsm', true), 'the LA Portfolio Transactions report (the .xls is fine)'),  // 1.0.106: which file goes where
            lab('The RR06 / ARO06 movements *', fileIn('movements', '.xlsx,.xls,.xlsm', true), 'the whole RR06 / ARO06 export, with its Contract Movement tab'),
            el('details', null, el('summary', { class: 'small', style: { cursor: 'pointer' } }, 'More files (optional)'),
              el('div', { class: 'stack', style: { gap: '10px', paddingTop: '10px' } },
                lab('The Sequentra export', fileIn('sequentra', '.xlsx,.xls'), "the 'Validation - Record Info' export"),
                lab('The step payments report', fileIn('steps', '.xlsx,.xls'), "the 'PIW Step Payments' report"),
                lab('The LA BU asset report', fileIn('assets', '.xlsx,.xls,.csv'), 'fills the Asset IDs of terminations'),
                lab('The CIW master', fileIn('ciw', '.xlsx,.xls')))),
            el('div', null, el('button', { class: 'btn primary', type: 'submit' }, 'Keep and read them')))),
        sec('2 · Kept uploads', 'pick a month to work out', el('div', { id: 'uploads' })),
        sec('Master report', 'the month and the ledger check in one workbook',
          el('form', { id: 'masf', class: 'stack', style: { gap: '10px' } },
            el('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap' } },
              el('label', { class: 'stack', style: { gap: '4px', flex: '1 1 180px' } }, k('Upload *'), el('select', { class: 'in', name: 'upload_id', required: true })),
              lab('Month *', el('select', { class: 'in', name: 'month', required: true }))),
            lab('The ledger export', fileIn('ledger', '.xlsx,.xls'), 'optional: adds the ledger check and the Recon'),
            el('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap' } }, lab('As at (month)', el('input', { class: 'in', name: 'as_at', type: 'month' })),
              el('label', { class: 'stack', style: { gap: '4px', flex: '1 1 160px' } }, k('Entity on the Recon'), el('input', { class: 'in', name: 'entity' }))),
            el('div', null, el('button', { class: 'btn primary', type: 'submit' }, 'Make the master report'))),
          el('div', { id: 'masters' }))),
      sec('3 · Months', 'import workbooks · report · working papers', el('div', { id: 'months' }))),
    // ledger
    el('div', { id: 'tab-ledger', class: 'grid21 hide' },
      sec('Check a ledger', null,
        el('form', { id: 'ledf', class: 'stack', style: { gap: '10px' } },
          lab('The ledger export *', fileIn('file', '.xlsx,.xls', true)),
          lab('As at (month)', el('input', { class: 'in', name: 'as_at', type: 'month' })),
          lab('Months back', el('input', { class: 'in', name: 'months_back', type: 'number', min: '1', max: '120', value: '12', style: { width: '110px' } })),
          lab('Periods', el('select', { class: 'in', name: 'span' }, el('option', { value: 'life' }, 'Full lease life'), el('option', { value: 'from' }, 'From the as-at month on'), el('option', { value: 'back' }, 'Only the months back'))),
          lab('Lease names, dates and entities from', el('select', { class: 'in', name: 'upload_id' }, el('option', { value: '' }, 'Your newest upload'))),
          lab('Entity on the Recon', el('input', { class: 'in', name: 'entity' })),
          lab('The Sequentra validation report', fileIn('sequentra', '.xlsx,.xls,.csv'), 'for the discount rates · left empty, the one kept with that upload is used'),
          el('div', null, el('button', { class: 'btn primary', type: 'submit' }, 'Check it')))),
      sec('Checks', null, el('div', { id: 'ledger' }))),
    // LA vs RR06
    el('div', { id: 'tab-recon', class: 'hide stack', style: { gap: '18px' } },
      el('div', { class: 'grid21' },
        sec('LA vs RR06', 'ABSA Bank Limited · South Africa',
          el('form', { id: 'recf', class: 'stack', style: { gap: '10px' } },
            lab('Period *', el('input', { class: 'in', name: 'period', type: 'month', required: true })),
            lab('The LA transaction report', fileIn('la', '.xlsx,.xls,.xlsm')),
            lab('The RR06 contract list', fileIn('rr06', '.xlsx,.xls,.xlsm')),
            lab('Or use the files kept with an upload', el('select', { class: 'in', name: 'upload_id' }, el('option', { value: '' }, 'No, the files above'))),
            el('div', null, el('button', { class: 'btn primary', type: 'submit' }, 'Reconcile'))),
          el('div', { class: 'small muted' }, 'Lease reference is the first 8 characters of Transaction #. Disposed (Modification) is left out. Leases on the RR06 and not on LA are sorted, first match wins: Receivable, Future acquisition, Monthly, A year or less, Zero rental. The rest are exceptions to investigate.')),
        sec('Reconciliations', null, el('div', { id: 'recons' }))),
      el('section', { class: 'sec hide', id: 'recdetail' })),
    // Sequentra
    el('div', { id: 'tab-sequentra', class: 'grid21 hide' },
      el('div', { class: 'stack' },
        sec('Ask Sequentra', { id: 'connword', text: '' },
          el('form', { id: 'apif', class: 'stack', style: { gap: '10px' } },
            lab('What', el('select', { class: 'in', name: 'kind' }, el('option', { value: 'check' }, 'Check every report (rows and fields, no data kept)'), el('option', { value: 'download' }, 'Download every report (CSV, zipped)'))),
            el('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap' } }, lab('Month', el('input', { class: 'in', name: 'month', type: 'month' })), lab('To month', el('input', { class: 'in', name: 'to_month', type: 'month' }))),
            el('div', null, el('button', { class: 'btn primary', type: 'submit' }, 'Ask')))),
        el('section', { class: 'sec hide', id: 'connsec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, 'The connection'), k('admins · the key is never shown again')),
          el('div', { class: 'small', id: 'conninfo' }),
          el('form', { id: 'connf', class: 'stack', style: { gap: '10px' } },
            lab('Address', el('input', { class: 'in', name: 'base_url', value: 'https://api.excellerate.tech/sequentra/' })),
            lab('Key sent in the header', el('input', { class: 'in', name: 'header_name', value: 'Clientkey' })),
            lab('The key (the one used in Power BI)', el('input', { class: 'in', name: 'secret', type: 'password', autocomplete: 'new-password', placeholder: 'Paste to set or replace, or leave empty to keep it' })),
            el('label', { style: { display: 'flex', gap: '8px', alignItems: 'center' } }, el('input', { type: 'checkbox', name: 'is_active', checked: true }), el('span', { class: 'small' }, 'Switched on')),
            el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, el('button', { class: 'btn sm primary', type: 'submit' }, 'Save'), el('button', { class: 'btn sm', type: 'button', id: 'conntest' }, 'Test it'))))),
      sec('What Sequentra said', null, el('div', { id: 'apiruns' }))),
    // rates
    el('div', { id: 'tab-rates', class: 'hide stack', style: { gap: '18px' } },
      sec('SA discount rates', 'one row a month, bands A to F, as treasury sends them · shared',
        el('div', { class: 'scrollx' }, el('div', { id: 'rates', style: { minWidth: '720px' } })),
        el('form', { id: 'ratef', style: { display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
          lab('Month', el('input', { class: 'in', name: 'month', type: 'month', required: true })),
          'abcdef'.split('').map((b) => lab(b.toUpperCase() + ' %', el('input', { class: 'in', name: b, type: 'number', step: '0.0001', min: '0', max: '50', style: { width: '92px' } }))),
          el('button', { class: 'btn sm primary', type: 'submit' }, 'Save the month')),
        el('div', { class: 'small muted' }, "Without a row for the month, SA leases take their rate from Sequentra and the month's reconciliation flags it for review."))));
}

export default {
  title: 'IFRS 16',
  async render(rootEl, ctx) {
    rootEl.innerHTML = '';
    const root = skeleton();
    rootEl.appendChild(root);
    const $ = (id) => root.querySelector('#' + id);
    const ST = { new: ["New", ""], reading: ["Reading", "warn"], ready: ["Ready", "good"], failed: ["Didn't work", "bad"], working: ["Working", "warn"], done: ["Done", "good"] };
    let data = null, timer = null;

  function tab(name) {
    root.querySelectorAll(".tabs [data-tab]").forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false"));
    ["months", "ledger", "recon", "sequentra", "rates"].forEach((t) => $("tab-" + t).classList.toggle("hide", t !== name));
    try { history.replaceState(null, "", "#/ifrs16" + (name === "months" ? "" : "?tab=" + name)); } catch (e) { /* not in a browser that allows it */ }
  }
  root.querySelectorAll(".tabs [data-tab]").forEach((b) => b.addEventListener("click", () => tab(b.dataset.tab)));

  const pill = (s) => el("span", { class: "pill " + ((ST[s] || [])[1] || "") }, (ST[s] || [s])[0]);
  const file = (f, label) => f ? el("a", { class: "link small", href: `${B}/api/ifrs16/files/${f.id}` }, label || f.name) : null;
  const when = (iso) => iso ? new Date(iso).toLocaleString("en-ZA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const monthLabel = (v) => { const d = new Date(v + "-01T00:00:00"); return d.toLocaleDateString("en-ZA", { month: "long", year: "numeric" }); };

  async function load() {
    if (!document.body.contains(root)) { clearTimeout(timer); return; } // the page was left
    data = await api("/api/ifrs16");
    drawUploads(); drawMonths(); drawMasters(); drawLedger(); drawRecons(); drawApi(); drawRates(); drawConn();
    const busy = data.uploads.some((u) => u.status === "reading") || data.months.some((m) => m.status === "working") || data.ledger.some((r) => r.status === "working") || data.api.some((r) => r.status === "working") || (data.recons || []).some((r) => r.status === "working") || (data.masters || []).some((r) => r.status === "working");
    clearTimeout(timer);
    if (busy) timer = setTimeout(load, 3000);
    // 1.0.106: a viewer's card that held only the form was left as an empty box (the title and nothing under it) — it goes too
    if (!data.can_write) root.querySelectorAll("#upf, #masf, #ledf, #recf, #apif, #ratef").forEach((f) => {
      f.classList.add("hide");
      const s = f.closest("section");
      if (s && ![...s.querySelectorAll("[id]")].some((x) => x !== f && !f.contains(x) && x.childElementCount)) s.classList.add("hide");
    });
  }

  // ---- monthly workflow ------------------------------------------------------------------------------
  $("upf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    ["la", "movements", "sequentra", "steps", "assets", "ciw"].forEach((k) => { const f = e.target[k].files[0]; if (f) fd.append(k, f); });
    await act(() => api("/api/ifrs16/uploads", { form: fd }), "Kept — reading them for their months.");
    e.target.reset(); load();
  });

  function drawUploads() {
    const box = clear($("uploads"));
    if (!data.uploads.length) { box.appendChild(el("div", { class: "small muted" }, data.can_write ? "Nothing kept yet. Add the LA register and the movements above." : "Nothing kept yet.")); return; }  // 1.0.106: a viewer has no "above"
    data.uploads.slice(0, 12).forEach((u) => {
      const sel = el("select", { class: "field", style: { minHeight: "30px", fontSize: "12.5px", width: "auto" } }, u.months.map((m) => el("option", { value: m.value }, m.rows ? `${m.label} · ${m.rows} row${m.rows === 1 ? "" : "s"}` : m.label)));  // 1.0.106: "1 row", not "1 rows"
      const src = el("select", { class: "field", style: { minHeight: "30px", fontSize: "12.5px", width: "auto" } }, el("option", { value: "files" }, "From the files"), el("option", { value: "api" }, "From the Sequentra API"));
      const go = el("button", { class: "btn sm", type: "button", onclick: async () => {
        await act(() => api(`/api/ifrs16/uploads/${u.id}/month`, { body: { month: sel.value, source: src.value } }), `Working out ${monthLabel(sel.value)} — it shows on the right when it's done.`);
        load();
      } }, "Work it out");
      const names = Object.entries(u.files).filter(([, f]) => f).map(([, f]) => f.name).join(" · ");
      box.appendChild(el("div", { class: "row", style: { alignItems: "flex-start" } },
        el("div", { style: { flex: "1 1 100%", minWidth: "0" } },
          el("div", { style: { display: "flex", gap: "10px", alignItems: "baseline", flexWrap: "wrap" } }, el("strong", null, `Upload #${u.id}`), u.movement ? el("span", { class: "mono small" }, u.movement) : null, pill(u.status),
            el("span", { class: "small muted" }, (data.is_admin && u.owner ? u.owner + " · " : "") + when(u.at))),
          el("div", { class: "small muted", style: { marginTop: "2px", wordBreak: "break-word" } }, names),
          u.note ? el("div", { class: "small", style: { marginTop: "4px", whiteSpace: "pre-line", color: u.status === "failed" ? "var(--bad)" : "var(--ink2)" } }, u.note) : null),
        u.status === "ready" && data.can_write ? el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } }, sel, src, go)
          : u.status === "failed" && data.can_write ? el("button", { class: "btn sm", type: "button", onclick: async () => { await act(() => api(`/api/ifrs16/uploads/${u.id}/read`, { method: "POST" }), "Reading it again."); load(); } }, "Read again") : null));
    });
  }

  function drawMonths() {
    const box = clear($("months"));
    if (!data.months.length) { box.appendChild(el("div", { class: "small muted" }, "No months yet.")); return; }
    data.months.forEach((m) => {
      const recon = el("pre", { class: "mono small hide", style: { whiteSpace: "pre-wrap", margin: "8px 0 0", padding: "10px 12px", background: "var(--surf)", border: "1px solid var(--rule)", maxHeight: "340px", overflow: "auto" } }, m.recon || "");
      box.appendChild(el("div", { class: "row", style: { alignItems: "flex-start" } },
        el("div", { style: { flex: "1 1 100%", minWidth: "0" } },
          el("div", { style: { display: "flex", gap: "12px", alignItems: "baseline", flexWrap: "wrap" } },
            el("strong", { style: { fontSize: "15px" } }, monthLabel(m.month)), el("span", { class: "small muted" }, m.source === "api" ? "from the Sequentra API" : "from the files"), pill(m.status),
            m.status === "done" ? el("span", { class: "mono small" }, `${m.imports} import workbook${m.imports === 1 ? "" : "s"}`) : null,
            el("div", { class: "grow" }), el("span", { class: "small muted" }, (data.is_admin && m.owner ? m.owner + " · " : "") + when(m.finished_at || m.at))),
          m.note ? el("div", { class: "small", style: { color: m.status === "failed" ? "var(--bad)" : "var(--ink2)", marginTop: "4px", whiteSpace: "pre-line" } }, m.note) : null,
          m.status === "done" ? el("div", { style: { display: "flex", gap: "16px", flexWrap: "wrap", marginTop: "6px" } },
            file(m.workbooks, "Import workbooks (zip)"), file(m.report, "Monthly report"), file(m.papers, "Working papers"),
            m.recon ? el("button", { class: "link small", type: "button", onclick: () => recon.classList.toggle("hide") }, "The reconciliation") : null) : null,
          recon)));
    });
  }

  // ---- master report -----------------------------------------------------------------------------------
  function uploadOptions(sel, first, onlyReady) {
    const keep = sel.value;
    clear(sel);
    if (first) sel.appendChild(el("option", { value: "" }, first));
    data.uploads.filter((u) => !onlyReady || u.status === "ready").slice(0, 20).forEach((u) => sel.appendChild(el("option", { value: u.id }, `Upload #${u.id}` + (u.files.la ? ` · ${u.files.la.name}` : ""))));
    if ([...sel.options].some((o) => o.value === keep)) sel.value = keep;
  }
  function masterMonths() {
    const f = $("masf"), u = data.uploads.find((x) => String(x.id) === f.upload_id.value), keep = f.month.value;
    clear(f.month);
    ((u && u.months) || []).forEach((m) => f.month.appendChild(el("option", { value: m.value }, m.label)));
    if ([...f.month.options].some((o) => o.value === keep)) f.month.value = keep;
  }
  $("masf").upload_id.addEventListener("change", masterMonths);
  $("masf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, fd = new FormData();
    ["month", "as_at", "entity"].forEach((k) => fd.append(k, f[k].value));
    if (f.ledger.files[0]) fd.append("ledger", f.ledger.files[0]);
    await act(() => api(`/api/ifrs16/uploads/${f.upload_id.value}/master`, { form: fd }), "Making the master report.");
    f.ledger.value = ""; load();
  });
  function drawMasters() {
    uploadOptions($("masf").upload_id, null, true); masterMonths();
    uploadOptions($("ledf").upload_id, "Your newest upload", false);
    const box = clear($("masters"));
    (data.masters || []).slice(0, 12).forEach((r) => box.appendChild(el("div", { class: "row", style: { alignItems: "flex-start" } },
      el("div", { style: { flex: "1 1 100%", minWidth: "0" } },
        el("div", { style: { display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "baseline" } }, el("strong", null, monthLabel(r.month)), pill(r.status),
          el("span", { class: "small muted" }, (r.ledger ? "with the ledger check · " : "") + when(r.at)), el("div", { class: "grow" }), file(r.report, "The master report")),
        r.note ? el("div", { class: "small bad", style: { whiteSpace: "pre-line" } }, r.note) : null))));
  }

  // ---- ledger ------------------------------------------------------------------------------------------
  $("ledf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    if (!fd.get("upload_id")) fd.delete("upload_id");
    await act(() => api("/api/ifrs16/ledger", { form: fd }), "Checking it.");
    e.target.reset(); load();
  });
  function drawLedger() {
    const box = clear($("ledger"));
    if (!data.ledger.length) { box.appendChild(el("div", { class: "small muted" }, "No checks yet.")); return; }
    data.ledger.forEach((r) => box.appendChild(el("div", { class: "row", style: { alignItems: "flex-start" } },
      el("div", { style: { flex: "1 1 100%", minWidth: "0" } },
        el("div", { style: { display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "baseline" } }, el("strong", null, r.ledger ? r.ledger.name : "Ledger"), pill(r.status),
          el("span", { class: "small muted" }, `as at ${r.as_at || "today"} · ${r.span_words || r.months_back + " months back"} · ${when(r.at)}`), el("div", { class: "grow" }), file(r.report, "The consolidated report"), file(r.simple, "The simple report")),
        r.note ? el("div", { class: "small bad", style: { whiteSpace: "pre-line" } }, r.note) : null,
        r.overview ? el("pre", { class: "mono small", style: { whiteSpace: "pre-wrap", margin: "6px 0 0" } }, r.overview) : null))));
  }

  // ---- LA vs RR06 --------------------------------------------------------------------------------------
  $("recf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, fd = new FormData();
    fd.append("period", f.period.value);
    if (f.la.files[0]) fd.append("la", f.la.files[0]);
    if (f.rr06.files[0]) fd.append("rr06", f.rr06.files[0]);
    if (f.upload_id.value) fd.append("upload_id", f.upload_id.value);
    await act(() => api("/api/ifrs16/recon", { form: fd }), "Reconciling — it shows on the right when it's done.");
    f.reset(); load();
  });
  let openRecon = null;
  function drawRecons() {
    const sel = $("recf").upload_id, keep = sel.value;
    clear(sel).appendChild(el("option", { value: "" }, "No, the files above"));
    data.uploads.filter((u) => u.files.la && u.files.movements).slice(0, 12).forEach((u) => sel.appendChild(el("option", { value: u.id }, `Upload #${u.id} · ${u.files.la.name} · ${u.files.movements.name}`)));
    sel.value = keep;
    const box = clear($("recons"));
    const rows = data.recons || [];
    if (!rows.length) { box.appendChild(el("div", { class: "small muted" }, "None yet.")); return; }
    rows.forEach((r) => {
      const exc = (r.summary.find((x) => x.Category === "Exception") || {}).Leases;
      box.appendChild(el("div", { class: "row", style: { alignItems: "flex-start" } },
        el("div", { style: { flex: "1 1 100%", minWidth: "0" } },
          el("div", { style: { display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "baseline" } }, el("strong", null, monthLabel(r.period)), pill(r.status),
            r.status === "done" ? el("span", { class: "small muted" }, `${exc || 0} exception${exc === 1 ? "" : "s"}`) : null, el("div", { class: "grow" }),
            el("span", { class: "small muted" }, when(r.at))),
          el("div", { class: "small muted", style: { wordBreak: "break-word" } }, [r.la && r.la.name, r.rr06 && r.rr06.name].filter(Boolean).join(" · ")),
          r.note ? el("div", { class: "small bad", style: { whiteSpace: "pre-line" } }, r.note) : null,
          r.status === "done" ? el("div", { style: { display: "flex", gap: "16px", flexWrap: "wrap", marginTop: "6px" } },
            el("button", { class: "link small", type: "button", onclick: () => showRecon(r.id) }, "Open"), file(r.report, "The reconciliation (Excel)")) : null)));
    });
    if (openRecon && !rows.some((r) => r.id === openRecon)) { openRecon = null; $("recdetail").classList.add("hide"); }
  }

  const money = (v) => (v === null || v === undefined || v === "") ? "" : Number(v).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  function csv(rows, name) {
    if (!rows.length) return;
    const cols = Object.keys(rows[0]);
    const q = (v) => { const t = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(t) ? '"' + t.replace(/"/g, '""') + '"' : t; };
    const text = [cols.map(q).join(",")].concat(rows.map((r) => cols.map((c) => q(r[c])).join(","))).join("\r\n");
    const a = el("a", { href: URL.createObjectURL(new Blob(["\ufeff" + text], { type: "text/csv" })), download: name });
    document.body.appendChild(a); a.click(); a.remove();
  }
  function listTable(rows, title, fname) {
    const wrap = el("div", { class: "stack", style: { gap: "8px" } });
    const cols = rows.length ? Object.keys(rows[0]) : [];
    const find = el("input", { class: "field", placeholder: "Find a lease", style: { maxWidth: "260px" } });
    const body = el("div", { class: "scrollx" });
    const draw = () => {
      const q = find.value.trim().toLowerCase();
      const shown = q ? rows.filter((r) => cols.some((c) => String(r[c] ?? "").toLowerCase().includes(q))) : rows;
      clear(body).appendChild(el("table", { class: "tbl" }, el("tr", null, cols.map((c) => el("th", null, c))),
        shown.slice(0, 500).map((r) => el("tr", null, cols.map((c) => el("td", { class: /charges|payment/i.test(c) ? "mono" : "", style: /charges|payment/i.test(c) ? { textAlign: "right" } : null },
          /charges|payment/i.test(c) ? money(r[c]) : String(r[c] ?? "")))))));
      if (shown.length > 500) body.appendChild(el("div", { class: "small muted" }, `First 500 of ${shown.length} shown. The CSV and the Excel file have them all.`));
    };
    find.addEventListener("input", draw);
    wrap.appendChild(el("div", { style: { display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" } }, el("strong", { style: { fontSize: "15px" } }, `${title} · ${rows.length}`),
      el("div", { class: "grow" }), find, rows.length ? el("button", { class: "btn ghost sm", type: "button", onclick: () => csv(rows, fname) }, "Download this list") : null));
    if (!rows.length) wrap.appendChild(el("div", { class: "small muted" }, "None."));
    else { wrap.appendChild(body); draw(); }
    return wrap;
  }
  async function showRecon(id) {
    openRecon = id;
    const d = await api(`/api/ifrs16/recon/${id}`);
    const box = clear($("recdetail"));
    box.classList.remove("hide");
    const list = el("div", { style: { marginTop: "18px" } });
    const pick = (title, rows, fname) => { clear(list).appendChild(listTable(rows, title, fname)); list.scrollIntoView({ behavior: "smooth", block: "start" }); };
    const tag = d.period;
    box.appendChild(el("div", { class: "sec-h" }, el("h2", { class: "h2" }, `LA vs RR06 · ${monthLabel(d.period)}`), el("div", { class: "grow" }), file(d.report, "The reconciliation (Excel)")));
    box.appendChild(el("div", { style: { display: "flex", gap: "22px", flexWrap: "wrap", margin: "4px 0 16px" } },
      d.overview.filter((o) => !["Lessee", "Asset location"].includes(o.Item)).map((o) => el("div", null, el("div", { class: "k" }, o.Item), el("div", { style: { fontSize: "18px", fontWeight: "600" } }, String(o.Value))))));
    const t = el("table", { class: "tbl" }, el("tr", null, ["On the RR06, not on LA", "Rule", "Leases", "Current charges", ""].map((h) => el("th", null, h))));
    d.summary.forEach((s) => {
      const rows = d.rr06_not_la.filter((r) => r.Category === s.Category);
      t.appendChild(el("tr", null, el("td", null, el("strong", null, s.Category)), el("td", { class: "small muted" }, s.Rule), el("td", { class: "mono" }, String(s.Leases)),
        el("td", { class: "mono", style: { textAlign: "right" } }, money(s["Current Charges"])),
        el("td", null, s.Leases ? el("button", { class: "link small", type: "button", onclick: () => pick(s.Category, rows, `RR06 not on LA - ${s.Category} - ${tag}.csv`) }, "See them") : null)));
    });
    const total = d.rr06_not_la.length;
    t.appendChild(el("tr", null, el("td", null, el("strong", null, "All")), el("td"), el("td", { class: "mono" }, String(total)),
      el("td", { class: "mono", style: { textAlign: "right" } }, money(d.summary.reduce((a, s) => a + (Number(s["Current Charges"]) || 0), 0))),
      el("td", null, total ? el("button", { class: "link small", type: "button", onclick: () => pick("On the RR06, not on LA", d.rr06_not_la, `RR06 not on LA - ${tag}.csv`) }, "See them") : null)));
    t.appendChild(el("tr", null, el("td", null, el("strong", null, "Booked on LA, not on the RR06")), el("td", { class: "small muted" }, "To investigate"), el("td", { class: "mono" }, String(d.la_not_rr06.length)), el("td"),
      el("td", null, d.la_not_rr06.length ? el("button", { class: "link small", type: "button", onclick: () => pick("Booked on LA, not on the RR06", d.la_not_rr06, `LA not on RR06 - ${tag}.csv`) }, "See them") : null)));
    box.appendChild(el("div", { class: "scrollx" }, t));
    if (d.capped) box.appendChild(el("div", { class: "small muted" }, "Long lists are cut at 5 000 rows here. The Excel file has every row."));
    if (d.notes.length) box.appendChild(el("details", { style: { marginTop: "10px" } }, el("summary", { class: "small", style: { cursor: "pointer" } }, "What was left out"), el("ul", { class: "small" }, d.notes.map((n) => el("li", null, n)))));
    box.appendChild(list);
    const exc = d.rr06_not_la.filter((r) => r.Category === "Exception");
    if (exc.length) pick("Exception", exc, `RR06 not on LA - Exception - ${tag}.csv`);
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ---- Sequentra ---------------------------------------------------------------------------------------
  $("apif").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    await act(() => api("/api/ifrs16/api", { body: { kind: f.kind.value, month: f.month.value, to_month: f.to_month.value } }), "Asking Sequentra.");
    load();
  });
  function drawApi() {
    const box = clear($("apiruns"));
    if (!data.api.length) { box.appendChild(el("div", { class: "small muted" }, "Nothing asked yet.")); return; }
    data.api.forEach((r) => box.appendChild(el("div", { class: "row", style: { alignItems: "flex-start" } },
      el("div", { style: { flex: "1 1 100%", minWidth: "0" } },
        el("div", { style: { display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "baseline" } }, el("strong", null, r.kind === "check" ? "Check every report" : `Download ${r.month}${r.to_month ? " to " + r.to_month : ""}`), pill(r.status),
          r.ok ? el("span", { class: "pill " + (r.ok === "yes" ? "good" : "bad") }, r.ok === "yes" ? "Connected" : "Not connected") : null, el("div", { class: "grow" }), file(r.download, "The reports (zip)"), el("span", { class: "small muted" }, when(r.at))),
        r.note ? el("div", { class: "small bad", style: { whiteSpace: "pre-line" } }, r.note) : null,
        r.result ? el("pre", { class: "mono small", style: { whiteSpace: "pre-wrap", margin: "6px 0 0", maxHeight: "260px", overflow: "auto" } }, r.result) : null))));
  }
  function drawConn() {
    const c = data.connection;
    $("connword").textContent = !c.set ? "no connection yet" : !c.active ? "switched off" : !c.has_key ? "no key" : "connected by an admin";
    if (!data.is_admin) return;
    $("connsec").classList.remove("hide");
    const d = c.detail;
    $("conninfo").textContent = d ? `Key ${d.has_key ? "set (…" + (d.key_hint || "") + ")" : "not set"} · ${d.is_active ? "on" : "off"}${d.last_test && d.last_test.note ? " · last test: " + d.last_test.note : ""}` : "Not set yet: paste the key and save.";
    if (d) { const f = $("connf"); f.base_url.value = d.base_url; f.header_name.value = d.header_name || "Clientkey"; f.is_active.checked = d.is_active; }
  }
  $("connf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target;
    const body = { base_url: f.base_url.value, header_name: f.header_name.value, is_active: f.is_active.checked };
    if (f.secret.value) body.secret = f.secret.value;
    await act(() => api("/api/ifrs16/connection", { body }), "Saved — the key is sealed and won't be shown again.");
    f.secret.value = ""; load();
  });
  $("conntest").addEventListener("click", async () => { const r = await act(() => api("/api/ifrs16/connection/test", { method: "POST" })); toast(r.note, 6000); load(); });

  // ---- rates -------------------------------------------------------------------------------------------
  function drawRates() {
    const box = clear($("rates"));
    const head = el("div", { style: { display: "grid", gridTemplateColumns: "1.2fr repeat(6, 1fr) 1.4fr 70px", borderBottom: "1px solid var(--strong)" } },
      ["Month", "A", "B", "C", "D", "E", "F", "Note", ""].map((h, i) => el("div", { class: "k", style: { padding: "10px 8px", textAlign: i && i < 7 ? "right" : "left" } }, h)));
    box.appendChild(head);
    if (!data.rates.length) box.appendChild(el("div", { class: "small muted", style: { padding: "12px 8px" } }, "No rates yet."));
    data.rates.forEach((r) => box.appendChild(el("div", { style: { display: "grid", gridTemplateColumns: "1.2fr repeat(6, 1fr) 1.4fr 70px", borderBottom: "1px solid var(--rule)" } },
      el("div", { style: { padding: "10px 8px", fontWeight: "500" } }, monthLabel(r.month)),
      "abcdef".split("").map((b) => el("div", { class: "mono", style: { padding: "10px 8px", textAlign: "right" } }, r[b] === null || r[b] === undefined ? "—" : Number(r[b]).toFixed(2))),
      el("div", { class: "small muted", style: { padding: "10px 8px" } }, r.note || ""),
      el("div", { style: { padding: "6px 8px" } }, data.can_write ? el("button", { class: "link small bad", type: "button", onclick: async () => { await act(() => api("/api/ifrs16/rates/" + r.id, { method: "DELETE" }), "Removed."); load(); } }, "Remove") : null))));
  }
  $("ratef").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = { month: f.month.value };
    "abcdef".split("").forEach((b) => { if (f[b].value !== "") body[b] = parseFloat(f[b].value); });
    await act(() => api("/api/ifrs16/rates", { body }), "Saved."); f.reset(); load();
  });

    tab(["months", "ledger", "recon", "sequentra", "rates"].includes(ctx.params.tab) ? ctx.params.tab : "months");
    await load();
  },
};
