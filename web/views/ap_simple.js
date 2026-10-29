// 1.0.105: new — Builder S (SPEC-1105). The AP Tool's one screen, "Make SAP upload": drop invoices, one button, one
// result (the summary, one Download, what was left out and the one thing a person can do about it), recent runs.
// Everything else (Inbox, Needs you, The team, Controls, Settings) is behind a small "More" link for admins and people
// with a reviewer / approver / poster role of their own. Server: cbx/api/ap_simple_api.py over cbx/ap/simple.py.
import { api } from '../api.js';
import { toast } from '../ui.js';

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const k in attrs || {}) {
    const v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (k === 'class') n.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(n.style, v);
    else if (v === true) n.setAttribute(k, '');
    else n.setAttribute(k, v);
  }
  const add = (c) => { if (c === null || c === undefined || c === false) return; if (Array.isArray(c)) c.forEach(add); else n.appendChild(c instanceof Node ? c : document.createTextNode(String(c))); };
  kids.forEach(add);
  return n;
}
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const when = (iso) => iso ? new Date(iso).toLocaleString('en-ZA', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '';
const err = (e) => toast((e && (e.detail || e.message)) || 'That did not work.', 'err');

// a POST or GET that answers with a file: saved under the name the server gives
async function fetchFile(path, method, fallback) {
  const r = await fetch(path, { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' });
  if (!r.ok) { let m = 'Something went wrong (' + r.status + ').'; try { m = (await r.json()).detail || m; } catch (e) { /* not JSON */ } throw new Error(m); }
  const blob = await r.blob(), u = URL.createObjectURL(blob);
  const m = (r.headers.get('content-disposition') || '').match(/filename="([^"]+)"/);
  const a = el('a', { href: u, download: m ? m[1] : fallback }); document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(u), 4000);
}

export async function renderSimple(rootEl, ctx) {
  let home = null, run = null, timer = null, picked = [], gone = false;
  const runId = () => (run && run.run && run.run.id) || null;
  const page = el('div', { class: 'ifv apv aps', id: 'aps' });
  clear(rootEl).appendChild(page);

  // ---- the frame -------------------------------------------------------------------------------------------------------------
  const more = el('a', { class: 'link small hide', id: 'apsmore', href: '#/ap?tab=inbox' }, 'More');
  const head = el('div', { class: 'aps-head' }, el('h1', { class: 'h1' }, 'Make SAP upload'), el('div', { class: 'grow' }), more);
  const fileIn = el('input', { type: 'file', multiple: true, accept: '.pdf,.zip,application/pdf,application/zip', hidden: true, id: 'apsfiles' });
  const pickedLine = el('div', { class: 'small muted', id: 'apspicked' });
  const go = el('button', { class: 'btn primary aps-go', type: 'button', id: 'apsgo', disabled: true }, 'Make SAP upload');
  const drop = el('label', { class: 'aps-drop', id: 'apsdrop' }, el('b', null, 'Drop invoices here (PDFs or a zip)'), el('span', { class: 'small muted' }, 'or tap to pick them'), fileIn);
  const dropSec = el('section', { class: 'sec aps-start' }, drop, pickedLine, el('div', null, go));
  const busyLine = el('div', { class: 'aps-busy', id: 'apsbusy' });
  const bar = el('div', { class: 'bar aps-bar' }, el('i', { style: { width: '0%' } }));
  const busySec = el('section', { class: 'sec hide', id: 'apsrun' }, busyLine, bar);
  const result = el('section', { class: 'sec hide', id: 'apsresult' });
  const recentSec = el('section', { class: 'sec hide', id: 'apsrecent' });
  // 1.0.106: someone who may look but not make an upload (a viewer) with no runs to see got a blank page under the title
  const emptySec = el('section', { class: 'sec hide', id: 'apsempty' }, el('div', { class: 'small muted' }, 'Nothing to show yet. You can look here; making an SAP upload is for the people who capture invoices.'));
  page.append(head, dropSec, busySec, result, recentSec, emptySec);

  const setFiles = (files) => {
    picked = [...files].filter((f) => /\.(pdf|zip)$/i.test(f.name));
    const skipped = files.length - picked.length;
    pickedLine.textContent = picked.length ? picked.length + ' file' + (picked.length !== 1 ? 's' : '') + ' ready' + (skipped ? ' (' + skipped + ' not a PDF or zip, left out)' : '') : (skipped ? 'Only PDFs or a zip.' : '');
    go.disabled = !picked.length || !(home && home.can_write);
  };
  fileIn.addEventListener('change', () => setFiles(fileIn.files));
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('over'));
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); setFiles(e.dataTransfer.files); });

  go.addEventListener('click', async () => {
    if (!picked.length) return;
    go.disabled = true;
    showBusy({ progress: { done: 0, total: picked.length, line: 'Sending ' + picked.length + ' file' + (picked.length !== 1 ? 's' : '') + '…' } });
    try {
      run = await api.upload('/api/ap/simple/runs', picked);
      picked = []; fileIn.value = ''; pickedLine.textContent = '';
      remember();
      draw();
    } catch (e) { err(e); busySec.classList.add('hide'); go.disabled = false; }
  });

  function remember() {
    try { history.replaceState(null, '', '#/ap' + (runId() ? '?run=' + runId() : '')); } catch (e) { /* not allowed here */ }
  }

  function showBusy(r) {
    busySec.classList.remove('hide');
    result.classList.add('hide');
    const p = r.progress || {};
    busyLine.textContent = p.line || 'Reading…';
    bar.firstChild.style.width = (p.total ? Math.round(100 * (p.done || 0) / p.total) : 0) + '%';
  }

  // ---- the run ------------------------------------------------------------------------------------------------------------------
  async function poll() {
    clearTimeout(timer);
    if (gone || !runId()) return;
    try { run = await api.get('/api/ap/simple/runs/' + runId()); } catch (e) { timer = setTimeout(poll, 5000); return; }
    draw();
  }

  function draw() {
    if (!run || gone) return;
    if (run.phase === 'reading') { showBusy(run); clearTimeout(timer); timer = setTimeout(poll, 4000); return; }  // 1.0.105 review: every 4 s, and only while it reads
    busySec.classList.add('hide');
    drawResult();
    loadHome();
  }

  function drawResult() {
    clear(result).classList.remove('hide');
    const r = run, d = r.download || {};
    if (r.phase === 'failed') {
      result.append(el('div', { class: 'aps-sum' }, 'These files could not be read.'), r.run.note ? el('div', { class: 'small muted' }, r.run.note) : null);
      return;
    }
    result.append(el('div', { class: 'small muted' }, r.run.name), el('div', { class: 'aps-sum', id: 'apssum' }, r.summary.line));
    const dl = el('button', { class: 'btn primary aps-dl', type: 'button', id: 'apsdl', disabled: !d.can || !(home && home.can_write) },
      d.can ? d.label : (d.sent ? 'Downloaded' : 'Nothing to download yet'));
    dl.addEventListener('click', async () => {
      dl.disabled = true;
      try { await fetchFile('/api/ap/simple/runs/' + r.run.id + '/download', 'POST', 'SAP_upload.xlsx'); toast('Downloaded. These invoices are marked as sent.'); }
      catch (e) { err(e); }
      poll();
    });
    // 1.0.105 review: what went out is never offered as a file SAP could take again — only its record
    const again = d.sent ? el('a', { class: 'link small', href: '/api/ap/simple/runs/' + r.run.id + '/sent', download: '', id: 'apssent' }, 'What was sent') : null;
    const review = el('a', { class: 'link small', href: '/api/ap/simple/runs/' + r.run.id + '/review', id: 'apsreview' }, 'Review workbook');
    result.append(el('div', { class: 'aps-actions' }, dl, again, review));
    if (r.ai_line) result.append(el('div', { class: 'small aps-ai' }, r.ai_line));
    if ((r.left_out || []).length) {
      result.append(el('h2', { class: 'h2', style: { marginTop: '6px' } }, 'Left out'),
        el('div', { class: 'aps-left', id: 'apsleft' }, r.left_out.map(leftRow)));
    }
    result.append(teachLine());
    if ((r.docs || []).length) {
      result.append(el('details', { class: 'aps-docs' }, el('summary', { class: 'small' }, 'Every invoice (' + r.docs.length + ')'),
        el('div', { class: 'aps-list' }, r.docs.map((x) => el('div', { class: 'aps-doc' }, el('span', { class: 'aps-word w-' + x.word.toLowerCase().replace(/\s+/g, '') }, x.word),
          el('span', { class: 'aps-file' }, x.file), el('span', { class: 'small muted' }, [x.supplier, x.amount].filter(Boolean).join(' · ')),
          x.note && x.word !== 'Left out' ? el('div', { class: 'small muted aps-note' }, x.note) : null)))));
    }
  }

  function leftRow(x) {
    const row = el('div', { class: 'aps-row' });
    row.append(el('div', null, el('b', null, x.file), x.supplier || x.amount ? el('span', { class: 'small muted' }, ' · ' + [x.supplier, x.amount].filter(Boolean).join(' · ')) : null),
      el('div', { class: 'small' }, x.note));
    if (x.kind === 'vat' && x.may) row.append(vatButtons(x));
    else if (x.kind === 'company' && x.may) row.append(companyPicker(x));
    else if (x.kind === 'review' && x.may) row.append(el('div', null, el('button', { class: 'btn sm', type: 'button', onclick: () => checkIt(x) }, 'Check it')));
    else if (x.waits) row.append(el('div', { class: 'small muted' }, x.waits));
    return row;
  }

  // 1.0.105 review: "company unknown" (Builder G) — pick the company it is billed to; Mia matches it again
  let companyList = null;
  function companyPicker(x) {
    const pick = el('select', { class: 'in', style: { maxWidth: '360px' }, 'aria-label': 'Company' }, el('option', { value: '' }, 'Loading the companies…'));
    const save = el('button', { class: 'btn sm primary', type: 'button', disabled: true }, 'Save');
    (async () => {
      try { companyList = companyList || await api.get('/api/ap/simple/companies'); } catch (e) { err(e); return; }
      const opt = (c) => el('option', { value: c.code }, c.code + ' ' + c.name);
      clear(pick).append(el('option', { value: '' }, 'Pick the company'),
        companyList.group.length ? el('optgroup', { label: 'Group companies' }, companyList.group.map(opt)) : null,
        el('optgroup', { label: 'Other companies' }, companyList.other.map(opt)));
    })();
    pick.addEventListener('change', () => { save.disabled = !pick.value; });
    save.addEventListener('click', async () => {
      save.disabled = true;
      try { await api.post('/api/ap/captures/' + x.id + '/decide', { action: 'manual', data: { company_code: pick.value } }); toast('Saved. CBX matches it again.'); poll(); }
      catch (e) { err(e); save.disabled = false; }
    });
    return el('div', { class: 'aps-vat' }, pick, save);
  }

  function vatButtons(x) {
    const box = el('div', { class: 'aps-vat' });
    const ask = (decision, label) => {
      const why = el('input', { class: 'in', placeholder: 'Why? (kept with the decision)', style: { maxWidth: '360px' } });
      const ok = el('button', { class: 'btn sm primary', type: 'button' }, label);
      ok.addEventListener('click', async () => {
        ok.disabled = true;
        try { await api.post('/api/ap/captures/' + x.id + '/vat', { decision, reason: why.value }); toast('Saved.'); poll(); }
        catch (e) { err(e); ok.disabled = false; }
      });
      clear(box).append(why, ok, el('button', { class: 'link small', type: 'button', onclick: () => { clear(box).append(...buttons()); } }, 'Cancel'));
      why.focus();
    };
    const buttons = () => [el('button', { class: 'btn sm', type: 'button', onclick: () => ask('claim', 'Claim VAT') }, 'Claim VAT'),
      el('button', { class: 'btn sm', type: 'button', onclick: () => ask('no_claim', "Don't claim") }, "Don't claim")];
    box.append(...buttons());
    return box;
  }

  // ---- "Check it": the page beside the filled fields; Agree, or Save corrections -------------------------------------------------------
  async function checkIt(x) {
    let doc;
    try { doc = await api.get('/api/ap/review/' + x.id); } catch (e) { err(e); return; }
    const dlg = el('dialog', { class: 'dlg ss-dlg ifv apv aps-dlg', 'aria-label': 'Check ' + x.file });
    const pages = el('div', { class: 'aps-pages' });
    for (let n = 1; n <= Math.min(doc.pages || 0, 5); n++) pages.append(el('img', { src: '/api/ap/captures/' + x.id + '/page/' + n, alt: 'Page ' + n, loading: 'lazy' }));
    if (!doc.pages) pages.append(el('div', { class: 'small muted' }, 'No page to show.'));
    const LABEL = { supplier: 'Supplier', supplier_vat: 'Supplier VAT no', bill_to: 'Billed to', bill_to_vat: 'Billed to VAT no', invoice_no: 'Invoice no', invoice_date: 'Invoice date',
      due_date: 'Due date', excl: 'Excl VAT', vat: 'VAT', incl: 'Total', gl: 'G/L', cost_centre: 'Cost centre', account: 'Account', billing_date: 'Bill date' };
    const inputs = {};
    const fields = el('div', { class: 'aps-fields' }, Object.entries(doc.editable || {}).map(([k, v]) => {
      const now = (doc.lines || []).find((l) => !l.vat_line) || {};  // a blank G/L or cost centre here means "as the lines below"
      inputs[k] = el('input', { class: 'in', value: v || '', 'data-field': k, placeholder: k === 'gl' ? (now.gl || '') : k === 'cost_centre' ? (now.cost_centre || '') : '' });
      return el('label', { class: 'stack', style: { gap: '2px' } }, el('span', { class: 'k' }, LABEL[k] || k), inputs[k]);
    }));
    const facts = el('div', { class: 'small muted' }, [doc.company && 'Company ' + doc.company, doc.vendor && 'Vendor ' + doc.vendor, doc.amount && 'R ' + doc.amount, doc.why && 'Why: ' + doc.why].filter(Boolean).join(' · '));
    const lines = (doc.lines || []).length ? el('div', { class: 'small' }, doc.lines.map((l) => el('div', null, [l.gl + (l.gl_name ? ' ' + l.gl_name : ''), l.cost_centre, l.tax_code, l.amount].filter(Boolean).join(' · ')))) : null;
    const send = async (action) => {
      const body = { action };
      if (action === 'correct') {
        body.fields = {};
        for (const k in inputs) if (inputs[k].value.trim() !== String((doc.editable || {})[k] || '').trim()) body.fields[k] = inputs[k].value.trim();
        if (!Object.keys(body.fields).length) { toast('Nothing changed: Agree, or change a field first.', 'err'); return; }
      }
      try { await api.post('/api/ap/review/' + x.id, body); toast(action === 'agree' ? 'Agreed.' : 'Corrections saved.'); dlg.close(); poll(); } catch (e) { err(e); }
    };
    dlg.append(el('div', { class: 'stack' },
      el('div', { class: 'aps-head' }, el('h2', { class: 'h2' }, x.file), el('div', { class: 'grow' }), el('button', { class: 'link small', type: 'button', onclick: () => dlg.close() }, 'Close')),
      facts,
      el('div', { class: 'aps-side' }, pages, el('div', { class: 'stack' }, fields, lines,
        el('div', { class: 'aps-actions' }, el('button', { class: 'btn primary', type: 'button', id: 'apsagree', onclick: () => send('agree') }, 'Agree'),
          el('button', { class: 'btn', type: 'button', onclick: () => send('correct') }, 'Save corrections'))))));
    dlg.addEventListener('close', () => dlg.remove());
    document.body.appendChild(dlg);
    dlg.showModal();
  }

  // ---- "Drop the corrected Excel here to teach CBX" (Builder G's learn_from_upload) -------------------------------------------
  function teachLine() {
    const input = el('input', { type: 'file', accept: '.xlsx,.xlsm', hidden: true });
    const line = el('label', { class: 'aps-teach small', id: 'apsteach' }, 'Drop the corrected Excel here to teach CBX', input);
    const send = async (f) => {
      if (!f) return;
      const fd = new FormData(); fd.append('file', f, f.name);
      try { const r = await api.post('/api/ap/simple/learn', fd); toast((r && (r.said || r.message || r.text)) || 'Thank you: CBX learnt from your file.'); if (runId()) poll(); }
      catch (e) { err(e); }
      input.value = '';
    };
    input.addEventListener('change', () => send(input.files[0]));
    line.addEventListener('dragover', (e) => { e.preventDefault(); line.classList.add('over'); });
    line.addEventListener('dragleave', () => line.classList.remove('over'));
    line.addEventListener('drop', (e) => { e.preventDefault(); line.classList.remove('over'); send(e.dataTransfer.files[0]); });
    return line;
  }

  // ---- recent runs ------------------------------------------------------------------------------------------------------------------
  async function loadHome() {
    try { home = await api.get('/api/ap/simple'); } catch (e) { return; }
    if (gone) return;
    more.classList.toggle('hide', !home.more);
    dropSec.classList.toggle('hide', !home.can_write);
    go.disabled = !picked.length || !home.can_write;
    clear(recentSec).classList.toggle('hide', !(home.recent || []).length);
    emptySec.classList.toggle('hide', !!(home.can_write || (home.recent || []).length || runId()));  // 1.0.106
    if (!(home.recent || []).length) return;
    recentSec.append(el('h2', { class: 'h2' }, 'Recent runs'), el('div', { class: 'aps-recent' }, home.recent.map((x) => el('div', { class: 'aps-rrow' + (x.id === runId() ? ' on' : '') },
      el('a', { class: 'link small', href: '#/ap?run=' + x.id, onclick: (e) => { e.preventDefault(); open(x.id); } }, when(x.at)),
      el('span', { class: 'small muted' }, x.files + ' invoice' + (x.files !== 1 ? 's' : '') + (x.reading ? ' · reading' : '')),
      x.sent ? el('a', { class: 'link small', href: '/api/ap/simple/runs/' + x.id + '/sent', download: '' }, 'What was sent') : el('span')))));
  }

  async function open(id) {
    try { run = await api.get('/api/ap/simple/runs/' + id); } catch (e) { err(e); return; }
    remember();
    draw();
  }

  await loadHome();
  const first = Number(ctx.params.run || ctx.params.batch) || 0;
  if (first) await open(first);
  return () => { gone = true; clearTimeout(timer); };
}
