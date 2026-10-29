// 1.0.104 (Builder R): Controls → Review — V05 the independent review of captured documents before the SAP upload, and
// V06 the VAT decision on invoices that are not valid tax invoices. One document at a time beside its page: Agree ·
// Correct (the corrected fields go back to Pip and Tess) · Reject (red until it changes); Claim VAT · Don't claim with a
// reason. Never someone who dropped or worked on the document. The monthly coverage report and the "posted without the
// VAT claim" supplier report. Server: cbx/api/ap_review_api.py over cbx/ap/review.py and cbx/ap/vatcheck.py.

const SHOW = [['review', 'To review'], ['vat', 'VAT to decide'], ['done', 'Reviewed']];
const STATE = { selected: ['Waiting', 'warn'], agreed: ['Agreed', 'good'], corrected: ['Corrected', 'info'], rejected: ['Rejected', 'crit'] };
const FIELD = { supplier: 'Supplier', supplier_vat: 'Supplier VAT no', bill_to: 'Bill-to', bill_to_vat: 'Bill-to VAT no', invoice_no: 'Invoice no', invoice_date: 'Invoice date',
  due_date: 'Due date', excl: 'Excl VAT', vat: 'VAT', incl: 'Total incl. VAT', gl: 'G/L (this invoice)', cost_centre: 'Cost centre (this invoice)', account: 'Account no', billing_date: 'Billing date' };

export default {
  key: 'review',
  title: 'Review',
  roles: ['reviewer'],
  async render(root, kit) {
    const { api, act, el, clear, t, money, when } = kit;
    const st = { show: SHOW.some(([k]) => k === kit.params.show) ? kit.params.show : 'review', portfolio: kit.params.portfolio || '', open: Number(kit.params.capture) || null };
    const box = el('div', { class: 'stack', style: { gap: '16px' } });
    root.appendChild(box);
    const month = new Date().toISOString().slice(0, 7);
    const pill = (s) => (STATE[s] ? el('span', { class: 'pill ' + STATE[s][1] }, t(STATE[s][0])) : null);
    const base = (n) => String(n || '').split('/').pop();
    const sw = (s) => el('span', { class: 'sw', style: { background: s === 'green' ? 'var(--good)' : s === 'yellow' ? 'var(--warn)' : s === 'red' ? 'var(--bad)' : 'var(--rule)' } });

    async function load() {
      if (st.open) return openDoc(st.open);
      let r;
      try { r = await api('/api/ap/review?' + new URLSearchParams({ show: st.show, portfolio: st.portfolio })); }
      catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, e.detail || e.message || t('This view could not open.'))); return; }
      drawList(r);
    }

    function bar(r) {
      const c = r.counts || {};
      const tabs = el('div', { class: 'tabs', role: 'tablist', style: { flexWrap: 'wrap' } }, SHOW.map(([k, l]) => el('button', { type: 'button', role: 'tab', class: k === st.show ? 'on' : '',
        'aria-selected': k === st.show ? 'true' : 'false', onclick: () => { st.show = k; load(); } }, t(l) + (k === 'review' && c.review ? ` (${c.review})` : k === 'vat' && c.vat ? ` (${c.vat})` : ''))));
      const pf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Portfolio') }, el('option', { value: '' }, t('Every portfolio')),
        (kit.portfolios || []).map((p) => el('option', { value: p.code, selected: p.code === st.portfolio }, `${p.code} · ${p.name}`)));
      pf.addEventListener('change', () => { st.portfolio = pf.value; load(); });
      const m = el('input', { class: 'in', type: 'month', value: month, style: { width: 'auto' }, 'aria-label': t('Month') });
      const q = () => new URLSearchParams({ month: m.value || month, portfolio: st.portfolio }).toString();
      const rep = el('a', { class: 'link small', href: '/api/ap/reports/review?' + q() }, t('Coverage report'));
      const vat = el('a', { class: 'link small', href: '/api/ap/reports/vat?' + q() }, t('VAT not claimed'));
      m.addEventListener('change', () => { rep.href = '/api/ap/reports/review?' + q(); vat.href = '/api/ap/reports/vat?' + q(); });
      return el('div', { class: 'stack', style: { gap: '10px' } }, tabs,
        el('div', { style: { display: 'flex', gap: '10px 14px', flexWrap: 'wrap', alignItems: 'center' } }, pf, el('div', { class: 'grow' }), m, rep, vat));
    }

    function drawList(r) {
      clear(box).appendChild(bar(r));
      const tbl = el('table', { class: 'tbl' });
      const cols = [t('File'), t('Portfolio'), t('Supplier'), t('Invoice no'), t('Amount'), st.show === 'vat' ? t('Not a valid tax invoice') : st.show === 'done' ? t('Outcome') : t('Why'), ''];
      tbl.appendChild(el('thead', null, el('tr', null, el('th', null, ''), cols.map((h) => el('th', null, h)))));
      const body = el('tbody');
      if (!r.docs.length) body.appendChild(el('tr', null, el('td', { colspan: cols.length + 1, class: 'small muted' }, st.show === 'review' ? t('Nothing waits for a review.') : st.show === 'vat' ? t('No invoice waits for a VAT decision.') : t('No reviews yet.'))));
      r.docs.forEach((d) => body.appendChild(el('tr', { style: { cursor: 'pointer' }, onclick: () => openDoc(d.id) },
        el('td', { style: { width: '18px' } }, sw(d.status)),
        el('td', { style: { minWidth: '150px', maxWidth: '260px', overflowWrap: 'anywhere' } }, base(d.file), el('div', { class: 'small muted' }, d.batch)),
        el('td', { class: 'mono small' }, d.portfolio), el('td', { class: 'small' }, d.supplier, d.vendor ? el('div', { class: 'muted mono' }, d.vendor) : null),
        el('td', { class: 'mono small' }, d.invoice_no), el('td', { class: 'mono small', style: { textAlign: 'end', whiteSpace: 'nowrap' } }, d.amount ? (d.credit ? '−' : '') + money(d.amount) : ''),
        el('td', { class: 'small', style: { maxWidth: '320px' } }, st.show === 'vat' ? (d.vat.vat_reasons || []).join('; ') : st.show === 'done' ? [pill(d.state), ' ', d.reviewed_by, d.reviewed_at ? ' · ' + when(d.reviewed_at) : ''] : d.why),
        el('td', { class: 'small muted' }, d.can ? '' : d.cannot))));
      tbl.appendChild(body);
      box.appendChild(el('div', { style: { overflowX: 'auto' } }, tbl));
    }

    async function openDoc(id) {
      let d;
      try { d = await api(`/api/ap/review/${id}`); } catch (e) { st.open = null; kit.toast(e.detail || e.message); return load(); }
      st.open = id;
      clear(box);
      const back = el('button', { class: 'link small', type: 'button', onclick: () => { st.open = null; load(); } }, t('Back to the list'));
      box.appendChild(el('div', { style: { display: 'flex', gap: '12px', alignItems: 'baseline', flexWrap: 'wrap' } }, back,
        el('h2', { class: 'h2', style: { overflowWrap: 'anywhere' } }, base(d.file)), sw(d.status), pill(d.state),
        el('span', { class: 'small muted' }, [d.portfolio, d.batch].filter(Boolean).join(' · ')),
        el('div', { class: 'grow' }), el('button', { class: 'link small', type: 'button', onclick: () => kit.openCapture(d.id) }, t('Open the document'))));
      if (d.why) box.appendChild(el('div', { class: 'small' }, el('span', { class: 'k' }, t('Picked for review')), ' ', d.why));
      if ((d.issues || []).length) box.appendChild(el('div', { class: 'stack', style: { gap: '3px' } }, d.issues.map((x) => el('div', { class: 'small', style: { color: x.level === 'red' ? 'var(--bad)' : x.level === 'yellow' ? 'var(--warn)' : 'var(--ink2)' } }, x.text))));

      // what was read and how it posts, beside the page
      const kv = (k, v) => el('div', { class: 'stack', style: { gap: '2px' } }, el('span', { class: 'k' }, k), el('span', { class: 'small', style: { overflowWrap: 'anywhere' } }, v || '–'));
      const facts = el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(150px,1fr))', gap: '10px' } },
        kv(t('Supplier'), d.supplier), kv(t('Vendor'), [d.vendor, d.vendor_name].filter(Boolean).join(' · ')), kv(t('Invoice no'), d.invoice_no), kv(t('Date'), d.date),
        kv(t('Amount'), d.amount ? (d.credit ? '−' : '') + money(d.amount) : ''), kv(t('Company'), d.company));
      const lines = el('table', { class: 'tbl' }, el('thead', null, el('tr', null, [t('G/L'), t('Cost centre'), t('Tax code'), t('Amount'), t('Text')].map((h) => el('th', null, h)))),
        el('tbody', null, d.lines.map((x) => el('tr', null, el('td', { class: 'mono small' }, x.gl, x.gl_name ? el('div', { class: 'muted', style: { fontFamily: 'inherit' } }, x.gl_name) : null, x.vat_line ? el('div', { class: 'muted' }, t('VAT')) : null),
          el('td', { class: 'mono small' }, x.cost_centre), el('td', { class: 'mono small' }, x.tax_code), el('td', { class: 'mono small', style: { textAlign: 'end' } }, (x.debit ? '' : '−') + x.amount), el('td', { class: 'small' }, x.text)))));
      const side = el('div', { class: 'stack', style: { gap: '12px', minWidth: '0' } }, facts, el('div', { style: { overflowX: 'auto' } }, lines),
        el('div', { class: 'small' }, el('span', { class: 'k' }, t('Duplicate search')), ' ', d.duplicate));
      // V06: a tax invoice that is not valid
      if (d.vat.vat_valid === false) side.appendChild(vatBlock(d));
      else if (d.vat.vat_valid === true) side.appendChild(el('div', { class: 'small muted' }, t('A valid tax invoice.')));
      // V05: the reviewer's outcome
      if (['selected', 'rejected'].includes(d.state) && !d.uploaded) side.appendChild(reviewBlock(d));
      else if (d.reviewed_by) side.appendChild(el('div', { class: 'small' }, pill(d.state), ' ', t('by {name}', { name: d.reviewed_by }), d.reviewed_at ? ' · ' + when(d.reviewed_at) : '', d.note ? ' · ' + d.note : '',
        (d.errors || []).length ? ' · ' + t('wrong: {list}', { list: d.errors.map((e) => (d.error_fields || {})[e] || e).join(', ') }) : ''));
      if (d.is_pdf && d.pages) {
        let page = 1;
        const img = el('img', { alt: t('Page {n}', { n: 1 }), src: `/api/ap/captures/${d.id}/page/1`, style: { width: '100%', border: '1px solid var(--rule)', background: '#fff', cursor: 'zoom-in' }, onclick: () => window.open(img.src, '_blank', 'noopener') });
        const lab = el('span', { class: 'small muted' }, t('Page {n} of {total}', { n: 1, total: d.pages }));
        const go = (k) => { page = Math.min(d.pages, Math.max(1, page + k)); img.src = `/api/ap/captures/${d.id}/page/${page}`; img.alt = t('Page {n}', { n: page }); lab.textContent = t('Page {n} of {total}', { n: page, total: d.pages }); };
        const pager = d.pages > 1 ? el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center' } }, el('button', { class: 'link small', type: 'button', onclick: () => go(-1) }, t('Previous')), lab, el('button', { class: 'link small', type: 'button', onclick: () => go(1) }, t('Next'))) : null;
        box.appendChild(el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,420px),1fr))', gap: '22px', alignItems: 'start' } },
          el('div', { class: 'stack', style: { gap: '6px', maxHeight: '78vh', overflowY: 'auto' } }, img, pager), side));
      } else box.appendChild(side);
      box.appendChild(el('details', null, el('summary', { class: 'small', style: { cursor: 'pointer' } }, t('History ({n})', { n: (d.history || []).length })),
        el('div', { class: 'stack', style: { gap: '4px', marginTop: '8px' } }, (d.history || []).slice().reverse().map((h) => el('div', { class: 'small' }, el('span', { class: 'muted' }, `${when(h.at)} · ${h.who}: `), h.text)))));
    }

    function vatBlock(d) {
      const v = d.vat;
      const wrap = el('section', { class: 'sec', style: { margin: '0', padding: '10px 12px', border: '1px solid var(--rule)' } },
        el('div', { class: 'k' }, t('Not a valid tax invoice')), el('div', { class: 'small', style: { color: v.vat_decision ? 'var(--ink2)' : 'var(--bad)' } }, (v.vat_reasons || []).join('; ')));
      if (v.vat_decision) wrap.appendChild(el('div', { class: 'small' }, v.vat_decision === 'claim' ? t('VAT claimed') : t("VAT not claimed"), ' · ', v.vat_decided_by, v.vat_decided_at ? ' · ' + when(v.vat_decided_at) : '', v.vat_decision_note ? ': ' + v.vat_decision_note : ''));
      if (d.uploaded) return wrap;
      if (!d.can) { wrap.appendChild(el('div', { class: 'small muted' }, d.cannot)); return wrap; }
      const reason = el('input', { class: 'in', placeholder: t('Why (kept with the decision)') });
      const send = (decision, ok) => act(() => api(`/api/ap/captures/${d.id}/vat`, { body: { decision, reason: reason.value } }), ok).then(() => { kit.refresh(); openDoc(d.id); }).catch(() => {});
      wrap.append(reason, el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '6px' } },
        el('button', { class: 'btn sm', type: 'button', onclick: () => send('claim', t('VAT claimed. Pip builds it again.')) }, t('Claim VAT')),
        el('button', { class: 'btn sm primary', type: 'button', onclick: () => send('no_claim', t("Not claimed: posted without the VAT. Pip builds it again.")) }, t("Don't claim"))));
      return wrap;
    }

    function reviewBlock(d) {
      const wrap = el('section', { class: 'sec', style: { margin: '0', padding: '10px 12px', border: '1px solid var(--rule)' } }, el('div', { class: 'k' }, t('Your review')));
      if (!d.can) { wrap.appendChild(el('div', { class: 'small muted' }, d.cannot)); return wrap; }
      const note = el('input', { class: 'in', placeholder: t('Note (required to reject: what is wrong)') });
      const errs = {};
      const ticks = el('div', { style: { display: 'flex', gap: '6px 14px', flexWrap: 'wrap' } }, Object.entries(d.error_fields || {}).map(([k, l]) => {
        errs[k] = el('input', { type: 'checkbox' });
        return el('label', { class: 'small', style: { display: 'flex', gap: '5px', alignItems: 'center' } }, errs[k], t(l));
      }));
      const fix = {}, form = el('div', { class: 'hide', style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(160px,1fr))', gap: '8px' } });
      Object.entries(d.editable || {}).forEach(([k, v]) => { fix[k] = el('input', { class: 'in', value: v, type: /date$/.test(k) ? 'date' : 'text' }); form.appendChild(el('label', { class: 'stack', style: { gap: '3px' } }, el('span', { class: 'k' }, t(FIELD[k] || k)), fix[k])); });
      const wrong = () => Object.entries(errs).filter(([, c]) => c.checked).map(([k]) => k);
      const send = (action, body, ok) => act(() => api(`/api/ap/review/${d.id}`, { body: { action, note: note.value, errors: wrong(), ...body } }), ok)
        .then(() => { kit.refresh(); st.open = null; load(); }).catch(() => {});
      const correctBtn = el('button', { class: 'btn sm', type: 'button', onclick: () => {
        if (form.classList.contains('hide')) { form.classList.remove('hide'); correctBtn.textContent = t('Save the correction'); return; }
        const fields = {}; Object.entries(fix).forEach(([k, i]) => { if (i.value !== (d.editable[k] || '')) fields[k] = i.value; });
        send('correct', { fields }, t('Corrected. Pip and Tess have it again.'));
      } }, t('Correct'));
      wrap.append(el('div', { class: 'small muted' }, t('Check it against the page: supplier, vendor, number, date, amount, company, G/L, cost centre, tax code and the duplicate search.')),
        note, el('details', null, el('summary', { class: 'small', style: { cursor: 'pointer' } }, t('What was wrong (for Correct or Reject)')), ticks), form,
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '6px' } },
          el('button', { class: 'btn sm primary', type: 'button', onclick: () => send('agree', {}, t('Agreed.')) }, t('Agree')), correctBtn,
          el('button', { class: 'btn sm', type: 'button', onclick: () => send('reject', {}, t('Rejected: red until it is fixed.')) }, t('Reject'))));
      return wrap;
    }

    load();
  },
};
