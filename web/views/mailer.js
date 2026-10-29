// The Mailer (1.0.70): every mailbox CBX works with. Pick one on the left —
//   a support mailbox: its recent mail straight from Outlook and what became of each (a ticket, an answer,
//   skipped and why, not read yet); make a ticket from one the agents skipped, or put it on a ticket;
//   Mine: your own mailbox and its three agents (My mail);
//   Settings (owner/admins): mail on/off, how it goes out and from where, which mailboxes are read for tickets.
// 1.0.71 (owner/admins): Drafts — automatic mail waiting to be checked (change it, send it, throw it away);
//   Sent — what CBX sent lately; Ticket mails — the wording of every ticket mail; "check first" in Settings.
import { api } from '../api.js';
import { esc, toast, pill, fmtDate, confirmBox, el, modal, clip } from '../ui.js';
import mailDesk from './mail-desk.js';

const WHAT = { ticket: ['good'], reply: ['info'], attached: ['info'], skipped: ['neutral'], unread: ['warn'], other: ['neutral'] };

export default {
  title: 'Mailer',
  async render(root, ctx) {
    let d;
    try { d = await api.get('/api/mailer'); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Mailer</h1><p>${esc(e.detail || 'Could not load.')}</p></div></div>`; return; }
    // 1.0.83: opens on what needs you — the drafts — not on set-up
    const pick = ctx.params.box || (d.can_settings && d.drafts ? 'drafts' : d.boxes[0] ? 's:' + d.boxes[0].store : d.can_settings ? 'sent' : 'mine');
    const go = (box) => ctx.go('mailer', { box });
    const s = d.sending;
    const navBox = (b) => `<button class="mr-nav ${pick === 's:' + b.store ? 'on' : ''}" data-box="s:${esc(b.store)}"><span class="mr-dot ${b.on ? 'on' : ''}"></span><span class="t"><b>${esc(b.store)}</b><span>${b.on ? (b.last && !b.last.startsWith('ok') ? 'Problem — see it' : 'Reading for tickets') : 'Off'}</span></span></button>`;
    root.innerHTML = `<div class="head"><div><h1>Mailer</h1><p>Every mailbox CBX reads — what came in, and what became of it.</p></div>
        <div class="row" style="gap:8px;align-items:center">${s.enabled ? (s.ready ? pill(`Mail out: ${s.transport === 'outlook' ? 'Outlook' : 'SMTP'}${s.from ? ' · ' + s.from : ''}`, 'good') : pill('Mail out: not working', 'crit')) : pill('Mail out: off', 'neutral')}</div></div>
      <div class="mr-wrap">
        <div class="card mr-side">
          ${d.boxes.length || d.can_settings ? `<div class="eyebrow">Support mailboxes</div>${d.boxes.map(navBox).join('') || '<p class="small muted" style="margin:6px 0">None yet — add one in Mail set-up.</p>'}` : ''}
          ${d.mine ? `<div class="eyebrow" style="margin-top:12px">Mine</div><button class="mr-nav ${pick === 'mine' ? 'on' : ''}" data-box="mine"><span class="mr-dot ${d.mine.linked ? 'on' : ''}"></span><span class="t"><b>${esc(d.mine.linked ? d.mine.store : 'My mailbox')}</b><span>${d.mine.linked ? 'Scanner · Sorter · Writer' : 'Not linked yet'}</span></span></button>` : ''}
          ${d.can_settings ? `<div class="eyebrow" style="margin-top:12px">Going out</div>
            <button class="mr-nav ${pick === 'drafts' ? 'on' : ''}" data-box="drafts"><span class="mr-dot ${d.drafts ? 'warn' : ''}"></span><span class="t"><b>Drafts${d.drafts ? ` <span class="mr-count">${d.drafts}</span>` : ''}</b><span>${d.drafts ? 'Waiting for you to check' : 'Nothing waiting'}</span></span></button>
            <button class="mr-nav ${pick === 'sent' ? 'on' : ''}" data-box="sent"><span class="mr-dot"></span><span class="t"><b>Sent</b><span>What CBX sent lately</span></span></button>
            <div class="eyebrow" style="margin-top:12px">Set-up</div>
            <button class="mr-nav ${pick === 'templates' ? 'on' : ''}" data-box="templates"><span class="mr-dot gear"></span><span class="t"><b>Ticket mails</b><span>The wording of each mail</span></span></button>
            <button class="mr-nav ${pick === 'settings' ? 'on' : ''}" data-box="settings"><span class="mr-dot gear"></span><span class="t"><b>Mail set-up</b><span>Mail out · check first · mailboxes</span></span></button>` : ''}
        </div>
        <div class="mr-main" id="mr-main"></div></div>`;
    root.querySelectorAll('[data-box]').forEach((b) => (b.onclick = () => go(b.dataset.box)));
    const main = root.querySelector('#mr-main');
    if (pick === 'mine' && d.mine) return mailDesk.render.call(mailDesk, main, { ...ctx, params: {} });
    if (pick === 'settings' && d.can_settings) return settings(main, () => this.render(root, ctx));
    if (pick === 'drafts' && d.can_settings) return drafts(main, () => this.render(root, ctx));
    if (pick === 'sent' && d.can_settings) return sent(main);
    if (pick === 'templates' && d.can_settings) return templates(main);
    const b = d.boxes.find((x) => 's:' + x.store === pick);
    if (b) return supportBox(main, ctx, b);
    main.innerHTML = '<div class="card"><div class="bd empty">Pick a mailbox on the left.</div></div>';
  },
};

async function supportBox(main, ctx, b) {
  let days = 7, show = 'all', data = null;
  async function load() {
    main.innerHTML = '<div class="card"><div class="bd"><span class="spin"></span></div></div>';
    try { data = await api.get(`/api/mailer/box?store=${encodeURIComponent(b.store)}&days=${days}`); } catch (e) { main.innerHTML = `<div class="card"><div class="bd" style="color:var(--crit)">${esc(e.detail || 'Could not load')}</div></div>`; return; }
    draw();
  }
  function draw() {
    const rows = data.mail.filter((m) => show === 'all' || (show === 'tickets' ? ['ticket', 'reply', 'attached'].includes(m.what) : m.what === show));
    const n = (w) => data.mail.filter((m) => (w === 'tickets' ? ['ticket', 'reply', 'attached'].includes(m.what) : m.what === w)).length;
    const problem = b.last && !b.last.startsWith('ok') ? b.last : '';
    main.innerHTML = `<div class="card"><div class="bd">
        <div class="row between" style="gap:12px;align-items:flex-start;flex-wrap:wrap"><div><h2 style="margin:0">${esc(b.store)}</h2>
          <div class="small muted">${esc(b.folder || 'Inbox')} · ${b.on ? `read every minute${b.since ? ', mail from ' + fmtDate(b.since) + ' on' : ''}` : 'not being read for tickets (Settings)'}${b.last_at ? ` · last read ${fmtDate(b.last_at, { rel: true })}` : ''}</div></div>
          <div class="row" style="gap:8px"><select id="mr-days">${[1, 7, 30].map((x) => `<option value="${x}" ${x === days ? 'selected' : ''}>Last ${x === 1 ? 'day' : x + ' days'}</option>`).join('')}</select><button class="btn" id="mr-read">Read now</button></div></div>
        ${problem || data.error ? `<p class="small" style="color:var(--crit);margin-bottom:0">${esc(data.error || problem)}</p>` : ''}</div></div>
      <div class="segctl" role="tablist" style="margin-top:12px">${[['all', `All ${data.mail.length}`], ['tickets', `Tickets ${n('tickets')}`], ['unread', `Not read ${n('unread')}`], ['skipped', `Skipped ${n('skipped')}`]].map(([k, t]) => `<button data-show="${k}" aria-selected="${k === show}">${esc(t)}</button>`).join('')}</div>
      <div class="card"><ul class="list mr-list">${rows.map((m) => `<li data-e="${esc(m.entry_id)}"><div class="t" style="flex:1;min-width:0">
          <div class="row between" style="gap:8px"><b class="mr-subj">${esc(m.subject || '(no subject)')}</b><span class="small muted">${fmtDate(m.received, { rel: true })}</span></div>
          <span>${esc(m.sender_name || m.sender)} &lt;${esc(m.sender)}&gt; — ${esc(m.preview)}</span>
          <div class="row" style="gap:6px;margin-top:5px;flex-wrap:wrap;align-items:center">${m.ticket ? `<a class="pill ${WHAT[m.what][0]}" href="#/support-tickets?t=${m.ticket.id}">${esc(m.ticket.ref)} · ${esc(m.ticket.word)}</a> <span class="small muted">${esc(m.why)}</span>` : pill(m.why, WHAT[m.what] ? WHAT[m.what][0] : 'neutral')}
            <span style="flex:1"></span>${m.ticket ? '' : `<button class="btn sm" data-make="1">Make a ticket</button>`}<button class="btn sm ghost" data-put="1">Put on a ticket…</button></div>
          <div class="mr-body" hidden></div></div></li>`).join('') || '<li class="empty">No mail here.</li>'}</ul></div>`;
    main.querySelector('#mr-days').onchange = (e) => { days = +e.target.value; load(); };
    main.querySelector('#mr-read').onclick = async (e) => { e.target.disabled = true; try { const r = await api.post('/api/mailer/read', { store: b.store }); toast(r.error ? r.error : `Read ${r.read}: ${r.tickets} new ticket(s), ${r.replies} answer(s), ${r.ignored} skipped`, r.error ? 'err' : ''); load(); } catch (er) { toast(er.detail || 'Could not read', 'err'); e.target.disabled = false; } };
    main.querySelectorAll('[data-show]').forEach((x) => (x.onclick = () => { show = x.dataset.show; draw(); }));
    main.querySelectorAll('.mr-list li[data-e]').forEach((li) => {
      const e = li.dataset.e;
      li.querySelector('.mr-subj').onclick = async () => {
        const box = li.querySelector('.mr-body');
        if (!box.hidden) { box.hidden = true; return; }
        box.hidden = false; box.innerHTML = '<span class="spin"></span>';
        try { const m = await api.get(`/api/mailer/mail?entry_id=${encodeURIComponent(e)}`); box.innerHTML = `<pre class="mm-draft">${esc(m.body)}</pre>${m.files.length ? `<div class="small muted">Attachments: ${m.files.map(esc).join(', ')}</div>` : ''}`; }
        catch (er) { box.innerHTML = `<span class="small" style="color:var(--crit)">${esc(er.detail || 'Could not open it')}</span>`; }
      };
      const mk = li.querySelector('[data-make]');
      if (mk) mk.onclick = async () => { mk.disabled = true; try { const r = await api.post('/api/mailer/ticket', { entry_id: e }); toast(`Made ${r.ticket.ref} — C-EXA is reading it`); load(); } catch (er) { toast(er.detail || 'Could not make it', 'err'); mk.disabled = false; } };
      li.querySelector('[data-put]').onclick = () => {
        const f = el('<div class="field"><label>Ticket number</label><input id="mr-t" type="text" placeholder="e.g. T-0012 or 12"></div>');
        const dlg = modal({ title: 'Put this mail on a ticket', body: f, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="mr-t-ok">Put it on</button>' });
        dlg.root.querySelector('#mr-t-ok').onclick = async () => {
          const id = parseInt(String(f.querySelector('#mr-t').value).replace(/\D/g, ''), 10); if (!id) return;
          try { const r = await api.post('/api/mailer/attach', { entry_id: e, ticket_id: id }); dlg.close(); toast(`Added to ${r.ticket.ref}`); load(); } catch (er) { toast(er.detail || 'Could not add it', 'err'); }
        };
      };
    });
  }
  load();
}

async function settings(main, reload) {
  let d;
  try { d = await api.get('/api/mailer/settings'); } catch (e) { main.innerHTML = `<div class="card"><div class="bd">${esc(e.detail || 'Could not load')}</div></div>`; return; }
  const st = JSON.parse(JSON.stringify(d.settings));
  const stores = d.stores;
  const storeOpts = (cur, forSupport) => `<option value="">${forSupport ? '' : '— Outlook only —'}</option>${stores.filter((x) => !forSupport || !x.personal).map((x) => `<option value="${esc(x.name)}" ${x.name === cur ? 'selected' : ''}>${esc(x.name)}${x.address && x.address !== x.name ? ' — ' + esc(x.address) : ''}</option>`).join('')}${cur && !stores.some((x) => x.name === cur) ? `<option value="${esc(cur)}" selected>${esc(cur)} (not in Outlook now)</option>` : ''}`;
  function draw() {
    main.innerHTML = `${d.outlook_error ? `<div class="card" style="margin-bottom:12px;border-color:var(--crit)"><div class="bd small" style="color:var(--crit)">Outlook: ${esc(d.outlook_error)}<br><span class="muted">Outlook must be open and signed in on the CBX server, as the same Windows user that runs CBX.</span></div></div>` : ''}
      <div class="card"><div class="hd"><h2>Mail out</h2></div><div class="bd" style="display:flex;flex-direction:column;gap:10px">
        <label class="row" style="gap:8px;align-items:center"><input type="checkbox" id="ms-on" ${st.enabled ? 'checked' : ''}> CBX may send mail (codes, reminders, tickets)</label>
        <div class="grid g2" style="gap:10px"><div class="field"><label>Goes out through</label><select id="ms-tr"><option value="outlook" ${st.transport === 'outlook' ? 'selected' : ''}>Outlook on the server</option><option value="smtp" ${st.transport === 'smtp' ? 'selected' : ''}>SMTP (set on the server)</option></select></div>
          <div class="field"><label>From the mailbox</label><select id="ms-from" ${st.transport === 'outlook' ? '' : 'disabled'}>${storeOpts(st.send_store, false)}</select></div></div>
        <p class="small muted" style="margin:0">${esc(d.sending.detail || '')}</p>
        <div class="row" style="gap:8px"><input type="text" id="ms-to" placeholder="Send a test to (empty: me)" style="max-width:280px"><button class="btn sm" id="ms-test">Send a test</button></div></div></div>
      <div class="card" style="margin-top:16px"><div class="hd"><h2>Check before it goes</h2><span class="small muted">waits in Drafts until you send it</span></div><div class="bd" style="display:flex;flex-direction:column;gap:8px">
        ${d.groups.map((g) => `<label class="row" style="gap:8px;align-items:center"><input type="checkbox" class="ms-hold" value="${esc(g.key)}" ${(st.drafts || []).includes(g.key) ? 'checked' : ''}> ${esc(g.label)}</label>`).join('')}
        <p class="small muted" style="margin:0">Ticked mail is saved as a draft — in Mailer → Drafts, and in Outlook → Drafts when mail goes out through Outlook. Sign-in codes and tests always go straight.${d.redirect.length ? ` <b>Test run:</b> all mail goes to ${esc(d.redirect.join(', '))} instead, a test setting on this server.` : ''}</p></div></div>
      <div class="card" style="margin-top:16px"><div class="hd"><h2>Support mailboxes</h2><span class="small muted">every new mail in each becomes a ticket</span></div><div class="bd">
        <table class="mr-boxes"><thead><tr><th>Mailbox</th><th>Folder</th><th>Read for tickets</th><th>Mail from</th><th></th></tr></thead><tbody>
        ${st.boxes.map((b, i) => `<tr data-i="${i}"><td><select class="mb-store">${storeOpts(b.store, true)}</select></td><td><input type="text" class="mb-folder" value="${esc(b.folder || 'Inbox')}" style="width:110px"></td>
          <td><label class="row small" style="gap:6px;align-items:center"><input type="checkbox" class="mb-on" ${b.on ? 'checked' : ''}> on</label></td>
          <td><input type="date" class="mb-since" value="${b.since ? String(b.since).slice(0, 10) : ''}" title="Empty: from the moment it is switched on"></td><td><button class="btn sm ghost" data-rm="${i}">Remove</button></td></tr>`).join('') || '<tr><td colspan="5" class="empty">None yet.</td></tr>'}</tbody></table>
        <div class="row" style="gap:8px;margin-top:10px"><button class="btn sm" id="mb-add">+ a support mailbox</button></div>
        <p class="small muted">Only mail from the start date on is read — never the whole history. Read mail gets the Outlook category “CBX”; nothing is moved or deleted. Someone's own mailbox (Mine) can't be a support mailbox.</p></div></div>
      <div class="row" style="justify-content:flex-end;margin-top:12px"><button class="btn primary" id="ms-save">Save</button></div>`;
    main.querySelector('#ms-tr').onchange = (e) => { keep(); st.transport = e.target.value; draw(); };
    main.querySelector('#mb-add').onclick = () => { keep(); st.boxes.push({ store: '', folder: 'Inbox', on: true, since: null }); draw(); };
    main.querySelectorAll('[data-rm]').forEach((x) => (x.onclick = () => { keep(); st.boxes.splice(+x.dataset.rm, 1); draw(); }));
    main.querySelector('#ms-test').onclick = async (e) => {
      e.target.disabled = true;
      try { const r = await api.post('/api/mailer/test', { to: main.querySelector('#ms-to').value }); toast(r.ok ? 'Sent — check the inbox' : r.detail, r.ok ? '' : 'err'); } catch (er) { toast(er.detail || 'Could not send', 'err'); }
      e.target.disabled = false;
    };
    main.querySelector('#ms-save').onclick = async (e) => {
      keep();
      e.target.disabled = true;
      try {
        const r = await api.put('/api/mailer/settings', { enabled: st.enabled, transport: st.transport, send_store: st.send_store || '', drafts: st.drafts || [], boxes: st.boxes.map((b) => ({ store: b.store, folder: b.folder, on: b.on, since: b.since ? String(b.since).slice(0, 10) : null })) });
        toast(r.sending.ready ? 'Saved' : `Saved — mail out: ${r.sending.detail}`, r.sending.ready ? '' : 'err'); reload();
      } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
    };
  }
  function keep() {
    const q = (s) => main.querySelector(s);
    st.enabled = q('#ms-on').checked; st.transport = q('#ms-tr').value; st.send_store = q('#ms-from').value;
    st.drafts = [...main.querySelectorAll('.ms-hold:checked')].map((x) => x.value).concat((st.drafts || []).filter((k) => k.includes(':')));
    main.querySelectorAll('tr[data-i]').forEach((tr) => { const b = st.boxes[+tr.dataset.i]; if (!b) return; b.store = tr.querySelector('.mb-store').value; b.folder = tr.querySelector('.mb-folder').value; b.on = tr.querySelector('.mb-on').checked; b.since = tr.querySelector('.mb-since').value || null; });
  }
  draw();
}

// ---- 1.0.71: Drafts · Sent · Ticket mails ----------------------------------------------------------------

const testRun = (r) => (r && r.length ? `<div class="card" style="margin-bottom:12px;border-color:var(--warn)"><div class="bd small"><b>Test run:</b> everything sent goes to ${esc(r.join(', '))} instead of the real person, a test setting on this server.</div></div>` : '');

async function drafts(main, reload) {
  let d;
  async function load() {
    main.innerHTML = '<div class="card"><div class="bd"><span class="spin"></span></div></div>';
    try { d = await api.get('/api/mailer/drafts'); } catch (e) { main.innerHTML = `<div class="card"><div class="bd">${esc(e.detail || 'Could not load')}</div></div>`; return; }
    draw();
  }
  const card = (x) => `<li data-d="${x.id}"><div class="t" style="flex:1;min-width:0">
      <div class="row between" style="gap:8px"><b class="mr-subj">${esc(x.subject)}</b><span class="small muted">${fmtDate(x.at, { rel: true })}</span></div>
      <span>To ${esc(x.to)}${x.ticket ? ` · <a href="#/support-tickets?t=${x.ticket.id}">${esc(x.ticket.ref)}</a>` : ''} — ${esc(clip(x.text, 160))}</span>
      <div class="row" style="gap:6px;margin-top:5px;flex-wrap:wrap;align-items:center">${pill(x.what, 'neutral')}${x.thread ? '<span class="small muted">answers their mail — the chain is underneath</span>' : ''}${x.outlook ? '<span class="small muted">· also in Outlook → Drafts</span>' : ''}${x.edited ? '<span class="small muted">· changed</span>' : ''}
        <span style="flex:1"></span><button class="btn sm primary" data-send="1" ${d.ready === false ? `disabled title="${esc(d.why || 'Mail out is off')}"` : ''}>Send</button><button class="btn sm ghost" data-open="1">Check / change</button><button class="btn sm ghost" data-drop="1">Throw away</button></div>
      <div class="mr-body" hidden></div></div></li>`;
  const done = (x) => `<li><div class="t" style="flex:1;min-width:0"><div class="row between" style="gap:8px"><b>${esc(x.subject)}</b><span class="small muted">${fmtDate(x.decided_at || x.at, { rel: true })}</span></div>
      <span>To ${esc(x.to)} — ${x.status === 'sent' ? `sent${x.decided_by ? ' by ' + esc(x.decided_by) : ''}` : x.status === 'discarded' ? `thrown away${x.decided_by ? ' by ' + esc(x.decided_by) : ''}` : esc(x.detail)}</span></div>
      ${pill(x.status === 'sent' ? 'Sent' : x.status === 'discarded' ? 'Not sent' : x.status === 'outlook' ? 'In Outlook' : x.status, x.status === 'sent' ? 'good' : 'neutral')}</li>`;
  function draw() {
    main.innerHTML = `${testRun(d.redirect)}<div class="card"><div class="hd"><h2>Waiting for you</h2><div class="row" style="gap:8px">${d.waiting.length > 1 && d.ready !== false ? '<button class="btn sm" id="dr-all">Send all</button>' : ''}<button class="btn sm ghost" id="dr-re">Refresh</button></div></div>
        <ul class="list mr-list">${d.waiting.map(card).join('') || '<li class="empty">Nothing waiting. Mail ticked “check first” in Mail set-up lands here.</li>'}</ul></div>
      ${d.decided.length ? `<div class="card" style="margin-top:16px"><div class="hd"><h2>Lately</h2></div><ul class="list">${d.decided.map(done).join('')}</ul></div>` : ''}`;
    main.querySelector('#dr-re').onclick = load;
    const all = main.querySelector('#dr-all');
    if (all) all.onclick = async () => {
      if (!(await confirmBox(`Send all ${d.waiting.length} mails as they are?`, 'Send all'))) return;
      let ok = 0;
      for (const x of d.waiting) { try { const r = await api.post(`/api/mailer/drafts/${x.id}/send`, {}); if (r.ok) ok += 1; } catch (e) { /* counted below */ } }
      toast(`Sent ${ok} of ${d.waiting.length}`, ok === d.waiting.length ? '' : 'err'); reload();
    };
    main.querySelectorAll('li[data-d]').forEach((li) => {
      const x = d.waiting.find((y) => y.id === +li.dataset.d);
      const box = li.querySelector('.mr-body');
      const send = async (btn) => { btn.disabled = true; try { const r = await api.post(`/api/mailer/drafts/${x.id}/send`, {}); toast(r.ok ? 'Sent' : r.detail, r.ok ? '' : 'err'); reload(); } catch (e) { toast(e.detail || 'Could not send', 'err'); btn.disabled = false; } };
      li.querySelector('[data-send]').onclick = (e) => send(e.target);
      li.querySelector('[data-drop]').onclick = async () => { if (!(await confirmBox('Throw this mail away? It will not be sent.', 'Throw away'))) return; try { await api.post(`/api/mailer/drafts/${x.id}/discard`, {}); toast('Thrown away'); reload(); } catch (e) { toast(e.detail || 'Could not', 'err'); } };
      const open = () => {
        if (!box.hidden) { box.hidden = true; return; }
        box.hidden = false;
        box.innerHTML = `<div class="field"><label>Subject</label><input type="text" class="dr-s" value="${esc(x.subject)}"></div>
          <div class="field"><label>Message</label><textarea class="dr-t" rows="9">${esc(x.text)}</textarea></div>
          <details><summary class="small muted">How it goes out (with the button and the earlier messages)</summary><pre class="mm-draft">${esc(x.full)}</pre></details>
          <div class="row" style="gap:8px;margin-top:8px;justify-content:flex-end"><button class="btn sm" data-save="1">Save changes</button><button class="btn sm primary" data-savesend="1">Save and send</button></div>`;
        const save = async () => { const r = await api.put(`/api/mailer/drafts/${x.id}`, { subject: box.querySelector('.dr-s').value, text: box.querySelector('.dr-t').value }); Object.assign(x, r.draft); return r; };
        box.querySelector('[data-save]').onclick = async (e) => { e.target.disabled = true; try { await save(); toast('Saved — still waiting'); draw(); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; } };
        box.querySelector('[data-savesend]').onclick = async (e) => { e.target.disabled = true; try { await save(); await send(e.target); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; } };
      };
      li.querySelector('[data-open]').onclick = open;
      li.querySelector('.mr-subj').onclick = open;
    });
  }
  load();
}

async function sent(main) {
  main.innerHTML = '<div class="card"><div class="bd"><span class="spin"></span></div></div>';
  let d;
  try { d = await api.get('/api/mailer/sent'); } catch (e) { main.innerHTML = `<div class="card"><div class="bd">${esc(e.detail || 'Could not load')}</div></div>`; return; }
  const state = (m) => (m.ok ? pill('Sent', 'good') : m.detail.startsWith('draft:') ? pill('Draft', 'warn') : m.detail.startsWith('refused') ? pill('Refused', 'crit') : pill('Not sent', 'crit'));
  main.innerHTML = `${testRun(d.redirect)}<div class="card"><div class="hd"><h2>Sent lately</h2><span class="sp"></span>${d.ready ? '' : '<span class="small muted">Mail out is off — switch it on in Mail set-up</span>'}<button class="btn sm primary" id="mr-new" ${d.ready ? '' : 'disabled'}>New mail</button></div>
    <ul class="list">${d.mail.filter((m) => !String(m.detail || '').startsWith('draft:')).map((m) => `<li><div class="t" style="flex:1;min-width:0"><div class="row between" style="gap:8px"><b>${esc(m.subject)}</b><span class="small muted">${fmtDate(m.at, { rel: true })}</span></div>
      <span>To ${esc(m.to)}${m.delivered_to && m.delivered_to !== m.to ? ` (went to ${esc(m.delivered_to)})` : ''} · ${esc(m.by)} — ${esc(m.detail)}</span></div>${state(m)}</li>`).join('') || '<li class="empty">Nothing sent yet.</li>'}</ul></div>`;
  main.querySelector('#mr-new').onclick = () => compose(d, () => sent(main));
}

// 1.0.79 (was Settings → Mail): one message to people CBX knows, or the company's own domain — signed with who sent it.
function compose(d, after) {
  const body = el(`<div style="display:flex;flex-direction:column;gap:10px">
    <div class="field"><label>To</label><input id="c-to" type="text" list="c-list" placeholder="name@company — several separated by commas" autocomplete="off"><datalist id="c-list">${(d.recipients || []).map((r) => `<option value="${esc(r.email)}">${esc(r.name || '')}</option>`).join('')}</datalist></div>
    <div class="field"><label>Subject</label><input id="c-subj" type="text" maxlength="200"></div>
    <div class="field"><label>Message</label><textarea id="c-msg" rows="6"></textarea><span class="small muted">Signed with your name and a link to CBX-Connect. Replies come to you. People CBX knows${d.domain ? ` and addresses at ${esc(d.domain)}` : ''} only.</span></div></div>`);
  const m = modal({ title: 'New mail', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="c-send">Send</button>' });
  m.root.querySelector('#c-send').onclick = async (e) => {
    e.target.disabled = true;
    const to = body.querySelector('#c-to').value.split(/[,;]/).map((x) => x.trim()).filter(Boolean);
    try { const r = await api.post('/api/mailer/compose', { to, subject: body.querySelector('#c-subj').value, message: body.querySelector('#c-msg').value }); toast(r.message, r.ok ? undefined : 'err'); m.close(); after(); }
    catch (err) { toast(err.detail || 'Not sent', 'err'); e.target.disabled = false; }
  };
}

const SAMPLE = { first_name: 'Alex', requester_name: 'Alex Smith', ref: 'T-0012', title: 'Vendor portal won’t accept the invoice upload', system: 'Vendor portal',
  questions: '1. Which vendor is it?\n2. What does the error say?', how_to_reply: 'Reply to this e-mail, or answer in CBX-Connect under My tickets.', lead_first: 'Sam',
  staff_first: 'Sam', staff_name: 'Sam Jones', message: 'We fixed the upload limit — please try again.', what_was_done: 'The upload limit was raised to 20 MB.',
  summary: 'Invoice upload fails with error 500 for Acme Plumbing.', requester: 'Alex Smith <alex@example.com>, Finance', assigned_by: 'Jordan Lee' };
const fill = (t) => String(t).replace(/\{(\w+)\}/g, (m, k) => (k in SAMPLE ? SAMPLE[k] : m));

async function templates(main) {
  let d;
  try { d = await api.get('/api/mailer/templates'); } catch (e) { main.innerHTML = `<div class="card"><div class="bd">${esc(e.detail || 'Could not load')}</div></div>`; return; }
  function row(t) {
    return `<li data-k="${t.key}"><div class="t" style="flex:1;min-width:0"><div class="row between" style="gap:8px"><b class="mr-subj">${esc(t.label)}</b>${t.changed ? pill('Changed', 'info') : ''}</div>
      <span>[${SAMPLE.ref}] ${esc(fill(t.subject))}</span><div class="mr-body" hidden></div></div></li>`;
  }
  function draw() {
    const part = (who) => d.templates.filter((t) => t.who === who).map(row).join('');
    main.innerHTML = `<div class="card"><div class="hd"><h2>Ticket mails</h2><span class="small muted">what CBX writes, and to whom</span></div><div class="bd small muted" style="padding-bottom:0">
        Words in braces are filled in for each ticket: <code>{first_name}</code> <code>{ref}</code> <code>{title}</code> <code>{system}</code> <code>{questions}</code> <code>{how_to_reply}</code> <code>{lead_first}</code> <code>{staff_name}</code> <code>{message}</code> <code>{what_was_done}</code> <code>{summary}</code> <code>{requester}</code> <code>{assigned_by}</code>.
        The first mail of a ticket gets this subject with the ticket number in front; after that CBX replies on the last mail of the chain and keeps its subject (RE: …), so it stays one conversation. A button to the ticket and a reply line are added underneath.</div>
      <div class="eyebrow" style="margin:12px 16px 0">To the person who asked</div><ul class="list mr-list">${part('requester')}</ul>
      <div class="eyebrow" style="margin:12px 16px 0">To the support lead</div><ul class="list mr-list">${part('lead')}</ul></div>`;
    main.querySelectorAll('li[data-k]').forEach((li) => {
      const t = d.templates.find((x) => x.key === li.dataset.k);
      const box = li.querySelector('.mr-body');
      li.querySelector('.mr-subj').onclick = () => {
        if (!box.hidden) { box.hidden = true; return; }
        box.hidden = false;
        box.innerHTML = `<div class="field"><label>Subject</label><input type="text" class="tp-s" value="${esc(t.subject)}"></div>
          <div class="field"><label>Message</label><textarea class="tp-b" rows="9">${esc(t.body)}</textarea></div>
          <div class="small muted">Example</div><pre class="mm-draft tp-p"></pre>
          <div class="row" style="gap:8px;justify-content:flex-end">${t.changed ? '<button class="btn sm ghost" data-reset="1">Back to the original</button>' : ''}<button class="btn sm primary" data-save="1">Save</button></div>`;
        const prev = () => { box.querySelector('.tp-p').textContent = `[${SAMPLE.ref}] ${fill(box.querySelector('.tp-s').value)}\n\n${fill(box.querySelector('.tp-b').value)}`; };
        box.querySelector('.tp-s').oninput = prev; box.querySelector('.tp-b').oninput = prev; prev();
        box.querySelector('[data-save]').onclick = async (e) => {
          e.target.disabled = true;
          try { const r = await api.put(`/api/mailer/templates/${t.key}`, { subject: box.querySelector('.tp-s').value, body: box.querySelector('.tp-b').value }); Object.assign(t, r.template); toast('Saved'); draw(); } catch (er) { toast(er.detail || 'Could not save', 'err'); e.target.disabled = false; }
        };
        const rs = box.querySelector('[data-reset]');
        if (rs) rs.onclick = async () => { try { const r = await api.post(`/api/mailer/templates/${t.key}/reset`, {}); Object.assign(t, r.template); toast('Back to the original'); draw(); } catch (er) { toast(er.detail || 'Could not', 'err'); } };
      };
    });
  }
  draw();
}
