// Site Schedules (1.0.89): the same page as CBX-Connect 2 — agreements on the left, the agreement with its Word
// page beside the form, download it; the template (admins) and the people who sign. Every read, change, Word
// file and download is kept on the agreement's history. Ported from CBX-Connect 2 (static/js/ss_page.js and
// schedules.js); the server side is cbx/api/schedules_api.py over cbx/schedules/service.py.
import { api as API } from '../api.js';
import { toast as TOAST } from '../ui.js';

// CBX-Connect 2's little DOM helper: el(tag, attrs, ...children) — elements only, never HTML strings
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
const toast = (m, kind) => TOAST(m, kind === 'err' || typeof kind === 'number' ? 'err' : (kind || ''));
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.detail || e.message, 'err'); throw e; } }
function api(path, o) {
  o = o || {};
  if (o.form) return API.post(path, o.form);
  if (o.body !== undefined) return o.method === 'PUT' ? API.put(path, o.body) : API.post(path, o.body);
  if (o.method === 'POST') return API.post(path, {});
  if (o.method === 'DELETE') return API.del(path);
  return API.get(path);
}
const B = '';

// ---- the agreement as a Word page (cbx/schedules/template.py, in the browser) ---------------------------
const P = (() => {
  const TOKEN = /\{([a-z0-9_]+)(?:\|([^}]*))?\}/g;
  const IND = [0, 28.4, 48, 72]; // px for 0 / 426 / 720 / 1080 twips
  const COLS = ["Leased Item", "Requirement", "Comment/Equipment Description", "Amount (excl VAT)"];
  const DOTS = "…………………………………";

  function num(s) {
    s = String(s == null ? "" : s).trim().replace(/^R/, "").replace(/\s+/g, "");
    if (/,\d{1,3}$/.test(s) && !/\.\d/.test(s)) s = s.replace(/\./g, "").replace(",", "."); else s = s.replace(/,/g, "");
    const v = parseFloat(s.replace(/[^\d.-]/g, ""));
    return isNaN(v) ? 0 : v;
  }
  function zar(v) {
    const [w, d] = Math.abs(v).toFixed(2).split(".");
    return "R " + w.replace(/\B(?=(\d{3})+(?!\d))/g, " ") + "." + d;
  }

  // text with {tokens}, **bold**, __underline__ → spans; opts.mark wraps filled tokens so the editor can show them
  function textNodes(text, vals, opts) {
    const out = [];
    let bold = false, under = false;
    const pieces = [];
    let last = 0;
    String(text || "").replace(TOKEN, (m, key, fb, at) => {
      if (at > last) pieces.push({ t: String(text).slice(last, at) });
      const v = vals[key];
      pieces.push({ t: v ? String(v) : (fb || ""), tok: key, empty: !v && !fb });
      last = at + m.length;
      return m;
    });
    if (last < String(text || "").length) pieces.push({ t: String(text).slice(last) });
    pieces.forEach((p) => {
      if (p.tok) {
        const s = el("span", { class: "tk" + (p.empty ? " empty" : ""), title: "{" + p.tok + "}", style: { fontWeight: bold ? "700" : null, textDecoration: under ? "underline" : null } },
          p.empty && opts && opts.mark ? "{" + p.tok + "}" : p.t);
        if (!(opts && opts.mark)) s.className = "";
        out.push(s);
        return;
      }
      p.t.split(/(\*\*|__)/).forEach((x) => {
        if (x === "**") bold = !bold;
        else if (x === "__") under = !under;
        else if (x) out.push(el("span", { style: { fontWeight: bold ? "700" : null, textDecoration: under ? "underline" : null } }, x));
      });
    });
    return out;
  }

  function shown(b, vals, tables) {
    if (!b.if) return true;
    if (["t1", "t2", "t3", "t4", "extra"].includes(b.if)) return !!(tables[b.if] && tables[b.if].length);
    return !!vals[b.if];
  }

  function para(b, vals, opts, heading) {
    const ind = IND[Math.max(0, Math.min(3, Number(b.indent || 0)))];
    const number = b.number ? String(b.number).replace(TOKEN, (m, k, fb) => vals[k] || fb || "") : "";
    const size = b.size ? b.size * 1.333 : null;
    const box = el("div", { class: "dp" + (heading ? " dh" : ""), style: {
      paddingLeft: (ind + (number ? 48 : 0)) + "px", textAlign: { center: "center", justify: "justify", right: "right" }[b.align] || "left",
      fontWeight: (b.bold || heading) ? "700" : null, fontSize: size ? size + "px" : null,
      marginTop: (b.space_before != null ? b.space_before : (heading ? 12 : 0)) * 1.333 + "px",
      marginBottom: (b.space_after != null ? b.space_after : 6) * 1.333 + "px" } });
    if (number) box.appendChild(el("span", { class: "dn", style: { left: ind + "px" } }, number));
    const main = el("span", { class: b.right ? "dl" : null }, textNodes(b.text, vals, opts));
    box.appendChild(main);
    if (b.right) box.appendChild(el("span", { class: "dr" }, textNodes(b.right, vals, opts)));
    if (!(b.text || "").trim() && !b.right && !number) box.appendChild(el("span", null, " "));
    return box;
  }

  function table(cols, rows, totalLabel, total) {
    const w = cols.length === 4 ? [18, 18, 42, 22] : cols.map(() => 100 / cols.length);
    const t = el("table", { class: "dt" });
    t.appendChild(el("colgroup", null, w.map((x) => el("col", { style: { width: x + "%" } }))));
    t.appendChild(el("tr", null, cols.map((c) => el("th", null, c))));
    const body = rows.length ? rows : [cols.map((_, i) => i === cols.length - 1 ? "R 0.00" : "N/A")];
    body.forEach((r) => t.appendChild(el("tr", null, r.map((v, i) => el("td", null, v === "" || v == null ? (i === cols.length - 1 ? "" : "N/A") : String(v))))));
    t.appendChild(el("tr", { class: "tot" }, el("td", { colspan: String(cols.length - 1) }, totalLabel), el("td", null, total)));
    return el("div", { class: "dtw" }, t);
  }

  function render(tpl, data, opts) {
    opts = opts || {};
    const vals = data.values || {};
    const tables = data.tables || {};
    const signing = data.signing || {};
    const people = data.people || {};
    const page = el("div", { class: "docpage", style: { fontFamily: (tpl.font || "Arial") + ", Helvetica, sans-serif", fontSize: (tpl.size || 11) * 1.333 + "px" } });
    const nodes = [];
    (tpl.blocks || []).forEach((b, i) => {
      const visible = shown(b, vals, tables);
      let n;
      if (!visible && !opts.editor) return;
      if (b.type === "para" || b.type === "heading") n = para(b, vals, opts, b.type === "heading");
      else if (b.type === "pagebreak") n = el("div", { class: "dbreak" }, el("span", null, "Page break"));
      else if (b.type === "table") {
        if (b.table === "extra") {
          const xs = tables.extra || [];
          n = el("div", null, xs.length ? xs.map((x) => [x.title ? para({ text: "**" + x.title + "**", indent: 1 }, vals, opts) : null,
            table(x.cols || COLS, x.rows || [], b.total || "Sub-Total", zar((x.rows || []).reduce((s, r) => s + num(r[r.length - 1]), 0)))])
            : (opts.editor ? el("div", { class: "dnote" }, "The extra tables go here (when an agreement has any).") : null));
        } else {
          const rows = (tables[b.table] || []).map((r) => [r.item, r.req, r.desc, r.amt]);
          n = table(COLS, rows, String(b.total || "").replace(TOKEN, (m, k, fb) => vals[k] || fb || ""), vals[b.table + "_total"] || "R 0.00");
        }
      } else if (b.type === "witnesses" || b.type === "signatories") {
        // as the real agreement (Edwin, 12 Oct): witnesses on the left, the people who sign on the right
        const nb = (tpl.blocks || [])[i + 1] || {};
        const prev = (tpl.blocks || [])[i - 1] || {};
        if (b.type === "signatories" && prev.type === "witnesses" && prev.side === b.side && shown(prev, vals, tables)) return;
        const pair = b.type === "witnesses" && nb.type === "signatories" && nb.side === b.side && shown(nb, vals, tables);
        const wit = () => {
          const w = ((signing[b.side] || {}).witnesses || []).filter((x) => String(x).trim());
          return el("div", { class: "dwl" }, (w.concat(["", ""])).slice(0, Math.max(2, w.length)).map((x) => el("div", { class: "dwi" }, x ? el("u", null, x) : DOTS)));
        };
        const signers = (blk) => {
          // 1.0.107: Swiftnet's signatories are the template's fixed list (an admin setting); the co-user's are picked
          let ppl = blk.side === "swift" && tpl.swift_sign ? (tpl.swift_sign.people || []) : ((signing[blk.side] || {}).people || []);
          if (!ppl.length) ppl = [{ name: "", title: "" }];
          return el("div", { class: "dsr" }, ppl.map((p) => {
            const known = [].concat(people.swift || [], people.couser || []).find((x) => (x.name || "").trim().toLowerCase() === (p.name || "").trim().toLowerCase() && x.sig);
            return el("div", { class: "ds" }, known ? el("img", { src: known.sig, alt: "signature", class: "dsig" }) : el("div", null, DOTS),
              p.name ? el("div", { style: { fontWeight: "700" } }, p.name) : null, p.title ? el("div", { style: { fontWeight: "700" } }, p.title) : null,
              blk.caption ? el("div", null, blk.caption) : null);
          }));
        };
        if (b.type === "witnesses") n = el("div", { class: "dsign" }, wit(), pair ? signers(nb) : el("div", { class: "dsr" }));
        else n = el("div", { class: "dsign" }, el("div", { class: "dwl" }), signers(b));
      }
      if (!n) return;
      if (!visible) n.classList.add("hidden-if");
      n.dataset.i = String(i);
      nodes.push(n);
    });
    nodes.forEach((n) => page.appendChild(n));
    if (tpl.footer) page.appendChild(el("div", { class: "dfoot" }, textNodes(tpl.footer, vals, opts), el("span", { style: { marginLeft: "auto" } }, "Page 1 of n")));
    return page;
  }

  // keep a 794px page fitted to its box
  function fit(holder) {
    const pg = holder.querySelector(".docpage");
    if (!pg) return;
    const s = Math.min(1, holder.clientWidth / 794);
    pg.style.transform = `scale(${s})`;
    holder.style.height = pg.offsetHeight * s + "px";
  }

  return { render, fit, num, zar, COLS };
})();

export default {
  title: 'Site Schedules',
  async render(rootEl, ctx) {
    rootEl.innerHTML = '';
    const root = el('div', { class: 'ssv' },
      el('div', { class: 'head' }, el('div', null, el('h1', null, 'Site Schedules'), el('p', null, "The site's Swiftnet PDFs in, the Site Schedule Agreement out, as Word, ready to sign."))));
    const tabsBar = el('div', { class: 'tabs', role: 'tablist' });
    const pane = el('div');
    root.appendChild(tabsBar);
    root.appendChild(pane);
    rootEl.appendChild(root);
    const isAdmin = ['owner', 'admin'].includes(ctx.me.role);
    const canWrite = ctx.me.role !== 'viewer';
  let home = null, tplInfo = null, people = null, tab = "list", openId = null;

  const sec = (title, k, ...body) => el("section", { class: "ss-card" }, el("div", { class: "ss-hd" }, el("h2", null, title), k ? el("span", { class: "ss-k" }, k) : null, el("div", { class: "grow" })), ...body);
  const btn = (label, onclick, ghost) => el("button", { type: "button", class: "btn sm" + (ghost ? " ghost" : ""), onclick }, label);
  const fileLink = (f, label) => f ? el("a", { href: `${B}/api/schedules/files/${f.id}` }, label || f.name) : null;
  const grow = (x) => { x.style.height = "auto"; x.style.height = Math.max(38, x.scrollHeight + 2) + "px"; };
  const when = (iso) => iso ? new Date(iso).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "";

  async function loadTemplate() { tplInfo = await api("/api/schedules/template"); return tplInfo; }
  async function loadPeople() { people = await api("/api/schedules/people"); return people; }

  function pickFiles(multiple, cb) {
    const inp = el("input", { type: "file", accept: ".pdf", multiple: multiple ? true : null, hidden: true, onchange: () => cb(Array.from(inp.files)) });
    document.body.appendChild(inp);
    inp.click();
    setTimeout(() => inp.remove(), 60000);
  }
  function formData(files) { const fd = new FormData(); files.forEach((f) => fd.append("files", f)); return fd; }

  // ---- the list ------------------------------------------------------------------------------------
  async function drawList() {
    home = await api("/api/schedules");
    const grid = el("div", { class: "ss-cols" });
    const left = el("div", { class: "ss-stack", style: { gap: "18px" } });
    if (canWrite) {
      const drop = el("label", { class: "ss-drop" }, el("strong", null, "Drop the site's PDFs here"),
        el("span", { class: "small muted" }, "the quote(s), the NTP, the SOW and/or the signed agreement — all for one site"));
      const send = async (files) => {
        files = files.filter((f) => /\.pdf$/i.test(f.name));
        if (!files.length) return;
        const a = await act(() => api("/api/schedules", { form: formData(files) }), "Read — check the agreement.");
        openId = a.id; draw();
      };
      drop.addEventListener("click", (e) => { e.preventDefault(); pickFiles(true, send); });
      drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
      drop.addEventListener("dragleave", () => drop.classList.remove("over"));
      drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); send(Array.from(e.dataTransfer.files)); });
      left.appendChild(sec("A new agreement", null, drop,
        el("div", null, el("button", { type: "button", class: "ss-link small", onclick: async () => { const a = await act(() => api("/api/schedules", { form: new FormData() })); openId = a.id; draw(); } }, "or start an empty one and type it in"))));
    }
    left.appendChild(sec("How it works", null, el("ol", { class: "small", style: { margin: "0", paddingLeft: "18px", lineHeight: "1.7", color: "var(--ink2)" } },
      el("li", null, "Drop every PDF for the site at once. The fields and the charge tables are read — each comment on the quote is its own line in the table."),
      el("li", null, "Check what's marked, fill in anything missing; the page on the right is the Word document as it will print."),
      el("li", null, "Pick who signs, then Make the Word file."))));
    const list = sec("Agreements", String(home.agreements.length));
    if (!home.agreements.length) list.appendChild(el("div", { class: "small muted" }, "None yet."));
    else {
      const t = el("table", { class: "ss-tbl" }, el("tr", null, ["Site", "Co-user", "Lease", "Starts", "", "Word"].map((h) => el("th", null, h))));
      home.agreements.forEach((a) => t.appendChild(el("tr", null,
        el("td", null, el("button", { type: "button", class: "ss-link", style: { fontWeight: "600", textDecoration: "none", textAlign: "left" }, onclick: () => { openId = a.id; draw(); } }, a.title)),
        el("td", null, a.company), el("td", null, a.lease_type), el("td", { class: "small" }, a.start_date),
        el("td", null, a.missing.length ? el("span", { class: "pill warn" }, `${a.missing.length} to fill in`) : el("span", { class: "pill " + (a.status === "built" ? "good" : "") }, a.status === "built" ? "Made" : "Ready")),
        el("td", null, a.agreement ? [fileLink(a.agreement, "Download"), el("span", { class: "muted small" }, a.downloads ? ` · ${a.downloads}× taken` : "")] : el("span", { class: "muted small" }, "—")))));
      list.appendChild(el("div", { class: "ss-scroll" }, t));
    }
    const right = el("div", { class: "ss-stack", style: { gap: "18px" } }, list);
    if ((home.old || []).length) {
      right.appendChild(sec("From the old CBX", String(home.old.length), el("div", null, home.old.map((q) => el("div", { class: "ss-line" },
        el("span", { style: { flex: "1" } }, q.site || "Quotation", q.quote_no ? el("span", { class: "small muted" }, " · " + q.quote_no) : null),
        fileLink(q.quote_file, "Quote (PDF)"), fileLink(q.agreement, "Agreement (Word)"))))));
    }
    grid.appendChild(left); grid.appendChild(right);
    pane.appendChild(grid);
  }

  // ---- one agreement -------------------------------------------------------------------------------
  async function drawAgreement() {
    const [a] = await Promise.all([api(`/api/schedules/${openId}`), tplInfo ? null : loadTemplate(), people ? null : loadPeople(), home ? null : (async () => { home = await api("/api/schedules"); })()]);
    let data = { fields: { ...a.fields }, tables: JSON.parse(JSON.stringify(a.tables || {})), signing: JSON.parse(JSON.stringify(a.signing || {})) };
    ["t1", "t2", "t3", "t4"].forEach((k) => { data.tables[k] = data.tables[k] || []; });
    data.tables.extra = data.tables.extra || [];
    ["swift", "couser"].forEach((s) => { data.signing[s] = data.signing[s] || { city: "", date: "", witnesses: ["", ""], people: [] }; });
    let saveT = null, dirty = false, values = a.values;
    const status = el("span", { class: "small muted" });
    const head = el("div", { style: { display: "flex", flexWrap: "wrap", gap: "10px 16px", alignItems: "center", marginBottom: "16px" } },
      el("button", { type: "button", class: "ss-link small", onclick: () => { openId = null; draw(); } }, "← All agreements"),
      el("strong", { style: { fontSize: "18px" } }, a.title), status, el("div", { class: "grow" }));
    const wordLink = el("span");
    const setWord = (x) => { clear(wordLink); if (x.agreement) wordLink.appendChild(el("a", { class: "btn sm", href: `${B}/api/schedules/files/${x.agreement.id}` }, "Download Word")); };
    setWord(a);
    if (canWrite) {
      head.appendChild(btn("Add PDFs / read again", () => pickFiles(true, async (files) => {
        if (!confirm("Read the PDFs again? The fields and tables start over from the PDFs (who signs stays).")) return;
        await act(() => api(`/api/schedules/${a.id}/read`, { form: formData(files) }), "Read again."); draw();
      }), true));
      head.appendChild(wordLink);
      head.appendChild(el("button", { type: "button", class: "btn sm primary", onclick: async () => {
        await saveNow();
        const r = await act(() => api(`/api/schedules/${a.id}/build`, { method: "POST" }), "The Word file is made.");
        setWord(r); status.textContent = "Made " + new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        location.href = `${B}/api/schedules/files/${r.agreement.id}`;
      } }, "Make the Word file"));
      head.appendChild(el("button", { type: "button", class: "ss-link small ss-bad", onclick: async () => { if (!confirm("Put this agreement away? It leaves the list.")) return; await act(() => api(`/api/schedules/${a.id}/archive`, { method: "POST" })); openId = null; draw(); } }, "Put away"));
    } else head.appendChild(wordLink);
    pane.appendChild(head);
    const info = el("div", { class: "ss-stack", style: { gap: "6px", marginBottom: "14px" } });
    const drawInfo = (x) => {
      clear(info);
      if (x.files && x.files.length) info.appendChild(el("div", { class: "small muted" }, "Read from: ", x.files.map((f, i) => [i ? " · " : "", fileLink({ id: f.file_id, name: f.name }), f.kind ? ` (${f.kind === "ntp" ? "NTP" : f.kind === "sow" ? "SOW" : f.kind})` : ""])));
      (x.notes || []).forEach((n) => info.appendChild(el("div", { class: "small ss-warn" }, "⚠ " + n)));
      if (x.missing && x.missing.length) info.appendChild(el("div", { class: "small ss-warn" }, `${x.missing.length} field${x.missing.length === 1 ? "" : "s"} to fill in — marked below.`));
    };
    drawInfo(a);
    pane.appendChild(info);

    const form = el("div");
    const side = el("div", { class: "ss-side" });
    const hold = el("div", { class: "dochold" });
    side.appendChild(hold);
    pane.appendChild(el("div", { class: "ss-grid" }, form, side));

    function preview() {
      values = localValues();
      clear(hold).appendChild(P.render(tplInfo.template, { values, tables: data.tables, signing: data.signing, people }));
      requestAnimationFrame(() => P.fit(hold));
    }
    window.addEventListener("resize", () => P.fit(hold));

    const swiftSign = () => tplInfo.template.swift_sign || { city: "", people: [] };
    // 1.0.107: a lease starting 01 January–31 March is first escalated on 01 April that year; later, the next year
    const escYear = (year, monthIndex) => (monthIndex <= 2 ? year : year + 1);
    const startOf = (s) => {
      const mt = /^\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*$/.exec(s || "");
      const NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];
      const mi = mt ? NAMES.indexOf(mt[2].toLowerCase().slice(0, 3)) : -1;
      return mi >= 0 ? escYear(Number(mt[3]), mi) : null;
    };
    // 1.0.108: the reference lines read "T100/<number>" and "T000/<number>" — the prefix is never typed or read (reader.ref_number)
    const REF_PRE = { t100_ref: "T100/", t000_ref: "T000/" };
    const refNum = (s) => { const mt = /^\s*(?:T\s*[01]\s*0\s*0\s*[-–/\\:.,_ ]*\s*)?(?:No\.?\s*)?(\d{1,12})(?!\d)/i.exec(String(s || "")); return mt ? mt[1] : ""; };
    // the same sums and dates as the server (cbx/schedules/template.py values())
    function localValues() {
      const f = data.fields, v = { ...values };
      Object.keys(f).forEach((k) => { v[k] = f[k] == null ? "" : String(f[k]).trim(); });
      const y = Number(f.term_years || 0), m = Number(f.term_months || 0);
      v.term = [y ? `${y} Year${y === 1 ? "" : "s"}` : "", m ? `${m} Month${m === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ") || "0 Months";
      const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
      const mt = /^\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s*$/.exec(f.start_date || "");
      if (mt) {
        const mi = MONTHS.findIndex((n) => n.toLowerCase().slice(0, 3) === mt[2].toLowerCase().slice(0, 3));
        if (mi >= 0) {
          const e = new Date(Number(mt[3]) + y, mi + m, Number(mt[1]) - 1);
          v.end_date = `${String(e.getDate()).padStart(2, "0")} ${MONTHS[e.getMonth()]} ${e.getFullYear()}`;
          if (!f.esc_year) v.esc_year = String(escYear(Number(mt[3]), mi));
        }
      } else v.end_date = "";
      v.esc_date = (f.esc_year || v.esc_year) ? `01 April ${f.esc_year || v.esc_year}` : "";
      ["mast_hagl", "inst_hagl"].forEach((k) => { if (v[k] && !/m\s*$/i.test(v[k])) v[k] += "m"; });
      ["t1", "t2", "t3", "t4"].forEach((k) => {
        let s = data.tables[k].reduce((t, r) => t + P.num(r.amt), 0);
        if (k === "t1") s += data.tables.extra.reduce((t, x) => t + (x.rows || []).reduce((u, r) => u + P.num(r[r.length - 1]), 0), 0);
        v[k + "_total"] = P.zar(s);
      });
      ["swift", "couser"].forEach((s) => { v["sign_city_" + s] = data.signing[s].city || ""; v["sign_date_" + s] = data.signing[s].date || ""; });
      v.sign_city_swift = swiftSign().city || ""; // 1.0.107: the template's setting, the same on every agreement
      return v;
    }

    async function saveNow() {
      if (!dirty || !canWrite) return;
      clearTimeout(saveT);
      dirty = false;
      status.textContent = "Saving…";
      try {
        const r = await api(`/api/schedules/${a.id}`, { body: data });
        values = r.values; status.textContent = "Saved"; drawInfo(r); setWord(r); if (typeof drawHist === "function") drawHist(r.history);
        markNeeds(r.missing);
      } catch (e) { status.textContent = ""; toast(e.message, "err"); dirty = true; }
    }
    function changed() {
      dirty = true; status.textContent = "…";
      preview();
      clearTimeout(saveT); saveT = setTimeout(saveNow, 900);
    }
    const needs = new Set(a.missing || []);
    const fieldBoxes = {};
    function markNeeds(list) { Object.keys(fieldBoxes).forEach((k) => fieldBoxes[k].classList.toggle("need", (list || []).includes(k))); }

    // fields, section by section
    home.form.forEach((s, si) => {
      const body = el("div", { class: "ss-body" });
      s.fields.forEach((f) => {
        let input;
        const val = data.fields[f.key] == null ? "" : String(data.fields[f.key]);
        if (f.kind === "lease") {
          input = el("select", { class: "ss-in" }, home.leases.concat(home.leases.includes(val) || !val ? [] : [val]).map((x) => el("option", { value: x, selected: x === val }, x)));
        } else if (f.kind === "long") {
          input = el("textarea", { class: "ss-in", rows: "3" }); input.value = val;
          input.addEventListener("input", () => grow(input)); requestAnimationFrame(() => grow(input));
        } else if (f.kind === "ref") {
          // 1.0.108: "T100/" and "T000/" are fixed text; a person types the number only (an older "T100 9130" shows as 9130)
          const n = refNum(val);
          data.fields[f.key] = n ? REF_PRE[f.key] + n : "";
          input = el("input", { class: "ss-in", value: n, inputmode: "numeric", autocomplete: "off", "aria-label": f.label, style: { flex: "1", minWidth: "0" } });
          input.addEventListener("change", () => { input.value = refNum(input.value); });
        } else input = el("input", { class: "ss-in", value: val, inputmode: f.kind === "int" ? "numeric" : null });
        input.disabled = !canWrite;
        input.addEventListener("input", () => {
          if (f.kind === "ref") {
            // a pasted or typed "T100/9130" keeps its letters until the box is left (then: the number); anything else: digits only
            const prefixed = /^\s*t/i.test(input.value);
            const n = prefixed ? refNum(input.value) : input.value.replace(/\D+/g, "");
            if (!prefixed && input.value !== n) input.value = n;
            data.fields[f.key] = n ? REF_PRE[f.key] + n : ""; fieldBoxes[f.key].classList.remove("need"); changed();
            return;
          }
          // 1.0.107: the first escalation follows a new commencement date, unless a person set their own
          if (f.key === "start_date") {
            const was = startOf(data.fields.start_date), now = startOf(input.value), esc = fieldBoxes.esc_year && fieldBoxes.esc_year.querySelector("input");
            if (now && esc && (!String(data.fields.esc_year || "").trim() || Number(data.fields.esc_year) === was)) { data.fields.esc_year = now; esc.value = String(now); }
          }
          data.fields[f.key] = input.value; fieldBoxes[f.key].classList.remove("need"); changed();
        });
        const shownInput = f.kind === "ref"
          ? el("span", { class: "ss-ref", style: { display: "flex", alignItems: "center", gap: "6px" } }, el("strong", { class: "ss-pre" }, REF_PRE[f.key]), input)
          : input;
        const box = el("label", { class: "ss-f" + (f.kind === "long" ? " full" : "") + (needs.has(f.key) ? " need" : "") }, el("span", null, f.label), shownInput);
        fieldBoxes[f.key] = box;
        body.appendChild(box);
      });
      if (s.section === "Duration") body.appendChild(el("div", { class: "small muted full", id: "dur" }));
      form.appendChild(el("details", { class: "ss-sec", open: si < 3 || s.fields.some((f) => needs.has(f.key)) ? true : null }, el("summary", null, s.section), body));
    });

    // the charge tables: one row per line; each comment is its own line
    const TNAMES = { t1: "6.1 Rental charges", t2: "6.2 Electricity", t3: "6.3 Escort / road", t4: "6.4 Once-off" };
    function rowsEditor(k) {
      const box = el("div", { class: "ss-rows full" });
      const draw = () => {
        clear(box);
        box.appendChild(el("div", { class: "ss-row ss-charge ss-charge-hd" }, ["Leased item", "Requirement", "Description", "Amount (excl VAT)", ""].map((h) => el("span", { class: "ss-hd" }, h))));  // 1.0.106: ss-charge (app.css) — the headings ran into each other and the amounts were cut off
        data.tables[k].forEach((r, i) => {
          const inp = (key, ta) => {
            const ph = { item: "Leased item", req: "Requirement", desc: "Description", amt: "Amount (excl VAT)" }[key];  // 1.0.106: a phone hides the headings
            const x = ta ? el("textarea", { class: "ss-in", rows: "1", placeholder: ph, "aria-label": ph }) : el("input", { class: "ss-in", placeholder: ph, "aria-label": ph });
            x.value = r[key] || ""; x.disabled = !canWrite;
            if (!ta) { x.title = x.value; x.addEventListener("change", () => { x.title = x.value; }); }  // 1.0.106: a long item shows whole on hover
            x.addEventListener("input", () => { r[key] = x.value; if (ta) grow(x); changed(); });
            if (ta) requestAnimationFrame(() => grow(x));
            if (key === "amt") x.addEventListener("blur", () => { if (x.value.trim()) { x.value = P.zar(P.num(x.value)); r.amt = x.value; changed(); } });
            return x;
          };
          box.appendChild(el("div", { class: "ss-row ss-charge" }, inp("item"), inp("req"), inp("desc", true), inp("amt"),
            canWrite ? el("button", { type: "button", class: "ss-x", title: "Remove the line", onclick: () => { data.tables[k].splice(i, 1); draw(); changed(); } }, "×") : el("span")));
        });
        if (canWrite) box.appendChild(el("div", null, el("button", { type: "button", class: "ss-link small", onclick: () => { data.tables[k].push({ item: "", req: "", desc: "", amt: "" }); draw(); } }, "+ Add a line")));
      };
      draw();
      return box;
    }
    const charges = el("div", { class: "ss-body" });
    Object.keys(TNAMES).forEach((k) => { charges.appendChild(el("div", { class: "full", style: { fontWeight: "600", marginTop: "6px" } }, TNAMES[k])); charges.appendChild(rowsEditor(k)); });
    // extra tables
    const extraBox = el("div", { class: "full ss-stack", style: { gap: "12px" } });
    const drawExtra = () => {
      clear(extraBox);
      data.tables.extra.forEach((x, xi) => {
        const title = el("input", { class: "ss-in", value: x.title || "", placeholder: "Table title (e.g. Sector Antenna Requirements)" });
        title.addEventListener("input", () => { x.title = title.value; changed(); });
        const rows = el("div", { class: "ss-rows" });
        (x.rows || []).forEach((r, ri) => rows.appendChild(el("div", { class: "ss-row", style: { gridTemplateColumns: `repeat(${x.cols.length}, minmax(0,1fr)) 26px` } },
          x.cols.map((c, ci) => { const f = el("input", { class: "ss-in", value: r[ci] || "", placeholder: c }); f.addEventListener("input", () => { r[ci] = f.value; changed(); }); return f; }),
          el("button", { type: "button", class: "ss-x", onclick: () => { x.rows.splice(ri, 1); drawExtra(); changed(); } }, "×"))));
        extraBox.appendChild(el("div", { class: "ss-stack", style: { gap: "6px", borderTop: "1px solid var(--rule)", paddingTop: "10px" } },
          el("div", { style: { display: "flex", gap: "8px" } }, title, el("button", { type: "button", class: "ss-x", title: "Remove the table", onclick: () => { data.tables.extra.splice(xi, 1); drawExtra(); changed(); } }, "×")),
          rows, el("div", null, el("button", { type: "button", class: "ss-link small", onclick: () => { x.rows.push(x.cols.map(() => "")); drawExtra(); } }, "+ Add a line"))));
      });
      if (canWrite) extraBox.appendChild(el("div", null, el("button", { type: "button", class: "ss-link small", onclick: () => { data.tables.extra.push({ title: "", cols: P.COLS.slice(), rows: [P.COLS.map(() => "")] }); drawExtra(); changed(); } }, "+ Add an extra table")));
    };
    drawExtra();
    charges.appendChild(el("div", { class: "full", style: { fontWeight: "600", marginTop: "6px" } }, "Extra tables (counted in the 6.1 total)"));
    charges.appendChild(extraBox);
    form.insertBefore(el("details", { class: "ss-sec", open: true }, el("summary", null, "Charges"), charges), form.children[6] || null);

    // who signs
    const signing = el("div", { class: "ss-body" });
    ["swift", "couser"].forEach((side) => {
      const s = data.signing[side];
      const label = side === "swift" ? "Swiftnet" : "Co-user";
      // 1.0.107: where Swiftnet signs and who signs for it are fixed (an admin sets them under Template); the co-user's are picked here
      const fixed = side === "swift" ? swiftSign() : null;
      const city = fixed ? el("div", { style: { padding: "8px 0" } }, fixed.city || "—") : el("input", { class: "ss-in", value: s.city || "", placeholder: "Midrand" });
      const date = el("input", { class: "ss-in", value: s.date || "", placeholder: "12 January" });
      if (!fixed) city.addEventListener("input", () => { s.city = city.value; changed(); });
      date.addEventListener("input", () => { s.date = date.value; changed(); });
      const wit = el("div", { class: "ss-stack", style: { gap: "6px" } });
      const drawWit = () => {
        clear(wit);
        if (!s.witnesses.length || s.witnesses.every((w) => w.trim())) s.witnesses.push("");
        while (s.witnesses.length < 2) s.witnesses.push("");
        s.witnesses.forEach((w, i) => { const f = el("input", { class: "ss-in", value: w, placeholder: `Witness ${i + 1}` }); f.addEventListener("input", () => { s.witnesses[i] = f.value; changed(); }); f.addEventListener("blur", drawWit); wit.appendChild(f); });
      };
      drawWit();
      const ppl = el("div", { class: "ss-stack", style: { gap: "6px" } });
      const drawPpl = () => {
        clear(ppl);
        s.people.forEach((p, i) => {
          const has = [].concat(people.swift || [], people.couser || []).some((x) => x.name.trim().toLowerCase() === p.name.trim().toLowerCase() && x.sig);
          ppl.appendChild(el("div", { class: "ss-line", style: { padding: "6px 0" } }, el("div", { style: { flex: "1" } }, el("strong", null, p.name), el("div", { class: "small muted" }, p.title)),
            el("span", { class: "small " + (has ? "ss-good" : "muted") }, has ? "✓ signature" : "no signature"),
            el("button", { type: "button", class: "ss-x", onclick: () => { s.people.splice(i, 1); drawPpl(); changed(); } }, "×")));
        });
        const pick = el("select", { class: "ss-in" }, el("option", { value: "" }, "Add who signs…"),
          (people[side] || []).filter((x) => !s.people.some((p) => p.name === x.name)).map((x) => el("option", { value: x.name }, x.name + (x.title ? " — " + x.title : ""))));
        pick.addEventListener("change", () => { const x = (people[side] || []).find((q) => q.name === pick.value); if (x) { s.people.push({ name: x.name, title: x.title }); drawPpl(); changed(); } });
        ppl.appendChild(pick);
        ppl.appendChild(el("div", { class: "small muted" }, "Not in the list? Add them under People who sign."));
      };
      if (fixed) {
        clear(ppl);
        (fixed.people || []).forEach((p) => ppl.appendChild(el("div", null, el("strong", null, p.name), el("div", { class: "small muted" }, p.title))));
        ppl.appendChild(el("div", { class: "small muted" }, "The same on every agreement — an admin changes it under Template."));
      } else drawPpl();
      signing.appendChild(el("div", { class: "full", style: { fontWeight: "600", marginTop: "6px" } }, label));
      signing.appendChild(el(fixed ? "div" : "label", { class: "ss-f" }, el("span", null, "Signed at"), city));
      signing.appendChild(el("label", { class: "ss-f" }, el("span", null, "Signed on"), date));
      signing.appendChild(el("div", { class: "ss-f" }, el("span", null, "Witnesses"), wit));
      signing.appendChild(el("div", { class: "ss-f" }, el("span", null, "Signs for " + label), ppl));
    });
    form.appendChild(el("details", { class: "ss-sec" }, el("summary", null, "Who signs"), signing));
    // what happened to it: every read, change, Word file and download (1.0.89)
    const hist = el("div", { class: "ss-body" });
    const drawHist = (list) => {
      clear(hist);
      if (!(list || []).length) { hist.appendChild(el("div", { class: "small muted full" }, "Nothing yet.")); return; }
      (list || []).forEach((h) => hist.appendChild(el("div", { class: "full ss-hist" }, el("span", { class: "small muted" }, when(h.at) + " " + (h.at ? new Date(h.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "")),
        el("span", null, h.what), el("span", { class: "small muted" }, h.by || ""))));
    };
    drawHist(a.history);
    form.appendChild(el("details", { class: "ss-sec" }, el("summary", null, `History${a.downloads ? ` · Word taken ${a.downloads}×` : ""}`), hist));
    if (!canWrite) form.querySelectorAll("input,select,textarea").forEach((x) => { x.disabled = true; });
    preview();
    window.onbeforeunload = () => (dirty ? "Still saving." : undefined);
  }

  // ---- the template ---------------------------------------------------------------------------------
  async function drawTemplate() {
    await loadTemplate();
    if (!people) await loadPeople();
    const editable = isAdmin && canWrite;
    let tpl = JSON.parse(JSON.stringify(tplInfo.template));
    let sel = null, dirty = false, showSample = true;
    const vals = tplInfo.sample;
    const tables = tplInfo.sample_tables;
    const bar = el("div", { style: { display: "flex", flexWrap: "wrap", gap: "10px 14px", alignItems: "center", marginBottom: "14px" } });
    const state = el("span", { class: "small muted" });
    const holder = el("div", { class: "dochold" });
    const panel = el("div", { class: "ss-stack", style: { gap: "14px" } });
    pane.appendChild(bar);
    pane.appendChild(el("div", { class: "ss-grid", style: { gridTemplateColumns: "minmax(0,1.2fr) minmax(0,.8fr)" } }, holder, el("div", { class: "ss-side" }, panel)));

    const KINDS = { para: "Text", heading: "Heading", table: "Charge table", witnesses: "Witnesses", signatories: "Who signs", pagebreak: "Page break" };
    function drawBar() {
      clear(bar);
      bar.appendChild(el("strong", null, tplInfo.current ? `In use: version ${tplInfo.current}` : "In use: the standard wording"));
      bar.appendChild(state);
      bar.appendChild(el("label", { class: "ss-opt" }, el("input", { type: "checkbox", checked: showSample ? true : null, onchange: (e) => { showSample = e.target.checked; drawPage(); } }), " made-up values"));
      bar.appendChild(el("div", { class: "grow" }));
      bar.appendChild(btn("Try it in Word", async () => {
        const r = await fetch(`${B}/api/schedules/template/try`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ template: tpl }), credentials: "same-origin" });
        if (!r.ok) { toast((await r.json()).detail || "That didn't work.", "err"); return; }
        const blob = await r.blob(); const u = URL.createObjectURL(blob);
        const a = el("a", { href: u, download: "Site Schedule template - sample.docx" }); document.body.appendChild(a); a.click(); a.remove();
      }, true));
      if (editable) {
        bar.appendChild(btn("Save as a new version", async () => {
          const note = prompt("What changed? (one line, for the history)", "");
          if (note === null) return;
          await act(() => api("/api/schedules/template", { body: { template: tpl, note } }), "Saved — new agreements use it.");
          dirty = false; await loadTemplate(); drawBar(); drawHistory();
        }));
      }
    }
    function drawPage() {
      const v = showSample ? { ...vals, sign_city_swift: (tpl.swift_sign || {}).city || "" } : {};
      const page = P.render(tpl, { values: v, tables: showSample ? tables : {}, signing: {}, people }, { editor: true, mark: true });
      page.classList.add("edit");
      page.querySelectorAll(":scope > [data-i]").forEach((n) => {
        if (Number(n.dataset.i) === sel) n.classList.add("sel");
        n.addEventListener("click", () => { sel = Number(n.dataset.i); drawPage(); drawPanel(); });
      });
      clear(holder).appendChild(page);
      requestAnimationFrame(() => P.fit(holder));
    }
    function changed() { dirty = true; state.textContent = editable ? "Not saved yet" : ""; drawPage(); }
    window.addEventListener("resize", () => P.fit(holder));

    function drawPanel() {
      clear(panel);
      if (sel == null || !tpl.blocks[sel]) {
        panel.appendChild(sec("The template", null,
          el("p", { class: "small", style: { margin: "0", color: "var(--ink2)" } }, editable ? "Click any part of the page to change it. Yellow is filled in from the agreement; the {names} are the fields." : "Click any part of the page to see how it is made. Only admins change the template."),
          el("p", { class: "small muted", style: { margin: "0" } }, "**bold**, __underline__, {field|what to print when it's empty}.")));
        if (editable) {
          const f = (label, key, type) => { const x = el("input", { class: "ss-in", value: tpl[key] == null ? "" : String(tpl[key]), type: type || "text" }); x.addEventListener("input", () => { tpl[key] = type === "number" ? Number(x.value) : x.value; changed(); }); return el("label", { class: "ss-f" }, el("span", null, label), x); };
          panel.appendChild(sec("The page", null, f("Font", "font"), f("Text size (pt)", "size", "number"), f("Margins (cm)", "margin_cm", "number"), f("Footer", "footer")));
          panel.appendChild(swiftSigning());
        }
        drawHistory();
        return;
      }
      const b = tpl.blocks[sel];
      const body = el("div", { class: "ss-stack", style: { gap: "10px" } });
      const ro = !editable;
      const txt = (label, key, area) => {
        const x = area ? el("textarea", { class: "ss-in", rows: "5" }) : el("input", { class: "ss-in" });
        x.value = b[key] || ""; x.disabled = ro;
        x.addEventListener("input", () => { b[key] = x.value; changed(); });
        x.addEventListener("focus", () => { lastInput = x; lastKey = key; });
        return el("label", { class: "ss-f" }, el("span", null, label), x);
      };
      let lastInput = null, lastKey = null;
      const choice = (label, key, opts) => el("label", { class: "ss-f" }, el("span", null, label), (() => {
        const s = el("select", { class: "ss-in", disabled: ro ? true : null }, opts.map(([v, l]) => el("option", { value: v, selected: String(b[key] == null ? "" : b[key]) === String(v) }, l)));
        s.addEventListener("change", () => { b[key] = s.value === "" ? undefined : (/^\d+$/.test(s.value) && key !== "if" ? Number(s.value) : s.value); if (b[key] === undefined) delete b[key]; changed(); drawPanel(); });
        return s;
      })());
      body.appendChild(el("div", { class: "ss-k" }, KINDS[b.type] || b.type));
      if (b.type === "para" || b.type === "heading") {
        body.appendChild(txt("Text", "text", true));
        if (!ro) {
          const tok = el("select", { class: "ss-in" }, el("option", { value: "" }, "Put a field in the text…"), tplInfo.tokens.map((t) => el("option", { value: t.key }, `${t.label} — {${t.key}}`)));
          tok.addEventListener("change", () => {
            const inp = lastInput || body.querySelector("textarea"); const key = lastKey || "text";
            const at = inp.selectionStart == null ? inp.value.length : inp.selectionStart;
            inp.value = inp.value.slice(0, at) + "{" + tok.value + "}" + inp.value.slice(inp.selectionEnd || at);
            b[key] = inp.value; tok.value = ""; changed();
          });
          body.appendChild(tok);
        }
        body.appendChild(el("div", { class: "ss-grid2", style: { gap: "10px" } }, txt("Number (e.g. 6.2.1.)", "number"),
          choice("Indent", "indent", [["", "None"], ["1", "1"], ["2", "2"], ["3", "3"]])));
        if (b.type === "para") {
          body.appendChild(txt("Second column (at the middle of the line)", "right"));
          body.appendChild(el("div", { class: "ss-grid2", style: { gap: "10px" } },
            choice("Align", "align", [["", "Left"], ["center", "Centre"], ["justify", "Justify"], ["right", "Right"]]),
            choice("Bold", "bold", [["", "No"], ["true", "Yes"]]),
            choice("Size", "size", [["", "Normal"], ["10", "10 pt"], ["12", "12 pt"], ["14", "14 pt"], ["16", "16 pt"]]),
            choice("Space before", "space_before", [["", "Normal"], ["0", "None"], ["6", "6 pt"], ["12", "12 pt"], ["18", "18 pt"]])));
          if (b.bold === "true") b.bold = true;
        }
      } else if (b.type === "table") {
        body.appendChild(choice("Which table", "table", Object.entries(tplInfo.tables)));
        body.appendChild(txt("Total line", "total"));
      } else if (b.type === "witnesses" || b.type === "signatories") {
        body.appendChild(choice("Side", "side", [["swift", "Swiftnet"], ["couser", "Co-user"]]));
        if (b.type === "signatories") body.appendChild(txt("Under each name", "caption"));
      }
      if (b.type !== "pagebreak") body.appendChild(choice("Only when", "if", [["", "Always"]].concat(Object.entries(tplInfo.tables).filter(([k]) => k !== "extra").map(([k, l]) => [k, l + " has lines"]), tplInfo.tokens.map((t) => [t.key, t.label + " is filled in"]))));
      if (!ro) {
        const move = (d) => { const j = sel + d; if (j < 0 || j >= tpl.blocks.length) return; const [x] = tpl.blocks.splice(sel, 1); tpl.blocks.splice(j, 0, x); sel = j; changed(); drawPanel(); };
        const add = el("select", { class: "ss-in", style: { width: "auto" } }, el("option", { value: "" }, "+ Add below…"), Object.entries(KINDS).map(([k, l]) => el("option", { value: k }, l)));
        add.addEventListener("change", () => {
          const k = add.value; if (!k) return;
          const nb = { type: k };
          if (k === "para") Object.assign(nb, { text: "New text", indent: 1 });
          if (k === "heading") Object.assign(nb, { number: "", text: "NEW HEADING:" });
          if (k === "table") Object.assign(nb, { table: "t1", total: "Total" });
          if (k === "witnesses" || k === "signatories") nb.side = "swift";
          tpl.blocks.splice(sel + 1, 0, nb); sel += 1; changed(); drawPanel();
        });
        body.appendChild(el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } }, btn("↑", () => move(-1), true), btn("↓", () => move(1), true), add,
          el("div", { class: "grow" }), el("button", { type: "button", class: "ss-link small ss-bad", onclick: () => { tpl.blocks.splice(sel, 1); sel = null; changed(); drawPanel(); } }, "Remove")));
      }
      body.appendChild(el("button", { type: "button", class: "ss-link small", onclick: () => { sel = null; drawPage(); drawPanel(); } }, "Done"));
      panel.appendChild(sec("This part", null, body));
    }
    // 1.0.107: where Swiftnet signs and its signatories — on every agreement, so a change on Swiftnet's board needs no code
    function swiftSigning() {
      tpl.swift_sign = tpl.swift_sign || { city: "", people: [] };
      const ss = tpl.swift_sign;
      const city = el("input", { class: "ss-in", value: ss.city || "" });
      city.addEventListener("input", () => { ss.city = city.value; changed(); });
      const list = el("div", { class: "ss-stack", style: { gap: "6px" } });
      const drawList = () => {
        clear(list);
        ss.people.forEach((p, i) => {
          const n = el("input", { class: "ss-in", value: p.name || "", placeholder: "Name" }), ti = el("input", { class: "ss-in", value: p.title || "", placeholder: "Title / role" });
          n.addEventListener("input", () => { p.name = n.value; changed(); });
          ti.addEventListener("input", () => { p.title = ti.value; changed(); });
          list.appendChild(el("div", { style: { display: "flex", gap: "6px" } }, n, ti,
            el("button", { type: "button", class: "ss-x", title: "Remove", onclick: () => { ss.people.splice(i, 1); drawList(); changed(); } }, "×")));
        });
        if (ss.people.length < 6) list.appendChild(el("div", null, el("button", { type: "button", class: "ss-link small", onclick: () => { ss.people.push({ name: "", title: "" }); drawList(); } }, "+ Add a signatory")));
      };
      drawList();
      return sec("Swiftnet signs", null, el("label", { class: "ss-f" }, el("span", null, "Signed at"), city), el("div", { class: "ss-f" }, el("span", null, "Signatories, in order"), list),
        el("p", { class: "small muted", style: { margin: "0" } }, "On every agreement. Save as a new version to use it."));
    }
    function drawHistory() {
      if (!tplInfo.versions.length && !editable) return;
      const box = sec("Versions", null);
      box.appendChild(el("div", { class: "ss-line" }, el("span", { style: { flex: "1" } }, "The standard wording"), editable ? btn("Use", async () => {
        if (!confirm("Go back to the standard wording? It is saved as a new version; nothing is lost.")) return;
        await act(() => api("/api/schedules/template/use", { body: { n: 0 } }), "Standard wording in use."); await loadTemplate(); tpl = JSON.parse(JSON.stringify(tplInfo.template)); drawBar(); drawPage(); drawPanel();
      }, true) : null));
      tplInfo.versions.forEach((v) => box.appendChild(el("div", { class: "ss-line" },
        el("div", { style: { flex: "1" } }, el("strong", null, "Version " + v.n), v.n === tplInfo.current ? el("span", { class: "pill good", style: { marginLeft: "8px" } }, "in use") : null,
          el("div", { class: "small muted" }, [when(v.at), v.by, v.note].filter(Boolean).join(" · "))),
        editable && v.n !== tplInfo.current ? btn("Use", async () => { await act(() => api("/api/schedules/template/use", { body: { n: v.n } }), `Version ${v.n} in use.`); await loadTemplate(); tpl = JSON.parse(JSON.stringify(tplInfo.template)); drawBar(); drawPage(); drawPanel(); }, true) : null)));
      panel.appendChild(box);
    }
    drawBar(); drawPage(); drawPanel();
    window.onbeforeunload = () => (dirty ? "The template isn't saved." : undefined);
  }

  // ---- people who sign ------------------------------------------------------------------------------
  async function drawPeople() {
    await loadPeople();
    const data = JSON.parse(JSON.stringify(people));
    const grid = el("div", { class: "ss-grid2" });
    ["swift", "couser"].forEach((side) => {
      const list = el("div");
      const drawList = () => {
        clear(list);
        data[side].forEach((p, i) => {
          const sigBox = el("div", { style: { minWidth: "120px" } }, p.sig ? el("img", { src: p.sig, alt: "signature", style: { height: "40px", display: "block" } }) : el("span", { class: "small muted" }, "no signature"));
          list.appendChild(el("div", { class: "ss-line", style: { alignItems: "center" } },
            el("div", { style: { flex: "1 1 200px" } }, el("strong", null, p.name), el("div", { class: "small muted" }, p.title)), sigBox,
            canWrite ? btn(p.sig ? "Draw again" : "Draw signature", () => drawSig(p, drawList), true) : null,
            canWrite ? el("button", { type: "button", class: "ss-x", title: "Remove", onclick: () => { if (confirm(`Remove ${p.name}?`)) { data[side].splice(i, 1); drawList(); save(); } } }, "×") : null));
        });
        if (canWrite) {
          const n = el("input", { class: "ss-in", placeholder: "Name" }), t = el("input", { class: "ss-in", placeholder: "Title / role" });
          list.appendChild(el("div", { style: { display: "flex", gap: "8px", marginTop: "10px", flexWrap: "wrap" } }, n, t, btn("Add", () => { if (!n.value.trim()) return; data[side].push({ name: n.value.trim(), title: t.value.trim(), sig: null }); drawList(); save(); })));
        }
      };
      drawList();
      grid.appendChild(sec(side === "swift" ? "For Swiftnet" : "For the co-user", String(data[side].length), list));
    });
    pane.appendChild(grid);
    pane.appendChild(el("p", { class: "small muted" }, "A drawn signature is put on the agreement for that name. Everyone with Site Schedules sees this list."));
    async function save() { people = await act(() => api("/api/schedules/people", { body: data }), "Saved."); }
    function drawSig(p, after) {
      const dlg = el("dialog", { class: "ss-dlg" });
      const cv = el("canvas", { class: "ss-sigpad" });
      dlg.appendChild(el("div", { class: "ss-stack", style: { gap: "12px" } }, el("h2", null, "Signature: " + p.name), cv,
        el("div", { style: { display: "flex", gap: "8px" } }, btn("Clear", () => { ctx.clearRect(0, 0, cv.width, cv.height); }, true), el("div", { class: "grow" }),
          btn("Cancel", () => dlg.close(), true), btn("Save the signature", () => { p.sig = cv.toDataURL("image/png"); dlg.close(); after(); save(); }))));
      document.body.appendChild(dlg);
      dlg.showModal();
      const r = window.devicePixelRatio || 1;
      cv.width = cv.clientWidth * r; cv.height = cv.clientHeight * r;
      const ctx = cv.getContext("2d"); ctx.scale(r, r); ctx.lineWidth = 2.2; ctx.lineCap = "round"; ctx.strokeStyle = "#1a2540";
      let drawing = false;
      const pos = (e) => { const b = cv.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
      cv.addEventListener("pointerdown", (e) => { drawing = true; ctx.beginPath(); ctx.moveTo(...pos(e)); cv.setPointerCapture(e.pointerId); });
      cv.addEventListener("pointermove", (e) => { if (!drawing) return; ctx.lineTo(...pos(e)); ctx.stroke(); });
      cv.addEventListener("pointerup", () => { drawing = false; });
      dlg.addEventListener("close", () => dlg.remove());
    }
  }


    async function draw() {
      clear(pane);
      window.onbeforeunload = null;
      try {
        if (tab === 'list') { if (openId) await drawAgreement(); else await drawList(); }
        else if (tab === 'template') await drawTemplate();
        else await drawPeople();
      } catch (e) { pane.appendChild(el('div', { class: 'small muted' }, e.detail || e.message)); }
    }
    const TABS = [['list', 'Agreements'], ['template', 'Template'], ['people', 'People who sign']];
    TABS.forEach(([k, label]) => tabsBar.appendChild(el('button', { type: 'button', role: 'tab', 'aria-selected': k === tab ? 'true' : 'false', 'data-tab': k, onclick: (e) => {
      if (window.onbeforeunload && window.onbeforeunload() && !confirm('Leave without saving?')) return;
      tab = k; openId = null;
      tabsBar.querySelectorAll('button').forEach((b) => b.setAttribute('aria-selected', b === e.currentTarget ? 'true' : 'false'));
      draw();
    } }, label)));
    const q = (ctx.params || {});
    if (q.id) openId = Number(q.id);
    await draw();
    return () => { window.onbeforeunload = null; };
  },
};
