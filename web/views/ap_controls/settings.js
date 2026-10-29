// 1.0.104: AP Settings → Portfolios and AP people (admins) — the Valuence controls' foundations (F0a, F0b).
// Called by web/views/ap.js drawPane with the page's kit: { api, act, el, clear, t }.

const lines = (o) => Object.entries(o || {}).map(([k, v]) => `${k} = ${v}`).join('\n');
const unlines = (s) => {
  const out = {};
  String(s || '').split('\n').forEach((x) => { const i = x.indexOf('='); if (i > 0 && x.slice(0, i).trim()) out[x.slice(0, i).trim()] = x.slice(i + 1).trim(); });
  return out;
};

// ---- Settings → Portfolios -------------------------------------------------------------------------------------------
export function portfolioPane(kit) {
  const { api, act, el, clear, t } = kit;
  const box = el('div', { class: 'stack', style: { gap: '14px' } });
  let pick = '', got = null;
  const draw = async () => {
    try { got = await api('/api/ap/portfolios'); } catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, t('Only an admin sees this.'))); return; }
    if (!got.portfolios.find((p) => p.code === pick)) pick = (got.portfolios[0] || {}).code || '';
    clear(box);
    const sel = el('select', { class: 'in', style: { width: 'auto' } }, got.portfolios.map((p) => el('option', { value: p.code, selected: p.code === pick }, `${p.code} · ${p.name}${p.is_active ? '' : ' (' + t('not active') + ')'}`)));
    sel.addEventListener('change', () => { pick = sel.value; draw(); });
    const addCode = el('input', { class: 'in mono', placeholder: t('New code, e.g. ACME'), style: { width: '160px' } });
    box.append(el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Portfolios')), el('span', { class: 'k' }, t('company codes, SAP overrides and the control settings of each portfolio'))),
      el('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' } }, sel, el('div', { class: 'grow' }), addCode,
        el('button', { class: 'link small', type: 'button', onclick: async () => { const r = await act(() => api('/api/ap/portfolios', { body: { code: addCode.value, name: addCode.value } }), t('Added.')); if (r) { pick = r.code; draw(); } } }, t('Add a portfolio'))));
    const p = got.portfolios.find((x) => x.code === pick);
    if (p) box.appendChild(form(p));
  };

  function field(label, input, help) {
    return el('label', { class: 'stack', style: { gap: '4px' } }, el('span', { class: 'k' }, label), input, help ? el('span', { class: 'small muted' }, help) : null);
  }

  function form(p) {
    const f = {
      name: el('input', { class: 'in', value: p.name }),
      company_codes: el('input', { class: 'in mono', value: p.company_codes, placeholder: 'T100, T101, XA00-XA60' }),
      fixed: el('textarea', { class: 'in mono', rows: '3', placeholder: 'Company = T100' }),
      gl: el('textarea', { class: 'in mono', rows: '3', placeholder: 'electricity = SA101' }),
      note: el('textarea', { class: 'in', rows: '3' }),
      is_active: el('input', { type: 'checkbox', checked: p.is_active, disabled: p.is_default }),
      is_default: el('input', { type: 'checkbox', checked: p.is_default, disabled: p.is_default }),
    };
    f.fixed.value = lines(p.fixed); f.gl.value = lines(p.gl); f.note.value = p.note || '';
    const set = {}, notes = {}, back = new Set();
    const rows = got.keys.map((k) => {
      const v = p.settings[k.key], own = p.own.includes(k.key);
      let input;
      if (k.kind === 'choice') input = el('select', { class: 'in' }, k.choices.map((c) => el('option', { value: c, selected: c === v }, c)));
      else if (k.kind === 'bool') input = el('input', { type: 'checkbox', checked: !!v });
      else if (k.kind === 'lines') { input = el('textarea', { class: 'in', rows: '3' }); input.value = (v || []).join('\n'); }
      else if (k.kind === 'table') { input = el('textarea', { class: 'in mono', rows: '2' }); input.value = Object.entries(v || {}).map(([a, b]) => `${a}: ${b}`).join('\n'); }
      else if (k.kind === 'json') { input = el('textarea', { class: 'in mono', rows: '3' }); input.value = JSON.stringify(v, null, 1); }
      else input = el('input', { class: 'in' + (k.kind === 'number' ? ' mono' : ''), type: k.kind === 'number' ? 'number' : 'text', step: 'any', value: v === null || v === undefined ? '' : String(v) });
      set[k.key] = input;
      notes[k.key] = el('input', { class: 'in small', value: (p.notes || {})[k.key] || '', placeholder: t('Note (optional)') });
      const def = el('span', { class: 'small muted' }, own ? el('button', { class: 'link small', type: 'button', onclick: () => { back.add(k.key); def.textContent = t('Back to the default ({v}) when saved.', { v: JSON.stringify(k.default) }); } }, t('Use the default'))
        : t('Default'));
      return el('div', { class: 'stack', style: { gap: '4px', padding: '8px 0', borderBottom: '1px solid var(--rule)' } },
        el('div', { style: { display: 'flex', gap: '8px', alignItems: 'baseline', flexWrap: 'wrap' } }, el('span', { class: 'k' }, k.label), el('span', { class: 'mono small muted' }, k.key), el('div', { class: 'grow' }), def),
        input, k.help ? el('span', { class: 'small muted' }, k.help) : null, notes[k.key]);
    });
    const read = (k, input) => {
      if (k.kind === 'bool') return input.checked;
      if (k.kind === 'lines') return input.value.split('\n').map((x) => x.trim()).filter(Boolean);
      return input.value;
    };
    const save = async () => {
      const settings = {};
      got.keys.forEach((k) => { settings[k.key] = back.has(k.key) ? null : read(k, set[k.key]); });
      const n = {}; Object.entries(notes).forEach(([k, i]) => { if (i.value.trim()) n[k] = i.value.trim(); });
      const body = { name: f.name.value, company_codes: f.company_codes.value, fixed: unlines(f.fixed.value), gl: unlines(f.gl.value), note: f.note.value, settings, notes: n, is_active: f.is_active.checked };
      if (f.is_default.checked && !p.is_default) body.is_default = true;
      const r = await act(() => api(`/api/ap/portfolios/${encodeURIComponent(p.code)}`, { method: 'PUT', body }), t('Saved. Documents not uploaded yet are built again.'));
      if (r) draw();
    };
    return el('section', { class: 'sec', style: { maxWidth: '860px' } },
      el('div', { class: 'small muted', style: { marginBottom: '8px' } }, p.updated_by ? t('Changed by {name}', { name: p.updated_by }) : ''),
      el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(240px,1fr))', gap: '10px' } },
        field(t('Name'), f.name), field(t('Company codes'), f.company_codes, t('A range as XA00-XA60. A document whose company code is here falls in this portfolio.')),
        field(t('Fixed values (override)'), f.fixed, t('One per line, e.g. Company = T100. The rest come from SAP values → Fixed.')),
        field(t('G/L codes (override)'), f.gl, t('One per line, e.g. electricity = SA101. The rest come from SAP values → GL Codes.'))),
      field(t('Note'), f.note),
      el('div', { style: { display: 'flex', gap: '18px', flexWrap: 'wrap', margin: '8px 0' } },
        el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, f.is_active, t('Active')),
        el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, f.is_default, t('The default (anything no other portfolio claims)'))),
      el('div', { class: 'sec-h', style: { marginTop: '10px' } }, el('h2', { class: 'h2' }, t('Control settings')), el('span', { class: 'k' }, t('the default unless set here'))),
      rows,
      el('div', { style: { marginTop: '12px' } }, el('button', { class: 'btn primary', type: 'button', onclick: save }, t('Save'))));
  }

  draw();
  return el('section', { class: 'sec' }, box);
}

// ---- Settings → AP people ------------------------------------------------------------------------------------------------
export function peoplePane(kit) {
  const { api, act, el, clear, t } = kit;
  const box = el('div', { class: 'stack', style: { gap: '12px' } });
  let pf = '';
  const draw = async () => {
    let g;
    try { g = await api('/api/ap/roles'); } catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, t('Only an admin sees this.'))); return; }
    clear(box);
    const sel = el('select', { class: 'in', style: { width: 'auto' } }, g.portfolios.map((p) => el('option', { value: p.code, selected: p.code === pf }, p.code ? `${p.code} · ${p.name}` : p.name)));
    sel.addEventListener('change', () => { pf = sel.value; draw(); });
    box.append(el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('AP people')), el('span', { class: 'k' }, t('who captures, reviews, posts and approves, per portfolio'))),
      el('div', { style: { display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' } }, sel));
    const boot = pf ? g.bootstrap.includes(pf) : g.bootstrap.length === g.portfolios.length - 1;
    if (boot) box.appendChild(el('div', { class: 'small', style: { color: 'var(--warn)' } },
      pf ? t('No roles are set for {p} yet: everyone with the AP Tool captures, reviews, posts and approves there. A second person still reviews what someone worked on.', { p: pf })
        : t('No roles are set yet: everyone with the AP Tool captures, reviews, posts and approves. A second person still reviews what someone worked on.')));
    const tbl = el('table', { class: 'tbl' });
    tbl.appendChild(el('thead', null, el('tr', null, el('th', null, t('Person')), g.roles.map((r) => el('th', { style: { textAlign: 'center' } }, r.label)))));
    const body = el('tbody');
    if (!g.people.length) body.appendChild(el('tr', null, el('td', { colspan: g.roles.length + 1, class: 'small muted' }, t('Nobody has the AP Tool yet.'))));
    g.people.forEach((u) => {
      const mine = (u.roles[pf] || []).slice(), all = pf ? (u.roles[''] || []) : [];
      const boxes = g.roles.map((r) => {
        const cb = el('input', { type: 'checkbox', checked: mine.includes(r.key), title: r.label, 'aria-label': `${u.name}: ${r.label}` });
        cb.addEventListener('change', async () => {
          const want = g.roles.map((x) => x.key).filter((k) => (k === r.key ? cb.checked : mine.includes(k)));
          try { await act(() => api(`/api/ap/roles/${u.id}`, { method: 'PUT', body: { portfolio: pf, roles: want } }), t('Saved.')); draw(); } catch (e) { cb.checked = !cb.checked; }
        });
        return el('td', { style: { textAlign: 'center' } }, cb, all.includes(r.key) ? el('div', { class: 'small muted' }, t('every portfolio')) : null);
      });
      body.appendChild(el('tr', null, el('td', { class: 'small' }, u.name, u.admin ? el('span', { class: 'muted' }, ' · ' + t('admin')) : null), boxes));
    });
    tbl.appendChild(body);
    box.append(el('div', { style: { overflowX: 'auto' } }, tbl),
      el('div', { class: 'small muted' }, t('Admins give roles but are held to the same rule: nobody reviews or approves a document they worked on.')));
  };
  draw();
  return el('section', { class: 'sec' }, box);
}
