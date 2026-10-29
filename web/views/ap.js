// AP Tool (1.0.95; 1.0.98: CBX-Connect 2.10.3's page — invoice capture: a folder's buckets and its SAP sheet with the two
// downloads, Needs you in sections, the live line on The team, and Settings with the master lists and SAP's own lists).
// Before: the same page as CBX-Connect 2.8.6 — drop a folder; Ivy, Bo, Rae and Quinn read and check every file;
// Paula sorts what they can't; what AXL can't place comes to you; people fix, teach and sign off (two people on payment
// runs and bank details). Ported from CBX-Connect 2 (templates/ap.html and static/js/ap.js); the server side is
// cbx/api/ap_api.py over cbx/ap/service.py (2.x's service, line for line).
// 1.0.102: the SAP sheet's posting date (today unless a person sets another); Needs you → Sites to map; Code this vendor
// prefilled from SAP's vendor list, with payment days and line rules; Settings → Sites and Vendors (SAP general data).
import { api as API } from '../api.js';
import { toast as TOAST, busyChip, lineChip } from '../ui.js';
import * as CTL from './ap_controls/index.js';  // 1.0.104: the Controls tab's sub-views (one line each in that file)
import { portfolioPane, peoplePane } from './ap_controls/settings.js';  // 1.0.104: Settings → Portfolios, AP people (admins)
import { tariffPane } from './ap_controls/tariffs.js';  // 1.0.104 (Builder U, V07): Settings → Tariffs (admins)
import { renderSimple } from './ap_simple.js';  // 1.0.105 (SPEC-1105): the AP Tool opens on "Make SAP upload"

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
const toast = (m) => TOAST(m, '');
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { TOAST(e.detail || e.message, 'err'); throw e; } }
function api(path, o) {
  o = o || {};
  if (o.form) return API.post(path, o.form);
  const m = (o.method || (o.body !== undefined ? 'POST' : 'GET')).toUpperCase();
  if (m === 'PUT') return API.put(path, o.body || {});
  if (m === 'DELETE') return API.del(path);  // 1.0.98
  if (m === 'POST') return API.post(path, o.body !== undefined ? o.body : {});
  return API.get(path);
}
// 1.0.98: 2.x's words function, English only here (the old core views are English): "{n} files" filled in, "@ctx|" dropped
function t(s, vars) {
  return String(s).replace(/^@[a-z]+\|/, '').replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] !== undefined && vars[k] !== null ? String(vars[k]) : m));
}
const cbx = { t, busyChip, lineChip };  // 1.0.103: the live chips (web/ui.js)
const B = '';
// the page's frame: static markup from CBX-Connect 2's template (no data in it)
const SKELETON = "<div class=\"ifv apv\" id=\"ap\">\n  <div class=\"head\"><div><h1>AP Tool</h1><p>Drop a folder. Ivy, Bo, Rae and Quinn read and check every file; Paula sorts what they can't; what AXL can't place comes to you. People approve. The bots never pay or change bank details.</p></div></div>\n  <div class=\"tabs\" role=\"tablist\" style=\"margin-bottom:18px\">\n    <button type=\"button\" role=\"tab\" data-tab=\"inbox\" class=\"on\">Inbox</button>\n    <button type=\"button\" role=\"tab\" data-tab=\"needs\">Needs you <span id=\"needn\" class=\"mono small\"></span></button>\n    <button type=\"button\" role=\"tab\" data-tab=\"team\">The team</button>\n    <button type=\"button\" role=\"tab\" data-tab=\"controls\" id=\"ctltab\" class=\"hide\">Controls</button>\n    <button type=\"button\" role=\"tab\" data-tab=\"settings\" id=\"settab\">Settings</button>\n  </div>\n\n  <div id=\"board\" style=\"display:flex;flex-wrap:wrap;gap:0;margin-bottom:22px\"></div>\n\n  <div id=\"tab-inbox\">\n    <div class=\"grid21 g13\" style=\"margin-bottom:22px\">\n      <section class=\"sec\" id=\"dropsec\">\n        <div class=\"sec-h\"><h2 class=\"h2\">Drop a folder</h2><span class=\"k\">PDF \u00b7 Excel \u00b7 CSV \u00b7 mail \u00b7 zip</span></div>\n        <div id=\"drop\" class=\"stack\" style=\"gap:10px;border:1px dashed var(--strong);padding:18px;text-align:center\">\n          <div class=\"small muted\">Drag the folder or files here, or</div>\n          <div style=\"display:flex;gap:10px;justify-content:center;flex-wrap:wrap\">\n            <label class=\"btn sm primary\" style=\"cursor:pointer\">Pick a folder<input type=\"file\" id=\"pickdir\" webkitdirectory directory multiple hidden></label>\n            <label class=\"btn sm\" style=\"cursor:pointer\">Pick files or a zip<input type=\"file\" id=\"pickfiles\" multiple hidden></label>\n          </div>\n          <input class=\"in\" id=\"bname\" placeholder=\"Name (optional, e.g. Mid-month run 15 Oct)\">\n          <select class=\"in\" id=\"bportfolio\" aria-label=\"Portfolio\"></select>\n          <div id=\"picked\" class=\"small muted\"></div>\n          <div><button class=\"btn primary\" type=\"button\" id=\"send\" disabled>Give it to the team</button></div>\n        </div>\n      </section>\n      <section class=\"sec\">\n        <div class=\"sec-h\"><h2 class=\"h2\">Folders</h2><span class=\"k\">pick one to see its files</span></div>\n        <div id=\"batches\"></div>\n      </section>\n    </div>\n    <section class=\"sec hide\" id=\"capsec\" style=\"margin-bottom:22px\">\n      <div class=\"sec-h\"><h2 class=\"h2\">Invoice capture</h2><span class=\"k\" id=\"capk\"></span></div>\n      <div id=\"buckets\" style=\"display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0 18px\"></div>\n      <div style=\"display:flex;gap:10px 14px;flex-wrap:wrap;align-items:center;margin-top:6px\">\n        <h2 class=\"h2\" style=\"margin-inline-end:6px\">SAP sheet</h2>\n        <select class=\"in\" id=\"sapf\" style=\"width:auto\">\n          <option value=\"\">Every document</option><option value=\"green\">Green</option><option value=\"yellow\">Yellow</option>\n          <option value=\"red\">Red</option><option value=\"uploaded\">Uploaded</option><option value=\"open\">Not read yet</option><option value=\"review\">To review</option>\n        </select>\n        <div class=\"grow\"></div>\n        <label class=\"small hide\" id=\"pfbox\" style=\"display:flex;gap:6px;align-items:center\">Portfolio <select class=\"in\" id=\"batchpf\" style=\"width:auto\"></select></label>\n        <label class=\"small\" id=\"pdbox\" style=\"display:flex;gap:6px;align-items:center\">Posting date <input type=\"date\" class=\"in\" id=\"postdate\" style=\"width:auto\"></label>\n        <button class=\"link small hide\" type=\"button\" id=\"pdtoday\">Today</button>\n        <label class=\"small\" id=\"yelbox\" style=\"display:flex;gap:6px;align-items:center\"><input type=\"checkbox\" id=\"incyel\"> include yellow</label>\n        <button class=\"btn sm primary\" type=\"button\" id=\"sapdl\">Download SAP upload</button>\n        <a class=\"link small\" id=\"reviewdl\" href=\"#\">Review workbook</a>\n        <button class=\"link small hide\" type=\"button\" id=\"saprel\">Release the upload mark</button>\n      </div>\n      <div style=\"overflow-x:auto\"><table class=\"tbl\" id=\"saptbl\"></table></div>\n    </section>\n    <section class=\"sec\">\n      <div class=\"sec-h\"><h2 class=\"h2\" id=\"listh\">Every file</h2><span class=\"k\" id=\"listk\"></span></div>\n      <div id=\"filters\" style=\"display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px\">\n        <input class=\"in\" id=\"fq\" placeholder=\"Search name, supplier, reference\" style=\"flex:1 1 220px\">\n        <select class=\"in\" id=\"fstatus\" style=\"width:auto\"><option value=\"\">Any status</option></select>\n        <select class=\"in\" id=\"fkind\" style=\"width:auto\"><option value=\"\">Any kind</option></select>\n        <select class=\"in\" id=\"fbot\" style=\"width:auto\"><option value=\"\">Any bot</option></select>\n        <label class=\"small\" style=\"display:flex;gap:6px;align-items:center\"><input type=\"checkbox\" id=\"fopen\"> Only what needs a person</label>\n      </div>\n      <div style=\"overflow-x:auto\"><table class=\"tbl\" id=\"items\"></table></div>\n    </section>\n  </div>\n\n  <div id=\"tab-needs\" class=\"hide\">\n    <div id=\"capneeds\" class=\"stack\" style=\"gap:22px;margin-bottom:22px\"></div>\n    <section class=\"sec\" id=\"oldneeds\">\n      <div class=\"sec-h\"><h2 class=\"h2\">Needs you</h2><span class=\"k\">the bots, Paula and AXL could not place these</span></div>\n      <p class=\"small muted\" style=\"margin-top:0\">Open a file, say what it is and fix its fields. Tick \"teach the team\" so files like it are read by themselves next time.</p>\n      <div style=\"overflow-x:auto\"><table class=\"tbl\" id=\"needs\"></table></div>\n    </section>\n  </div>\n\n  <div id=\"tab-team\" class=\"hide\">\n    <section class=\"sec\" style=\"margin-bottom:22px\" id=\"livesec\">\n      <div class=\"sec-h\"><h2 class=\"h2\">On the line</h2><span class=\"k\" id=\"livek\"></span></div>\n      <div id=\"scan\" class=\"stack\" style=\"gap:6px\"></div>\n      <div id=\"line\" style=\"display:flex;flex-wrap:wrap;gap:10px;align-items:stretch\"></div>\n    </section>\n    <section class=\"sec\" style=\"margin-bottom:22px\">\n      <div class=\"sec-h\"><h2 class=\"h2\">The chain</h2><span class=\"k\">what nobody knows goes up</span></div>\n      <div id=\"chain\" style=\"display:flex;gap:8px;flex-wrap:wrap;align-items:center\"></div>\n    </section>\n    <div id=\"bots\" style=\"display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin-bottom:22px\"></div>\n    <div class=\"grid21 g13\">\n      <section class=\"sec\"><div class=\"sec-h\"><h2 class=\"h2\">Rules</h2><span class=\"k\">from the SOP review</span></div><ol id=\"rules\" class=\"small\" style=\"padding-left:18px;margin:0\"></ol></section>\n      <section class=\"sec\"><div class=\"sec-h\"><h2 class=\"h2\">What people taught</h2><span class=\"k\">used before the bots' own rules</span></div><div id=\"taught\"></div>\n        <div class=\"sec-h\" style=\"margin-top:18px\"><h2 class=\"h2\">KPIs</h2></div><ul id=\"kpis\" class=\"small\" style=\"padding-left:18px;margin:0\"></ul></section>\n    </div>\n  </div>\n\n  <div id=\"tab-controls\" class=\"hide\">\n    <div class=\"tabs\" id=\"ctlnav\" role=\"tablist\" style=\"margin-bottom:18px;flex-wrap:wrap\"></div>\n    <div id=\"ctlpane\"></div>\n  </div>\n\n  <div id=\"tab-settings\" class=\"hide\">\n    <div class=\"tabs\" id=\"setnav\" role=\"tablist\" style=\"margin-bottom:18px;flex-wrap:wrap\">\n      <button type=\"button\" role=\"tab\" data-set=\"entities\" class=\"on\">Entities</button>\n      <button type=\"button\" role=\"tab\" data-set=\"utilities\">Utilities accounts</button>\n      <button type=\"button\" role=\"tab\" data-set=\"vendors\">Vendors</button>\n      <button type=\"button\" role=\"tab\" data-set=\"sites\">Sites</button>\n      <button type=\"button\" role=\"tab\" data-set=\"sap_vendors\">Vendors (SAP general data)</button>\n      <button type=\"button\" role=\"tab\" data-set=\"sap\">SAP values</button>\n      <button type=\"button\" role=\"tab\" data-set=\"doctypes\">Document types</button>\n      <button type=\"button\" role=\"tab\" data-set=\"gl_accounts\">G/L accounts</button>\n      <button type=\"button\" role=\"tab\" data-set=\"vendor_companies\">Vendors per company code</button>\n      <button type=\"button\" role=\"tab\" data-set=\"cost_centres\">Cost centres</button>\n      <button type=\"button\" role=\"tab\" data-set=\"history\">Posting history</button>\n      <button type=\"button\" role=\"tab\" data-set=\"models\" id=\"setmodels\">Models</button>\n      <button type=\"button\" role=\"tab\" data-set=\"portfolios\" id=\"setportfolios\" class=\"hide\">Portfolios</button>\n      <button type=\"button\" role=\"tab\" data-set=\"people\" id=\"setpeople\" class=\"hide\">AP people</button>\n      <button type=\"button\" role=\"tab\" data-set=\"tariffs\" id=\"settariffs\" class=\"hide\">Tariffs</button>\n      <button type=\"button\" role=\"tab\" data-set=\"thresholds\">Thresholds</button>\n    </div>\n    <div id=\"setpane\"></div>\n    <section class=\"sec hide\" style=\"max-width:620px\" id=\"threshsec\">\n      <div class=\"sec-h\"><h2 class=\"h2\">Thresholds</h2><span class=\"k\">admins</span></div>\n      <form id=\"setf\" class=\"stack\" style=\"gap:10px\"></form>\n    </section>\n  </div>\n</div>\n\n<dialog id=\"dlg\" class=\"dlg ss-dlg ifv apv\" aria-labelledby=\"dlgt\" style=\"width:min(900px,calc(100vw - 32px))\">\n  <form method=\"dialog\" class=\"stack\" style=\"gap:14px\">\n    <div style=\"display:flex;align-items:baseline;gap:12px\"><h2 class=\"h2\" id=\"dlgt\" style=\"font-size:18px;word-break:break-word\"></h2><div class=\"grow\"></div><button class=\"link small\" value=\"cancel\" type=\"submit\">Close</button></div>\n    <div class=\"small muted\" id=\"dlgsub\"></div>\n    <div id=\"dlgbody\" class=\"stack\" style=\"gap:16px\"></div>\n  </form>\n</dialog>";

export default {
  title: 'AP Tool',
  async render(rootEl, ctx) {
    // 1.0.105 (SPEC-1105): the AP Tool opens on "Make SAP upload" for everyone; these tabs open from its small "More" link
    // (admins, reviewers, approvers, posters) or a link that names a tab, a document or a file
    if (!ctx.params.tab && !ctx.params.capture && !ctx.params.item) return renderSimple(rootEl, ctx);
    rootEl.innerHTML = SKELETON;
    const root = rootEl.querySelector('#ap');
    root.querySelector('.head').appendChild(Object.assign(document.createElement('a'), { className: 'link small', href: '#/ap', textContent: 'Make SAP upload' }));  // 1.0.105: back to the one screen
  const $ = (id) => rootEl.querySelector("#" + id);  // the dialog sits beside the page
  const ST = { new: [t("Reading"), "warn"], ok: [t("Fine"), "good"], check: [t("Look at it"), "warn"], exception: [t("Stopped"), "bad"], unknown: [t("Not placed"), "bad"],
    waiting: [t("Second person"), "warn"], cleared: [t("Signed off"), "good"], rejected: [t("Rejected"), ""], reading: [t("Reading"), "warn"], ready: [t("Ready"), "good"], failed: [t("Didn't work"), "bad"],
    ai_wait: [t("Waiting for the AI"), "warn"] };  // 1.0.103: a file waiting for the capture bots while the AI is off
  const LABEL = { vat_rate: t("VAT rate %"), change_pct: t("Run change: % over last run"), change_amount: t("Run change: rand amount"), old_invoice_months: t("Old invoice: months"),
    query_days: t("Queries: answer within working days"), write_off_days: t("Write-off candidate: days"), aged_share_pct: t("Ageing: most over 90 days %"), match_tolerance: t("Match tolerance (rand)") };
  const FIELD = { supplier: t("Supplier"), supplier_vat: t("Supplier VAT no"), vendor: t("Vendor no"), invoice_no: t("Invoice / document no"), date: t("Date"), po: t("Order no (PO)"), excl: t("Excl VAT"),
    vat: t("VAT"), total: t("Total"), account_no: t("Account no"), bank: t("Bank"), branch: t("Branch code"), amount: t("Amount"), ref: t("Reference"), beneficiary: t("Beneficiary"), subject: t("Subject"), from: t("From"), balance: t("Balance") };
  // 2.10.0
  const SAPST = { green: [t("Green"), "good"], yellow: [t("Yellow"), "warn"], red: [t("Red"), "bad"] };
  const BUCKET = { utility: t("Utilities"), manual: t("Manual capture"), other: t("Other"), odd: t("Odd"), ap: t("Other AP files"), skipped: t("Skipped") };
  const STAGE = { sort: t("Sorting"), read: t("Uma reads it"), queued: t("Waiting for a capture bot"), capturing: t("Being read"), senior: t("Waiting for Seth"), senior_busy: t("Seth reads it"),
    match: t("Mia matches it"), sap: t("Pip builds it"), test: t("Tess tests it"), ready: t("Tested"), needs: t("Needs you"), skipped: t("Skipped"), ap: t("The AP team's checks") };
  const DOES = { sol: t("Opens every file and sends it to its bucket."), uma: t("Reads municipal, Eskom and Telkom bills with PDF Studio's profiles."),
    cara: t("Reads manual invoices with vision, one at a time."), dex: t("Reads manual invoices with vision, one at a time."), eli: t("Reads manual invoices with vision, one at a time."),
    seth: t("Reads the invoices the capture bots could not, with the senior model."), mia: t("Finds each invoice's company code and vendor in the lists."),
    pip: t("Builds the SAP upload rows."), tess: t("Tests every SAP document before it can be downloaded.") };
  const SVC = { electricity: t("Electricity"), water: t("Water"), sewerage: t("Sanitation"), refuse: t("Refuse"), rates: t("@service|Rates"), sundry: t("Sundry") };
  const HERE = "#/ap";
  const RTL = document.documentElement.dir === "rtl";
  const ARROW = RTL ? "←" : "→";
  let data = null, batchId = Number(ctx.params.batch) || null, rows = [], sortKey = "id", sortDir = -1, timer = null, picked = [];
  let sap = null, liveTimer = null, masters = null, setPane = "entities";
  let needsTimer = null, needsAt = 0;  // 1.0.99
  let busyMap = { items: {}, captures: {}, capOfItem: {}, line: {}, lineItems: {}, any: false }, busyTimer = null;  // 1.0.103
  // 1.0.104: the Controls tab — its sub-view, that view's own parameters, and its clean-up when it is left
  let ctlSub = String(ctx.params.sub || ""), ctlParams = Object.fromEntries(Object.entries(ctx.params || {}).filter(([k]) => !["tab", "sub", "batch"].includes(k))), ctlDispose = null;
  const TABS = ["inbox", "needs", "team", "controls", "settings"];

  const pill = (s) => el("span", { class: "pill " + ((ST[s] || [])[1] || "") }, (ST[s] || [s])[0]);
  const money = (v) => v === null || v === undefined || v === "" ? "" : "R " + Number(v).toLocaleString("en-ZA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const when = (iso) => iso ? new Date(iso).toLocaleString("en-ZA", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const base = (n) => String(n || "").split("/").pop();
  const sapDay = (s) => /^\d{8}$/.test(s || "") ? new Date(+s.slice(4), +s.slice(2, 4) - 1, +s.slice(0, 2)).toLocaleDateString("en-ZA", { day: "numeric", month: "short", year: "numeric" }) : "";
  const sw = (status) => el("span", { class: "sw", title: (SAPST[status] || [""])[0], style: { background: status === "green" ? "var(--good)" : status === "yellow" ? "var(--warn)" : status === "red" ? "var(--bad)" : "var(--rule)" } });
  const sapPill = (s) => s ? el("span", { class: "pill " + ((SAPST[s] || [])[1] || "") }, (SAPST[s] || [s])[0]) : null;
  const still = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // 2.10.3: a G/L field that searches the chart of accounts by number or words and shows the account's name beside it
  let glSeq = 0;
  function glField(value, opts) {
    const id = "gl-list-" + (++glSeq);
    const input = el("input", Object.assign({ class: "in mono", value: value || "", list: id, autocomplete: "off" }, opts || {}));
    const list = el("datalist", { id });
    const name = el("div", { class: "small muted", style: { minHeight: "1.2em", overflowWrap: "anywhere" } });
    const known = {}, MISSING = t("not in the chart of accounts CBX has");
    // a G/L the chart doesn't have is allowed (the chart export may be incomplete), with this warning beside it
    const say = () => { const v = input.value.trim().toUpperCase(), n = v ? (known[v] !== undefined ? known[v] : "") : ""; name.textContent = n; name.style.color = n === MISSING ? "var(--warn)" : ""; };
    let tm = null;
    const look = async (q, exact) => {
      if (!q) { clear(list); say(); return; }
      let r; try { r = await api("/api/ap/gl-search?q=" + encodeURIComponent(q)); } catch (e) { return; }
      clear(list);
      r.rows.forEach((x) => { known[x.account.toUpperCase()] = x.name; list.appendChild(el("option", { value: x.account }, x.name)); });
      if (exact && known[q.toUpperCase()] === undefined) known[q.toUpperCase()] = MISSING;
      say();
    };
    input.addEventListener("input", () => { clearTimeout(tm); say(); tm = setTimeout(() => look(input.value.trim(), false), 220); });
    input.addEventListener("change", () => look(input.value.trim(), true));
    if (value) look(String(value).trim(), true);
    return { input, wrap: el("div", { class: "stack", style: { gap: "2px" } }, input, list, name) };
  }
  // 1.0.103: Code this vendor — the vendor's last postings in SAP (date, reference, amount, G/L, cost centre, text)
  function postedBefore(h) {
    if (!h || !(h.recent || []).length) return null;
    return el("details", { open: true }, el("summary", { class: "small", style: { cursor: "pointer" } }, t("Posted before in SAP ({n} invoices)", { n: h.docs })),
      el("div", { style: { overflowX: "auto" } }, el("table", { class: "tbl small" }, el("tbody", null, h.recent.map((x) => el("tr", null,
        el("td", { style: { whiteSpace: "nowrap" } }, x.date || ""), el("td", { class: "mono" }, x.reference || ""), el("td", { style: { textAlign: "end", whiteSpace: "nowrap" } }, money(x.amount)),
        el("td", { class: "mono" }, x.gl || ""), el("td", { class: "mono" }, x.cost_centre || x.profit_centre || ""), el("td", null, x.text || "")))))));
  }
  // 1.0.102: a vendor coding's line rules — words in a line's description → its G/L (and cost centre, tax code)
  function rulesField(rules) {
    const list = el("div", { class: "stack", style: { gap: "6px" } }), rows = [];
    const add = (r) => {
      r = r || {};
      const g = glField(r.gl || "", { placeholder: t("G/L") });
      const item = { words: el("input", { class: "in", value: r.words || "", placeholder: t("Words in the line, e.g. biokinetic") }), gl: g.input,
        cc: el("input", { class: "in", value: r.cost_centre || "", placeholder: t("Cost centre (optional)") }),
        tx: el("input", { class: "in mono", value: r.tax_code || "", placeholder: t("Tax code (optional)") }) };
      const row = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(140px,1fr))", gap: "6px", alignItems: "start" } }, item.words, g.wrap, item.cc, item.tx,
        el("button", { class: "link small", type: "button", style: { justifySelf: "start" }, onclick: () => { row.remove(); rows.splice(rows.indexOf(item), 1); } }, t("Remove")));
      rows.push(item);
      list.appendChild(row);
    };
    (rules || []).forEach(add);
    const wrap = el("div", { class: "stack", style: { gap: "6px" } },
      el("div", { class: "small muted" }, t("A line whose description has the words posts to that G/L; the other lines take the vendor's G/L.")), list,
      el("div", null, el("button", { class: "link small", type: "button", onclick: () => add({}) }, t("Add a rule"))));
    return { wrap, read: () => rows.map((x) => ({ words: x.words.value, gl: x.gl.value, cost_centre: x.cc.value, tax_code: x.tx.value })).filter((x) => x.words.trim()) };
  }
  const glWarn = (r) => { if (r && r.warning) toast((r.missing || []).length > 1 ? t("Not in the chart of accounts CBX has: {list}", { list: r.missing.join(", ") }) : t("G/L {g} is not in the chart of accounts CBX has.", { g: (r.missing || [r.gl])[0] }), 5200); };

  function tab(name) {
    root.querySelectorAll("[data-tab]").forEach((b) => { b.classList.toggle("on", b.dataset.tab === name); b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false"); });
    TABS.forEach((x) => $("tab-" + x).classList.toggle("hide", x !== name));  // 1.0.104: + controls
    const q = new URLSearchParams();
    q.set("tab", name);  // 1.0.105: every tab says so (no tab now opens "Make SAP upload")
    if (batchId) q.set("batch", batchId);
    try { history.replaceState(null, "", HERE + (q.toString() ? "?" + q : "")); } catch (e) { /* not allowed here */ }
    if (name === "needs" && data) loadNeeds(); else if (name !== "needs") { clearTimeout(needsTimer); needsTimer = null; }
    if (name === "team") loadLive(); else { clearTimeout(liveTimer); liveTimer = null; }
    if (name === "settings") loadMasters();
    if (name === "controls") loadControls(); else stopControls();  // 1.0.104
  }
  root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => tab(b.dataset.tab)));

  async function load() {
    if (!root.isConnected) return;  // the page was left
    const first = !data;
    data = await api("/api/ap");
    if (first && !$("tab-needs").classList.contains("hide")) loadNeeds();
    drawBoard(); drawBatches(); drawTeam(); drawSettings(); fillFilters();
    if (first) {  // 1.0.103: a link from the Office or The team opens the document ("#/ap?capture=12" / "#/ap?item=34")
      if (Number(ctx.params.capture)) openCapture(Number(ctx.params.capture)); else if (Number(ctx.params.item)) openItem(Number(ctx.params.item));
    }
    $("setmodels").classList.toggle("hide", !data.is_admin);  // 1.0.98: model names are for admins
    if (!data.is_admin && ["models", "portfolios", "people"].includes(setPane)) setPane = "entities";
    drawPortfolios();  // 1.0.104
    const need = (data.capture && data.capture.need_all) || data.board.need_you;
    $("needn").textContent = need ? "(" + need + ")" : "";
    $("dropsec").classList.toggle("hide", !data.can_write);
    await loadItems();
    await loadCapture();
    clearTimeout(timer);
    const busy = data.batches.some((b) => b.status === "reading") || (data.capture && data.capture.to_read) || (sap && sap.capture.busy);
    if (busy) timer = setTimeout(load, 3000);
    if (busy && !first && !needsTimer && Date.now() - needsAt > 10000 && !$("tab-needs").classList.contains("hide")) loadNeeds();  // 1.0.99: work started: Needs you follows it
    if (!busyTimer) pollBusy();  // 1.0.103
  }

  // ---- 1.0.103: which bot is on which file now: a live chip in the Bot / status cell, "In line — 3rd" for the readers' queue
  const slot = (kind, id, plain) => el("span", { class: "busyslot", "data-busy-kind": kind, "data-busy-id": String(id), "data-plain": plain || "" }, plain || "");
  function paintBusy(where) {
    (where || root).querySelectorAll(".busyslot").forEach((s) => {
      const id = s.dataset.busyId, item = s.dataset.busyKind === "item";
      const m = item ? (busyMap.items[id] || busyMap.capOfItem[id]) : busyMap.captures[id];
      const n = item ? busyMap.lineItems[id] : busyMap.line[id];
      const key = m ? [m.bot, m.step, m.since, m.stuck ? 1 : 0].join("|") : n ? "line" + n : "";
      if (s.dataset.key === key && s.childNodes.length) return;  // the same: the ticker counts it on
      s.dataset.key = key;
      clear(s);
      if (m) s.appendChild(cbx.busyChip(m)); else if (n) s.appendChild(cbx.lineChip(n)); else s.textContent = s.dataset.plain || "";
    });
  }
  async function pollBusy() {
    clearTimeout(busyTimer); busyTimer = null;
    if (!root.isConnected) return;
    let r;
    try { r = await api("/api/ap/busy"); } catch (e) { return; }
    const capOfItem = {}, line = {}, lineItems = {};
    Object.values(r.captures).forEach((m) => { capOfItem[m.item_id] = m; });
    r.line.forEach((x) => { line[x.id] = x.n; if (x.item_id) lineItems[x.item_id] = x.n; });
    busyMap = { items: r.items, captures: r.captures, capOfItem, line, lineItems, any: r.any };
    paintBusy(rootEl);
    const reading = data && (data.batches.some((b) => b.status === "reading") || (data.capture && data.capture.to_read) || (sap && sap.capture.busy));
    if ((r.any || reading) && root.isConnected) busyTimer = setTimeout(pollBusy, 5000);  // a light poll while anything is busy; it stops when nothing is
  }

  // ---- board -----------------------------------------------------------------------------------------
  function drawBoard() {
    const b = data.board, c = data.capture || {}, box = clear($("board"));
    [[t("Files read"), b.files, t("{n}% sorted by the bots", { n: b.sorted_by_bots_pct })], [t("Needs a person"), b.open, t("{n} stopped by a check", { n: b.stopped })],
      [t("Second person"), b.waiting_second, t("payment runs and bank details")], [t("Needs you"), c.need_all !== undefined ? c.need_all : b.need_you, c.need_all ? t("the team could not settle them") : t("AXL could not place them")],
      [t("Duplicates stopped"), b.duplicates_stopped, (b.dup_cases_open !== undefined ? t("{o} open in the register", { o: b.dup_cases_open }) + " · " : "") + t("{n} bank details to verify", { n: b.bank_changes })],  // 1.0.104 (Builder D, V09): the register's KPI
      [t("Queries open"), b.queries_open, t("{n} past the answer date", { n: b.queries_late })],
      [t("Captured"), c.captured || 0, t("{n} still to read", { n: c.to_read || 0 })], [t("SAP ready"), c.sap_ready || 0, t("{y} yellow · {r} red", { y: c.sap_yellow || 0, r: c.sap_red || 0 })]]
      .forEach(([k, v, s]) => box.appendChild(el("div", { class: "stat", style: { flex: window.innerWidth < 720 ? "1 1 120px" : "1 1 180px" } }, el("div", { class: "k" }, k), el("div", { class: "disp", style: { fontSize: "34px", lineHeight: "1.1" } }, String(v)), el("div", { class: "small muted" }, s))));
  }

  // ---- drop -----------------------------------------------------------------------------------------
  function pick(list) {
    picked = Array.from(list).filter((f) => !f.name.startsWith("."));
    const mb = (picked.reduce((a, f) => a + f.size, 0) / 1048576).toFixed(1);
    $("picked").textContent = picked.length ? (picked.length === 1 ? t("{n} file · {mb} MB", { n: picked.length, mb }) : t("{n} files · {mb} MB", { n: picked.length, mb })) : "";
    $("send").disabled = !picked.length;
  }
  $("pickdir").addEventListener("change", (e) => pick(e.target.files));
  $("pickfiles").addEventListener("change", (e) => pick(e.target.files));
  const drop = $("drop");
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.style.background = "var(--surf2)"; });
  drop.addEventListener("dragleave", () => { drop.style.background = ""; });
  drop.addEventListener("drop", async (e) => {
    e.preventDefault(); drop.style.background = "";
    const out = [];
    const walk = async (entry, path) => {
      if (entry.isFile) await new Promise((res) => entry.file((f) => { out.push(new File([f], path + f.name, { type: f.type })); res(); }, res));
      else if (entry.isDirectory) {
        const reader = entry.createReader();
        let batch;
        do { batch = await new Promise((res) => reader.readEntries(res, () => res([]))); for (const x of batch) await walk(x, path + entry.name + "/"); } while (batch.length);
      }
    };
    const items = Array.from(e.dataTransfer.items || []).map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (items.length) { for (const it of items) await walk(it, ""); pick(out); } else pick(e.dataTransfer.files);
  });
  $("send").addEventListener("click", async () => {
    const fd = new FormData();
    picked.forEach((f) => fd.append("files", f, f.webkitRelativePath || f.name));
    fd.append("name", $("bname").value);
    fd.append("portfolio", $("bportfolio").value);  // 1.0.104: "" = from the company codes
    $("send").disabled = true;
    try {
      const b = await act(() => api("/api/ap/batches", { form: fd }), t("The team has it — reading every file now."));
      batchId = b.id; picked = []; $("picked").textContent = ""; $("bname").value = ""; $("pickdir").value = ""; $("pickfiles").value = "";
      load();
    } finally { $("send").disabled = !picked.length; }
  });

  // ---- folders ----------------------------------------------------------------------------------------
  function drawBatches() {
    const box = clear($("batches"));
    if (!data.batches.length) { box.appendChild(el("div", { class: "small muted" }, t("No folders yet."))); return; }
    box.appendChild(el("div", { class: "row", style: { cursor: "pointer", fontWeight: batchId ? "400" : "600" }, onclick: () => { batchId = null; tab("inbox"); loadItems(); loadCapture(); drawBatches(); } }, t("Every folder")));
    data.batches.slice(0, 20).forEach((b) => {
      box.appendChild(el("div", { class: "row", style: { alignItems: "flex-start", cursor: "pointer", background: b.id === batchId ? "var(--surf2)" : "" }, onclick: () => { batchId = b.id; tab("inbox"); loadItems(); loadCapture(); drawBatches(); } },
        el("div", { style: { flex: "1 1 100%", minWidth: "0" } },
          el("div", { style: { display: "flex", gap: "10px", alignItems: "baseline", flexWrap: "wrap" } }, el("strong", null, b.name), pill(b.status), el("span", { class: "small muted" }, `${b.by} · ${when(b.at)}`), b.portfolio && b.portfolio.code ? el("span", { class: "small muted mono" }, b.portfolio.code) : null),  // 1.0.104
          el("div", { class: "small", style: { color: b.status === "failed" ? "var(--bad)" : "var(--ink2)", whiteSpace: "pre-line" } }, b.note || ""),
          el("div", { style: { display: "flex", gap: "14px", marginTop: "4px" } },
            b.report ? el("a", { class: "link small", href: `${B}/api/ap/files/${b.report.id}`, onclick: (e) => e.stopPropagation() }, t("Excel report")) : null,
            data.can_write && b.status !== "reading" ? el("button", { class: "link small", type: "button", onclick: async (e) => { e.stopPropagation(); await act(() => api(`/api/ap/batches/${b.id}/read`, { method: "POST" }), t("Reading it again.")); load(); } }, t("Read again")) : null))));
    });
  }

  // ---- the files table ---------------------------------------------------------------------------------
  function fillFilters() {
    const fill = (sel, opts) => { const v = sel.value; clear(sel); sel.appendChild(el("option", { value: "" }, sel.id === "fstatus" ? t("Any status") : sel.id === "fkind" ? t("Any kind") : t("Any bot"))); opts.forEach(([k, l]) => sel.appendChild(el("option", { value: k }, l))); sel.value = v; };
    fill($("fstatus"), Object.entries(ST).filter(([k]) => !["reading", "ready", "failed", "new"].includes(k)).map(([k, v]) => [k, v[0]]));
    fill($("fkind"), data.kinds.map((k) => [k.key, k.key === "utility" ? t("Utility bill") : k.label]));
    fill($("fbot"), data.team.bots.map((b) => [b.key, b.name]));
  }
  ["fq", "fstatus", "fkind", "fbot", "fopen"].forEach((id) => $(id).addEventListener(id === "fq" ? "input" : "change", () => { clearTimeout(window.__apq); window.__apq = setTimeout(loadItems, 250); }));

  async function loadItems() {
    const q = new URLSearchParams();
    if (batchId) q.set("batch", batchId);
    if ($("fq").value.trim()) q.set("q", $("fq").value.trim());
    if ($("fstatus").value) q.set("status", $("fstatus").value);
    if ($("fkind").value) q.set("kind", $("fkind").value);
    if ($("fbot").value) q.set("bot", $("fbot").value);
    if ($("fopen").checked) q.set("open", "true");
    rows = (await api("/api/ap/items?" + q)).items;
    const b = data.batches.find((x) => x.id === batchId);
    $("listh").textContent = b ? b.name : t("Every file");
    $("listk").textContent = rows.length === 1 ? t("{n} file", { n: rows.length }) : t("{n} files", { n: rows.length });
    drawTable($("items"), rows);
  }

  const COLS = [["name", t("File")], ["kind_label", t("What it is")], ["bot_name", t("Bot")], ["status", t("Status")], ["with", t("With")], ["supplier", t("Supplier")], ["ref", t("Reference")], ["amount", t("Amount")], ["top", t("Top finding")]];
  function drawTable(tbl, list) {
    clear(tbl);
    const sorted = list.slice().sort((a, b) => { const x = a[sortKey], y = b[sortKey]; return (x === y ? 0 : x === null || x === undefined ? 1 : y === null || y === undefined ? -1 : x > y ? 1 : -1) * sortDir; });
    tbl.appendChild(el("thead", null, el("tr", null, COLS.map(([k, l]) => el("th", { style: { cursor: "pointer" }, onclick: () => { sortDir = sortKey === k ? -sortDir : 1; sortKey = k; drawTable(tbl, list); } }, l + (sortKey === k ? (sortDir > 0 ? " ↑" : " ↓") : ""))))));
    const body = el("tbody");
    if (!sorted.length) body.appendChild(el("tr", null, el("td", { colspan: COLS.length, class: "small muted" }, t("Nothing here."))));
    sorted.forEach((r) => body.appendChild(el("tr", { style: { cursor: "pointer" }, onclick: () => openItem(r.id) },
      el("td", { style: { minWidth: "170px", maxWidth: "280px", overflowWrap: "anywhere" }, title: r.name }, r.name.split("/").pop(), r.name.includes("/") ? el("div", { class: "small muted" }, r.name.split("/").slice(0, -1).join(" / ")) : null), el("td", null, r.kind === "utility" ? t("Utility bill") : r.kind_label, r.confidence && r.confidence < 100 ? el("span", { class: "small muted" }, ` ${r.confidence}%`) : null),
      el("td", null, slot("item", r.id, r.bot_name)), el("td", null, pill(r.ai_wait ? "ai_wait" : r.status)), el("td", { class: "small" }, r.with), el("td", { class: "small" }, r.supplier), el("td", { class: "mono small" }, r.ref),
      el("td", { class: "mono small", style: { textAlign: "right", whiteSpace: "nowrap" } }, money(r.amount)),
      el("td", { class: "small", style: { color: r.stops ? "var(--bad)" : "var(--ink2)", maxWidth: "340px" } }, r.top || ""))));
    tbl.appendChild(body);
    paintBusy(tbl);
  }

  // ---- 2.10.0: the folder's buckets and its SAP sheet --------------------------------------------------------
  async function loadCapture() {
    const sec = $("capsec");
    if (!batchId) { sec.classList.add("hide"); sap = null; return; }
    try { sap = await api(`/api/ap/batches/${batchId}/sap`); } catch (e) { sec.classList.add("hide"); return; }
    if (!sap.capture.started) { sec.classList.add("hide"); return; }
    sec.classList.remove("hide");
    drawBuckets(); drawSap();
    evidenceLine();  // 1.0.104 (Builder R, V12)
  }
  // 1.0.104 (Builder R, V12): what the batch's evidence checklist still misses (people who see Controls), a link into Controls → Evidence
  async function evidenceLine() {
    let box = $("evline");
    if (!box) { box = el("div", { id: "evline", class: "small", style: { marginTop: "8px" } }); $("saptbl").parentNode.after(box); }
    clear(box);
    if (!batchId || !(data && data.controls && data.controls.controls)) return;
    let r; try { r = await api(`/api/ap/batches/${batchId}/evidence`); } catch (e) { return; }
    if (!r.items.length) return;
    const bid = batchId;
    box.append(r.missing.length ? el("span", { style: { color: r.late ? "var(--warn)" : "var(--ink2)" } }, t("Evidence missing: {list}", { list: r.missing.join(", ") })) : el("span", { class: "muted" }, t("Evidence on file: every checklist item.")),
      " ", el("button", { class: "link small", type: "button", onclick: () => goControls("evidence", { batch: bid }) }, r.missing.length ? t("Attach") : t("See it")));
  }

  function drawBuckets() {
    const c = sap.capture, box = clear($("buckets"));
    $("capk").textContent = c.busy ? t("Sorted {n} of {total}", { n: c.sorted, total: c.files }) : t("{g} green · {y} yellow · {r} red · {u} uploaded", { g: c.sap.green, y: c.sap.yellow, r: c.sap.red, u: c.sap.uploaded });
    ["utility", "manual", "other", "odd", "ap"].forEach((k) => {
      const v = c.buckets[k] || { n: 0, done: 0 };
      box.appendChild(el("div", { style: { padding: "8px 0 10px", borderBottom: "1px solid var(--rule)", opacity: v.n ? "1" : ".55" } },
        el("div", { class: "k" }, BUCKET[k]),
        el("div", { style: { display: "flex", alignItems: "baseline", gap: "8px" } }, el("span", { class: "disp", style: { fontSize: "26px" } }, String(v.n)),
          v.n && k !== "ap" ? el("span", { class: "small muted" }, k === "other" || k === "odd" ? t("for a person") : t("{n} read", { n: v.done })) : null),
        v.n && (k === "utility" || k === "manual") ? el("div", { class: "bar", style: { marginTop: "6px" } }, el("i", { style: { width: Math.round(100 * v.done / v.n) + "%" } })) : null,
        // 1.0.99: invoices waiting for the AI (switched off, or down) say so — they are read as soon as it is on
        k === "manual" && c.ai_wait ? el("div", { class: "small", style: { marginTop: "6px", color: "var(--warn)" } }, t("Waiting for the AI: {n}", { n: c.ai_wait })) : null));
    });
  }

  const SAPCOLS = [t("File"), t("Entity"), t("Vendor"), t("Invoice no"), t("Date"), t("Amount"), t("GL"), t("Why")];
  function drawSap() {
    const tbl = clear($("saptbl")), f = $("sapf").value;
    const docs = sap.docs.filter((d) => !f || (f === "uploaded" ? d.uploaded : f === "open" ? !d.status : f === "review" ? d.review === "selected" && !d.uploaded : d.status === f && !d.uploaded));  // 1.0.104 (Builder R, V05): + To review
    tbl.appendChild(el("thead", null, el("tr", null, el("th", null, ""), SAPCOLS.map((h) => el("th", null, h)))));
    const body = el("tbody");
    if (!docs.length) body.appendChild(el("tr", null, el("td", { colspan: SAPCOLS.length + 1, class: "small muted" }, t("Nothing here."))));
    docs.forEach((d) => body.appendChild(el("tr", { style: { cursor: "pointer" }, onclick: () => openCapture(d.id) },
      el("td", { style: { width: "18px" } }, sw(d.status)),
      el("td", { style: { minWidth: "160px", maxWidth: "260px", overflowWrap: "anywhere" } }, base(d.file), el("div", { class: "small muted" }, slot("cap", d.id, d.uploaded ? t("Uploaded {when} by {name}", { when: when(d.uploaded.at), name: d.uploaded.by }) : d.status ? "" : d.ai_wait ? t("Waiting for the AI") : STAGE[d.stage] || d.stage))),
      el("td", { class: "mono small" }, d.entity), el("td", { class: "small" }, el("span", { class: "mono" }, d.vendor || ""), d.vendor_name ? el("div", { class: "muted" }, d.vendor_name) : null),
      el("td", { class: "mono small" }, d.invoice_no), el("td", { class: "small", style: { whiteSpace: "nowrap" } }, sapDay(d.date)),
      el("td", { class: "mono small", style: { textAlign: "end", whiteSpace: "nowrap" } }, d.amount ? (d.credit ? "−" : "") + money(d.amount) : ""),
      el("td", { class: "mono small" }, d.gl), el("td", { class: "small", style: { color: d.status === "red" ? "var(--bad)" : "var(--ink2)", maxWidth: "320px" } }, (d.issues || []).map((x) => x.text).join("; "),
        d.review === "selected" && !d.uploaded ? el("div", { class: "muted" }, t("For review: {why}", { why: d.review_why })) : null))));  // 1.0.104 (Builder R, V05)
    tbl.appendChild(body);
    paintBusy(tbl);
    const ready = sap.capture.sap.green + ($("incyel").checked ? sap.capture.sap.yellow : 0);
    $("sapdl").disabled = !data.can_write || !ready;
    $("sapdl").classList.toggle("hide", !data.can_write);
    $("yelbox").classList.toggle("hide", !data.can_write || !sap.capture.sap.yellow);
    $("reviewdl").href = `${B}/api/ap/batches/${batchId}/sap/review`;
    $("saprel").classList.toggle("hide", !(data.is_admin && sap.capture.sap.uploaded));
    // 1.0.102: the posting date — today unless a person set another (then "Today" puts it back)
    const pd = sap.posting || {}, box = $("postdate");
    if (document.activeElement !== box) box.value = pd.date || "";
    box.disabled = !data.can_write;
    box.style.borderColor = pd.far ? "var(--warn)" : "";
    $("pdbox").title = pd.set ? t("Set by {name}", { name: pd.by || "" }) : t("Today: a batch left at today posts on the day it is downloaded");
    $("pdtoday").classList.toggle("hide", !(data.can_write && pd.set));
    // 1.0.104: the batch's portfolio — picked in the drop form, or from each document's company code
    const pf = sap.portfolio || {}, ps = $("batchpf");
    $("pfbox").classList.toggle("hide", !data.can_write || !(data.portfolios || []).length);
    if (document.activeElement !== ps) {
      clear(ps).appendChild(el("option", { value: "" }, pf.picked ? t("From the company codes") : t("From the company codes ({code})", { code: pf.code || "" })));
      (data.portfolios || []).forEach((p) => ps.appendChild(el("option", { value: p.code, selected: pf.picked && p.code === pf.code }, p.code)));
    }
  }
  $("batchpf").addEventListener("change", async () => {
    if (!sap) return;
    await act(() => api(`/api/ap/batches/${batchId}/portfolio`, { body: { portfolio: $("batchpf").value } }), t("Portfolio saved. Pip builds the rows again."));
    loadCapture();
  });
  async function postingDate(value) {
    const r = await act(() => api(`/api/ap/batches/${batchId}/sap/posting-date`, { body: { date: value } }), value ? t("Posting date saved. Pip builds the rows again.") : t("Back to today."));
    if (r && sap) { sap.posting = r.posting; sap.capture = r.capture; sap.docs = r.docs; drawBuckets(); drawSap(); }
  }
  $("postdate").addEventListener("change", () => { if (sap && $("postdate").value) postingDate($("postdate").value); });
  $("pdtoday").addEventListener("click", () => sap && postingDate(""));
  $("sapf").addEventListener("change", () => sap && drawSap());
  $("incyel").addEventListener("change", () => sap && drawSap());

  async function download(path, body, name) {
    const r = await fetch(B + path, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body || {}), credentials: "same-origin" });
    if (!r.ok) { let m = t("Something went wrong ({n}).", { n: r.status }); try { m = (await r.json()).detail || m; } catch (e) { /* not JSON */ } toast(m, 5200); return false; }
    const blob = await r.blob(), u = URL.createObjectURL(blob);
    const cd = r.headers.get("content-disposition") || "", m = cd.match(/filename="([^"]+)"/);
    const a = el("a", { href: u, download: m ? m[1] : name }); document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(u), 4000);
    return true;
  }
  $("sapdl").addEventListener("click", async () => {
    $("sapdl").disabled = true;
    try { if (await download(`/api/ap/batches/${batchId}/sap/upload`, { include_yellow: $("incyel").checked }, "SAP_upload.xlsx")) toast(t("Downloaded. These documents are marked as uploaded.")); }
    finally { load(); }
  });
  $("saprel").addEventListener("click", async () => {
    if (!confirm(t("Release the upload mark on this folder's documents? They can then go out in an upload again."))) return;
    await act(() => api(`/api/ap/batches/${batchId}/sap/release`, { method: "POST" }), t("Released."));
    load();
  });

  // ---- one capture document ---------------------------------------------------------------------------------
  const MAN = [["supplier", t("Supplier")], ["supplier_vat", t("Supplier VAT no")], ["bill_to", t("Bill-to")], ["bill_to_vat", t("Bill-to VAT no")], ["invoice_no", t("Invoice no")],
    ["invoice_date", t("Invoice date")], ["due_date", t("Due date")], ["po", t("Order no (PO)")], ["our_account", t("Our account with them")], ["currency", t("Currency")], ["excl", t("Excl VAT")], ["vat", t("VAT")], ["incl", t("Total incl. VAT")]];
  const UTIL = [["account", t("Account no")], ["invoice_no", t("Invoice no")], ["billing_date", t("Billing date")], ["due_date", t("Due date")], ["statement_month", t("Statement month")]];

  async function openCapture(id) {
    const c = await api(`/api/ap/captures/${id}`);
    const dlg = $("dlg"), f = c.fields || {}, util = f.kind === "utility" || c.bucket === "utility";
    const W = data.can_write && !c.uploaded_at && !["capturing", "senior_busy", "skipped", "ap"].includes(c.stage);
    dlg.style.width = c.is_pdf && c.pages ? "min(1180px,calc(100vw - 32px))" : "min(900px,calc(100vw - 32px))";
    $("dlgt").textContent = base(c.file);
    clear($("dlgsub")).append(BUCKET[c.bucket] || c.bucket, " · ", c.stage === "ready" ? sapPill(c.sap_status) : (STAGE[c.stage] || c.stage),
      c.read_by ? " · " + t("read by {name}", { name: c.read_by === "person" ? t("a person") : c.read_by }) : "",
      c.file_id ? el("a", { class: "link small", style: { marginInlineStart: "12px" }, href: `${B}/api/ap/files/${c.file_id}` }, t("Open the file")) : "",
      el("span", { style: { marginInlineStart: "12px" } }, slot("cap", c.id, "")));  // 1.0.103
    paintBusy($("dlgsub"));
    const body = clear($("dlgbody"));
    if (c.sap_issues.length) body.appendChild(el("div", { class: "stack", style: { gap: "4px" } }, c.sap_issues.map((x) => el("div", { class: "small", style: { color: x.level === "red" ? "var(--bad)" : "var(--warn)" } }, x.text))));
    if (c.uploaded_at) body.appendChild(el("div", { class: "small muted" }, t("In an SAP upload since {when}.", { when: when(c.uploaded_at) })));
    // 1.0.104 (Builder R): picked for review (V05) and the VAT decision (V06), with a link into Controls → Review for reviewers
    const rv = c.review || {}, vt = c.vat || {}, reviewer = data.is_admin || Object.values((data.controls || {}).roles || {}).some((x) => x.includes("reviewer"));
    if (rv.state || vt.vat_valid === false) body.appendChild(el("div", { class: "small", style: { display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "baseline" } },
      rv.state === "selected" ? el("span", null, t("Picked for review: {why}", { why: rv.why })) : rv.state ? el("span", { class: "muted" }, t("Review: {state} by {name}", { state: rv.state, name: rv.by || "" }) + (rv.note ? ` (${rv.note})` : "")) : null,
      vt.vat_valid === false ? el("span", { style: { color: vt.vat_decision ? "var(--ink2)" : "var(--bad)" } }, vt.vat_decision ? t("VAT: {d} by {name}", { d: vt.vat_decision === "claim" ? t("claimed") : t("not claimed"), name: vt.vat_decided_by }) : t("Not a valid tax invoice: a reviewer decides on the VAT")) : null,
      reviewer && !c.uploaded_at && (rv.state === "selected" || rv.state === "rejected" || (vt.vat_valid === false && !vt.vat_decision)) ? el("button", { class: "link small", type: "button", onclick: () => { $("dlg").close(); goControls("review", { capture: c.id }); } }, t("Review it")) : null));

    // the page beside the fields
    const form = el("div", { class: "stack", style: { gap: "10px", minWidth: "0" } });
    const inputs = {};
    const grid = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))", gap: "10px" } });
    (util ? UTIL : MAN).forEach(([k, l]) => { inputs[k] = el("input", { class: "in", value: f[k] === null || f[k] === undefined ? "" : String(f[k]), disabled: !W, type: /date$/.test(k) ? "date" : "text" }); grid.appendChild(el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, l), inputs[k])); });
    form.appendChild(grid);
    let credit = null, svcRows = [], taxWords = null;  // 1.0.104 (Builder R): + taxWords
    if (!util) {
      credit = el("input", { type: "checkbox", checked: f.credit ? true : null, disabled: !W });
      const v = (c.match && c.match.vendor) || {};
      const glf = glField(f.gl || "", { placeholder: v.gl || "", disabled: !W });
      inputs.gl = glf.input;
      inputs.cost_centre = el("input", { class: "in", value: f.cost_centre || "", placeholder: v.cost_centre || "", disabled: !W });
      form.appendChild(el("label", { class: "small", style: { display: "flex", gap: "6px", alignItems: "center" } }, credit, " ", t("A credit note")));
      taxWords = el("input", { type: "checkbox", checked: f.tax_invoice_words ? true : null, disabled: !W });  // 1.0.104 (Builder R, V06)
      form.appendChild(el("label", { class: "small", style: { display: "flex", gap: "6px", alignItems: "center" } }, taxWords, " ", t('The words "Tax Invoice" are printed on it')));
      form.appendChild(el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))", gap: "10px" } },
        el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, t("GL (this invoice only)")), glf.wrap),
        el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, t("Cost centre (this invoice only)")), inputs.cost_centre)));
      if (c.bill_to_site) form.appendChild(el("div", { class: "small muted" }, t("Site in the bill-to: {site}", { site: c.bill_to_site })));
      if ((f.lines || []).length) form.appendChild(el("details", null, el("summary", { class: "small", style: { cursor: "pointer" } }, t("Lines ({n})", { n: f.lines.length })),
        el("table", { class: "tbl" }, el("tbody", null, f.lines.map((x) => el("tr", null, el("td", { class: "small" }, x.description), el("td", { class: "mono small", style: { textAlign: "end" } }, money(x.excl)), el("td", { class: "mono small" }, x.vat_rate === null || x.vat_rate === undefined ? "" : x.vat_rate + "%")))))));
    } else {
      const tb = el("tbody");
      (f.services || []).forEach((s) => {
        const ex = el("input", { class: "in", value: s.ex || "", disabled: !W, style: { maxWidth: "140px" } }), vt = el("input", { class: "in", value: s.vat || "", disabled: !W, style: { maxWidth: "120px" } });
        svcRows.push({ service: s.service, ex, vt });
        tb.appendChild(el("tr", null, el("td", { class: "small" }, SVC[s.service] || s.service, s.how ? el("div", { class: "muted" }, s.how) : null), el("td", null, ex), el("td", null, vt)));
      });
      form.appendChild(el("table", { class: "tbl" }, el("thead", null, el("tr", null, el("th", null, t("Service")), el("th", null, t("Excl VAT")), el("th", null, t("VAT")))), tb));
      const m = (c.match && c.match.account) || {};
      if (m.found) form.appendChild(el("div", { class: "small muted" }, t("Vendor {v} · PID/CC {cc} · {site}", { v: m.vendor, cc: m.cc, site: m.name1 || "" })));
    }
    if (W) form.appendChild(el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
      el("button", { class: "btn sm primary", type: "button", onclick: async () => {
        const out = {}; Object.entries(inputs).forEach(([k, i]) => { out[k] = i.value; });
        if (credit) out.credit = credit.checked;
        if (taxWords) out.tax_invoice_words = taxWords.checked;  // 1.0.104 (Builder R, V06)
        if (svcRows.length) out.services = svcRows.map((x) => ({ service: x.service, ex: x.ex.value, vat: x.vt.value }));
        await act(() => api(`/api/ap/captures/${c.id}/decide`, { body: { action: "save", data: { fields: out } } }), t("Saved. Pip and Tess have it again."));
        openCapture(c.id); load(); if (!$("tab-needs").classList.contains("hide")) loadNeeds();
      } }, t("Save and test again")),
      el("button", { class: "btn sm", type: "button", onclick: async () => { await act(() => api(`/api/ap/captures/${c.id}/decide`, { body: { action: "skip" } }), t("Skipped.")); dlg.close(); load(); loadNeeds(); } }, t("Skip it"))));
    if (c.is_pdf && c.pages) {
      let page = 1;
      const img = el("img", { alt: t("Page {n}", { n: 1 }), src: `${B}/api/ap/captures/${c.id}/page/1`, style: { width: "100%", border: "1px solid var(--rule)", background: "#fff", cursor: "zoom-in" },
        onclick: () => window.open(img.src, "_blank", "noopener") });
      const lab = el("span", { class: "small muted" }, t("Page {n} of {total}", { n: 1, total: c.pages }));
      const go = (d) => { page = Math.min(c.pages, Math.max(1, page + d)); img.src = `${B}/api/ap/captures/${c.id}/page/${page}`; img.alt = t("Page {n}", { n: page }); lab.textContent = t("Page {n} of {total}", { n: page, total: c.pages }); };
      const pager = c.pages > 1 ? el("div", { style: { display: "flex", gap: "10px", alignItems: "center" } }, el("button", { class: "link small", type: "button", onclick: () => go(-1) }, t("Previous")), lab, el("button", { class: "link small", type: "button", onclick: () => go(1) }, t("Next"))) : null;
      body.appendChild(el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,420px),1fr))", gap: "22px", alignItems: "start" } },
        el("div", { class: "stack", style: { gap: "6px", maxHeight: "72vh", overflowY: "auto" } }, img, pager), form));
    } else body.appendChild(form);

    if (c.sap_rows.length) body.appendChild(el("div", { class: "stack", style: { gap: "6px" } }, el("div", { class: "k" }, t("SAP rows")),
      el("div", { style: { overflowX: "auto" } }, el("table", { class: "tbl" }, el("thead", null, el("tr", null, ["pkey", "account", "amount", "tc", "dt", "costcentre", "text"].map((h) => el("th", null, h)))),
        el("tbody", null, c.sap_rows.map((r) => el("tr", null, el("td", { class: "mono small" }, r.pkey), el("td", { class: "mono small" }, r.account, r.gl_name ? el("div", { class: "muted", style: { fontFamily: "inherit" } }, r.gl_name) : null), el("td", { class: "mono small", style: { textAlign: "end" } }, r.amount),
          el("td", { class: "mono small" }, r.tc), el("td", { class: "mono small" }, r.dt), el("td", { class: "mono small" }, r.costcentre, r.cc_name ? el("div", { class: "muted", style: { fontFamily: "inherit" } }, r.cc_name) : null), el("td", { class: "small" }, r.text))))))));
    if (data.is_admin && c.uploaded_at) body.appendChild(el("div", null, el("button", { class: "link small", type: "button", onclick: async () => { await act(() => api(`/api/ap/captures/${c.id}/release`, { method: "POST" }), t("Released.")); openCapture(c.id); load(); } }, t("Release the upload mark"))));
    body.appendChild(el("details", null, el("summary", { class: "small", style: { cursor: "pointer" } }, t("History ({n})", { n: c.history.length })),
      el("div", { class: "stack", style: { gap: "4px", marginTop: "8px" } }, c.history.slice().reverse().map((h) => el("div", { class: "small" }, el("span", { class: "muted" }, `${when(h.at)} · ${h.who}: `), h.text)))));
    if (!dlg.open) dlg.showModal();
  }
  $("dlg").addEventListener("close", () => { $("dlg").style.width = "min(900px,calc(100vw - 32px))"; });

  // ---- Needs you -------------------------------------------------------------------------------------------------
  // 1.0.99: while the team works, Needs you refreshes on The team's beat (and once more when the work stops), so a file
  // that leaves it (read again, back in the queue) disappears and a new one shows up without leaving the tab
  async function loadNeeds() {
    clearTimeout(needsTimer); needsTimer = null; needsAt = Date.now();
    if (!root.isConnected) return;
    let n, list;
    try { [n, list] = await Promise.all([api("/api/ap/needs"), api("/api/ap/items?level=3&open=true")]); } catch (e) { return; }
    const odd = new Set((n.odd || []).concat(n.other || []).map((x) => x.item_id));
    drawNeeds(n);
    const rest = list.items.filter((i) => !odd.has(i.id));
    $("oldneeds").classList.toggle("hide", !rest.length && n.count > 0);
    drawTable($("needs"), rest);
    if (n.busy && !$("tab-needs").classList.contains("hide")) needsTimer = setTimeout(loadNeeds, 2500);
  }
  // how long a file has waited, in the words people use ("waiting 1 day")
  function waited(iso) {
    if (!iso) return "";
    const m = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000));
    if (m < 60) return m <= 1 ? t("waiting 1 minute") : t("waiting {n} minutes", { n: m });
    const h = Math.floor(m / 60);
    if (h < 24) return h === 1 ? t("waiting 1 hour") : t("waiting {n} hours", { n: h });
    const d = Math.floor(h / 24);
    return d === 1 ? t("waiting 1 day") : t("waiting {n} days", { n: d });
  }
  const batchLine = (c) => [c.batch_named ? c.batch : c.batch_at ? t("Batch of {when}", { when: when(c.batch_at) }) : c.batch, waited(c.waiting_since)].filter(Boolean).join(" · ");

  function sec(title, sub, ...kids) {
    return el("section", { class: "sec" }, el("div", { class: "sec-h" }, el("h2", { class: "h2" }, title), el("span", { class: "k" }, sub)), kids);
  }
  const decide = (c, action, body, ok) => act(() => api(`/api/ap/captures/${c.id}/decide`, { body: { action, data: body || {} } }), ok).then(() => { loadNeeds(); load(); });
  const fileCell = (c) => el("div", { style: { flex: "1 1 240px", minWidth: "0" } }, el("button", { class: "link", type: "button", style: { fontWeight: "600", overflowWrap: "anywhere", textAlign: "start" }, onclick: () => openCapture(c.id) }, base(c.file)), el("div", { class: "small muted" }, batchLine(c)),
    el("div", { class: "small" }, slot("cap", c.id, "")));  // 1.0.103: a bot on it now (read again)

  function drawNeeds(n) {
    const box = clear($("capneeds")), W = data.can_write;
    setTimeout(() => paintBusy(box), 0);  // 1.0.103: once the sections are in
    // 1.0.104: the Valuence controls' person-actionable lines (every builder's, from /api/ap/needs "controls"): one small line each, into Controls
    if ((n.controls || []).length) box.appendChild(el("section", { class: "sec stack", style: { gap: "6px" } }, n.controls.map((x) =>
      el("div", { class: "small" }, el("button", { class: "link", type: "button", onclick: () => goControls(x.sub, x.params || {}) }, x.text)))));
    const ents = (masters && masters.entities) || null;
    if (n.other.length) box.appendChild(sec(t("Other entities"), t("not in the master lists"), n.other.map((c) => {
      const f = c.fields || {}, util = f.kind === "utility";
      const what = c.match && c.match.account && c.match.account.found === false ? t("Account {a} is not in the utilities list.", { a: f.account || "?" })
        : t('The bill-to "{name}" is no company code in the list.', { name: (c.match && c.match.entity && c.match.entity.bill_to) || f.bill_to || "?" });
      const more = el("div", { class: "stack hide", style: { gap: "8px", flex: "1 1 100%" } });
      const known = util && c.profile_known;  // 1.0.101: PDF Studio reads it: the account is what is missing, not a profile
      const row = el("div", { class: "row" }, fileCell(c), el("div", { class: "small", style: { flex: "2 1 260px" } }, what,
        known ? el("div", { class: "muted" }, t("PDF Studio reads these bills (profile {name}).", { name: c.profile_known }))
          : c.needs === "profile" ? el("div", { class: "muted" }, t("Waiting for a PDF Studio profile")) : null), more);
      if (W && known) {
        const acts = el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } });
        if (data.is_admin) {
          const a = c.add || {};
          const muni = el("input", { class: "in", value: a.municipality || f.municipality || "", placeholder: t("Municipality") });
          const v = el("input", { class: "in", placeholder: t("Vendor (BP) no") }), cc = el("input", { class: "in", placeholder: t("PID / cost centre") }), site = el("input", { class: "in", placeholder: t("Site (NAME1)") });
          more.append(el("div", { class: "small muted" }, t("Account {a}", { a: a.account || f.account || "?" })),
            el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))", gap: "8px" } }, v, cc, site, muni),
            el("div", null, el("button", { class: "btn sm primary", type: "button", onclick: () => decide(c, "add_account", { vendor: v.value, cost_centre: cc.value, site: site.value, municipality: muni.value }, t("Added. Mia matches it now.")) }, t("Save"))));
          acts.append(el("button", { class: "btn sm primary", type: "button", onclick: () => { more.classList.toggle("hide"); if (!more.classList.contains("hide")) v.focus(); } }, t("Add to the utilities list")));
        }
        acts.append(el("button", { class: "btn sm", type: "button", onclick: () => decide(c, "manual", {}, t("The capture bots read it next.")) }, t("Manual capture")),
          el("button", { class: "btn sm", type: "button", onclick: () => decide(c, "skip", {}, t("Skipped.")) }, t("Skip")));
        row.insertBefore(acts, more);
      } else if (W && c.needs === "profile") row.insertBefore(el("button", { class: "btn sm", type: "button", onclick: () => decide(c, "again", {}, t("Reading it again.")) }, t("Read again")), more);
      else if (W) {
        const acts = el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } });
        acts.append(
          el("button", { class: "btn sm", type: "button", onclick: () => decide(c, "profile", {}, t("Asked for a PDF Studio profile.")) }, t("New profile")),
          el("button", { class: "btn sm primary", type: "button", onclick: () => { more.classList.toggle("hide"); } }, t("Manual capture")),
          el("button", { class: "btn sm", type: "button", onclick: () => decide(c, "skip", {}, t("Skipped.")) }, t("Skip")));
        row.insertBefore(acts, more);
        if (util) {  // 1.0.101: only admins add accounts to the utilities list; for others it goes to the capture bots
          const v = el("input", { class: "in", placeholder: t("Vendor (BP) no") }), cc = el("input", { class: "in", placeholder: t("PID / cost centre") }), site = el("input", { class: "in", placeholder: t("Site (NAME1)") });
          if (data.is_admin) more.append(el("div", { class: "small muted" }, t("Add the account to the utilities list and it goes on as a utility bill; leave it empty and the capture bots read it instead.")),
            el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))", gap: "8px" } }, v, cc, site));
          more.append(el("div", null, el("button", { class: "btn sm primary", type: "button", onclick: () => decide(c, "manual", data.is_admin ? { vendor: v.value, cost_centre: cc.value, site: site.value } : {}, t("On its way.")) }, t("Continue"))));
        } else {
          const sel = el("select", { class: "in", style: { width: "auto" } }, el("option", { value: "" }, t("Pick the company code")), (ents || []).map((e) => el("option", { value: e.code }, `${e.code} · ${e.name}`)));
          const rem = el("input", { type: "checkbox", checked: true });
          if (!ents) loadEntityList().then((list) => list.forEach((e) => sel.appendChild(el("option", { value: e.code }, `${e.code} · ${e.name}`))));
          more.append(el("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" } }, sel,
            el("label", { class: "small", style: { display: "flex", gap: "6px", alignItems: "center" } }, rem, " ", t("Remember this bill-to name for it")),
            el("button", { class: "btn sm primary", type: "button", onclick: () => decide(c, "manual", { company_code: sel.value, remember: rem.checked }, t("On its way.")) }, t("Continue"))));
        }
      }
      return row;
    })));
    if (n.odd.length) box.appendChild(sec(t("Odd files"), t("not an invoice the team can place"), n.odd.map((c) => el("div", { class: "row" }, fileCell(c),
      el("div", { class: "small muted", style: { flex: "2 1 260px" } }, c.why || ""),
      W ? el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
        el("button", { class: "btn sm primary", type: "button", onclick: () => decide(c, "manual", {}, t("The capture bots read it next.")) }, t("Manual capture")),
        el("button", { class: "btn sm", type: "button", onclick: async () => { await decide(c, "ap", {}, t("Back to the AP team's checks.")); openItem(c.item_id); } }, t("Not an invoice")),
        el("button", { class: "btn sm", type: "button", onclick: () => decide(c, "skip", {}, t("Skipped.")) }, t("Skip"))) : null))));
    if (n.unread.length) box.appendChild(sec(t("Invoices the senior couldn't read"), t("type them in beside the page"), n.unread.map((c) => el("div", { class: "row" }, fileCell(c),
      el("div", { class: "small muted", style: { flex: "2 1 260px" } }, [c.fields.supplier, c.fields.invoice_no].filter(Boolean).join(" · ") || (c.problems || []).join("; ")),
      el("button", { class: "btn sm primary", type: "button", onclick: () => openCapture(c.id) }, W ? t("Type it in") : t("Open"))))));
    if (n.vendors.length) box.appendChild(sec(t("Vendors to code"), t("saved to the vendor list and used from then on"), n.vendors.map((g) => {
      const sv = g.sap || null;  // 1.0.102: SAP's vendor list knows it: its number, name and payment days to start from
      const h0 = g.history || null;  // 1.0.103: how SAP coded this vendor before: the most used G/L, cost centre and tax code
      const glf = glField(h0 ? h0.gl : "", { placeholder: t("GL: number or words") }), gl = glf.input;
      const vno = el("input", { class: "in", placeholder: t("Vendor (BP) no"), value: sv ? sv.vendor : "" }),
        cc = el("input", { class: "in", placeholder: t("Cost centre"), value: h0 ? h0.cost_centre || "" : "" }), tx = el("input", { class: "in", value: h0 && h0.tax_code ? h0.tax_code : "I9", placeholder: t("Tax code") });
      const dayOf = (x) => x && x.payment_days !== null && x.payment_days !== undefined ? String(x.payment_days) : "";
      const days = el("input", { class: "in", type: "number", min: "0", max: "365", placeholder: t("Payment days"), value: dayOf(sv) || dayOf(h0) });
      const before = el("div", null, postedBefore(h0));
      // 1.0.103: one VAT number, several SAP vendors: a person picks one; its history in the posting company fills the form
      const pickSel = sv && (sv.several || []).length ? el("select", { class: "in", style: { width: "auto", maxWidth: "100%" }, onchange: async () => {
        vno.value = pickSel.value;
        if (!pickSel.value) return;
        let r; try { r = await api(`/api/ap/vendors/history?vendor=${encodeURIComponent(pickSel.value)}&company=${encodeURIComponent(g.company_code || "")}`); } catch (e) { return; }
        const h = r.history;
        if (h) { gl.value = h.gl || ""; gl.dispatchEvent(new Event("change")); cc.value = h.cost_centre || ""; if (h.tax_code) tx.value = h.tax_code; }
        days.value = dayOf(r.sap) || dayOf(h);
        clear(before).appendChild(postedBefore(h) || el("span"));
      } }, el("option", { value: "" }, t("Pick the SAP vendor")),
        sv.several.map((x) => el("option", { value: x.vendor }, [x.vendor, x.name, x.city, x.docs ? t("{n} posted", { n: x.docs }) : ""].filter(Boolean).join(" · ")))) : null;
      const rf = rulesField([]);
      const only = el("input", { type: "checkbox" });
      const inSap = sv && sv.vendor ? el("div", { class: "small", style: { color: sv.blocked || sv.how === "name" ? "var(--warn)" : "var(--ink2)" } },
        t("In SAP: vendor {v} · {name}", { v: sv.vendor, name: sv.name }), sv.how === "name" ? " · " + t("matched by name: check it") : "", sv.blocked ? " · " + t("blocked in SAP") : "")
        : sv && sv.note ? el("div", { class: "small", style: { color: "var(--warn)" } }, sv.note) : null;  // 1.0.103: several SAP vendors (pick one), or only blocked ones
      const posted = (g.posted || []).length ? el("div", { class: "small", style: { color: g.posted.every((x) => x.reversed) ? "var(--warn)" : "var(--bad)" } }, g.posted.map((x) => x.text).filter((x, i, a) => a.indexOf(x) === i).join("; ")) : null;  // 1.0.103
      const nameOf = () => (sv && sv.several ? ((sv.several.find((x) => x.vendor === vno.value) || {}).name || "") : (sv ? sv.name : "")) || g.supplier;
      return el("div", { class: "row" },
        el("div", { style: { flex: "1 1 240px", minWidth: "0" } }, el("strong", null, g.supplier || "?"), el("div", { class: "small muted" }, (g.vat ? t("VAT {v}", { v: g.vat }) + " · " : "") + (g.ids.length === 1 ? t("{n} invoice", { n: 1 }) : t("{n} invoices", { n: g.ids.length })) + " · " + g.files.map(base).slice(0, 3).join(", ")), inSap, posted),
        W ? el("div", { class: "stack", style: { gap: "8px", flex: "2 1 420px" } },
          pickSel,
          el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(130px,1fr))", gap: "8px", alignItems: "start" } }, vno, glf.wrap, cc, tx, days),
          before,
          el("details", null, el("summary", { class: "small", style: { cursor: "pointer" } }, t("Line rules")), rf.wrap),
          el("div", { style: { display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" } },
            g.company_code ? el("label", { class: "small", style: { display: "flex", gap: "6px", alignItems: "center" } }, only, " ", t("Only for {code}", { code: g.company_code })) : null,
            el("button", { class: "btn sm primary", type: "button", onclick: async () => {
              const r = await act(() => api("/api/ap/vendors/code", { body: { ids: g.ids, vendor_no: vno.value, gl: gl.value, cost_centre: cc.value, tax_code: tx.value, name: nameOf(), vat_no: g.vat,
                company_code: g.company_code, only_this_company: only.checked, payment_days: days.value === "" ? null : Number(days.value), line_rules: rf.read() } }), t("Coded. Pip and Tess have it."));
              if (r && r.warning) glWarn({ ...r, gl: r.vendor.gl });
              loadNeeds(); load();
            } }, t("Save")))) : null);
    })));
    // 1.0.102: a site in the bill-to that Settings → Sites has no cost centre for (the invoices go on with the vendor's)
    if ((n.sites || []).length) box.appendChild(sec(t("Sites to map"), t("a site in the bill-to with no cost centre yet"), n.sites.map((g) => {
      const co = el("input", { class: "in", value: g.companies.length === 1 ? g.companies[0] : "", placeholder: t("Company (blank = any)") });
      const cc = el("input", { class: "in", placeholder: t("Cost centre") }), note = el("input", { class: "in", placeholder: t("Note") });
      return el("div", { class: "row" },
        el("div", { style: { flex: "1 1 200px", minWidth: "0" } }, el("strong", { class: "mono" }, g.site),
          el("div", { class: "small muted" }, (g.n === 1 ? t("{n} invoice", { n: 1 }) : t("{n} invoices", { n: g.n })) + (g.companies.length ? " · " + g.companies.join(", ") : ""))),
        W && data.is_admin ? el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(130px,1fr))", gap: "8px", alignItems: "center", flex: "2 1 420px" } }, co, cc, note,
          el("div", null, el("button", { class: "btn sm primary", type: "button", onclick: async () => { await act(() => api("/api/ap/sites/map", { body: { site_code: g.site, company_code: co.value, cost_centre: cc.value, note: note.value } }), t("Saved. Pip builds its invoices again.")); loadNeeds(); load(); } }, t("Save"))))
          : el("div", { class: "small muted", style: { flex: "2 1 300px" } }, t("An admin gives the site its cost centre (Settings → Sites).")));
    })));
    if (n.red.length) box.appendChild(sec(t("Red SAP documents"), t("never in the upload until fixed"), n.red.map((c) => el("div", { class: "row" }, fileCell(c),
      el("div", { class: "small", style: { flex: "2 1 300px", color: "var(--bad)" } }, (c.issues || []).filter((x) => x.level === "red").map((x) => x.text).join("; ")),
      el("div", { style: { display: "flex", gap: "8px" } }, el("button", { class: "btn sm primary", type: "button", onclick: () => openCapture(c.id) }, W ? t("Fix it") : t("Open")),
        W ? el("button", { class: "btn sm", type: "button", onclick: () => decide(c, "skip", {}, t("Skipped.")) }, t("Skip")) : null)))));
    // 2.10.0: what people added (a vendor coding, an account from Other) stays yellow until an admin confirms it once
    if ((n.confirm || []).length) box.appendChild(sec(t("To confirm"), t("added by people: yellow until an admin confirms it"), n.confirm.map((x) => el("div", { class: "row" },
      el("div", { style: { flex: "1 1 240px", minWidth: "0" } }, el("strong", null, x.kind === "vendors" ? `${x.vendor} · ${x.name || ""}` : t("Account {a}", { a: x.account })),
        el("div", { class: "small muted" }, (x.kind === "vendors" ? [t("GL {g}", { g: x.gl || "-" }), x.cost_centre, x.tax_code] : [t("Vendor {v}", { v: x.vendor }), x.cost_centre, x.name]).filter(Boolean).join(" · "))),
      el("div", { class: "small muted", style: { flex: "1 1 160px" } }, [x.by, when(x.at)].filter(Boolean).join(", ")),
      el("button", { class: "btn sm primary", type: "button", onclick: async () => { await act(() => api(`/api/ap/masters/${x.kind}/${x.id}/confirm`, { method: "POST" }), t("Confirmed. Tess tests it again.")); loadNeeds(); load(); } }, t("Confirm"))))));
  }

  async function loadEntityList() {
    try { const r = await api("/api/ap/masters/entities?limit=500"); return r.rows; } catch (e) { return []; }
  }

  // ---- one file ---------------------------------------------------------------------------------------
  async function openItem(id) {
    const it = await api(`/api/ap/items/${id}`);
    const dlg = $("dlg");
    $("dlgt").textContent = it.name;
    clear($("dlgsub")).append(`${it.kind === "utility" ? t("Utility bill") : it.kind_label} · ${it.bot_name} · `, pill(it.ai_wait ? "ai_wait" : it.status), " · " + t("with {name}", { name: it.with }), it.file_id ? el("a", { class: "link small", style: { marginLeft: "12px" }, href: `${B}/api/ap/files/${it.file_id}` }, t("Open the file")) : "",
      el("span", { style: { marginInlineStart: "12px" } }, slot("item", it.id, "")));  // 1.0.103
    paintBusy($("dlgsub"));
    const body = clear($("dlgbody"));
    const W = data.can_write && !["cleared", "rejected"].includes(it.status);

    if (it.findings.length) body.appendChild(el("div", { class: "stack", style: { gap: "6px" } }, el("div", { class: "k" }, t("What the checks found")),
      it.findings.map((f) => el("div", { class: "small", style: { color: f.severity === "stop" ? "var(--bad)" : f.severity === "check" ? "var(--warn)" : "var(--ink2)" } }, `${f.severity === "stop" ? t("STOP") : f.severity === "check" ? t("CHECK") : t("NOTE")} · ${f.text}`))));
    if (it.suggestion) body.appendChild(el("div", { class: "small", style: { padding: "10px 12px", border: "1px solid var(--rule)", background: "var(--surf)" } }, t("AXL read it as {kind} ({n}%): {why}", { kind: (data.kinds.find((k) => k.key === it.suggestion.kind) || {}).label || it.suggestion.kind, n: it.suggestion.confidence, why: it.suggestion.reason || "" })));

    // fields + fix
    const kindSel = el("select", { class: "in", disabled: !W }, data.kinds.map((k) => el("option", { value: k.key, selected: k.key === it.kind }, k.key === "utility" ? t("Utility bill") : k.label)));
    const inputs = {};
    const grid = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: "10px" } });
    const shown = it.editable.filter((k) => it.fields[k] !== undefined || ["supplier", "invoice_no", "total"].includes(k) && ["invoice", "credit_note", "unknown"].includes(it.kind));
    shown.forEach((k) => { inputs[k] = el("input", { class: "in", value: it.fields[k] === null || it.fields[k] === undefined ? "" : String(it.fields[k]), disabled: !W }); grid.appendChild(el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, FIELD[k] || k), inputs[k])); });
    const teach = el("input", { class: "in", placeholder: t("e.g. the supplier's name or a heading on the file"), disabled: !W });
    const fixNote = el("input", { class: "in", placeholder: t("Note (optional)"), disabled: !W });
    body.appendChild(el("div", { class: "stack", style: { gap: "10px" } }, el("div", { class: "k" }, t("What it is and what was read")),
      el("label", { class: "stack", style: { gap: "4px", maxWidth: "320px" } }, el("span", { class: "k" }, t("Kind")), kindSel), grid,
      W ? el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, t("Teach the team (optional): files with this text are this kind")), teach) : null,
      W ? fixNote : null,
      W ? el("div", null, el("button", { class: "btn sm primary", type: "button", onclick: async () => {
        const fields = {}; Object.entries(inputs).forEach(([k, i]) => { if (String(it.fields[k] ?? "") !== i.value) fields[k] = i.value; });
        await act(() => api(`/api/ap/items/${it.id}/fix`, { body: { kind: kindSel.value, fields, teach: teach.value, note: fixNote.value } }), t("Fixed and checked again."));
        openItem(it.id); load();
      } }, t("Save and check again"))) : null));

    if (it.rows && it.rows.length) {
      const t = el("table", { class: "tbl" }, el("thead", null, el("tr", null, [cbx.t("Row"), cbx.t("Vendor"), cbx.t("Name"), cbx.t("Reference"), cbx.t("Amount"), cbx.t("Due"), cbx.t("Pay date"), cbx.t("Bank")].map((h) => el("th", null, h)))),
        el("tbody", null, it.rows.slice(0, 200).map((r) => el("tr", { style: { background: it.findings.some((f) => f.row === r.row && f.severity === "stop") ? "color-mix(in srgb,var(--bad) 10%,transparent)" : "" } },
          el("td", { class: "mono small" }, r.row), el("td", { class: "small" }, r.vendor), el("td", { class: "small" }, r.name), el("td", { class: "mono small" }, r.ref),
          el("td", { class: "mono small", style: { textAlign: "right" } }, money(r.amount)), el("td", { class: "small" }, r.due || ""), el("td", { class: "small" }, r.pay_date || ""),
          el("td", { class: "mono small" }, r.bank_account ? "…" + String(r.bank_account).slice(-4) : "")))));
      body.appendChild(el("div", { class: "stack", style: { gap: "6px" } }, el("div", { class: "k" }, cbx.t("Lines ({n})", { n: it.rows.length })), el("div", { style: { overflowX: "auto", maxHeight: "300px" } }, t)));
    }

    // Quinn
    if (it.kind === "query") {
      const ta = el("textarea", { class: "in", rows: 9, style: { fontFamily: "inherit" }, disabled: !data.can_write }, it.reply || "");
      body.appendChild(el("div", { class: "stack", style: { gap: "8px" } }, el("div", { class: "k" }, t("Quinn's answer (a person sends it)")),
        it.text ? el("details", null, el("summary", { class: "small", style: { cursor: "pointer" } }, t("The query")), el("pre", { class: "small", style: { whiteSpace: "pre-wrap" } }, it.text)) : null, ta,
        data.can_write ? el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
          el("button", { class: "btn sm", type: "button", onclick: async () => { await act(() => api(`/api/ap/items/${it.id}/draft`, { method: "POST" }), t("Quinn drafted it.")); openItem(it.id); } }, it.reply ? t("Draft again") : t("Ask Quinn to draft")),
          el("button", { class: "btn sm", type: "button", onclick: async () => { await act(() => api(`/api/ap/items/${it.id}/reply`, { body: { text: ta.value } }), t("Saved.")); } }, t("Save")),
          el("button", { class: "btn sm", type: "button", onclick: async () => { await navigator.clipboard.writeText(ta.value); toast(t("Copied: paste it into your reply.")); } }, t("Copy"))) : null));
    }

    // sign off
    if (W) {
      const note = el("input", { class: "in", placeholder: it.stops ? t("Required: what you verified, or why it can go ahead") : t("Note (optional)") });
      const btn = (label, action, ghost) => el("button", { class: "btn sm" + (ghost ? "" : " primary"), type: "button", onclick: async () => {
        await act(() => api(`/api/ap/items/${it.id}/sign`, { body: { action, note: note.value } }), label + ".");
        dlg.close(); load();
      } }, label);
      const acts = [];
      if (it.four_eye) {
        if (it.status === "waiting") acts.push(it.checked_by_id === data.me ? el("span", { class: "small muted" }, t("You checked it. A second person approves.")) : btn(t("Approve (second person)"), "approve"));
        else acts.push(btn(t("Checked (first person)"), "check"));
      } else if (it.kind === "query") acts.push(btn(t("Answered"), "answered"));
      else if (it.kind !== "unknown") acts.push(btn(t("Clear"), "clear"));
      acts.push(btn(t("Reject"), "reject", true));
      body.appendChild(el("div", { class: "stack", style: { gap: "8px" } }, el("div", { class: "k" }, it.four_eye ? t("Sign off (two different people)") : t("Sign off")), note,
        el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" } }, acts),
        it.checked_by ? el("div", { class: "small muted" }, t("Checked by {name}", { name: it.checked_by })) : null));
    } else if (it.checked_by || it.approved_by) body.appendChild(el("div", { class: "small muted" }, [it.checked_by && t("Checked by {name}", { name: it.checked_by }), it.approved_by && t("approved by {name}", { name: it.approved_by })].filter(Boolean).join(", ")));

    body.appendChild(el("details", null, el("summary", { class: "small", style: { cursor: "pointer" } }, t("History ({n})", { n: it.history.length })),
      el("div", { class: "stack", style: { gap: "4px", marginTop: "8px" } }, it.history.slice().reverse().map((h) => el("div", { class: "small" }, el("span", { class: "muted" }, `${when(h.at)} · ${h.who}: `), h.text)))));
    if (!dlg.open) dlg.showModal();
  }

  // ---- the team ------------------------------------------------------------------------------------------
  function drawTeam() {
    const t = data.team, chain = clear($("chain"));
    t.chain.forEach((c, i) => { if (i) chain.appendChild(el("span", { class: "muted" }, ARROW)); chain.appendChild(el("span", { class: "pill", style: { border: "1px solid var(--rule)", padding: "4px 10px" } }, c)); });
    const bots = clear($("bots"));
    t.bots.forEach((b) => bots.appendChild(el("section", { class: "sec", style: { margin: "0" } },
      el("div", { class: "sec-h" }, el("h2", { class: "h2" }, b.name), el("span", { class: "k" }, b.role)),
      el("p", { class: "small", style: { marginTop: "0" } }, b.does),
      b.kinds.length ? el("div", { class: "small muted", style: { marginBottom: "6px" } }, cbx.t("Takes: {list}", { list: b.kinds.join(", ") })) : null,
      el("ul", { class: "small", style: { paddingLeft: "18px", margin: "0" } }, b.can.map((c) => el("li", null, c))),
      el("div", { class: "small", "data-team-doc": b.name, style: { marginTop: "6px", overflowWrap: "anywhere" } }))));  // 1.0.103: the file they are on now
    const rules = clear($("rules")); t.rules.forEach((r) => rules.appendChild(el("li", { style: { marginBottom: "6px" } }, r)));
    const kpis = clear($("kpis")); t.kpis.forEach((r) => kpis.appendChild(el("li", null, r)));
    const taught = clear($("taught"));
    if (!data.rules.length) taught.appendChild(el("div", { class: "small muted" }, cbx.t('Nothing taught yet. Fix a file and fill in "teach the team".')));
    data.rules.forEach((r) => taught.appendChild(el("div", { class: "row" }, el("div", { class: "grow small" }, `"${r.match}" → ${(data.kinds.find((k) => k.key === r.kind) || {}).label || r.kind}`, el("span", { class: "muted" }, ` · ${r.by} · ` + cbx.t("used {n}×", { n: r.uses }))),
      data.can_write ? el("button", { class: "link small", type: "button", onclick: async () => { await act(() => api(`/api/ap/rules/${r.id}/off`, { method: "POST" }), cbx.t("Turned off.")); load(); } }, cbx.t("Turn off")) : null)));
  }

  // 2.10.0: the live line — Sol → (Uma | Cara, Dex, Eli → Seth) → Mia → Pip → Tess
  async function loadLive() {
    clearTimeout(liveTimer);
    if ($("tab-team").classList.contains("hide") || !root.isConnected) return;
    let lv;
    try { lv = await api("/api/ap/live" + (batchId ? "?batch=" + batchId : "")); } catch (e) { return; }
    drawLive(lv);
    const busy = (lv.batch && lv.batch.busy) || lv.bots.some((b) => b.status === "working" || b.queue);
    liveTimer = setTimeout(loadLive, busy ? 2500 : 12000);
  }

  function botCard(b, short) {
    const dot = el("span", { style: { display: "inline-block", width: "9px", height: "9px", borderRadius: "50%", flex: "none",
      background: b.status === "working" ? "var(--good)" : b.status === "waiting" || b.status === "ai_wait" ? "var(--warn)" : b.status === "paused" ? "var(--bad)" : "var(--rule)" } });
    if (b.status === "working" && !still() && dot.animate) dot.animate([{ opacity: 1, transform: "scale(1)" }, { opacity: 0.3, transform: "scale(.7)" }], { duration: 850, iterations: Infinity, direction: "alternate" });
    // 1.0.99: a reader whose invoices wait for the AI says so (not idle, not reading)
    const state = b.status === "working" ? (b.task || t("Working")) : b.status === "ai_wait" ? t("Waiting for the AI") : b.status === "waiting" ? t("Waiting: {n} in the queue", { n: b.queue }) : b.status === "paused" ? t("Paused") : t("Idle");
    const doc = b.doc ? docLink(b.doc) : null;  // 1.0.103: the document it is on now ("INV-2758.pdf · reading 0:42"); a click opens it
    return el("div", { style: { border: "1px solid var(--rule)", background: "var(--surf)", padding: "8px 10px", display: "flex", flexDirection: "column", gap: "3px", width: "128px", flex: "none", boxSizing: "border-box", borderRadius: "var(--rs,0)" } },
      el("div", { style: { display: "flex", alignItems: "center", gap: "8px" } }, dot, el("strong", null, b.name)),
      short ? null : el("div", { class: "small muted" }, DOES[b.key] || ""),
      doc || el("div", { class: "small", style: { overflowWrap: "anywhere", color: b.status === "working" ? "var(--ink)" : "var(--ink2)" } }, state),
      el("div", { class: "small muted mono" }, t("{n} today", { n: b.done_today }) + (b.queue ? " · " + t("{n} waiting", { n: b.queue }) : "")),
      b.model ? el("div", { class: "small muted mono", style: { overflowWrap: "anywhere" } }, b.model) : null);
  }

  function docLink(d) {
    return el("div", { class: "small stack", style: { gap: "2px", overflowWrap: "anywhere" } },
      el("button", { class: "link small", type: "button", style: { textAlign: "start", overflowWrap: "anywhere" }, onclick: () => d.capture_id ? openCapture(d.capture_id) : openItem(d.item_id) }, base(d.file)), cbx.busyChip(d, true));
  }
  function drawLive(lv) {
    root.querySelectorAll("[data-team-doc]").forEach((x) => { const d = (lv.team_docs || {})[x.dataset.teamDoc]; clear(x); if (d) x.appendChild(docLink(d)); });  // 1.0.103: Paula, AXL, Ivy …
    const by = Object.fromEntries(lv.bots.map((b) => [b.key, b]));
    const scan = clear($("scan"));
    $("livek").textContent = lv.batch ? lv.batch.name : "";
    if (lv.batch) {
      const pct = lv.batch.files ? Math.round(100 * lv.batch.sorted / lv.batch.files) : 0;
      scan.append(el("div", { class: "small" }, lv.batch.busy ? t("Scanning {n} of {total} PDFs", { n: lv.batch.sorted, total: lv.batch.files }) : t("All {total} files sorted", { total: lv.batch.files })),
        el("div", { class: "bar" }, el("i", { style: { width: pct + "%" } })));
    } else scan.appendChild(el("div", { class: "small muted" }, t("No folder yet. Drop one on the Inbox.")));
    const line = clear($("line"));
    line.style.alignItems = "center";
    line.style.gap = "6px";
    const arrow = () => el("div", { class: "muted", "aria-hidden": "true", style: { fontSize: "16px", width: "14px", textAlign: "center" } }, ARROW);
    // the readers: Uma on the utilities lane; the three capture bots on one queue, and Seth behind them
    const readers = el("div", { style: { display: "grid", gridTemplateColumns: "auto auto auto", gap: "6px", alignItems: "center", padding: "8px", border: "1px dashed var(--rule)", borderRadius: "var(--rs,0)" } },
      el("div", { class: "k", style: { gridColumn: "1 / -1" } }, t("Utilities")), botCard(by.uma), el("div"), el("div"),
      el("div", { style: { gridColumn: "1 / -1", marginTop: "4px", maxWidth: "280px" } }, el("div", { class: "k" }, t("Manual capture")), el("div", { class: "small muted" }, DOES.cara)),
      el("div", { style: { display: "flex", flexDirection: "column", gap: "6px" } }, botCard(by.cara, true), botCard(by.dex, true), botCard(by.eli, true)), arrow(), botCard(by.seth));
    line.append(botCard(by.sol), arrow(), readers, arrow(), botCard(by.mia), arrow(), botCard(by.pip), arrow(), botCard(by.tess));
  }

  // ---- settings ------------------------------------------------------------------------------------------
  function drawSettings() {
    const f = clear($("setf"));
    Object.entries(LABEL).forEach(([k, l]) => f.appendChild(el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, l), el("input", { class: "in", name: k, type: "number", step: "any", value: data.settings[k], disabled: !data.is_admin }))));
    if (data.is_admin) f.appendChild(el("div", null, el("button", { class: "btn primary", type: "submit" }, t("Save"))));
  }
  $("setf").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {}; Object.keys(LABEL).forEach((k) => { body[k] = e.target[k].value; });
    await act(() => api("/api/ap/settings", { method: "PUT", body }), t("Saved. The next folder is checked with these."));
    load();
  });

  $("setnav").querySelectorAll("[data-set]").forEach((b) => b.addEventListener("click", () => { setPane = b.dataset.set; drawPane(); }));
  async function loadMasters() {
    try { masters = await api("/api/ap/masters"); } catch (e) { return; }
    drawPane();
  }

  function drawPane() {
    $("setnav").querySelectorAll("[data-set]").forEach((b) => b.classList.toggle("on", b.dataset.set === setPane));
    $("threshsec").classList.toggle("hide", setPane !== "thresholds");
    const pane = clear($("setpane"));
    if (!masters || setPane === "thresholds") return;
    if (["entities", "utilities", "vendors", "sites", "sap_vendors", "gl_accounts", "vendor_companies", "cost_centres"].includes(setPane)) pane.appendChild(listPane(setPane));
    else if (setPane === "sap") pane.appendChild(sapPane());
    else if (setPane === "doctypes") pane.appendChild(docTypePane());
    else if (setPane === "history") pane.appendChild(historyPane());  // 1.0.103
    else if (setPane === "models" && masters.is_admin) pane.appendChild(modelPane());
    else if (setPane === "portfolios" && masters.is_admin) pane.appendChild(portfolioPane({ api, act, el, clear, t }));  // 1.0.104
    else if (setPane === "people" && masters.is_admin) pane.appendChild(peoplePane({ api, act, el, clear, t }));  // 1.0.104
    else if (setPane === "tariffs" && masters.is_admin) pane.appendChild(tariffPane({ api, act, el, clear, t, money }));  // 1.0.104 (Builder U, V07)
  }

  // ---- 1.0.104: portfolios (the drop form) and the Controls tab (reviewers and admins) ---------------------------------------
  function drawPortfolios() {
    const s = $("bportfolio"), v = s.value;
    clear(s).appendChild(el("option", { value: "" }, t("Portfolio: from the company codes")));
    (data.portfolios || []).forEach((p) => s.appendChild(el("option", { value: p.code }, `${p.code} · ${p.name}`)));
    s.value = v;
    s.classList.toggle("hide", !(data.portfolios || []).length);
    ["setportfolios", "setpeople", "settariffs"].forEach((id) => $(id).classList.toggle("hide", !data.is_admin));
    const views = ctlViews();
    $("ctltab").classList.toggle("hide", !views.length);
    if (!views.length && !$("tab-controls").classList.contains("hide")) tab("inbox");
  }
  function ctlViews() {
    if (!data || !(data.controls || {}).controls) return [];
    const held = Object.values(data.controls.roles || {}).flat();
    const order = CTL.ORDER || [];
    const rank = (v) => (order.includes(v.key) ? order.indexOf(v.key) : order.length);
    return Object.values(CTL).filter((v) => v && typeof v === "object" && v.key && typeof v.render === "function")
      .filter((v) => data.is_admin || !(v.roles || []).length || v.roles.some((r) => held.includes(r)))
      .sort((a, b) => rank(a) - rank(b) || String(a.title).localeCompare(String(b.title)));
  }
  function stopControls() {
    if (ctlDispose) { try { ctlDispose(); } catch (e) { /* the view's own clean-up */ } }
    ctlDispose = null;
  }
  // a link into Controls (Needs you's lines use it): goControls("review", { capture: 12 })
  function goControls(sub, params) { ctlSub = sub || ""; ctlParams = params || {}; tab("controls"); }
  const ctlKit = () => ({ api, act, el, clear, t, money, when, toast, me: data.me, isAdmin: data.is_admin, roles: (data.controls || {}).roles || {},
    portfolios: data.portfolios || [], params: ctlParams, go: goControls, openCapture, openItem, refresh: load });
  async function loadControls() {
    stopControls();
    const nav = clear($("ctlnav")), pane = clear($("ctlpane"));
    if (!data) return;  // load() opens it once the page has its data
    const views = ctlViews();
    if (!views.length) { pane.appendChild(el("div", { class: "small muted" }, t("Nothing here yet."))); return; }
    const cur = views.find((v) => v.key === ctlSub) || views[0];
    if (cur.key !== ctlSub) ctlParams = {};
    ctlSub = cur.key;
    views.forEach((v) => nav.appendChild(el("button", { type: "button", role: "tab", class: v.key === cur.key ? "on" : "", "aria-selected": v.key === cur.key ? "true" : "false",
      onclick: () => { ctlSub = v.key; ctlParams = {}; loadControls(); } }, v.title)));
    const q = new URLSearchParams({ tab: "controls", sub: cur.key, ...ctlParams });
    if (batchId) q.set("batch", batchId);
    try { history.replaceState(null, "", HERE + "?" + q); } catch (e) { /* not allowed here */ }
    try {
      const d = await cur.render(pane, ctlKit());
      if (typeof d === "function") ctlDispose = d;
    } catch (e) {
      pane.appendChild(el("div", { class: "small", style: { color: "var(--bad)" } }, t("This view could not open.")));
      console.error(e);  // eslint-disable-line no-console
    }
  }

  const LIST = {
    entities: { title: t("Entities"), sub: t("the company codes bills are addressed to"), cols: [["code", t("Code")], ["name", t("Company name")], ["entity", t("Entity")], ["vat_no", t("VAT no")], ["doc_type", t("Doc type")]],
      fields: [["code", t("Code")], ["name", t("Company name")], ["entity", t("Entity")], ["vat_no", t("VAT no")], ["doc_type", t("Doc type (blank = KR)")], ["doc_type_credit", t("Credit doc type (blank = KG)")], ["aliases", t("Other names (one per line)")]] },
    utilities: { title: t("Utilities accounts"), sub: t("account → vendor, PID/CC, site (the Harvester's vendor_mappings or Utilities Master List)"),
      cols: [["account", t("Account")], ["vendor", t("Vendor")], ["cost_centre", t("PID/CC")], ["site", t("Site")], ["municipality", t("Municipality")], ["company_code", t("Company")]] },
    vendors: { title: t("Vendors for manual invoices"), sub: t("vendor BP, GL, cost centre and tax code"), cols: [["vendor_no", t("Vendor")], ["name", t("Name")], ["vat_no", t("VAT no")], ["gl", t("GL")], ["cost_centre", t("Cost centre")], ["tax_code", t("Tax code")], ["company_code", t("Company")]],
      fields: [["vendor_no", t("Vendor (BP) no")], ["name", t("Name")], ["vat_no", t("VAT no")], ["gl", t("GL")], ["cost_centre", t("Cost centre")], ["tax_code", t("Tax code")], ["company_code", t("Company (blank = any)")],
        ["item_text", t("Line text (SGTXT)")], ["payment_days", t("Payment days")], ["aliases", t("Other names (one per line)")]] },
    // 1.0.102
    sites: { title: t("Sites"), sub: t('the site code after " - " in a bill-to → its cost centre'), cols: [["site_code", t("Site code")], ["company_code", t("Company")], ["cost_centre", t("Cost centre")], ["note", t("Note")]],
      fields: [["site_code", t("Site code")], ["company_code", t("Company (blank = any)")], ["cost_centre", t("Cost centre")], ["note", t("Note")]], noPull: true },
    sap_vendors: { title: t("Vendors (SAP general data)"), sub: t("SAP's vendor list (LFA1): Mia finds a supplier's vendor number by its VAT number"),
      cols: [["vendor", t("Vendor")], ["name", t("Name")], ["vat", t("VAT no")], ["city", t("City")], ["payment_terms", t("Terms of payment")], ["blocked", t("Blocked")]], noDelete: true },
    // 2.10.3: SAP's own lists
    gl_accounts: { title: t("G/L accounts"), sub: t("the chart of accounts: a G/L that is not in it is never posted"), cols: [["account", t("G/L account")], ["name", t("Name")], ["source", t("From")]] },
    vendor_companies: { title: t("Vendors per company code"), sub: t("which company codes each SAP vendor is opened in"), cols: [["vendor", t("Vendor")], ["companies", t("Company codes")]], noDelete: true },
    cost_centres: { title: t("Cost centres"), sub: t("SAP's cost centres per company code"), cols: [["cost_centre", t("Cost centre")], ["company_code", t("Company")], ["currency", t("Currency")],
      ["profit_centre", t("Profit centre")], ["description", t("Description")]], noDelete: true },
  };
  const SOURCE = { seed: t("SAP export (shipped)"), upload: t("Upload"), excel: t("Upload"), sap: t("Pull from SAP") };
  const COLNAME = { code: t("Code"), name: t("Name"), entity: t("Entity"), vat_no: t("VAT no"), aliases: t("Other names"), doc_type: t("Doc type"), doc_type_credit: t("Credit doc type"),
    vendor: t("Vendor"), cost_centre: t("Cost centre"), site: t("Site"), name3: "NAME3", account: t("Account"), municipality: t("Municipality"), company_code: t("Company"), vendor_no: t("Vendor"), gl: t("GL"), tax_code: t("Tax code"),
    // 1.0.102
    site_code: t("Site code"), note: t("Note"), payment_days: t("Payment days"), line_rules: t("Line rules"), name2: "NAME2", vat: t("VAT no"), tax1: t("Tax number 1"), tax2: t("Tax number 2"),
    city: t("City"), country: t("Country"), blocked: t("Blocked"), deleted: t("Deleted"), payment_terms: t("Terms of payment") };

  function listPane(kind) {
    const L = LIST[kind], A = masters.is_admin;
    const n = masters.counts[kind];
    const q = el("input", { class: "in", placeholder: kind === "vendor_companies" ? t("Search by vendor number") : ["gl_accounts", "cost_centres"].includes(kind) ? t("Search by number or words") : kind === "sap_vendors" ? t("Search by number, VAT number or name") : t("Search"), style: { flex: "1 1 220px" } });
    const tbl = el("table", { class: "tbl" });
    const out = el("div", { class: "stack", style: { gap: "8px" } });
    const draw = async () => {
      const r = await api(`/api/ap/masters/${kind}?q=${encodeURIComponent(q.value.trim())}&limit=100`);
      clear(tbl).appendChild(el("thead", null, el("tr", null, L.cols.map(([, l]) => el("th", null, l)), kind === "vendors" ? el("th", null, t("Learned")) : null, A ? el("th", null, "") : null)));
      const body = el("tbody");
      if (!r.rows.length) body.appendChild(el("tr", null, el("td", { colspan: L.cols.length + 2, class: "small muted" }, t("Nothing here."))));
      r.rows.forEach((x) => body.appendChild(el("tr", null, L.cols.map(([k]) => el("td", { class: ["code", "account", "vendor", "vendor_no", "gl", "vat_no", "cost_centre", "companies", "company_code", "profit_centre", "site_code", "vat", "payment_terms"].includes(k) ? "mono small" : "small", style: k === "blocked" && x[k] ? { color: "var(--bad)" } : null },
        Array.isArray(x[k]) ? x[k].join(" ") : k === "source" && kind === "gl_accounts" ? (SOURCE[x[k]] || x[k] || "") : k === "blocked" ? (x[k] ? t("Blocked") : "") : (x[k] || ""))),
        kind === "vendors" ? el("td", { class: "small" }, x.learned ? t("{name}, {when}", { name: x.learned_by, when: when(x.learned_at) }) : "") : null,
        A ? el("td", { style: { whiteSpace: "nowrap" } }, (kind === "vendors" && x.learned) || (kind === "utilities" && x.source === "person")  // 2.10.0: what a person added is confirmed once by an admin
          ? el("button", { class: "link small", type: "button", onclick: async () => { await act(() => api(`/api/ap/masters/${kind}/${x.id}/confirm`, { method: "POST" }), t("Confirmed. Tess tests it again.")); loadMasters(); } }, t("Confirm")) : null, " ",
          L.fields ? el("button", { class: "link small", type: "button", onclick: () => editRow(kind, x) }, t("Edit")) : null, " ",
          L.noDelete ? null : el("button", { class: "link small", type: "button", onclick: async () => { if (!confirm(t("Delete this row?"))) return; await act(() => api(`/api/ap/masters/${kind}/${x.id}`, { method: "DELETE" }), t("Deleted.")); loadMasters(); } }, t("Delete"))) : null)));
      tbl.appendChild(body);
      cnt.textContent = kind === "vendor_companies" ? t("{n} pairs · {v} vendors · {c} company codes", { n: masters.counts.vendor_companies, v: masters.counts.vendor_companies_vendors, c: masters.counts.vendor_companies_codes })
        : r.total === r.rows.length ? (r.total === 1 ? t("{n} row", { n: r.total }) : t("{n} rows", { n: r.total })) : t("{n} of {total} rows shown", { n: r.rows.length, total: r.total });
    };
    const cnt = el("span", { class: "k" }, "");
    q.addEventListener("input", () => { clearTimeout(window.__apm); window.__apm = setTimeout(draw, 250); });
    out.appendChild(el("div", { class: "sec-h" }, el("h2", { class: "h2" }, L.title), el("span", { class: "k" }, L.sub)));
    out.appendChild(el("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" } }, q, cnt,
      kind === "vendors" && masters.counts.learned ? el("span", { class: "small muted" }, t("{n} learned from people", { n: masters.counts.learned })) : null,
      kind === "sap_vendors" && masters.counts.sap_vendors_blocked ? el("span", { class: "small muted" }, t("{n} blocked in SAP", { n: masters.counts.sap_vendors_blocked })) : null,
      el("a", { class: "link small", href: `${B}/api/ap/masters/${kind}/file` }, t("Download Excel"))));
    if (A) out.appendChild(adminTools(kind, L));
    out.appendChild(el("div", { style: { overflowX: "auto" } }, tbl));
    // 2.10.3: the G/Ls in use that the chart doesn't have (Tess marks their documents yellow: check with finance)
    if (kind === "gl_accounts" && (masters.gl_missing || []).length) out.appendChild(el("div", { class: "small", style: { color: "var(--warn)" } },
      t("Codes the GL Codes or vendor codings use that the chart doesn't have:"), " ", el("span", { class: "mono" }, masters.gl_missing.join(", "))));
    if (!n && !A) out.appendChild(el("div", { class: "small muted" }, t("An admin uploads this list.")));
    draw();
    return el("section", { class: "sec" }, out);
  }

  // 1.0.103: SAP's posting history — the vendor line items (FBL1N) and G/L line items (FBL3N): the duplicate check and how SAP coded each vendor
  function historyPane() {
    const A = masters.is_admin, h = masters.history || {};
    const out = el("div", { class: "stack", style: { gap: "10px" } });
    const range = h.from ? `${h.from} – ${h.to}` : "";
    out.append(el("div", { class: "sec-h" }, el("h2", { class: "h2" }, t("Posting history")), el("span", { class: "k" }, t("what SAP already posted: the duplicate check, and how SAP coded each vendor"))),
      el("div", { class: "small" }, h.documents ? t("{d} documents ({l} vendor lines), {range}, {c} company codes · {v} vendors with coding learned", { d: h.documents, l: h.vendor_lines, range, c: h.companies, v: h.vendors })
        : t("No posting history yet.")),
      h.at ? el("div", { class: "small muted" }, (SOURCE[h.source] || h.source || "") + (h.by ? ` · ${h.by}` : "") + ` · ${when(h.at)}`) : "");
    const q = el("input", { class: "in", placeholder: t("Search by vendor, reference or document number"), style: { flex: "1 1 220px" } });
    const tbl = el("table", { class: "tbl" });
    const draw = async () => {
      let r; try { r = await api(`/api/ap/history?q=${encodeURIComponent(q.value.trim())}&limit=50`); } catch (e) { return; }
      clear(tbl).appendChild(el("thead", null, el("tr", null, [t("Company"), t("Vendor"), t("Reference"), t("Doc type"), t("Document"), t("Posted"), t("Amount"), t("Tax code"), t("Text")].map((x) => el("th", null, x)))));
      const body = el("tbody");
      if (!r.rows.length) body.appendChild(el("tr", null, el("td", { colspan: 9, class: "small muted" }, t("Nothing here."))));
      r.rows.forEach((x) => body.appendChild(el("tr", null, el("td", { class: "mono small" }, x.company_code), el("td", { class: "mono small" }, x.vendor), el("td", { class: "mono small" }, x.reference),
        el("td", { class: "small" }, x.doc_type), el("td", { class: "mono small" }, x.doc_number), el("td", { class: "small", style: { whiteSpace: "nowrap" } }, x.posting_date),
        el("td", { class: "small", style: { textAlign: "end", whiteSpace: "nowrap" } }, money(x.amount)), el("td", { class: "small" }, x.tax_code), el("td", { class: "small" }, x.text))));
      tbl.appendChild(body);
    };
    q.addEventListener("input", () => { clearTimeout(window.__aph); window.__aph = setTimeout(draw, 250); });
    out.appendChild(el("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" } }, q));
    if (A) {
      const vf = el("input", { type: "file", accept: ".xlsx,.xlsm,.csv" }), gf = el("input", { type: "file", accept: ".xlsx,.xlsm,.csv" });
      const msg = el("div", { class: "small" });
      const send = async (confirmIt) => {
        const fd = new FormData();
        if (vf.files[0]) fd.append("vendor_items", vf.files[0], vf.files[0].name);
        if (gf.files[0]) fd.append("gl_items", gf.files[0], gf.files[0].name);
        fd.append("confirm", confirmIt ? "true" : "false");
        return act(() => api("/api/ap/history/import", { form: fd }));
      };
      const show = (r) => {
        if (!r) return;
        clear(msg).append(t("{d} documents: {a} new, {r} replaced · {w} with their G/L lines", { d: r.documents, a: r.added, r: r.replaced, w: r.with_lines }),
          r.from ? ` · ${r.from} – ${r.to}` : "", r.lines_without_document ? " · " + t("{n} G/L documents with no vendor document", { n: r.lines_without_document }) : "", " ");
        if (r.saved) { msg.appendChild(el("span", { class: "good" }, t("Saved."))); vf.value = ""; gf.value = ""; loadMasters(); return; }
        msg.append(el("button", { class: "btn sm", type: "button", onclick: async () => show(await send(true)) }, t("Save these changes")), " ", el("button", { class: "link small", type: "button", onclick: () => clear(msg) }, t("Cancel")));
      };
      out.append(el("div", { class: "small muted" }, t("Upload SAP's vendor line items (FBL1N), its G/L line items (FBL3N), or both: documents are added or replaced by company code and document number.")),
        el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: "10px" } },
          el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, t("Vendor line items (FBL1N)")), vf),
          el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, t("G/L line items (FBL3N)")), gf)),
        el("div", null, el("button", { class: "btn sm", type: "button", onclick: async () => { if (!vf.files[0] && !gf.files[0]) return; show(await send(false)); } }, t("Show what would change"))), msg);
    }
    out.appendChild(el("div", { style: { overflowX: "auto" } }, tbl));
    draw();
    return el("section", { class: "sec" }, out);
  }

  function adminTools(kind, L) {
    const box = el("div", { class: "stack", style: { gap: "10px" } });
    const msg = el("div", { class: "small" });
    const file = el("input", { type: "file", accept: ".xlsx,.xlsm,.csv", hidden: true });
    const result = (r, again) => {
      clear(msg).append(t("{a} added, {c} changed, {r} removed", { a: r.added, c: r.changed, r: r.removed }),
        r.kept_learned ? " · " + t("{n} learned rows kept", { n: r.kept_learned }) : "", r.skipped ? " · " + t("{n} rows without the key left out", { n: r.skipped }) : "", " ");
      if ((r.removed_in_use || []).length) msg.appendChild(el("div", { class: "small", style: { color: "var(--warn)" } }, t("Still used by a vendor coding or the GL Codes: {list}", { list: r.removed_in_use.join(", ") })));
      // 1.0.102: which of the sheet's columns went where; sites whose cost centre SAP doesn't have in that company code
      if (r.mapping && !r.saved) msg.appendChild(el("div", { class: "small muted" }, t("Columns read:"), " ", Object.entries(r.mapping).map(([f, h]) => `${h} ${ARROW} ${COLNAME[f] || f}`).join(" · ")));
      if (r.kind === "sap_vendors" && !r.saved) msg.appendChild(el("div", { class: "small muted" }, t("{v} with a VAT number · {b} blocked in SAP", { v: r.with_vat || 0, b: r.blocked || 0 })));
      if ((r.cc_unknown || []).length) msg.appendChild(el("div", { class: "small", style: { color: "var(--warn)" } }, t("Not in SAP's cost centres for that company: {list}", { list: r.cc_unknown.join(", ") })));
      if (r.saved) { msg.appendChild(el("span", { class: "good" }, t("Saved."))); loadMasters(); return; }
      if ((r.changes || []).length) msg.appendChild(el("ul", { class: "small muted", style: { margin: "6px 0", paddingInlineStart: "18px" } }, r.changes.map((ch) =>  // 2.10.0: what changes, field by field
        el("li", null, el("span", { class: "mono" }, ch.row), ": ", ch.fields.map((f) => `${COLNAME[f.field] || f.field} "${f.from}" ${ARROW} "${f.to}"`).join("; ")))));
      msg.append(el("button", { class: "btn sm primary", type: "button", onclick: again }, t("Save these changes")), " ", el("button", { class: "link small", type: "button", onclick: () => clear(msg) }, t("Cancel")));
    };
    file.addEventListener("change", async () => {
      const f = file.files[0]; if (!f) return;
      const send = async (confirmIt) => { const fd = new FormData(); fd.append("file", f, f.name); fd.append("confirm", confirmIt ? "true" : "false"); return act(() => api(`/api/ap/masters/${kind}/import`, { form: fd })); };
      result(await send(false), async () => { result(await send(true)); file.value = ""; });
    });
    const row = el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" } },
      el("button", { class: "btn sm primary", type: "button", onclick: () => file.click() }, t("Upload Excel")), file,
      L.noPull ? null : el("button", { class: "btn sm", type: "button", onclick: () => pull.classList.toggle("hide") }, t("Pull from SAP")),
      L.fields ? el("button", { class: "btn sm", type: "button", onclick: () => editRow(kind, null) }, t("Add")) : null);
    const pull = el("div", { class: "stack hide", style: { gap: "8px", padding: "12px", border: "1px solid var(--rule)" } });
    if (!masters.connections || !masters.connections.length) pull.appendChild(el("div", { class: "small" }, t("No connection is ticked for the AP Tool yet. An admin adds the SAP connection in Admin → Connections and ticks the AP Tool; it then shows here.")));
    else {
      const prev = (masters.pull || {})[kind] || {};
      const conn = el("select", { class: "in", style: { width: "auto" } }, masters.connections.map((c) => el("option", { value: c.id, selected: c.id === prev.connection_id }, c.name)));
      const path = el("input", { class: "in", value: prev.path || "", placeholder: t("Path under the connection, e.g. /A_Supplier?$top=5000") });
      const cols = {};
      const grid = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(170px,1fr))", gap: "8px" } });
      const dflt = kind === "sap_vendors" ? { vendor: "Supplier", name: "SupplierName", vat: "VATRegistration", blocked: "PostingIsBlocked", deleted: "DeletionIndicator" } : {};  // 1.0.102: OData A_Supplier's names
      masters.columns[kind].forEach((k) => { cols[k] = el("input", { class: "in", value: (prev.columns || dflt)[k] || "", placeholder: t("SAP column") }); grid.appendChild(el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, COLNAME[k] || k), cols[k])); });
      const go = async (confirmIt, previewId) => act(() => api(`/api/ap/masters/${kind}/pull`, { body: { connection_id: Number(conn.value), path: path.value, columns: Object.fromEntries(Object.entries(cols).map(([k, i]) => [k, i.value])), confirm: confirmIt, preview_id: previewId || "" } }));
      pull.append(el("div", { class: "small muted" }, t("Pick the connection, the path of the list, and which SAP column holds each value.")), conn, path, grid,
        el("div", null, el("button", { class: "btn sm primary", type: "button", onclick: async () => { const r = await go(false); result(r, async () => result(await go(true, r.preview_id))); } }, t("Show what would change"))));  // 2.10.0: Save stores what was shown
    }
    box.append(row, pull, msg);
    return box;
  }

  function editRow(kind, x) {
    const L = LIST[kind], dlg = $("dlg");
    $("dlgt").textContent = x ? t("Edit") : t("Add");
    clear($("dlgsub")).append(L.title);
    const body = clear($("dlgbody")), inputs = {};
    const grid = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(200px,1fr))", gap: "10px" } });
    L.fields.forEach(([k, l]) => {
      let box;
      if (k === "gl") { const g = glField((x && x[k]) || ""); inputs[k] = g.input; box = g.wrap; }  // 2.10.3: from the chart, with its name
      else { inputs[k] = k === "aliases" ? el("textarea", { class: "in", rows: 3 }, (x && x[k]) || "") : el("input", { class: "in", value: (x && x[k]) || "", placeholder: k === "item_text" ? "{site} {description}" : "" }); box = inputs[k]; }
      grid.appendChild(el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, l), box));
    });
    const rf = kind === "vendors" ? rulesField(x && x.line_rules) : null;  // 1.0.102: the vendor's line rules, under its G/L
    // 2.10.3: say which document types SAP lets a vendor line post with (YR / YG are customer types in SAP: account types D, S)
    const vend = kind === "entities" ? (masters.doc_types || []).filter((d) => d.vendor_lines).map((d) => d.code) : [];
    body.append(grid, rf ? el("div", { class: "stack", style: { gap: "6px" } }, el("div", { class: "k" }, t("Line rules")), rf.wrap) : "",
      vend.length ? el("div", { class: "small muted" }, t("Document types that allow vendor lines in SAP: {codes}", { codes: vend.join(", ") })) : "", el("div", null, el("button", { class: "btn sm primary", type: "button", onclick: async () => {
      const out = Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value]));
      if (rf) out.line_rules = rf.read();
      const r = await act(() => api(`/api/ap/masters/${kind}` + (x ? "/" + x.id : ""), { method: x ? "PUT" : "POST", body: out }), t("Saved."));
      glWarn(r);
      dlg.close(); loadMasters();
    } }, t("Save"))));
    if (!dlg.open) dlg.showModal();
  }

  function sapPane() {
    const A = masters.is_admin, v = masters.sap;
    const mk = (rows, withDesc) => {
      const tb = el("tbody");
      const add = (r) => tb.appendChild(el("tr", null, el("td", null, el("input", { class: "in mono", value: r.key || "", disabled: !A })), el("td", null, el("input", { class: "in mono", value: r.value || "", disabled: !A })),
        withDesc ? el("td", null, el("input", { class: "in", value: r.description || "", disabled: !A })) : null));
      rows.forEach(add);
      return { tb, add, read: () => Array.from(tb.rows).map((tr) => { const i = tr.querySelectorAll("input"); return { key: i[0].value, value: i[1].value, description: i[2] ? i[2].value : "" }; }) };
    };
    const fx = mk(v.fixed, false), gl = mk(v.gl, true);
    const msg = el("div", { class: "small" });
    const file = el("input", { type: "file", accept: ".xlsx", hidden: true });
    file.addEventListener("change", async () => {
      const f = file.files[0]; if (!f) return;
      const send = async (c) => { const fd = new FormData(); fd.append("file", f, f.name); fd.append("confirm", c ? "true" : "false"); return act(() => api("/api/ap/sap-values/import", { form: fd })); };
      const r = await send(false);
      clear(msg).append(t("{a} added, {c} changed, {r} removed", { a: r.added, c: r.changed, r: r.removed }), " ",
        el("button", { class: "btn sm primary", type: "button", onclick: async () => { await send(true); file.value = ""; toast(t("Saved.")); loadMasters(); } }, t("Save these changes")));
    });
    return el("section", { class: "sec" },
      el("div", { class: "sec-h" }, el("h2", { class: "h2" }, t("SAP values")), el("span", { class: "k" }, t("the Harvester's sap_config.xlsx: Fixed and GL Codes"))),
      el("div", { class: "small muted" }, t("A GL left blank makes every document that needs it red; nothing is guessed.")),
      el("div", { class: "grid2", style: { gap: "22px" } },
        el("div", { class: "stack", style: { gap: "6px" } }, el("div", { class: "k" }, t("Fixed")), el("table", { class: "tbl" }, el("thead", null, el("tr", null, el("th", null, t("Key")), el("th", null, t("Value")))), fx.tb),
          A ? el("div", null, el("button", { class: "link small", type: "button", onclick: () => fx.add({}) }, t("Add a row"))) : null),
        el("div", { class: "stack", style: { gap: "6px" } }, el("div", { class: "k" }, t("GL Codes")), el("table", { class: "tbl" }, el("thead", null, el("tr", null, el("th", null, t("Charge")), el("th", null, t("GL")), el("th", null, t("Description")))), gl.tb),
          A ? el("div", null, el("button", { class: "link small", type: "button", onclick: () => gl.add({}) }, t("Add a row"))) : null)),
      el("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" } },
        A ? el("button", { class: "btn sm primary", type: "button", onclick: async () => { await act(() => api("/api/ap/sap-values", { method: "PUT", body: { fixed: fx.read(), gl: gl.read() } }), t("Saved.")); loadMasters(); } }, t("Save")) : null,
        el("a", { class: "link small", href: `${B}/api/ap/sap-values/file` }, t("Download sap_config.xlsx")),
        A ? el("button", { class: "link small", type: "button", onclick: () => file.click() }, t("Upload sap_config.xlsx")) : null, file),
      msg);
  }

  function docTypePane() {
    const A = masters.is_admin;
    const tb = el("tbody");
    const MEAN = { invoice: t("Invoice"), credit: t("Credit memo"), payment: t("Payment"), clearing: t("Clearing"), journal: t("Journal"), migration: t("Migration"), other: t("Other") };
    const q = el("input", { class: "in", placeholder: t("Search"), style: { flex: "1 1 200px" } });
    const row = (d) => {
      const code = el("input", { class: "in mono", value: d.code || "", disabled: !A || !!d.description, style: { maxWidth: "80px" } }), label = el("input", { class: "in", value: d.label || "", disabled: !A });
      const mean = el("select", { class: "in", disabled: !A }, masters.meanings.map((m) => el("option", { value: m, selected: m === (d.meaning || "invoice") }, MEAN[m] || m)));
      const note = el("input", { class: "in", value: d.note || "", disabled: !A });
      const sap = d.description || d.account_types ? el("div", null, el("div", { class: "small" }, d.description), el("div", { class: "small muted mono", title: t("A assets · D customers · K vendors · M material · S G/L accounts") },
        d.account_types ? d.account_types.split("").join(" ") : "", d.reverse ? " · " + t("reverse {code}", { code: d.reverse }) : "")) : el("span", { class: "small muted" }, d.id ? t("Not in SAP's list") : "");
      // a row whose meaning and SAP's description disagree says Check (yellow) until an admin confirms it
      const check = d.needs_check ? el("div", { class: "stack", style: { gap: "4px" } }, el("span", { class: "pill warn" }, t("Check")), el("div", { class: "small", style: { color: "var(--warn)" } }, d.check_note),
        A ? el("button", { class: "link small", type: "button", onclick: async () => { await act(() => api(`/api/ap/doc-types/${d.id}/confirm`, { method: "POST" }), t("Confirmed.")); loadMasters(); } }, t("Confirm CBX's meaning")) : null)
        : d.checked_by ? el("span", { class: "small muted" }, t("Confirmed by {name}", { name: d.checked_by })) : "";
      const tr = el("tr", { "data-q": [d.code, d.label, d.description, d.note].join(" ").toLowerCase() }, el("td", null, code), el("td", null, sap), el("td", null, label), el("td", null, mean), el("td", null, note), el("td", null, check),
        A ? el("td", { style: { whiteSpace: "nowrap" } }, el("button", { class: "link small", type: "button", onclick: async () => { await act(() => api("/api/ap/doc-types" + (d.id ? "/" + d.id : ""), { method: d.id ? "PUT" : "POST", body: { code: code.value, label: label.value, meaning: mean.value, note: note.value } }), t("Saved.")); loadMasters(); } }, t("Save")),
          d.id && d.source !== "sap" ? " " : null, d.id && d.source !== "sap" ? el("button", { class: "link small", type: "button", onclick: async () => { if (!confirm(t("Delete this row?"))) return; await act(() => api(`/api/ap/masters/doc_types/${d.id}`, { method: "DELETE" }), t("Deleted.")); loadMasters(); } }, t("Delete")) : null) : null);
      tb.appendChild(tr);
    };
    masters.doc_types.forEach(row);
    q.addEventListener("input", () => { const v = q.value.trim().toLowerCase(); Array.from(tb.rows).forEach((tr) => { tr.classList.toggle("hide", !!v && !(tr.dataset.q || "").includes(v)); }); });
    const msg = el("div", { class: "small" });
    const file = el("input", { type: "file", accept: ".xlsx,.xlsm,.csv", hidden: true });
    file.addEventListener("change", async () => {
      const f = file.files[0]; if (!f) return;
      const send = async (c) => { const fd = new FormData(); fd.append("file", f, f.name); fd.append("confirm", c ? "true" : "false"); return act(() => api("/api/ap/doc-types/import", { form: fd })); };
      const r = await send(false);
      clear(msg).append(t("{a} added, {c} changed, {r} removed", { a: r.added, c: r.changed, r: r.removed }), r.kept ? " · " + t("{n} of CBX's rows not in SAP's list: kept, marked Check", { n: r.kept }) : "", " ",
        el("button", { class: "btn sm primary", type: "button", onclick: async () => { await send(true); file.value = ""; toast(t("Saved.")); loadMasters(); } }, t("Save these changes")), " ",
        el("button", { class: "link small", type: "button", onclick: () => { clear(msg); file.value = ""; } }, t("Cancel")));
    });
    const nCheck = masters.doc_types.filter((d) => d.needs_check).length;
    return el("section", { class: "sec" }, el("div", { class: "sec-h" }, el("h2", { class: "h2" }, t("Document types")), el("span", { class: "k" }, t("invoices post as KR, credit notes as KG"))),
      el("div", { class: "small muted" }, t("SAP's Document Type List beside what each type means for AP. Rae uses it to tell invoices, credits, payments and journals apart; Tess refuses a type that doesn't allow the lines a document has (vendor lines need K, G/L lines need S).")),
      nCheck ? el("div", { class: "small", style: { color: "var(--warn)" } }, t("{n} rows say Check: CBX's meaning and SAP's description disagree.", { n: nCheck })) : null,
      el("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap", alignItems: "center" } }, q,
        A ? el("button", { class: "btn sm primary", type: "button", onclick: () => file.click() }, t("Upload the Document Type List")) : null, file),
      msg,
      el("div", { style: { overflowX: "auto" } }, el("table", { class: "tbl" }, el("thead", null, el("tr", null, el("th", null, t("Code")), el("th", null, t("SAP says")), el("th", null, t("Label")), el("th", null, t("What it is")), el("th", null, t("Note")), el("th", null, ""), A ? el("th", null, "") : null)), tb)),
      A ? el("div", null, el("button", { class: "link small", type: "button", onclick: () => row({}) }, t("Add a row"))) : null);
  }

  function modelPane() {
    const A = masters.is_admin, s = masters.capture, d = masters.models;
    const f = {};
    const field = (k, label, ph, type) => { f[k] = el("input", { class: "in", value: s[k] === undefined || s[k] === null ? "" : String(s[k]), placeholder: ph || "", type: type || "text", disabled: !A }); return el("label", { class: "stack", style: { gap: "4px" } }, el("span", { class: "k" }, label), f[k]); };
    return el("section", { class: "sec", style: { maxWidth: "720px" } },
      el("div", { class: "sec-h" }, el("h2", { class: "h2" }, t("Models")), el("span", { class: "k" }, t("the capture bots and Seth"))),
      el("div", { class: "small muted" }, t("Leave a model blank to use the default. Both need to read images (vision).")),
      el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(220px,1fr))", gap: "10px" } },
        field("capture_model", t("Capture model (Cara, Dex, Eli)"), d.capture), field("senior_model", t("Senior model (Seth)"), d.senior),
        field("min_confidence", t("Sure enough: lowest % on a field"), "", "number"), field("dpi", t("Capture: image dpi"), "", "number"), field("pages", t("Capture: pages read"), "", "number"),
        field("senior_dpi", t("Seth: image dpi"), "", "number"), field("senior_pages", t("Seth: pages read"), "", "number")),
      A ? el("div", null, el("button", { class: "btn sm primary", type: "button", onclick: async () => { await act(() => api("/api/ap/capture-settings", { method: "PUT", body: Object.fromEntries(Object.entries(f).map(([k, i]) => [k, i.value])) }), t("Saved.")); loadMasters(); } }, t("Save"))) : null);
  }

  tab(TABS.includes(ctx.params.tab) ? ctx.params.tab : "inbox");
  await load();
  if (!$("tab-controls").classList.contains("hide")) loadControls();  // 1.0.104: a link straight into Controls opens once the page has its data
  return () => { clearTimeout(timer); clearTimeout(liveTimer); clearTimeout(needsTimer); clearTimeout(busyTimer); stopControls(); };  // 1.0.98: the page was left: no more polling (1.0.99: Needs you's too)
  },
};
