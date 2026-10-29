// 1.0.104 (Builder C): Controls → Reconciliations (V11) — the month's population per portfolio (built from the portfolio's
// rules), each reconciliation with its reasons, the municipal roll-forward, the bills and payments, the reconciling items
// (owner, target date, cleared with a note), prepare → review by a different person, and the report.
// Server: cbx/api/ap_completeness_api.py over cbx/ap/recons.py.

const STATUS_PILL = { open: 'neutral', prepared: 'info', reviewed: 'good', returned: 'warn' };

export default {
  key: 'reconciliations',
  title: 'Reconciliations',
  roles: ['reviewer', 'approver', 'recon_reviewer'],
  async render(root, kit) {
    const { api, act, el, clear, t, money } = kit;
    const now = new Date();
    const st = {
      portfolio: kit.params.portfolio || '', period: kit.params.period || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`,
      status: '', overdue: kit.params.overdue === '1', q: '', recon: Number(kit.params.recon) || 0,
    };
    let got = null, people = null, timer = null;
    const box = el('div', { class: 'stack', style: { gap: '18px' } });
    root.appendChild(box);
    const qs = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined && v !== false))).toString();
    const quiet = (p) => p.catch(() => null);
    const dmy = (iso) => iso ? new Date(iso.length === 10 ? iso + 'T00:00:00' : iso).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
    const amt = (v) => v === null || v === undefined ? '' : money(v);

    async function load() {
      try { got = await api('/api/ap/recons?' + qs({ portfolio: st.portfolio, period: st.period, status: st.status, overdue: st.overdue ? 'true' : '', q: st.q })); }
      catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, e.detail || e.message || t('This view could not open.'))); return; }
      st.portfolio = got.portfolio;
      if (st.recon) return openRecon(st.recon);
      drawList();
    }

    function bar() {
      const pf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Portfolio') }, got.portfolios.map((p) => el('option', { value: p.code, selected: p.code === got.portfolio }, `${p.code} · ${p.name}`)));
      pf.addEventListener('change', () => { st.portfolio = pf.value; load(); });
      const month = el('input', { class: 'in', type: 'month', value: got.period, style: { width: 'auto' }, 'aria-label': t('Month') });
      month.addEventListener('change', () => { if (month.value) { st.period = month.value; load(); } });
      const sf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Status') }, el('option', { value: '' }, t('Every status')),
        Object.entries(got.statuses).map(([k, l]) => el('option', { value: k, selected: k === st.status }, l)));
      sf.addEventListener('change', () => { st.status = sf.value; load(); });
      const od = el('input', { type: 'checkbox', checked: st.overdue });
      od.addEventListener('change', () => { st.overdue = od.checked; load(); });
      const q = el('input', { class: 'in', placeholder: t('Search account, vendor, site'), value: st.q, style: { flex: '1 1 180px' } });
      q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { st.q = q.value.trim(); load(); }, 300); });
      const build = got.can_build ? el('button', { class: 'btn sm primary', type: 'button', onclick: () => quiet(act(() => api('/api/ap/recons/build', { body: { portfolio: got.portfolio, period: got.period } }))
        .then((r) => { if (r) { kit.toast(t('{n} in the population ({a} new, {i} items raised).', { n: r.population, a: r.added, i: r.items })); load(); } })) }, t('Build the population')) : null;
      return el('div', { style: { display: 'flex', gap: '8px 10px', flexWrap: 'wrap', alignItems: 'center' } }, pf, month, sf,
        el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, od, t('Overdue items')), q, build,
        el('a', { class: 'link small', href: '/api/ap/recons/report?' + qs({ portfolio: got.portfolio, period: got.period }) }, t('Report')));
    }

    function stats() {
      const s = got.summary;
      const tile = (k, v, sub) => el('div', { class: 'stat', style: { flex: '1 1 130px' } }, el('div', { class: 'k' }, k), el('div', { class: 'disp', style: { fontSize: '28px', lineHeight: '1.1' } }, String(v)), sub ? el('div', { class: 'small muted' }, sub) : null);
      const ages = Object.entries(got.ages).map(([k, v]) => `${k}: ${v.items}`).join(' · ');
      return el('div', { class: 'stack', style: { gap: '6px' } },
        el('div', { style: { display: 'flex', flexWrap: 'wrap' } }, tile(t('Population'), s.population), tile(t('Prepared'), s.prepared), tile(t('Reviewed'), s.reviewed, t('{p}% complete', { p: s.complete_pct })),
          tile(t('Open items'), s.items_open, t('{n} overdue', { n: s.items_overdue })), tile(t('Credits to recover'), money(got.credits_recoverable))),
        el('div', { class: 'small muted' }, t('Open items by age (days): {a}', { a: ages }), ' · ',
          got.payments.payments ? t('SAP payments loaded to {d}', { d: dmy(got.payments.to) }) : t('No SAP payments loaded: the roll-forward waits for the FBL1N payments (AP Settings → Posting history).')));
    }

    function addForm() {
      if (!got.can_prepare) return null;
      const acct = el('input', { class: 'in mono', placeholder: t('Account number'), style: { width: '170px' } });
      const vend = el('input', { class: 'in mono', placeholder: t('or vendor'), style: { width: '120px' } });
      const why = el('input', { class: 'in', placeholder: t('Why (optional)'), style: { flex: '1 1 200px' } });
      return el('details', null, el('summary', { class: 'small', style: { cursor: 'pointer' } }, t('Add an account or vendor to this month\'s population')),
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginTop: '8px' } }, acct, vend, why,
          el('button', { class: 'btn sm', type: 'button', onclick: () => quiet(act(() => api('/api/ap/recons/add', { body: { portfolio: got.portfolio, period: got.period, account: acct.value, vendor: vend.value, why: why.value } }), t('Added.')).then((r) => { if (r) openRecon(r.id); })) }, t('Add'))));
    }

    function drawList() {
      clear(box).append(bar(), stats());
      const tb = el('tbody');
      got.rows.forEach((r) => tb.appendChild(el('tr', null,
        el('td', null, el('button', { class: 'link mono', type: 'button', onclick: () => openRecon(r.id) }, r.account || r.vendor), el('div', { class: 'small muted' }, r.name)),
        el('td', { class: 'small' }, (r.why[0] || {}).text || '', r.why.length > 1 ? el('span', { class: 'muted' }, ' ' + t('+{n} more', { n: r.why.length - 1 })) : null),
        el('td', { style: { textAlign: 'end', whiteSpace: 'nowrap' } }, amt(r.difference)),
        el('td', null, el('span', { class: 'pill ' + (STATUS_PILL[r.status] || 'neutral') }, r.status_label), r.reviewer ? el('div', { class: 'small muted' }, r.reviewer) : null),
        el('td', { class: 'small', style: { whiteSpace: 'nowrap' } }, r.items_open ? t('{n} open', { n: r.items_open }) : '', r.items_overdue ? el('span', { style: { color: 'var(--bad)' } }, ' · ' + t('{n} overdue', { n: r.items_overdue })) : null))));
      box.append(el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('This month\'s population')), el('span', { class: 'k' }, t('picked by the portfolio\'s rules; only exceptions need a person'))),
        got.rows.length ? el('div', { style: { overflowX: 'auto' } }, el('table', { class: 'tbl' }, el('thead', null, el('tr', null, [t('Account'), t('Why'), t('Difference'), t('Status'), t('Items')].map((h, i) => el('th', { style: i === 2 ? { textAlign: 'end' } : null }, h)))), tb))
          : el('div', { class: 'small muted' }, got.can_build ? t('Nothing yet: build the population for this month.') : t('Nothing yet.')),
        addForm()));
    }

    async function openRecon(id) {
      st.recon = id;
      let d;
      try { d = await api(`/api/ap/recons/${id}`); } catch (e) { st.recon = 0; kit.toast(e.detail || e.message); return drawList(); }
      if (!people && d.can_prepare) { try { people = (await api('/api/ap/recons/people')).people; } catch (e) { people = []; } }
      drawRecon(d);
    }

    function drawRecon(d) {
      const back = el('button', { class: 'link small', type: 'button', onclick: () => { st.recon = 0; load(); } }, t('← Back to the population'));
      const head = el('div', { class: 'stack', style: { gap: '4px' } },
        el('div', { style: { display: 'flex', gap: '10px', alignItems: 'baseline', flexWrap: 'wrap' } }, el('h2', { class: 'h2 mono' }, d.account || d.vendor), el('span', null, d.name),
          el('span', { class: 'pill ' + (STATUS_PILL[d.status] || 'neutral') }, d.status_label), el('span', { class: 'small muted' }, `${d.portfolio} · ${d.period}`)),
        el('ul', { class: 'small', style: { margin: '0', paddingLeft: '18px' } }, d.why.map((w) => el('li', null, w.text))),
        d.roll && d.roll.state === 'exception' ? el('div', { class: 'small', style: { color: 'var(--bad)' } }, t('Roll-forward: {x}', { x: d.roll.text })) : null,
        d.roll && d.roll.state === 'no_payments' ? el('div', { class: 'small muted' }, t('Roll-forward: no SAP payments loaded for those months yet.')) : null,
        d.roll && d.roll.state === 'no_balances' ? el('div', { class: 'small muted' }, t('Roll-forward: the bills show no opening or closing balance.')) : null);

      const bills = d.bills.length ? el('div', { style: { overflowX: 'auto' } }, el('table', { class: 'tbl small' }, el('thead', null, el('tr', null, [t('Month'), t('Bill'), t('Opening'), t('Excl. VAT'), t('Closing')].map((h) => el('th', null, h)))),
        el('tbody', null, d.bills.map((b) => el('tr', null, el('td', null, b.period), el('td', null, [b.invoice_no, dmy(b.bill_date), b.reading_type === 'E' ? t('estimated') : ''].filter(Boolean).join(' · ')),
          el('td', { class: 'mono' }, amt(b.opening)), el('td', { class: 'mono' }, amt(b.total_excl)), el('td', { class: 'mono' }, amt(b.closing))))))) : null;
      const pays = d.payments.length ? el('div', { class: 'small' }, el('div', { class: 'k' }, t('SAP payments')), d.payments.map((p) => el('div', null, `${dmy(p.posting_date)} · ${p.doc_type} ${p.doc_number} · ${money(d.account_key && p.allocations[d.account_key] !== undefined ? p.allocations[d.account_key] : p.amount)}`))) : null;

      // the preparer's balances
      const sb = el('input', { class: 'in mono', type: 'number', step: '0.01', value: d.statement_balance === null ? '' : String(d.statement_balance) });
      const lb = el('input', { class: 'in mono', type: 'number', step: '0.01', value: d.ledger_balance === null ? '' : String(d.ledger_balance) });
      const note = el('textarea', { class: 'in', rows: '2' }); note.value = d.note || '';
      const body = () => ({ statement_balance: sb.value === '' ? null : Number(sb.value), ledger_balance: lb.value === '' ? null : Number(lb.value), note: note.value });
      const lab = (k, i) => el('label', { class: 'stack', style: { gap: '2px' } }, el('span', { class: 'k' }, k), i);
      const prep = d.can_prepare && !['reviewed', 'prepared'].includes(d.status) ? el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Prepare')), el('span', { class: 'k' }, d.preparer ? t('prepared by {n}', { n: d.preparer }) : t('the statement against SAP\'s ledger'))),
        el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(180px,1fr))', gap: '8px' } }, lab(t('Statement balance'), sb), lab(t('Ledger balance (SAP)'), lb),
          lab(t('Difference'), el('div', { class: 'mono', style: { paddingTop: '6px' } }, amt(d.difference)))), lab(t('Note'), note),
        el('div', { style: { display: 'flex', gap: '10px', marginTop: '8px' } },
          el('button', { class: 'btn sm', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/recons/${d.id}`, { method: 'PUT', body: body() }), t('Saved.')).then((x) => { if (x) drawRecon({ ...x, can_prepare: d.can_prepare, can_review: d.can_review }); })) }, t('Save')),
          el('button', { class: 'btn sm primary', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/recons/${d.id}`, { method: 'PUT', body: { ...body(), sign: true } }), t('Prepared: a reviewer takes it from here.')).then((x) => { if (x) openRecon(d.id); })) }, t('Prepared: send for review'))))
        : el('div', { class: 'small' }, t('Statement {s} · ledger {l} · difference {d}', { s: amt(d.statement_balance) || '-', l: amt(d.ledger_balance) || '-', d: amt(d.difference) || '-' }), d.preparer ? ' · ' + t('prepared by {n}', { n: d.preparer }) : '');

      const rnote = el('input', { class: 'in', placeholder: t('Note (needed to return it)'), style: { flex: '1 1 240px' } });
      // 1.0.104 (review finding): a prepared reconciliation is reviewed as it is, or sent back (then the preparer changes it)
      const rev = d.status === 'prepared' && (d.can_review || d.can_send_back) ? el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Review')), el('span', { class: 'k' }, t('prepared by {n}: a different person reviews', { n: d.preparer }))),
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, rnote,
          d.can_review ? el('button', { class: 'btn sm primary', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/recons/${d.id}/review`, { body: { outcome: 'reviewed', note: rnote.value } }), t('Reviewed.')).then((x) => { if (x) openRecon(d.id); })) }, t('Reviewed')) : null,
          d.can_send_back ? el('button', { class: 'btn sm', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/recons/${d.id}/send-back`, { body: { note: rnote.value } }), t('Sent back to the preparer.')).then((x) => { if (x) openRecon(d.id); })) }, t('Send back')) : null))
        : (d.status === 'prepared' ? el('div', { class: 'small muted' }, t('Prepared by {n}: waiting for review.', { n: d.preparer }))
          : d.reviewer ? el('div', { class: 'small muted' }, t('{s} by {n}', { s: d.status_label, n: d.reviewer })) : null);

      box.replaceChildren(back, head, el('div', { class: 'grid21 g13' }, el('div', { class: 'stack', style: { gap: '10px' } }, bills, pays), el('div', { class: 'stack', style: { gap: '10px' } }, prep, rev)), itemsSec(d),
        d.history.length ? el('details', null, el('summary', { class: 'small', style: { cursor: 'pointer' } }, t('History')),
          el('ul', { class: 'small', style: { margin: '6px 0 0', paddingLeft: '18px' } }, d.history.slice().reverse().map((h) => el('li', null, `${dmy(h.at)} · ${h.who}: ${h.text}`)))) : null);
    }

    function itemsSec(d) {
      const ownerSel = (cur) => el('select', { class: 'in', style: { width: 'auto' } }, el('option', { value: '' }, t('(no owner)')), (people || []).map((p) => el('option', { value: String(p.id), selected: p.id === cur }, p.name)));
      const list = el('div', { class: 'stack', style: { gap: '8px' } });
      d.items.forEach((it) => {
        const parts = [el('div', { style: { flex: '2 1 300px', minWidth: '0' } }, el('span', { class: 'pill ' + (it.cleared ? 'good' : it.overdue ? 'crit' : 'neutral') }, it.label), ' ',
          el('span', { class: 'mono small' }, amt(it.amount)), ' ', el('span', { class: 'small' }, it.text),
          el('div', { class: 'small muted' }, it.cleared ? t('cleared by {n}: {x}', { n: it.cleared_by, x: it.cleared_note }) : [it.owner ? t('owner {n}', { n: it.owner }) : t('no owner'), it.target_date ? t('target {d}', { d: dmy(it.target_date) }) : '', t('{n} days old', { n: it.age_days })].filter(Boolean).join(' · ')))];
        if (!it.cleared && d.can_prepare && d.status !== 'prepared') {
          const own = ownerSel(it.owner_id);
          const tgt = el('input', { class: 'in', type: 'date', value: it.target_date || '', style: { width: 'auto' } });
          const how = el('input', { class: 'in', placeholder: t('How it was cleared'), style: { flex: '1 1 180px' } });
          parts.push(own, tgt, el('button', { class: 'link small', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/recons/${d.id}/items/${it.id}`, { method: 'PUT', body: { owner_id: own.value ? Number(own.value) : null, target_date: tgt.value } }), t('Saved.')).then((x) => { if (x) openRecon(d.id); })) }, t('Save')),
            how, el('button', { class: 'link small', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/recons/${d.id}/items/${it.id}/clear`, { body: { note: how.value } }), t('Cleared.')).then((x) => { if (x) openRecon(d.id); })) }, t('Clear')));
        }
        list.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', borderBottom: '1px solid var(--rule)', paddingBottom: '8px' } }, parts));
      });
      let add = null;
      if (d.can_prepare && d.status !== 'prepared') {
        const typ = el('select', { class: 'in', style: { width: 'auto' } }, Object.entries(d.item_types).map(([k, l]) => el('option', { value: k }, l)));
        const a = el('input', { class: 'in mono', type: 'number', step: '0.01', placeholder: t('Amount'), style: { width: '130px' } });
        const txt = el('input', { class: 'in', placeholder: t('What it is'), style: { flex: '1 1 220px' } });
        const own = ownerSel(kit.me);
        add = el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginTop: '8px' } }, typ, a, txt, own,
          el('button', { class: 'btn sm', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/recons/${d.id}/items`, { body: { type: typ.value, amount: a.value === '' ? null : Number(a.value), text: txt.value, owner_id: own.value ? Number(own.value) : null } }), t('Added.')).then((x) => { if (x) openRecon(d.id); })) }, t('Add an item')));
      }
      return el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Reconciling items')), el('span', { class: 'k' }, t('{n} open · {a}', { n: d.items_open, a: money(d.open_amount) }))),
        d.items.length ? list : el('div', { class: 'small muted' }, t('No items.')), add);
    }

    await load();
    return () => clearTimeout(timer);
  },
};
