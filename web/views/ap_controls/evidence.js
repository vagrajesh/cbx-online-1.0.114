// 1.0.104 (Builder R): Controls → Evidence — V12 the evidence pack per batch, payment run and account. What CBX filed at
// each SAP upload download (the upload and the review workbook, with their SHA-256) and what people attached (signed
// payment schedule, F110 proposal, funding request / N7, DocuSign memo, release audit report, POP, Council Liaison mail …,
// .msg and .eml too); each folder's checklist and what is missing; the zip per portfolio and month with its index; deletion
// only by an admin once the retention is over. Server: cbx/api/ap_review_api.py over cbx/ap/evidence.py.

export default {
  key: 'evidence',
  title: 'Evidence',
  roles: ['poster', 'reviewer', 'approver', 'recon_reviewer'],
  async render(root, kit) {
    const { api, act, el, clear, t, when } = kit;
    const st = { portfolio: kit.params.portfolio || '', month: kit.params.month || '', batch: Number(kit.params.batch) || null };
    const box = el('div', { class: 'stack', style: { gap: '18px' } });
    root.appendChild(box);
    const thisMonth = new Date().toISOString().slice(0, 7);
    const qs = (o) => new URLSearchParams(Object.fromEntries(Object.entries(o).filter(([, v]) => v !== '' && v !== null && v !== undefined))).toString();
    const kb = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB');

    async function load() {
      let r;
      try { r = await api('/api/ap/evidence?' + qs({ portfolio: st.portfolio, month: st.month, batch: st.batch })); }
      catch (e) { clear(box).appendChild(el('div', { class: 'small muted' }, e.detail || e.message || t('This view could not open.'))); return; }
      draw(r);
    }

    function bar() {
      const pf = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Portfolio') }, el('option', { value: '' }, t('Every portfolio')),
        (kit.portfolios || []).map((p) => el('option', { value: p.code, selected: p.code === st.portfolio }, `${p.code} · ${p.name}`)));
      pf.addEventListener('change', () => { st.portfolio = pf.value; load(); });
      const m = el('input', { class: 'in', type: 'month', value: st.month, style: { width: 'auto' }, 'aria-label': t('Month') });
      m.addEventListener('change', () => { st.month = m.value; load(); });
      const exp = el('a', { class: 'btn sm', href: '/api/ap/evidence/export?' + qs({ portfolio: st.portfolio, month: st.month || thisMonth }) },
        t('Export {p} {m} (zip)', { p: st.portfolio || t('the default portfolio'), m: st.month || thisMonth }));
      return el('div', { style: { display: 'flex', gap: '10px 14px', flexWrap: 'wrap', alignItems: 'center' } }, pf, m,
        st.batch ? el('button', { class: 'link small', type: 'button', onclick: () => { st.batch = null; load(); } }, t('Every folder')) : null, el('div', { class: 'grow' }), exp);
    }

    function missing(r) {
      if (!(r.missing || []).length) return null;
      return el('section', { class: 'sec', style: { margin: '0' } }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Missing')), el('span', { class: 'k' }, t('uploaded folders with checklist items not on file'))),
        r.missing.map((x) => el('div', { class: 'row' }, el('button', { class: 'link', type: 'button', onclick: () => { st.batch = x.batch_id; load(); } }, x.batch),
          el('span', { class: 'small muted' }, `${x.portfolio} · ${t('uploaded {when}', { when: when(x.first_upload) })}`), el('span', { class: 'small', style: { color: 'var(--warn)' } }, x.missing.join(', ')))));
    }

    function checklist(r) {
      const b = r.batch;
      if (!b) return null;
      return el('section', { class: 'sec', style: { margin: '0' } }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, b.batch), el('span', { class: 'k' }, t('{p} checklist', { p: b.portfolio }))),
        el('div', { class: 'stack', style: { gap: '4px' } }, b.items.map((x) => el('div', { class: 'small', style: { color: x.missing ? 'var(--warn)' : 'var(--ink)' } }, (x.missing ? '○ ' : '● ') + x.type + (x.missing ? ' — ' + t('missing') : ` (${x.n})`)))),
        b.uploaded ? null : el('div', { class: 'small muted' }, t('Not uploaded yet: CBX files the upload and the review workbook at the download.')));
    }

    function attachForm(r) {
      const kind = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('Attach to') }, [['batch', t('A folder')], ['run', t('A payment run')], ['account', t('A utility account')]].map(([v, l]) => el('option', { value: v }, l)));
      const batch = el('select', { class: 'in', style: { width: 'auto', maxWidth: '100%' }, 'aria-label': t('Folder') }, (r.recent_batches || []).map((b) => el('option', { value: String(b.id), selected: b.id === st.batch }, b.name)));
      const run = el('input', { class: 'in mono hide', placeholder: t('Payment run file no (from its dialog)'), style: { width: '200px' } });
      const acct = el('input', { class: 'in mono hide', placeholder: t('Account number'), style: { width: '200px' } });
      kind.addEventListener('change', () => { batch.classList.toggle('hide', kind.value !== 'batch'); run.classList.toggle('hide', kind.value !== 'run'); acct.classList.toggle('hide', kind.value !== 'account'); });
      const type = el('select', { class: 'in', style: { width: 'auto' }, 'aria-label': t('What it is') }, el('option', { value: '' }, t('What it is')), (r.types || []).filter((x) => !['SAP upload file', 'Review workbook'].includes(x)).map((x) => el('option', { value: x }, x)));
      const period = el('input', { class: 'in', type: 'month', value: st.month || thisMonth, style: { width: 'auto' }, 'aria-label': t('Month it belongs to') });
      const note = el('input', { class: 'in', placeholder: t('Note (optional)'), style: { flex: '1 1 220px' } });
      const file = el('input', { type: 'file', accept: '.pdf,.msg,.eml,.xlsx,.xlsm,.xls,.csv,.docx,.doc,.png,.jpg,.jpeg,.txt,.zip' });
      const go = el('button', { class: 'btn sm primary', type: 'button', onclick: async () => {
        if (!file.files.length) { kit.toast(t('Pick the file.')); return; }
        const fd = new FormData();
        fd.append('file', file.files[0], file.files[0].name);
        fd.append('type', type.value); fd.append('note', note.value); fd.append('period', period.value);
        if (kind.value === 'batch') fd.append('batch_id', batch.value); else if (kind.value === 'run') fd.append('run_item_id', run.value); else fd.append('account', acct.value);
        try { await act(() => api('/api/ap/evidence', { form: fd }), t('On file.')); load(); kit.refresh(); } catch (e) { /* said */ }
      } }, t('Attach'));
      return el('section', { class: 'sec', style: { margin: '0' } }, el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('Attach evidence')), el('span', { class: 'k' }, t('PDF, Outlook mail (.msg, .eml), Excel, Word, pictures'))),
        el('div', { style: { display: 'flex', gap: '8px 10px', flexWrap: 'wrap', alignItems: 'center' } }, kind, batch, run, acct, type, period),
        el('div', { style: { display: 'flex', gap: '8px 10px', flexWrap: 'wrap', alignItems: 'center', marginTop: '8px' } }, file, note, go));
    }

    function register(r) {
      const tbl = el('table', { class: 'tbl' });
      tbl.appendChild(el('thead', null, el('tr', null, [t('What'), t('Stage'), t('For'), t('File'), t('By'), t('When'), t('SHA-256'), t('Kept until'), ''].map((h) => el('th', null, h)))));
      const body = el('tbody');
      if (!r.evidence.length) body.appendChild(el('tr', null, el('td', { colspan: 9, class: 'small muted' }, t('Nothing on file here yet.'))));
      r.evidence.forEach((e) => body.appendChild(el('tr', null, el('td', { class: 'small' }, e.type, e.note ? el('div', { class: 'muted' }, e.note) : null), el('td', { class: 'small' }, e.stage),
        el('td', { class: 'small' }, e.batch ? e.batch : e.run_item_id ? t('Payment run {n}', { n: e.run_item_id }) : e.account ? t('Account {a}', { a: e.account }) : '', el('div', { class: 'muted mono' }, `${e.portfolio} · ${e.period}`)),
        el('td', { class: 'small', style: { maxWidth: '240px', overflowWrap: 'anywhere' } }, el('a', { class: 'link', href: `/api/ap/evidence/${e.id}/file` }, e.name), el('div', { class: 'muted' }, kb(e.size))),
        el('td', { class: 'small' }, e.source === 'download' ? t('{name} (download)', { name: e.by }) : e.by), el('td', { class: 'small', style: { whiteSpace: 'nowrap' } }, when(e.at)),
        el('td', { class: 'mono small', title: e.sha256 }, e.sha256.slice(0, 12) + '…'), el('td', { class: 'small', style: { whiteSpace: 'nowrap' } }, e.keep_until),
        el('td', null, kit.isAdmin && e.deletable ? el('button', { class: 'link small', type: 'button', onclick: async () => {
          if (!confirm(t('Delete this evidence for good?'))) return;
          try { await act(() => api(`/api/ap/evidence/${e.id}`, { method: 'DELETE' }), t('Deleted.')); load(); } catch (x) { /* said */ }
        } }, t('Delete')) : null))));
      tbl.appendChild(body);
      return el('div', { style: { overflowX: 'auto' } }, tbl);
    }

    function draw(r) {
      clear(box).append(bar(), ...[missing(r), checklist(r), attachForm(r)].filter(Boolean),
        el('div', { class: 'sec-h' }, el('h2', { class: 'h2' }, t('On file')), el('span', { class: 'k' }, t('{n} pieces', { n: r.evidence.length }))), register(r));
    }

    load();
  },
};
