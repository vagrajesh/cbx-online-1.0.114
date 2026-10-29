// 1.0.104 (Builder C): Controls → Completeness (V10) — per portfolio, cluster and month: each account that should bill is
// posted · bill read · estimated · missing (with its age and the day it counts as late); the Council Liaison list; the
// weekly workbook; an account's status, CO/DO, cluster, check meter and bill day; masterlist imports and their exceptions.
// Server: cbx/api/ap_completeness_api.py over cbx/ap/completeness.py.

const STATE_PILL = { posted: 'good', read: 'info', estimated: 'warn', missing: 'neutral' };

export default {
  key: 'completeness',
  title: 'Completeness',
  roles: ['reviewer', 'approver', 'recon_reviewer'],
  async render(root, kit) {
    const { api, act, el, clear, t, money } = kit;
    const now = new Date();
    const st = {
      portfolio: kit.params.portfolio || '', period: kit.params.period || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`,
      cluster: kit.params.cluster || '', state: kit.params.state || '', q: '',
    };
    let got = null, timer = null, open = 0;
    const box = el('div', { class: 'stack', style: { gap: '18px' } });
    root.appendChild(box);
    const qs = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined))).toString();
    const quiet = (p) => p.catch(() => null);
    const dmy = (iso) => iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' }) : '';

    async function load() {
      try { got = await api('/api/ap/completeness?' + qs({ portfolio: st.portfolio, period: st.period, cluster: st.cluster, state: st.state, q: st.q, limit: 400 })); }
      catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, e.detail || e.message || t('This view could not open.'))); return; }
      st.portfolio = got.portfolio;
      draw();
    }

    function bar() {
      const pf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Portfolio') }, got.portfolios.map((p) => el('option', { value: p.code, selected: p.code === got.portfolio }, `${p.code} · ${p.name}`)));
      pf.addEventListener('change', () => { st.portfolio = pf.value; st.cluster = ''; load(); });
      const month = el('input', { class: 'in', type: 'month', value: got.period, style: { width: 'auto' }, 'aria-label': t('Month') });
      month.addEventListener('change', () => { if (month.value) { st.period = month.value; load(); } });
      const cl = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Cluster') }, el('option', { value: '' }, t('Every cluster')),
        got.clusters.filter(Boolean).map((c) => el('option', { value: c, selected: c === st.cluster }, t('Cluster {c}', { c }))));
      cl.addEventListener('change', () => { st.cluster = cl.value; load(); });
      cl.classList.toggle('hide', !got.clusters.filter(Boolean).length);
      const sf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('State') }, [['', t('Every account')], ['late', t('Late: Council Liaison')], ['missing', t('Missing')],
        ['estimated', t('Estimated')], ['read', t('Bill read')], ['posted', t('Posted')]].map(([v, l]) => el('option', { value: v, selected: v === st.state }, l)));
      sf.addEventListener('change', () => { st.state = sf.value; load(); });
      const q = el('input', { class: 'in', placeholder: t('Search account, site, municipality'), value: st.q, style: { flex: '1 1 200px' } });
      q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { st.q = q.value.trim(); load(); }, 300); });
      const xl = el('a', { class: 'link small', href: '/api/ap/completeness/export?' + qs({ portfolio: got.portfolio, period: got.period, cluster: st.cluster }) }, t('Weekly workbook'));
      return el('div', { style: { display: 'flex', gap: '8px 10px', flexWrap: 'wrap', alignItems: 'center' } }, pf, month, cl, sf, q, xl);
    }

    function stats() {
      const s = got.summary;
      const tile = (k, v, sub) => el('div', { class: 'stat', style: { flex: '1 1 130px' } }, el('div', { class: 'k' }, k), el('div', { class: 'disp', style: { fontSize: '28px', lineHeight: '1.1' } }, String(v)), sub ? el('div', { class: 'small muted' }, sub) : null);
      const pay = got.payments.payments ? t('Payments from SAP loaded to {d}', { d: dmy(got.payments.to) }) : t('No SAP payments loaded yet (AP Settings → Posting history)');
      return el('div', { class: 'stack', style: { gap: '6px' } },
        el('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '0' } }, tile(t('Accounts'), s.accounts, t('{p}% complete', { p: s.complete_pct })), tile(t('Posted'), s.posted),
          tile(t('Bill read'), s.read), tile(t('Estimated'), s.estimated), tile(t('Missing'), s.missing, t('{n} late', { n: s.late }))),
        el('div', { class: 'small muted' }, pay, ' · ', t('late = {g} days past the usual bill day', { g: got.grace_days })));
    }

    function clusterTable() {
      const cs = got.by_cluster.filter((c) => c.cluster);
      if (cs.length < 2) return null;
      return el('div', { style: { overflowX: 'auto' } }, el('table', { class: 'tbl small' },
        el('thead', null, el('tr', null, [t('Cluster'), t('Accounts'), t('Posted'), t('Bill read'), t('Estimated'), t('Missing'), t('Late')].map((h) => el('th', null, h)))),
        el('tbody', null, cs.map((c) => el('tr', null, el('td', null, el('button', { class: 'link', type: 'button', onclick: () => { st.cluster = c.cluster; load(); } }, c.cluster)),
          [c.accounts, c.posted, c.read, c.estimated, c.missing, c.late].map((v) => el('td', { class: 'mono' }, String(v))))))));
    }

    function stateCell(r) {
      const lab = { posted: t('Posted'), read: t('Bill read'), estimated: t('Estimated'), missing: r.late ? t('Late') : t('Missing') }[r.state];
      const pill = el('span', { class: 'pill ' + (r.state === 'missing' && r.late ? 'crit' : STATE_PILL[r.state]) }, lab);
      let sub = '';
      if (r.bill) sub = [r.bill.invoice_no, dmy(r.bill.bill_date), r.bill.total_excl !== null && r.bill.total_excl !== undefined ? money(r.bill.total_excl) : '', r.bill.reading_type === 'E' ? t('estimated reading') : '', r.bill.bills > 1 ? t('{n} bills', { n: r.bill.bills }) : ''].filter(Boolean).join(' · ');
      else if (r.estimate && r.state === 'estimated') sub = t('estimate {v}', { v: money(r.estimate.value) });
      else if (r.state === 'missing') sub = [t('expected {d}', { d: dmy(r.expected) }), r.late ? t('{n} days', { n: r.age_days }) : t('late from {d}', { d: dmy(r.late_from) }), r.estimate ? t('estimate {s}', { s: r.estimate.status }) : ''].filter(Boolean).join(' · ');
      return el('td', null, pill, sub ? el('div', { class: 'small muted' }, sub) : null, r.after_termination ? el('div', { class: 'small', style: { color: 'var(--bad)' } }, t('billed after it was terminated')) : null);
    }

    function table() {
      if (!got.rows.length) return el('div', { class: 'small muted' }, t('No accounts here.'));
      const tb = el('tbody');
      got.rows.forEach((r) => {
        const tr = el('tr', null,
          el('td', null, got.manage ? el('button', { class: 'link mono', type: 'button', onclick: () => editRow(tr, r) }, r.account) : el('span', { class: 'mono' }, r.account),
            el('div', { class: 'small muted' }, [r.cluster ? t('cluster {c}', { c: r.cluster }) : '', r.co_do, r.check_meter ? t('check meter') : ''].filter(Boolean).join(' · '))),
          el('td', null, r.site, el('div', { class: 'small muted' }, r.municipality)),
          el('td', null, r.status === 'active' ? el('span', { class: 'small muted' }, t('active')) : el('span', { class: 'pill ' + (r.status === 'suspended' ? 'warn' : 'crit') }, r.status),
            r.status_from ? el('div', { class: 'small muted' }, dmy(r.status_from)) : null),
          stateCell(r),
          el('td', { style: { textAlign: 'end', whiteSpace: 'nowrap' } }, r.paid === null || r.paid === undefined ? '' : money(r.paid)));
        tb.appendChild(tr);
      });
      return el('div', { class: 'stack', style: { gap: '6px' } },
        el('div', { style: { overflowX: 'auto' } }, el('table', { class: 'tbl' }, el('thead', null, el('tr', null,
          [t('Account'), t('Site'), t('Status'), t('This month'), t('Paid')].map((h, i) => el('th', { style: i === 4 ? { textAlign: 'end' } : null }, h)))), tb)),
        got.total > got.rows.length ? el('div', { class: 'small muted' }, t('{n} of {m} shown: search or filter for the rest, or take the workbook.', { n: got.rows.length, m: got.total })) : null);
    }

    function editRow(tr, r) {
      if (tr.nextSibling && tr.nextSibling.dataset && tr.nextSibling.dataset.edit) { tr.nextSibling.remove(); return; }
      const f = {
        status: el('select', { class: 'in' }, got.statuses.map((s) => el('option', { value: s, selected: s === r.status }, s))),
        status_from: el('input', { class: 'in', type: 'date', value: r.status_from || '' }),
        co_do: el('select', { class: 'in' }, ['', 'CO', 'DO'].map((v) => el('option', { value: v, selected: v === (r.co_do || '') }, v || t('(none)')))),
        cluster: el('input', { class: 'in', value: r.cluster || '' }),
        check_meter: el('input', { type: 'checkbox', checked: !!r.check_meter }),
        bill_day: el('input', { class: 'in mono', type: 'number', min: '1', max: '31', value: r.bill_day_how === 'set' ? String(r.bill_day) : '', placeholder: t('learned: {d}', { d: r.bill_day }) }),
      };
      const lab = (k, i) => el('label', { class: 'stack', style: { gap: '2px' } }, el('span', { class: 'k' }, k), i);
      const save = () => quiet(act(() => api(`/api/ap/completeness/accounts/${r.id}`, { method: 'PUT', body: {
        status: f.status.value, status_from: f.status_from.value || '', co_do: f.co_do.value, cluster: f.cluster.value, check_meter: f.check_meter.checked,
        ...(f.bill_day.value ? { bill_day: Number(f.bill_day.value) } : { clear_bill_day: true }) } }), t('Saved.')).then((x) => { if (x) load(); }));
      const row = el('tr', { 'data-edit': '1' }, el('td', { colspan: '5' }, el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(150px,1fr))', gap: '8px', alignItems: 'end' } },
        lab(t('Status'), f.status), lab(t('From'), f.status_from), lab(t('CO / DO'), f.co_do), lab(t('Cluster'), f.cluster),
        lab(t('Usual bill day'), f.bill_day), el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, f.check_meter, t('Check meter')),
        el('div', null, el('button', { class: 'btn sm primary', type: 'button', onclick: save }, t('Save'))))));
      tr.after(row);
    }

    function importer() {
      if (!got.manage) return null;
      const kind = el('select', { class: 'in', style: { width: 'auto' } }, Object.entries(got.kinds).map(([k, l]) => el('option', { value: k }, l)));
      const source = el('input', { class: 'in', placeholder: t('What it is, e.g. Telkom SAP Masterfile Oct 2026'), style: { flex: '1 1 240px' } });
      const file = el('input', { type: 'file', accept: '.xlsx,.xlsm,.csv' });
      const out = el('div', { class: 'stack', style: { gap: '6px' } });
      const send = async (confirm) => {
        if (!file.files.length) { kit.toast(t('Pick the file first.')); return; }
        const fd = new FormData();
        fd.append('file', file.files[0]); fd.append('portfolio', got.portfolio); fd.append('kind', kind.value); fd.append('source', source.value); fd.append('confirm', confirm ? 'true' : 'false');
        const r = await quiet(act(() => api('/api/ap/completeness/import', { form: fd }), confirm ? t('Saved. The accounts are up to date.') : ''));
        if (!r) return;
        if (confirm) { load(); return; }
        const c = r.counts;
        clear(out).append(
          el('div', { class: 'small' }, t('{rows} rows · {a} accounts · {m} in CBX · {c} to change', { rows: c.rows, a: c.accounts, m: c.matched, c: c.changed })),
          el('div', { class: 'small' }, Object.entries(r.labels).filter(([k]) => c[k]).map(([k, l]) => `${l}: ${c[k]}`).join(' · ') || t('No exceptions.')),
          r.changes.length ? el('ul', { class: 'small', style: { margin: '0', paddingLeft: '18px' } }, r.changes.map((x) => el('li', null, `${x.account} ${x.site}: ${x.what}`))) : null,
          el('div', null, el('button', { class: 'btn sm primary', type: 'button', onclick: () => send(true) }, t('Save this refresh'))));
      };
      return el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Masterlist')), el('span', { class: 'k' }, t('refresh the accounts from the portfolio\'s own list'))),
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, kind, source, file, el('button', { class: 'btn sm', type: 'button', onclick: () => send(false) }, t('Compare'))),
        out,
        got.imports.length ? el('div', { class: 'small muted', style: { marginTop: '8px' } }, t('Last refreshes: '), got.imports.map((i) => `${i.source || i.kind_label} (${(i.at || '').slice(0, 10)}, ${i.by})`).join(' · ')) : null);
    }

    async function exceptionsSec() {
      const sec = el('section', { class: 'sec' });
      if (!got.exceptions_open) return sec;
      let ex;
      try { ex = await api('/api/ap/completeness/exceptions?' + qs({ portfolio: got.portfolio, status: 'open' })); } catch (e) { return sec; }
      sec.append(el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Exceptions')), el('span', { class: 'k' }, t('{n} open: each is resolved with a note', { n: ex.total }))));
      const list = el('div', { class: 'stack', style: { gap: '8px' } });
      ex.rows.forEach((e) => {
        const note = el('input', { class: 'in', placeholder: t('How it was resolved'), style: { flex: '1 1 220px' } });
        list.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', borderBottom: '1px solid var(--rule)', paddingBottom: '8px' } },
          el('div', { style: { flex: '2 1 320px', minWidth: '0' } }, el('span', { class: 'pill warn' }, e.label), ' ', el('span', { class: 'small' }, e.text)),
          got.manage ? note : null,
          got.manage ? el('button', { class: 'link small', type: 'button', onclick: () => quiet(act(() => api(`/api/ap/completeness/exceptions/${e.id}/resolve`, { body: { note: note.value } }), t('Resolved.')).then((x) => { if (x) load(); })) }, t('Resolve')) : null));
      });
      sec.appendChild(list);
      return sec;
    }

    async function draw() {
      const n = ++open;
      clear(box).append(bar(), stats());
      const ct = clusterTable();
      if (ct) box.appendChild(ct);
      box.appendChild(el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, st.state === 'late' ? t('Council Liaison') : t('Accounts')),
        el('span', { class: 'k' }, st.state === 'late' ? t('missing past the usual bill day and the grace days: chase these with the council') : t('every account that should bill this month'))), table()));
      const imp = importer();
      if (imp) box.appendChild(imp);
      const ex = await exceptionsSec();
      if (n === open) box.appendChild(ex);
    }

    await load();
    return () => clearTimeout(timer);
  },
};
