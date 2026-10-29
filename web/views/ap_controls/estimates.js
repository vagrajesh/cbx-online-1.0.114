// 1.0.104 (Builder U, V08): Controls → Estimates — accounts whose bill did not come (with the value proposed from their
// bill history), the estimates waiting for a second person's approval, the register with its true-ups, accounts on
// estimates for months running (escalate to Council Liaison), and the monthly report for the client.

export default {
  key: 'estimates',
  title: 'Estimates',
  roles: ['capturer', 'approver'],
  async render(root, kit) {
    const { api, act, el, clear, t, money, when, params, portfolios, openCapture } = kit;
    let pf = params.portfolio || '', status = params.status === 'prepared' ? 'prepared' : 'open';
    const box = el('div', { class: 'stack', style: { gap: '22px' } });
    const q = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== undefined && v !== null))).toString();
    const sec = (title, sub, ...kids) => el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, title), sub ? el('span', { class: 'k' }, sub) : null), kids);
    const row = (...kids) => el('div', { style: { display: 'flex', gap: '8px 14px', flexWrap: 'wrap', alignItems: 'baseline', paddingBottom: '8px', borderBottom: '1px solid var(--rule)' } }, kids);
    const done = () => { draw(); kit.refresh(); };

    function prepareForm(m, bases) {
      const basis = el('select', { class: 'in', style: { width: 'auto' } }, bases.map((b) => el('option', { value: b.key, selected: b.key === m.proposal.basis }, b.label)));
      const value = el('input', { class: 'in mono', placeholder: money(m.proposal.value), style: { width: '160px' } });
      const note = el('input', { class: 'in', placeholder: t('Note (needed when you change the value)') });
      const shown = el('span', { class: 'small' }, money(m.proposal.value) + ' ' + t('excl. VAT'));
      basis.addEventListener('change', async () => {
        try { const p = await api('/api/ap/estimates/propose?' + q({ account: m.account_key, period: m.period, basis: basis.value })); shown.textContent = money(p.value) + ' ' + t('excl. VAT') + (p.note ? ' — ' + p.note : ''); value.placeholder = money(p.value); } catch (e) { /* the server's words on the button */ }
      });
      return el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', flex: '1 1 100%' } }, basis, shown, value, el('div', { style: { flex: '1 1 200px' } }, note),
        el('button', { class: 'btn sm primary', type: 'button', onclick: async () => {
          try { await act(() => api('/api/ap/estimates', { body: { account_key: m.account_key, period: m.period, basis: basis.value, value: value.value || null, note: note.value } }), t('Prepared: a second person approves it.')); done(); } catch (e) { /* told */ }
        } }, t('Prepare')));
    }

    async function draw() {
      let got;
      try { got = await api('/api/ap/estimates?' + q({ status, portfolio: pf })); } catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, t('Only the AP team\'s reviewers, approvers and admins see this.'))); return; }
      clear(box);
      const me = got.me;

      // waiting for approval
      const waiting = got.estimates.filter((e) => e.status === 'prepared');
      box.appendChild(sec(t('Waiting for approval'), t('a second person approves; then Pip posts it as EST-<account>-<yyyymm>'),
        waiting.length ? waiting.map((e) => row(
          el('div', { style: { flex: '1 1 240px' } }, el('strong', null, e.reference), el('div', { class: 'small muted' }, [e.municipality, e.site, e.portfolio].filter(Boolean).join(' · '))),
          el('div', { class: 'small', style: { flex: '2 1 280px' } }, `${money(e.value)} ${t('excl. VAT')} · ${e.basis_words} (${(e.workings.periods || []).join(', ')})`,
            el('div', { class: 'muted' }, t('Prepared by {who} {at}', { who: e.prepared_by, at: when(e.prepared_at) }) + (e.note ? ' — ' + e.note : ''))),
          e.prepared_by_id === me ? el('span', { class: 'small muted' }, t('You prepared it: someone else approves.')) :
            el('button', { class: 'btn sm primary', type: 'button', onclick: async () => { try { await act(() => api(`/api/ap/estimates/${e.id}/approve`, { body: {} }), t('Approved: in the folder "Estimates {p}".', { p: e.period })); done(); } catch (x) { /* told */ } } }, t('Approve')),
          el('button', { class: 'link small', type: 'button', onclick: async () => { const why = window.prompt(t('Why is it rejected?')); if (!why) return; try { await act(() => api(`/api/ap/estimates/${e.id}/reject`, { body: { reason: why } }), t('Rejected.')); done(); } catch (x) { /* told */ } } }, e.prepared_by_id === me ? t('Withdraw') : t('Reject'))))
          : el('div', { class: 'small muted' }, t('Nothing waits for approval.'))));

      // escalate
      if (got.escalations.length) {
        box.appendChild(sec(t('Estimated for months running'), t('escalate to Council Liaison'), got.escalations.map((r) => row(
          el('div', { style: { flex: '1 1 240px' } }, el('strong', null, r.account), el('div', { class: 'small muted' }, [r.municipality, r.site, r.portfolio].filter(Boolean).join(' · '))),
          el('div', { class: 'small', style: { flex: '2 1 260px' } }, t('{n} estimates running ({p}), {v}', { n: r.count, p: r.periods.join(', '), v: money(r.value) })),
          r.escalated_at ? el('span', { class: 'small muted' }, t('Escalated by {who} {at}', { who: r.escalated_by, at: when(r.escalated_at) }))
            : el('button', { class: 'btn sm', type: 'button', onclick: async () => { const note = window.prompt(t('Note for the record (optional)'), '') ?? null; if (note === null) return; try { await act(() => api('/api/ap/estimates/escalate', { body: { account_key: r.account_key, note } }), t('Escalated.')); done(); } catch (x) { /* told */ } } }, t('Escalated to Council Liaison'))))));
      }

      // missing bills
      box.appendChild(sec(t('Bills that did not come'), t('active accounts past their usual bill day'),
        got.missing.length ? got.missing.map((m) => {
          const slot = el('div', { class: 'hide', style: { flex: '1 1 100%' } });
          return row(el('div', { style: { flex: '1 1 240px' } }, el('strong', null, m.account), el('div', { class: 'small muted' }, [m.municipality, m.site, m.portfolio].filter(Boolean).join(' · '))),
            el('div', { class: 'small', style: { flex: '2 1 260px' } }, t('{p} not in (due {d}); last bill {l}', { p: m.period, d: m.due, l: m.last_period }) + (m.missing > 1 ? ' · ' + t('{n} months missing', { n: m.missing }) : ''),
              el('div', { class: 'muted' }, t('Proposed {v} excl. VAT ({b}: {ps})', { v: money(m.proposal.value), b: m.proposal.basis_words, ps: (m.proposal.workings.periods || []).join(', ') }))),
            el('button', { class: 'link small', type: 'button', onclick: () => { if (!slot.firstChild) slot.appendChild(prepareForm(m, got.bases)); slot.classList.toggle('hide'); } }, t('Estimate')), slot);
        }) : el('div', { class: 'small muted' }, t('No bill is missing.'))));

      // the register
      const stSel = el('select', { class: 'in', style: { width: 'auto' } }, [['open', t('Open')], ['approved', t('Approved, not trued up')], ['trued_up', t('Trued up')], ['rejected', t('Rejected')], ['', t('All')]]
        .map(([k, l]) => el('option', { value: k, selected: k === status }, l)));
      stSel.addEventListener('change', () => { status = stSel.value; draw(); });
      const month = el('input', { class: 'in', type: 'month', style: { width: 'auto' } });
      const rep = el('a', { class: 'link small', href: '/api/ap/estimates/report.xlsx' }, t('Monthly report (count and value per portfolio)'));
      month.addEventListener('change', () => { rep.href = '/api/ap/estimates/report.xlsx?' + q({ month: month.value }); });
      const tbl = el('table', { class: 'tbl' }, el('thead', null, el('tr', null, ['Reference', 'Municipality', 'Value', 'Basis', 'Status', 'Prepared', 'Approved', 'Actual', 'Difference', ''].map((h) => el('th', null, t(h))))));
      tbl.appendChild(el('tbody', null, got.estimates.filter((e) => status !== 'open' || e.status !== 'prepared').map((e) => el('tr', null,
        el('td', { class: 'mono small' }, e.reference), el('td', { class: 'small' }, e.municipality), el('td', { class: 'mono small' }, money(e.value)), el('td', { class: 'small' }, e.basis_words),
        el('td', { class: 'small' }, e.status + (e.true_up ? ` (${e.true_up})` : '')), el('td', { class: 'small' }, e.prepared_by), el('td', { class: 'small' }, e.approved_by),
        el('td', { class: 'mono small' }, money(e.actual_value)), el('td', { class: 'mono small' }, money(e.difference)),
        el('td', { class: 'small' }, e.capture_id ? el('button', { class: 'link small', type: 'button', onclick: () => openCapture(e.capture_id) }, t('Document')) : '',
          e.actual_capture_id ? [' · ', el('button', { class: 'link small', type: 'button', onclick: () => openCapture(e.actual_capture_id) }, t('Real bill'))] : null)))));
      box.appendChild(sec(t('Register'), null, el('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '10px' } }, stSel, el('div', { class: 'grow' }), month, rep),
        el('div', { style: { overflowX: 'auto' } }, tbl)));
    }

    const pfSel = el('select', { class: 'in', style: { width: 'auto' } }, [['', t('Every portfolio')], ...(portfolios || []).map((p) => [p.code, p.code])].map(([k, l]) => el('option', { value: k, selected: k === pf }, l)));
    pfSel.addEventListener('change', () => { pf = pfSel.value; draw(); });
    root.append(el('div', { style: { marginBottom: '12px' } }, pfSel), box);
    draw();
  },
};
