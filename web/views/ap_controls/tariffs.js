// 1.0.104 (Builder U, V07): AP Settings → Tariffs (admins) — the tariff table the rate check uses before an account's own history.
// Called by web/views/ap.js drawPane with the page's kit: { api, act, el, clear, t, money }.

export function tariffPane(kit) {
  const { api, act, el, clear, t } = kit;
  const box = el('div', { class: 'stack', style: { gap: '12px' } });
  const FIELDS = [['profile', 'Profile'], ['municipality', 'Municipality'], ['tariff', 'Tariff'], ['effective_from', 'Effective from'], ['effective_to', 'Effective to'],
    ['c_per_kwh', 'c/kWh'], ['blocks', 'Blocks or TOU'], ['fixed_r', 'Fixed R per month'], ['note', 'Note']];
  const file = el('input', { type: 'file', accept: '.xlsx,.csv', hidden: true });
  file.addEventListener('change', async () => {
    const f = file.files[0]; file.value = '';
    if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    try { await act(() => api('/api/ap/tariffs/upload', { form: fd }), t('Loaded: the file replaces the table.')); draw(); } catch (e) { /* told */ }
  });

  function form(row, done) {
    const ins = {};
    const grid = el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(180px,1fr))', gap: '8px' } },
      FIELDS.map(([k, label]) => {
        ins[k] = el('input', { class: 'in' + (['c_per_kwh', 'fixed_r', 'blocks'].includes(k) ? ' mono' : ''), type: k.startsWith('effective') ? 'date' : 'text',
          value: row && row[k] !== null && row[k] !== undefined ? String(row[k]) : '', placeholder: k === 'blocks' ? '500=150.2; =180.4' : '' });
        return el('label', { class: 'stack', style: { gap: '3px' } }, el('span', { class: 'k' }, t(label)), ins[k]);
      }));
    const save = async () => {
      const body = {}; FIELDS.forEach(([k]) => { body[k] = ins[k].value; });
      try {
        await act(() => api(row ? `/api/ap/tariffs/${row.id}` : '/api/ap/tariffs', row ? { method: 'PUT', body } : { body }), t('Saved.'));
        done();
      } catch (e) { /* told */ }
    };
    return el('div', { class: 'stack', style: { gap: '8px', padding: '10px 0', borderBottom: '1px solid var(--rule)' } }, grid,
      el('div', { style: { display: 'flex', gap: '10px' } }, el('button', { class: 'btn sm primary', type: 'button', onclick: save }, t('Save')),
        el('button', { class: 'link small', type: 'button', onclick: done }, t('Cancel'))));
  }

  const draw = async () => {
    let got;
    try { got = await api('/api/ap/tariffs'); } catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, t('Only an admin sees this.'))); return; }
    clear(box);
    const adder = el('div');
    box.append(el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Tariffs')), el('span', { class: 'k' }, t('the rate check uses a row here before the account\'s own history'))),
      el('div', { style: { display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'center' } },
        el('button', { class: 'btn sm', type: 'button', onclick: () => { clear(adder).appendChild(form(null, () => { clear(adder); draw(); })); } }, t('Add a tariff')),
        el('a', { class: 'link small', href: '/api/ap/tariffs.xlsx' }, t('Download')),
        el('button', { class: 'link small', type: 'button', onclick: () => file.click() }, t('Upload (replaces the table)')), file),
      el('div', { class: 'small muted' }, t('Blocks: upto=c/kWh separated by ";" with the last one open (500=150.2; =180.4). Fixed charges are per month, excl. VAT.')),
      adder);
    const tbl = el('table', { class: 'tbl' });
    tbl.appendChild(el('thead', null, el('tr', null, FIELDS.map(([, l]) => el('th', null, t(l))), el('th', null, ''))));
    const body = el('tbody');
    if (!got.tariffs.length) body.appendChild(el('tr', null, el('td', { colspan: FIELDS.length + 1, class: 'small muted' }, t('No tariffs yet: the rate check uses each account\'s own last 6 bills.'))));
    got.tariffs.forEach((r) => {
      const tr = el('tr', null, FIELDS.map(([k]) => el('td', { class: ['c_per_kwh', 'fixed_r', 'blocks'].includes(k) ? 'mono small' : 'small' }, r[k] === null || r[k] === undefined ? '' : String(r[k]))),
        el('td', { class: 'small', style: { whiteSpace: 'nowrap' } },
          el('button', { class: 'link small', type: 'button', onclick: () => { tr.replaceWith(el('tr', null, el('td', { colspan: FIELDS.length + 1 }, form(r, draw)))); } }, t('Change')), ' · ',
          el('button', { class: 'link small', type: 'button', onclick: async () => { if (!window.confirm(t('Delete this tariff row?'))) return; try { await act(() => api(`/api/ap/tariffs/${r.id}`, { method: 'DELETE' }), t('Deleted.')); draw(); } catch (e) { /* told */ } } }, t('Delete'))));
      body.appendChild(tr);
    });
    tbl.appendChild(body);
    box.appendChild(el('div', { style: { overflowX: 'auto' } }, tbl));
  };
  draw();
  return el('section', { class: 'sec' }, box);
}
