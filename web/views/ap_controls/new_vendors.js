// 1.0.104 (Builder D): Controls → New vendors (V01) — SAP vendors created or changed from the go-live day, each reviewed by
// a person who neither created it in SAP nor attached its creation request: the SAP record beside the request and the bank
// letter, a checklist, Pass or Fail with a reason. Until it passes, Tess stops the vendor's invoices.
// Server: cbx/api/ap_vendor_dups_api.py over cbx/ap/vendor_review.py.

const STATE_PILL = { open: 'warn', passed: 'good', failed: 'crit' };
const FIELDS = [['name', 'Name'], ['vat', 'VAT number'], ['tax1', 'Tax number 1'], ['city', 'City'], ['country', 'Country'], ['payment_terms', 'Payment terms'],
  ['account_group', 'Account group'], ['blocked', 'Blocked']];

export default {
  key: 'new_vendors',
  title: 'New vendors',
  roles: ['reviewer'],
  async render(root, kit) {
    const { api, act, el, clear, t } = kit;
    const st = { state: kit.params.state || 'open', q: '', open: Number(kit.params.review || 0) || 0 };
    let got = null, timer = null;
    const box = el('div', { class: 'stack', style: { gap: '18px' } });
    const detail = el('div');
    root.append(box, detail);
    const qs = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined))).toString();
    const dmy = (iso) => iso ? new Date(iso.length === 10 ? iso + 'T00:00:00' : iso).toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
    const pill = (s) => el('span', { class: `pill ${STATE_PILL[s] || 'neutral'}` }, { open: t('To review'), passed: t('Passed'), failed: t('Failed') }[s] || s);

    async function load() {
      try { got = await api('/api/ap/vendor-reviews?' + qs({ state: st.state, q: st.q })); }
      catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, e.detail || e.message || t('This view could not open.'))); return; }
      draw();
      if (st.open) openReview(st.open);
    }

    function draw() {
      clear(box);
      const sf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('State') }, [['open', t('To review ({n})', { n: got.counts.open })], ['failed', t('Failed ({n})', { n: got.counts.failed })],
        ['passed', t('Passed ({n})', { n: got.counts.passed })], ['all', t('Every review')]].map(([v, l]) => el('option', { value: v, selected: v === st.state }, l)));
      sf.addEventListener('change', () => { st.state = sf.value; load(); });
      const q = el('input', { class: 'in', placeholder: t('Search vendor number or name'), value: st.q, style: { flex: '1 1 200px' } });
      q.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { st.q = q.value.trim(); load(); }, 300); });
      box.append(el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('New vendors to review')), el('span', { class: 'k' }, t('created or changed in SAP from the go-live day; their invoices wait until a second person passes them'))),
        el('div', { style: { display: 'flex', gap: '8px 10px', flexWrap: 'wrap', alignItems: 'center' } }, sf, q, el('a', { class: 'link small', href: '/api/ap/vendor-reviews/report' }, t('Report (review log, errors by month)'))));
      if (!got.rows.length) { box.appendChild(el('div', { class: 'small muted' }, st.state === 'open' ? t('No vendor waits for a review.') : t('Nothing here.'))); return; }
      const tbl = el('table', { class: 'tbl' }, el('thead', null, el('tr', null, [t('Vendor'), t('Name'), t('What'), t('Created by (SAP)'), t('Portfolio'), t('Waiting'), t('Files'), t('State')].map((h) => el('th', null, h)))));
      const body = el('tbody');
      got.rows.forEach((r) => {
        const tr = el('tr', { style: { cursor: 'pointer' }, onclick: () => openReview(r.id) },
          el('td', { class: 'mono' }, r.vendor), el('td', null, r.name),
          el('td', { class: 'small' }, r.kind === 'new' ? t('New, created {d}', { d: dmy(r.created_on) }) : t('Changed {d}', { d: dmy(r.changed_on) })),
          el('td', { class: 'mono small' }, r.created_by || ''), el('td', { class: 'small' }, r.portfolio),
          el('td', { class: 'small' }, r.state === 'open' ? (r.age_days === 1 ? t('1 day') : t('{n} days', { n: r.age_days })) : dmy(r.reviewed_at)),
          el('td', { class: 'small' }, [r.has_request ? t('request') : '', r.has_letter ? t('letter') : ''].filter(Boolean).join(' · ') || el('span', { class: 'muted' }, t('none yet'))),
          el('td', null, pill(r.state)));
        body.appendChild(tr);
      });
      tbl.appendChild(body);
      box.appendChild(el('div', { style: { overflowX: 'auto' } }, tbl));
    }

    async function openReview(id) {
      st.open = id;
      let r;
      try { r = await api(`/api/ap/vendor-reviews/${id}`); } catch (e) { clear(detail).appendChild(el('div', { class: 'small muted' }, e.detail || e.message)); return; }
      clear(detail);
      const snap = r.snapshot || {};
      const rec = el('table', { class: 'tbl' }, el('tbody', null, FIELDS.map(([k, l]) => el('tr', null, el('td', { class: 'k small' }, t(l)),
        el('td', { class: k === 'vat' || k === 'tax1' ? 'mono' : '' }, k === 'blocked' ? (snap[k] ? t('yes') : t('no')) : String(snap[k] === null || snap[k] === undefined ? '' : snap[k]))))),
        el('tr', null, el('td', { class: 'k small' }, t('Company codes')), el('td', { class: 'mono small' }, r.company_codes || t('none in SAP\'s vendor ↔ company list'))),
        el('tr', null, el('td', { class: 'k small' }, t('Created')), el('td', { class: 'small' }, `${dmy(r.created_on)} ${r.created_by ? '· ' + r.created_by : ''}`)),
        r.changed_on ? el('tr', null, el('td', { class: 'k small' }, t('Changed')), el('td', { class: 'small' }, dmy(r.changed_on))) : null);
      const left = el('section', { class: 'sec stack', style: { gap: '10px', flex: '1 1 340px', minWidth: '0' } },
        el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, `${r.vendor} · ${r.name}`), pill(r.state)),
        el('div', { class: 'small muted' }, t('SAP\'s record as the vendor list gave it')), rec,
        (r.changes || []).length ? el('div', { class: 'stack', style: { gap: '4px' } }, el('div', { class: 'k' }, t('What changed')),
          r.changes.map((c) => el('div', { class: 'small' }, `${c.field}: ${c.from === null || c.from === undefined ? '' : c.from} → ${c.to}`))) : null,
        (r.accounts || []).length ? el('div', { class: 'stack', style: { gap: '4px' } }, el('div', { class: 'k' }, t('On the utilities list')),
          r.accounts.map((a) => el('div', { class: 'small' }, `${a.account} → ${a.site || '?'} (${a.cost_centre || '?'}) · ${a.municipality || ''} ${a.company_code || ''}`))) : null);

      const fileRow = (kind, label, f, by) => {
        const inp = el('input', { type: 'file', class: 'hide', accept: '.pdf,.png,.jpg,.jpeg,.msg,.eml,.docx,.doc,.xlsx,.xls,.txt' });
        inp.addEventListener('change', async () => {
          if (!inp.files.length) return;
          const fd = new FormData(); fd.append('kind', kind); fd.append('file', inp.files[0]);
          try { await act(() => api(`/api/ap/vendor-reviews/${r.id}/files`, { form: fd }), t('Attached.')); load(); } catch (e) { /* shown */ }
        });
        return el('div', { style: { display: 'flex', gap: '8px', alignItems: 'baseline', flexWrap: 'wrap' } }, el('span', { class: 'k', style: { minWidth: '170px' } }, label),
          f ? el('a', { class: 'link small', href: `/api/ap/vendor-reviews/${r.id}/files/${f.id}`, target: '_blank', rel: 'noopener' }, f.name) : el('span', { class: 'small muted' }, t('not attached')),
          by ? el('span', { class: 'small muted' }, t('by {name}', { name: by })) : null,
          r.state !== 'passed' ? el('button', { class: 'link small', type: 'button', onclick: () => inp.click() }, f ? t('Replace') : t('Attach')) : null, inp);
      };
      const right = el('section', { class: 'sec stack', style: { gap: '10px', flex: '1 1 340px', minWidth: '0' } },
        el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('The review')), el('span', { class: 'k' }, t('against the request and the bank letter'))),
        fileRow('request', t('Creation request'), r.request, r.request_by), fileRow('letter', t('Bank confirmation letter'), r.letter, r.letter_by));
      if (r.state === 'passed') {
        right.appendChild(el('div', { class: 'small' }, t('Passed by {name} on {d}.', { name: r.reviewed_by, d: dmy(r.reviewed_at) })));
      } else {
        if (r.state === 'failed') right.appendChild(el('div', { class: 'small', style: { color: 'var(--bad)' } }, t('Failed by {name} on {d}: {why}', { name: r.reviewed_by, d: dmy(r.reviewed_at), why: r.reason })));
        const picks = {};
        const rows = r.checks.map((c) => {
          const s = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': c.label }, [['', t('Pick')], ['ok', t('Agrees')], ['wrong', t('Wrong')], ['na', t('Not applicable')]]
            .map(([v, l]) => el('option', { value: v, selected: (r.checklist || {})[c.key] === v }, l)));
          picks[c.key] = s;
          return el('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--rule)' } },
            el('span', { class: 'small' }, t(c.label)), s);
        });
        const why = el('textarea', { class: 'in', rows: '2', placeholder: t('Reason (needed to fail it)') });
        why.value = r.state === 'failed' ? '' : (r.reason || '');
        const send = async (outcome) => {
          const checklist = {}; Object.entries(picks).forEach(([k, s]) => { if (s.value) checklist[k] = s.value; });
          try { await act(() => api(`/api/ap/vendor-reviews/${r.id}/decide`, { body: { outcome, checklist, reason: why.value } }), outcome === 'pass' ? t('Passed. Its invoices go on.') : t('Failed. Its invoices stay red.')); }
          catch (e) { return; }
          load(); if (kit.refresh) kit.refresh();
        };
        right.append(el('div', { class: 'stack', style: { gap: '0' } }, rows), why);
        if ((r.blockers || []).length) right.appendChild(el('div', { class: 'small', style: { color: 'var(--warn)' } }, r.blockers.join(' ')));
        else right.appendChild(el('div', { style: { display: 'flex', gap: '8px' } },
          el('button', { class: 'btn sm primary', type: 'button', onclick: () => send('pass') }, t('Pass')),
          el('button', { class: 'btn sm', type: 'button', onclick: () => send('fail') }, t('Fail'))));
      }
      detail.appendChild(el('div', { style: { display: 'flex', gap: '18px', flexWrap: 'wrap', marginTop: '18px' } }, left, right));
    }

    await load();
    return () => clearTimeout(timer);
  },
};
