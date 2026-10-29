// 1.0.104 (Builder U, V07): Controls → Variances & disputes — utility bills Tess found out of line with the account's
// earlier bills or its tariff: Accept (with a reason, kept as evidence) or Dispute (held out of the SAP upload unless paid
// under dispute); the disputes register; check-meter readings (BCX CSV) and the sites without a check meter.

const STATES = [['open', 'To decide'], ['accepted', 'Accepted'], ['disputed', 'Disputed'], ['all', 'All']];

export default {
  key: 'variances',
  title: 'Variances & disputes',
  roles: ['reviewer', 'capturer'],
  async render(root, kit) {
    const { api, act, el, clear, t, money, when, params, openCapture, portfolios } = kit;
    let state = params.state || 'open', pf = params.portfolio || '';
    const list = el('div', { class: 'stack', style: { gap: '10px' } });
    const disp = el('div', { class: 'stack', style: { gap: '8px' } });
    const meters = el('div', { class: 'stack', style: { gap: '8px' } });
    const fmt = (v, d = 0) => (v === null || v === undefined || v === '' ? '' : Number(v).toLocaleString('en-ZA', { maximumFractionDigits: d }));
    const sel = (opts, v, on) => { const s = el('select', { class: 'in', style: { width: 'auto' } }, opts.map(([k, l]) => el('option', { value: k, selected: k === v }, l))); s.addEventListener('change', () => on(s.value)); return s; };
    const q = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== undefined && v !== null))).toString();

    async function detail(b, slot) {
      let d;
      try { d = await api(`/api/ap/variances/${b.id}`); } catch (e) { return; }
      clear(slot);
      const tbl = el('table', { class: 'tbl' }, el('thead', null, el('tr', null, ['Period', 'Read', 'kWh', 'R excl. VAT', 'R/kWh', 'A/E', 'Tariff'].map((h) => el('th', null, t(h))))));
      const rows = [...d.earlier, { ...d, this: true }];
      tbl.appendChild(el('tbody', null, rows.map((r) => {
        const elec = (r.amounts || {}).electricity;
        const rate = elec && r.kwh ? elec / r.kwh : null;
        return el('tr', { style: r.this ? { fontWeight: '600' } : null }, el('td', { class: 'mono small' }, r.period + (r.this ? ' ' + t('(this bill)') : '')),
          el('td', { class: 'small' }, [r.read_start, r.read_end].filter(Boolean).join(' – ')), el('td', { class: 'mono small' }, fmt(r.kwh)),
          el('td', { class: 'mono small' }, money(r.total_excl)), el('td', { class: 'mono small' }, rate ? rate.toFixed(4) : ''), el('td', { class: 'small' }, r.reading_type || ''),
          el('td', { class: 'small' }, r.tariff || ''));
      })));
      slot.appendChild(el('div', { style: { overflowX: 'auto' } }, tbl));
      if (d.decision) {
        slot.appendChild(el('div', { class: 'small' }, d.decision === 'accepted' ? t('Accepted by {who} {at}: {why}', { who: d.decided_by, at: when(d.decided_at), why: d.reason })
          : t('Disputed by {who} {at} (dispute #{id}{ref}): {why}', { who: d.decided_by, at: when(d.decided_at), id: d.dispute_id, ref: d.dispute && d.dispute.municipal_ref ? ', ref ' + d.dispute.municipal_ref : '', why: d.reason })));
        return;
      }
      if (b.uploaded) slot.appendChild(el('div', { class: 'small muted' }, t('This bill went out in an upload already: a dispute still goes in the register.')));
      const why = el('input', { class: 'in', placeholder: t('Why the variance is fine (kept as evidence)') });
      const dr = el('input', { class: 'in', placeholder: t('What is disputed') }), amt = el('input', { class: 'in mono', placeholder: t('Amount (blank = above the average)'), style: { width: '200px' } });
      const ref = el('input', { class: 'in', placeholder: t('Municipal reference'), style: { width: '180px' } }), own = el('input', { class: 'in', placeholder: t('Council Liaison owner'), style: { width: '200px' } });
      const pud = el('input', { type: 'checkbox' });
      slot.append(
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, el('div', { style: { flex: '1 1 260px' } }, why),
          el('button', { class: 'btn sm primary', type: 'button', onclick: async () => { try { await act(() => api(`/api/ap/variances/${b.id}/accept`, { body: { reason: why.value } }), t('Accepted.')); draw(); kit.refresh(); } catch (e) { /* told */ } } }, t('Accept'))),
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, el('div', { style: { flex: '1 1 260px' } }, dr), amt, ref, own,
          el('label', { class: 'small', style: { display: 'flex', gap: '6px', alignItems: 'center' } }, pud, t('pay under dispute')),
          el('button', { class: 'btn sm', type: 'button', onclick: async () => { try { await act(() => api(`/api/ap/variances/${b.id}/dispute`, { body: { reason: dr.value, amount: amt.value || null, municipal_ref: ref.value, owner: own.value, pay_under_dispute: pud.checked } }), t('Disputed.')); draw(); kit.refresh(); } catch (e) { /* told */ } } }, t('Dispute'))));
    }

    async function drawBills() {
      let got;
      try { got = await api('/api/ap/variances?' + q({ state, portfolio: pf })); } catch (e) { clear(list).appendChild(el('div', { class: 'small muted' }, t('Only the AP reviewers and admins see this.'))); return; }
      clear(list);
      if (!got.bills.length) { list.appendChild(el('div', { class: 'small muted' }, state === 'open' ? t('No bill waits for a decision.') : t('Nothing here.'))); return; }
      got.bills.forEach((b) => {
        const slot = el('div', { class: 'stack hide', style: { gap: '8px', flex: '1 1 100%' } });
        list.appendChild(el('div', { class: 'row', style: { display: 'flex', gap: '8px 14px', flexWrap: 'wrap', alignItems: 'baseline', paddingBottom: '8px', borderBottom: '1px solid var(--rule)' } },
          el('div', { style: { flex: '1 1 240px', minWidth: '0' } },
            b.capture_id ? el('button', { class: 'link', type: 'button', style: { fontWeight: '600', textAlign: 'start', overflowWrap: 'anywhere' }, onclick: () => openCapture(b.capture_id) }, b.file || b.account) : el('strong', null, b.account),
            el('div', { class: 'small muted' }, [b.municipality, b.site, t('account {a}', { a: b.account }), b.period, b.portfolio].filter(Boolean).join(' · '))),
          el('div', { class: 'small', style: { flex: '2 1 320px' } }, b.findings.map((f) => el('div', null, f.text)),
            b.decision ? el('div', { class: 'muted' }, b.decision === 'accepted' ? t('Accepted by {who}', { who: b.decided_by }) : t('Disputed (#{id})', { id: b.dispute_id })) : null),
          el('button', { class: 'link small', type: 'button', onclick: () => { slot.classList.toggle('hide'); if (!slot.classList.contains('hide')) detail(b, slot); } }, b.decision ? t('Look') : t('Decide')),
          slot));
      });
    }

    async function drawDisputes() {
      let got;
      try { got = await api('/api/ap/disputes?status=open' + (pf ? '&portfolio=' + encodeURIComponent(pf) : '')); } catch (e) { clear(disp); return; }
      clear(disp);
      if (!got.disputes.length) { disp.appendChild(el('div', { class: 'small muted' }, t('No open disputes.'))); return; }
      got.disputes.forEach((d) => {
        const res = el('input', { class: 'in', placeholder: t('How it ended') });
        const ref = el('input', { class: 'in', value: d.municipal_ref, placeholder: t('Municipal reference'), style: { width: '170px' } });
        const own = el('input', { class: 'in', value: d.owner, placeholder: t('Council Liaison owner'), style: { width: '190px' } });
        const put = (body, ok) => act(() => api(`/api/ap/disputes/${d.id}`, { method: 'PUT', body }), ok).then(() => { draw(); kit.refresh(); }).catch(() => null);
        disp.appendChild(el('div', { class: 'stack', style: { gap: '6px', paddingBottom: '8px', borderBottom: '1px solid var(--rule)' } },
          el('div', { class: 'small' }, el('strong', null, `#${d.id} `), [d.municipality, t('account {a}', { a: d.account_key }), d.period, money(d.amount), t('{n} days open', { n: d.age_days || 0 })].filter(Boolean).join(' · '),
            d.pay_under_dispute ? el('span', { class: 'muted' }, ' · ' + t('paid under dispute')) : el('span', { class: 'muted' }, ' · ' + t('held out of the upload'))),
          el('div', { class: 'small muted' }, d.reason),
          el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, ref, own,
            el('button', { class: 'link small', type: 'button', onclick: () => put({ municipal_ref: ref.value, owner: own.value }, t('Saved.')) }, t('Save')),
            el('button', { class: 'link small', type: 'button', onclick: () => put({ pay_under_dispute: !d.pay_under_dispute }, t('Saved.')) }, d.pay_under_dispute ? t('Hold it again') : t('Pay under dispute'))),
          el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' } }, el('div', { style: { flex: '1 1 240px' } }, res),
            el('button', { class: 'btn sm', type: 'button', onclick: () => put({ status: 'resolved', resolution: res.value }, t('Resolved.')) }, t('Resolved')),
            el('button', { class: 'link small', type: 'button', onclick: () => put({ status: 'withdrawn', resolution: res.value }, t('Withdrawn: the variance is open again.')) }, t('Withdraw')))));
      });
    }

    async function drawMeters() {
      clear(meters);
      const file = el('input', { type: 'file', accept: '.csv,.xlsx', hidden: true });
      file.addEventListener('change', async () => {
        const f = file.files[0]; file.value = '';
        if (!f) return;
        const fd = new FormData(); fd.append('file', f);
        try { const r = await act(() => api('/api/ap/check-meters', { form: fd })); kit.toast(t('{n} readings loaded. Bills are compared at their next test.', { n: r.readings })); drawMeters(); } catch (e) { /* told */ }
      });
      let rank = null;
      try { rank = await api('/api/ap/check-meters/ranking?' + q({ portfolio: pf })); } catch (e) { /* not shown */ }
      meters.append(el('div', { class: 'small muted' }, t('A CSV with site (or account), period and kWh: the billed kWh is compared with it.')),
        el('div', { style: { display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'center' } },
          el('button', { class: 'btn sm', type: 'button', onclick: () => file.click() }, t('Load check-meter readings')), file,
          el('a', { class: 'link small', href: '/api/ap/check-meters/ranking.xlsx?' + q({ portfolio: pf }) }, t('Sites without a check meter (ranked)')),
          rank ? el('span', { class: 'small muted' }, t('{n} sites with bills and no check meter', { n: rank.total })) : null));
    }

    function draw() { drawBills(); drawDisputes(); drawMeters(); }

    const pfs = [['', t('Every portfolio')], ...(portfolios || []).map((p) => [p.code, p.code])];
    root.append(
      el('section', { class: 'sec', style: { marginBottom: '22px' } },
        el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Utility bills out of line')), el('span', { class: 'k' }, t('against the account\'s earlier bills, its tariff and its check meter'))),
        el('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '10px' } },
          sel(STATES.map(([k, l]) => [k, t(l)]), state, (v) => { state = v; drawBills(); }), sel(pfs, pf, (v) => { pf = v; draw(); })),
        list),
      el('section', { class: 'sec', style: { marginBottom: '22px' } },
        el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Open disputes')), el('a', { class: 'link small', href: '/api/ap/disputes.xlsx' }, t('Download the register'))), disp),
      el('section', { class: 'sec' }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Check meters')), el('span', { class: 'k' }, t('Telkom: BCX readings'))), meters));
    draw();
  },
};
