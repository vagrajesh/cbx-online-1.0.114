// 1.0.104 (Builder D): Controls → Duplicates (V09) — every possible duplicate Tess, Ivy and Bo raise (and the lines of an
// imported MOL / Workflow listing) as a case: genuine (never posts) or valid (released) with a reason; a valid release at
// or above the review threshold waits for a second person. Open cases by age per portfolio and owner (target: nil at month
// end); the backlog import with proposals, confirmed in bulk; the conclusions as an Excel file to clear in SAP / MOL.
// Server: cbx/api/ap_vendor_dups_api.py over cbx/ap/dupcases.py.

const STATE_PILL = { open: 'warn', pending: 'info', genuine: 'crit', valid: 'good', gone: 'neutral' };
const STATE_WORDS = { open: 'Open', pending: 'Second person', genuine: 'Genuine duplicate', valid: 'Valid', gone: 'No longer raised' };
const RAISED = { tess: 'Tess', near: 'Tess (near-duplicate)', ivy: 'Ivy', bo: 'Bo', import: 'Listing' };

export default {
  key: 'duplicates',
  title: 'Duplicates',
  roles: ['reviewer', 'approver'],
  async render(root, kit) {
    const { api, act, el, clear, t, money } = kit;
    const st = { state: kit.params.state || 'live', portfolio: kit.params.portfolio || '', q: '', import_id: kit.params.import_id || '' };
    let got = null, timer = null;
    const picked = new Set();
    const box = el('div', { class: 'stack', style: { gap: '18px' } });
    root.appendChild(box);
    const qs = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined))).toString();
    const pill = (s) => el('span', { class: `pill ${STATE_PILL[s] || 'neutral'}` }, t(STATE_WORDS[s] || s));

    async function load() {
      try { got = await api('/api/ap/dups?' + qs(st)); }
      catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, e.detail || e.message || t('This view could not open.'))); return; }
      picked.clear();
      draw();
    }

    function tiles() {
      const live = got.counts.open + got.counts.pending;
      const tile = (k, v, sub) => el('div', { class: 'stat', style: { flex: '1 1 140px' } }, el('div', { class: 'k' }, k), el('div', { class: 'disp', style: { fontSize: '28px', lineHeight: '1.1' } }, String(v)), sub ? el('div', { class: 'small muted' }, sub) : null);
      const old = got.bands.reduce((a, b) => a + (b[got.ages[2]] || 0), 0);
      return el('div', { style: { display: 'flex', flexWrap: 'wrap' } }, tile(t('Open'), live, t('target: nil at month end')), tile(t('Second person'), got.counts.pending, t('valid releases over the threshold')),
        tile(t('Over 60 days'), old), tile(t('Genuine'), got.counts.genuine, t('never posted')), tile(t('Valid'), got.counts.valid, t('released')));
    }

    function ageing() {
      if (!got.bands.length) return null;
      const tbl = el('table', { class: 'tbl' }, el('thead', null, el('tr', null, el('th', null, t('Portfolio')), got.ages.map((a) => el('th', { style: { textAlign: 'right' } }, a)), el('th', { style: { textAlign: 'right' } }, t('Open')))),
        el('tbody', null, got.bands.map((b) => el('tr', null, el('td', null, b.portfolio || t('none')), got.ages.map((a) => el('td', { class: 'mono', style: { textAlign: 'right' } }, String(b[a] || 0))),
          el('td', { class: 'mono', style: { textAlign: 'right' } }, String(b.total))))));
      const own = el('div', { class: 'small muted' }, got.owners.map((o) => `${o.portfolio || t('none')}: ${o.owner} ${o.open}`).join(' · '));
      return el('div', { class: 'stack', style: { gap: '6px' } }, el('div', { style: { overflowX: 'auto' } }, tbl), own);
    }

    function bar() {
      const sf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('State') }, [['live', t('Open')], ['pending', t('Waiting for a second person')], ['genuine', t('Genuine')],
        ['valid', t('Valid')], ['gone', t('No longer raised')], ['all', t('Every case')]].map(([v, l]) => el('option', { value: v, selected: v === st.state }, l)));
      sf.addEventListener('change', () => { st.state = sf.value; load(); });
      const pf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Portfolio') }, el('option', { value: '' }, t('Every portfolio')),
        (kit.portfolios || []).map((p) => el('option', { value: p.code, selected: p.code === st.portfolio }, `${p.code} · ${p.name}`)));
      pf.addEventListener('change', () => { st.portfolio = pf.value; load(); });
      const im = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Listing') }, el('option', { value: '' }, t('Every source')),
        got.imports.map((i) => el('option', { value: String(i.id), selected: String(i.id) === String(st.import_id) }, t('Listing {name} ({n} lines)', { name: i.filename, n: i.rows }))));
      im.addEventListener('change', () => { st.import_id = im.value; load(); });
      im.classList.toggle('hide', !got.imports.length);
      const q = el('input', { class: 'in', placeholder: t('Search document, vendor, reference'), value: st.q, style: { flex: '1 1 200px' } });
      q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { st.q = q.value.trim(); load(); }, 300); });
      const xl = el('a', { class: 'link small', href: '/api/ap/dups/export?' + qs({ import_id: st.import_id }) }, st.import_id ? t('Conclusions (Excel) to clear in SAP / MOL') : t('Register (Excel)'));
      return el('div', { style: { display: 'flex', gap: '8px 10px', flexWrap: 'wrap', alignItems: 'center' } }, sf, pf, im, q, xl);
    }

    function decideForm(c, after) {
      const why = el('input', { class: 'in', placeholder: t('Why (needed)'), style: { flex: '1 1 260px' } });
      const go = (outcome) => act(() => api(`/api/ap/dups/${c.id}/decide`, { body: { outcome, reason: why.value } }), outcome === 'genuine' ? t('Genuine: it never posts.') : t('Saved.'))
        .then(() => { load(); if (kit.refresh) kit.refresh(); }).catch(() => {});
      const second = (agree) => act(() => api(`/api/ap/dups/${c.id}/second`, { body: { agree, reason: why.value } }), agree ? t('Released.') : t('Sent back.'))
        .then(() => { load(); if (kit.refresh) kit.refresh(); }).catch(() => {});
      const btns = c.state === 'pending'
        ? [el('button', { class: 'btn sm primary', type: 'button', onclick: () => second(true) }, t('Confirm: not a duplicate')), el('button', { class: 'btn sm', type: 'button', onclick: () => second(false) }, t('Send back'))]
        : [el('button', { class: 'btn sm', type: 'button', onclick: () => go('genuine') }, t('Genuine duplicate')), el('button', { class: 'btn sm primary', type: 'button', onclick: () => go('valid') }, t('Valid: release'))];
      if (c.proposed && c.state === 'open') why.value = t('As proposed: {why}', { why: c.proposed_why });
      return el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', padding: '6px 0' } }, why, btns,
        el('button', { class: 'link small', type: 'button', onclick: () => act(() => api(`/api/ap/dups/${c.id}/take`, { body: {} }), t('It is yours.')).then(load).catch(() => {}) }, t('Take it')), after);
    }

    function table() {
      if (!got.rows.length) return el('div', { class: 'small muted' }, st.state === 'live' ? t('No open possible duplicates.') : t('Nothing here.'));
      const all = el('input', { type: 'checkbox', 'aria-label': t('Pick every open case') });
      all.addEventListener('change', () => { got.rows.filter((c) => c.state === 'open').forEach((c) => (all.checked ? picked.add(c.id) : picked.delete(c.id))); draw(); });
      const tbl = el('table', { class: 'tbl' }, el('thead', null, el('tr', null, el('th', null, all), [t('Document'), t('Raised by'), t('Twin'), t('Amount'), t('Age'), t('Owner'), t('Proposed'), t('State')].map((h) => el('th', null, h)))));
      const body = el('tbody');
      got.rows.forEach((c) => {
        const cb = el('input', { type: 'checkbox', checked: picked.has(c.id), disabled: c.state !== 'open', 'aria-label': t('Pick') });
        cb.addEventListener('change', () => { cb.checked ? picked.add(c.id) : picked.delete(c.id); bulkBar(); });
        const open = c.capture_id ? el('button', { class: 'link small', type: 'button', onclick: () => kit.openCapture(c.capture_id) }, t('Open'))
          : c.item_id ? el('button', { class: 'link small', type: 'button', onclick: () => kit.openItem(c.item_id) }, t('Open')) : null;
        const more = el('tr', { class: 'hide' }, el('td', { colspan: '9' }, el('div', { class: 'small muted' }, c.text),
          c.reason ? el('div', { class: 'small' }, t('{who}: {why}', { who: c.decided_by, why: c.reason })) : null,
          ['open', 'pending'].includes(c.state) ? decideForm(c, open) : open));
        const tr = el('tr', null, el('td', null, cb),
          el('td', { style: { cursor: 'pointer' }, onclick: () => more.classList.toggle('hide') }, el('div', { class: 'small', style: { fontWeight: '600', overflowWrap: 'anywhere' } }, c.document || `#${c.id}`), el('div', { class: 'small muted' }, c.portfolio)),
          el('td', { class: 'small' }, t(RAISED[c.source] || c.source)), el('td', { class: 'small', style: { overflowWrap: 'anywhere' } }, c.twin),
          el('td', { class: 'mono small', style: { textAlign: 'right' } }, money(c.amount)), el('td', { class: 'small' }, t('{n} d', { n: c.age_days })),
          el('td', { class: 'small' }, c.owner || ''), el('td', { class: 'small' }, c.proposed ? t(c.proposed === 'genuine' ? 'Genuine' : 'Valid') : ''), el('td', null, pill(c.state)));
        body.append(tr, more);
      });
      tbl.appendChild(body);
      return el('div', { style: { overflowX: 'auto' } }, tbl);
    }

    const bulk = el('div');
    function bulkBar() {
      clear(bulk);
      if (!picked.size) return;
      const why = el('input', { class: 'in', placeholder: t('Reason (blank = each one\'s proposal)'), style: { flex: '1 1 260px' } });
      const send = (outcome) => act(() => api('/api/ap/dups/confirm', { body: { ids: [...picked], outcome, reason: why.value } }), '')
        .then((r) => { kit.toast(t('{n} settled, {w} wait for a second person, {s} left as they were.', { n: r.done, w: r.waiting, s: r.skipped_n })); load(); if (kit.refresh) kit.refresh(); }).catch(() => {});
      bulk.appendChild(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, el('span', { class: 'small' }, t('{n} picked', { n: picked.size })), why,
        el('button', { class: 'btn sm primary', type: 'button', onclick: () => send('') }, t('Confirm as proposed')),
        el('button', { class: 'btn sm', type: 'button', onclick: () => send('genuine') }, t('All genuine')),
        el('button', { class: 'btn sm', type: 'button', onclick: () => send('valid') }, t('All valid'))));
    }

    function importer() {
      const file = el('input', { type: 'file', accept: '.xlsx,.xls,.csv', class: 'in', style: { width: 'auto' } });
      const pf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Portfolio') }, el('option', { value: '' }, t('Portfolio: the default')),
        (kit.portfolios || []).map((p) => el('option', { value: p.code }, `${p.code} · ${p.name}`)));
      const out = el('div', { class: 'small' });
      const send = async (confirm) => {
        if (!file.files.length) { kit.toast(t('Pick the listing first.')); return; }
        const fd = new FormData(); fd.append('file', file.files[0]); fd.append('portfolio', pf.value); fd.append('confirm', confirm ? 'true' : 'false');
        let r;
        try { r = await act(() => api('/api/ap/dups/import', { form: fd }), confirm ? t('Saved: confirm the proposals below.') : ''); } catch (e) { return; }
        clear(out);
        if (confirm) { st.import_id = String(r.import_id); st.state = 'live'; load(); return; }
        out.append(el('div', null, t('{n} lines: {g} proposed genuine, {v} proposed valid, {l} for a person to look at.', { n: r.lines, g: r.genuine, v: r.valid, l: r.look })),
          el('div', { class: 'muted' }, t('Columns read: {m}', { m: Object.entries(r.mapping || {}).map(([k, v]) => `${k} ← ${v}`).join(', ') })),
          el('div', { class: 'stack', style: { gap: '2px', margin: '6px 0' } }, (r.sample || []).slice(0, 8).map((s) => el('div', { class: 'muted' }, `${s.line}. ${s.vendor || ''} ${s.reference || ''} ${money(s.amount)}: ${s.proposed || t('look')} (${s.why})`))),
          el('button', { class: 'btn sm primary', type: 'button', onclick: () => send(true) }, t('Save the {n} cases', { n: r.lines })));
      };
      return el('details', null, el('summary', { class: 'small' }, t('Import a MOL / Workflow Basket listing (the backlog)')),
        el('div', { class: 'stack', style: { gap: '8px', marginTop: '8px' } },
          el('div', { class: 'small muted' }, t('Each line is matched against SAP\'s posting history and CBX\'s uploads, and a classification is proposed. Confirm them in bulk, then download the conclusions to clear in SAP / MOL.')),
          el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, file, pf, el('button', { class: 'btn sm', type: 'button', onclick: () => send(false) }, t('Preview'))), out));
    }

    function draw() {
      clear(box);
      box.append(el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Possible duplicates')), el('span', { class: 'k' }, t('genuine never posts; valid is released (over the threshold by two people)'))),
        tiles(), ageing(), bar(), bulk, table(), importer());
      bulkBar();
    }

    await load();
    return () => clearTimeout(timer);
  },
};
