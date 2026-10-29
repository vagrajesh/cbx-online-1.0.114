// My mail (1.0.69): your own mailbox from the server's Outlook, looked after by three agents —
// the Scanner reads, the Sorter tags and files (only by rules you approve), the Writer drafts replies
// into Drafts (you send them) and keeps your to-do. Nothing is ever sent or deleted for you.
import { api } from '../api.js';
import { esc, toast, pill, fmtDate, confirmBox } from '../ui.js';

const CAT_PILL = { 'Needs reply': 'warn', Action: 'crit', Waiting: 'info', FYI: 'neutral', Newsletter: 'neutral', Finance: 'ai', Personal: 'good', 'Junk?': 'neutral' };
const STATE = { working: ['Working', 'info', true], idle: ['Idle', 'neutral'], stuck: ['Stuck', 'crit'] };

export default {
  title: 'My mail',
  async render(root, ctx) {
    let d;
    try { d = await api.get('/api/mymail'); } catch (e) { root.innerHTML = `<div class="head"><div><h1>My mail</h1><p>${esc(e.detail || 'Could not load.')}</p></div></div>`; return; }
    if (!d.link) return launcher(root, ctx, () => this.render(root, ctx));
    desk(root, ctx, d, () => this.render(root, ctx));
  },
};

async function launcher(root, ctx, reload) {
  root.innerHTML = `<div class="head"><div><h1>My mail</h1><p>Link your own mailbox; three agents read it, sort it, draft your replies and keep your to-do.</p></div><button class="btn ghost" id="mm-refresh">Refresh</button></div>
    <div class="card"><div class="hd"><h2>Mailboxes in Outlook on the CBX server</h2></div><div class="bd" id="mm-boxes"><span class="spin"></span></div></div>
    <div class="card" style="margin-top:16px"><div class="bd small">
      <p style="margin-top:0"><b>What they do:</b> the <b>Scanner</b> reads new mail (starting with the last 7 days); the <b>Sorter</b> puts a “CBX: …” category on each mail in Outlook, flags what needs a reply, and files mail into folders only by rules you approve; the <b>Writer</b> writes reply drafts into that mailbox's Drafts and adds what you have to do to your To-do.</p>
      <p class="muted" style="margin-bottom:0">They never send, delete or forward anything, and never open links or attachments. Only you see this page. Mail text is kept only while it's being read — the one-line summary stays.</p></div></div>`;
  root.querySelector('#mm-refresh').onclick = reload;
  const box = root.querySelector('#mm-boxes');
  let rows;
  try { rows = (await api.get('/api/mymail/mailboxes')).mailboxes; } catch (e) { box.innerHTML = `<p class="small" style="color:var(--crit)">${esc(e.detail || 'Outlook did not answer.')}</p><p class="small muted">Outlook must be open and signed in on the CBX server.</p>`; return; }
  box.innerHTML = `<div class="mm-boxes">${rows.map((m) => `<div class="mm-box ${m.free ? '' : 'off'}"><div><b>${esc(m.name)}</b>${m.address && m.address !== m.name ? `<div class="small muted">${esc(m.address)}</div>` : ''}</div>
      ${m.free ? `<button class="btn sm primary" data-link="${esc(m.name)}">Link</button>` : `<span class="small muted">${esc(m.why)}</span>`}</div>`).join('') || '<p class="small muted">No mailboxes found.</p>'}</div>
    <p class="small muted" style="margin-bottom:0">Not listed? Add the account to Outlook on the server (File → Add account), then press Refresh.</p>`;
  box.querySelectorAll('[data-link]').forEach((b) => (b.onclick = async () => {
    if (!(await confirmBox(`Link ${b.dataset.link}? The agents start with the last 7 days of mail. They tag, flag and draft — never send or delete.`, 'Link it'))) return;
    b.disabled = true;
    try { await api.post('/api/mymail/link', { store: b.dataset.link }); toast('Linked — the agents are starting'); api.post('/api/mymail/run', {}).catch(() => {}); reload(); } catch (e) { toast(e.detail || 'Could not link', 'err'); b.disabled = false; }
  }));
}

function desk(root, ctx, d, reload) {
  const l = d.link;
  let tab = ctx.params.tab || 'inbox', cat = '', q = '';
  const agents = l.agents.map((a) => { const [t, k, r] = STATE[a.state] || STATE.idle; return `<div class="card mm-agent"><div class="bd"><div class="row between" style="align-items:center"><b>${esc(a.name)}</b>${pill(t, k, r)}</div>
      <div class="small muted">${esc(a.job)}</div><div class="small" style="margin-top:6px">${esc(a.did || '')}</div>${a.at ? `<div class="small muted">${fmtDate(a.at, { rel: true })}</div>` : ''}</div></div>`; }).join('');
  root.innerHTML = `<div class="head"><div><h1>My mail</h1><p>${esc(l.store)}${l.address && l.address !== l.store ? ' · ' + esc(l.address) : ''} · ${l.total} mail · ${l.drafts} draft${l.drafts === 1 ? '' : 's'} in Outlook${l.active ? '' : ' · <b>paused</b>'}</p></div>
      <div class="row" style="gap:8px"><button class="btn" id="mm-run">Run now</button><button class="btn ghost" id="mm-pause">${l.active ? 'Pause' : 'Resume'}</button><button class="btn ghost" id="mm-unlink" style="color:var(--muted)">Unlink</button></div></div>
    ${l.last_error ? `<div class="card" style="margin-bottom:12px;border-color:var(--crit)"><div class="bd small" style="color:var(--crit)">${esc(l.last_error)}</div></div>` : ''}
    <div class="grid g3 mm-agents">${agents}</div>
    <div class="hub-bar" role="tablist" style="margin-top:16px"><button data-tab="inbox" aria-selected="${tab === 'inbox'}">Mail</button><button data-tab="rules" aria-selected="${tab === 'rules'}">Rules${l.waiting_rules ? ` (${l.waiting_rules} to approve)` : ''}</button><button data-tab="templates" aria-selected="${tab === 'templates'}">Templates${l.waiting_templates ? ` (${l.waiting_templates} offered)` : ''}</button><button data-tab="settings" aria-selected="${tab === 'settings'}">Settings</button></div>
    <div id="mm-body"></div>`;
  root.querySelector('#mm-run').onclick = async (e) => { e.target.disabled = true; try { const r = await api.post('/api/mymail/run', {}); toast(r.error ? r.error : `${r.new} new · ${r.drafts} draft(s) · ${r.todos} to-do(s)`, r.error ? 'err' : ''); reload(); } catch (er) { toast(er.detail || 'Did not run', 'err'); e.target.disabled = false; } };
  root.querySelector('#mm-pause').onclick = async () => { await api.patch('/api/mymail', { active: !l.active }); reload(); };
  root.querySelector('#mm-unlink').onclick = async () => { if (!(await confirmBox('Unlink this mailbox? CBX forgets what the agents made of it (summaries, rules). The mailbox and everything in Outlook stay as they are.', 'Unlink'))) return; await api.del('/api/mymail'); toast('Unlinked'); reload(); };
  root.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { tab = b.dataset.tab; root.querySelectorAll('[data-tab]').forEach((x) => x.setAttribute('aria-selected', String(x.dataset.tab === tab))); show(); }));
  const body = root.querySelector('#mm-body');
  function show() { ({ inbox, rules, templates, settings })[tab](); }

  async function inbox() {
    const chips = [['', 'All', l.total], ...d.categories.map((c) => [c, c, l.counts[c]]).filter((x) => x[2]), ['drafts', 'Drafts', l.drafts]];
    let items = [];
    try { items = (await api.get(`/api/mymail/items?category=${encodeURIComponent(cat)}&q=${encodeURIComponent(q)}`)).items; } catch (e) { toast(e.detail, 'err'); }
    body.innerHTML = `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px"><div class="chips">${chips.map(([k, t, n]) => `<button class="chip mm-chip ${k === cat ? 'on' : ''}" data-cat="${esc(k)}">${esc(t)} <span class="muted">${n || 0}</span></button>`).join('')}</div>
        <input type="text" id="mm-q" placeholder="Search" value="${esc(q)}" style="max-width:220px;margin-left:auto"></div>
      <div class="card"><ul class="list mm-list">${items.map((x) => `<li class="mm-item" data-i="${x.id}"><div class="t" style="flex:1;min-width:0"><div class="row between" style="gap:8px"><b class="mm-subj">${esc(x.subject || '(no subject)')}</b><span class="small muted">${fmtDate(x.received_at, { rel: true })}</span></div>
          <span>${esc(x.sender_name || x.sender)} — ${esc(x.summary || '')}</span>
          <div class="row" style="gap:6px;margin-top:4px;flex-wrap:wrap">${x.category ? pill(x.category, CAT_PILL[x.category] || 'neutral') : pill(x.stage === 'new' ? 'Not read yet' : x.stage, 'neutral')}${x.priority === 'high' ? pill('Urgent', 'crit') : ''}${x.draft_state === 'saved' ? pill('Draft in Outlook', 'ai') : ''}${(x.todos || []).length ? pill('To-do', 'good') : ''}${x.filed_to ? pill('Filed: ' + x.filed_to, 'neutral') : ''}</div>
          <div class="mm-more" hidden></div></div></li>`).join('') || '<li class="empty">Nothing here.</li>'}</ul></div>`;
    body.querySelectorAll('[data-cat]').forEach((b) => (b.onclick = () => { cat = b.dataset.cat; inbox(); }));
    const qi = body.querySelector('#mm-q'); qi.onkeydown = (e) => { if (e.key === 'Enter') { q = qi.value; inbox(); } };
    body.querySelectorAll('.mm-item').forEach((li) => (li.querySelector('.mm-subj').onclick = () => more(li, items.find((x) => x.id === +li.dataset.i))));
  }
  function more(li, x) {
    const box = li.querySelector('.mm-more');
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = `${(x.actions || []).length ? `<div class="eyebrow" style="margin-top:8px">To do</div><ul class="small">${x.actions.map((a) => `<li>${esc(a.task)}${a.due ? ` — <b>${esc(a.due)}</b>` : ''}</li>`).join('')}</ul>` : ''}
      ${x.draft ? `<div class="eyebrow" style="margin-top:8px">Draft reply ${x.draft_state === 'saved' ? '(in Outlook → Drafts — open it there, check it, send it)' : ''}</div><pre class="mm-draft">${esc(x.draft)}</pre>` : ''}
      <div class="row" style="gap:6px;margin-top:8px;flex-wrap:wrap"><button class="btn sm" data-a="draft">${x.draft ? 'Draft it again' : 'Draft a reply'}</button><button class="btn sm ghost" data-a="todo">Add to my to-do</button>
        <select data-a="cat" style="width:auto">${d.categories.map((c) => `<option ${c === x.category ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></div>`;
    box.querySelector('[data-a="draft"]').onclick = async (e) => { e.target.disabled = true; e.target.innerHTML = '<span class="spin"></span> Writing…'; try { await api.post(`/api/mymail/items/${x.id}/draft`, {}); toast('Draft saved in Outlook → Drafts'); inbox(); } catch (er) { toast(er.detail || 'Could not draft', 'err'); e.target.disabled = false; } };
    box.querySelector('[data-a="todo"]').onclick = async (e) => { e.target.disabled = true; try { await api.post(`/api/mymail/items/${x.id}/todo`, {}); toast('Added to your to-do'); inbox(); } catch (er) { toast(er.detail || 'Could not add', 'err'); e.target.disabled = false; } };
    box.querySelector('[data-a="cat"]').onchange = async (e) => { try { await api.patch(`/api/mymail/items/${x.id}`, { category: e.target.value }); toast('Changed'); } catch (er) { toast(er.detail || 'Could not change', 'err'); } };
  }

  async function rules() {
    let rs = [];
    try { rs = (await api.get('/api/mymail/rules')).rules; } catch (e) { toast(e.detail, 'err'); }
    const desc = (r) => { const w = r.when, t = r.then; return `When ${[w.sender && `from “${esc(w.sender)}”`, w.subject && `the subject has “${esc(w.subject)}”`, w.category && `it's ${esc(w.category)}`].filter(Boolean).join(' and ')} → ${[t.category && `call it ${esc(t.category)}`, t.folder && `move it to “${esc(t.folder)}”`, t.flag && 'flag it', t.no_draft && 'no reply draft'].filter(Boolean).join(', ')}`; };
    const prop = rs.filter((r) => r.status === 'proposed'), live = rs.filter((r) => r.status !== 'proposed');
    body.innerHTML = `${prop.length ? `<div class="card" style="margin-bottom:12px"><div class="hd"><h2>The Sorter suggests</h2></div><ul class="list">${prop.map((r) => `<li><div class="t"><b>${desc(r)}</b><span>${esc(r.why)}</span></div><span class="row" style="gap:6px"><button class="btn sm primary" data-r="${r.id}" data-do="approve">Yes</button><button class="btn sm ghost" data-r="${r.id}" data-do="decline">Not now</button></span></li>`).join('')}</ul></div>` : ''}
      <div class="card"><div class="hd"><h2>Rules</h2><span class="small muted">only these move mail</span></div><ul class="list">${live.map((r) => `<li><div class="t"><b>${desc(r)}</b><span>${r.status === 'on' ? 'On' : 'Off'} · used ${r.hits}× · ${r.by === 'owner' ? 'yours' : 'suggested by the Sorter'}</span></div><span class="row" style="gap:6px"><button class="btn sm ghost" data-r="${r.id}" data-do="${r.status === 'on' ? 'off' : 'on'}">${r.status === 'on' ? 'Turn off' : 'Turn on'}</button><button class="btn sm ghost" data-del="${r.id}">Delete</button></span></li>`).join('') || '<li class="empty">No rules yet.</li>'}</ul>
        <div class="bd" style="border-top:1px solid var(--line)"><div class="eyebrow">Add a rule</div>
          <div class="grid g3" style="gap:8px"><input type="text" id="r-sender" placeholder="From contains (e.g. @bank.co.za)"><input type="text" id="r-subject" placeholder="Subject contains"><select id="r-cat"><option value="">Any category</option>${d.categories.map((c) => `<option>${esc(c)}</option>`).join('')}</select></div>
          <div class="grid g3" style="gap:8px;margin-top:8px"><input type="text" id="r-folder" placeholder="Move to folder (optional)"><select id="r-then"><option value="">Keep its category</option>${d.categories.map((c) => `<option>${esc(c)}</option>`).join('')}</select>
            <div class="row small" style="gap:12px;align-items:center"><label class="row" style="gap:4px"><input type="checkbox" id="r-flag"> flag</label><label class="row" style="gap:4px"><input type="checkbox" id="r-nodraft"> no draft</label></div></div>
          <div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn primary" id="r-add">Add rule</button></div></div></div>`;
    body.querySelectorAll('[data-do]').forEach((b) => (b.onclick = async () => { try { await api.post(`/api/mymail/rules/${b.dataset.r}/${b.dataset.do}`, {}); rules(); } catch (e) { toast(e.detail, 'err'); } }));
    body.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => { await api.del(`/api/mymail/rules/${b.dataset.del}`); rules(); }));
    const v = (id) => body.querySelector(id).value;
    body.querySelector('#r-add').onclick = async () => {
      try { await api.post('/api/mymail/rules', { sender: v('#r-sender'), subject: v('#r-subject'), category: v('#r-cat'), folder: v('#r-folder'), then_category: v('#r-then'), flag: body.querySelector('#r-flag').checked, no_draft: body.querySelector('#r-nodraft').checked }); toast('Rule added'); rules(); }
      catch (e) { toast(e.detail || 'Could not add', 'err'); }
    };
  }

  async function templates() {
    let ts = [];
    try { ts = (await api.get('/api/mymail/templates')).templates; } catch (e) { toast(e.detail, 'err'); }
    body.innerHTML = `<div class="card"><div class="hd"><h2>Reply templates</h2><span class="small muted">the Writer starts from these · {first_name} {subject} {my_name}</span></div>
      <ul class="list">${ts.map((t) => `<li style="align-items:flex-start"><div class="t" style="flex:1"><b>${esc(t.name)}</b> ${t.status === 'proposed' ? pill('Offered by the Writer', 'ai') : ''}<span>${esc(t.when || '')}${t.used ? ` · used ${t.used}×` : ''}</span><pre class="mm-draft">${esc(t.body)}</pre></div>
        <span class="row" style="gap:6px">${t.status === 'proposed' ? `<button class="btn sm primary" data-t="${t.id}" data-do="keep">Keep</button>` : `<button class="btn sm ghost" data-edit="${t.id}">Edit</button>`}<button class="btn sm ghost" data-t="${t.id}" data-do="drop">${t.status === 'proposed' ? 'Drop' : 'Remove'}</button></span></li>`).join('') || '<li class="empty">No templates yet.</li>'}</ul>
      <div class="bd" style="border-top:1px solid var(--line)"><div class="eyebrow" id="t-title">Add a template</div>
        <div class="grid g2" style="gap:8px"><input type="text" id="t-name" placeholder="Name (e.g. Can't make it)"><input type="text" id="t-when" placeholder="When to use it (e.g. meeting invitations I can't attend)"></div>
        <textarea id="t-body" rows="5" style="margin-top:8px" placeholder="Hi {first_name},&#10;&#10;Thanks for …&#10;&#10;{my_name}"></textarea>
        <div class="row" style="justify-content:flex-end;margin-top:8px;gap:8px"><button class="btn primary" id="t-save">Save template</button></div></div></div>`;
    let editing = null;
    body.querySelectorAll('[data-do]').forEach((b) => (b.onclick = async () => { await api.post(`/api/mymail/templates/${b.dataset.t}/${b.dataset.do}`, {}); templates(); }));
    body.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => { const t = ts.find((x) => x.id === +b.dataset.edit); editing = t.id; body.querySelector('#t-title').textContent = `Edit “${t.name}”`; body.querySelector('#t-name').value = t.name; body.querySelector('#t-when').value = t.when; body.querySelector('#t-body').value = t.body; body.querySelector('#t-name').focus(); }));
    body.querySelector('#t-save').onclick = async () => {
      const p = { name: body.querySelector('#t-name').value, when: body.querySelector('#t-when').value, body: body.querySelector('#t-body').value };
      try { editing ? await api.put(`/api/mymail/templates/${editing}`, p) : await api.post('/api/mymail/templates', p); toast('Saved'); templates(); } catch (e) { toast(e.detail || 'Could not save', 'err'); }
    };
  }

  function settings() {
    body.innerHTML = `<div class="card"><div class="bd" style="display:flex;flex-direction:column;gap:10px">
      <label class="row" style="gap:8px;align-items:center"><input type="checkbox" id="s-drafts" ${l.auto_drafts ? 'checked' : ''}> The Writer drafts replies on its own (into Drafts — you send them)</label>
      <label class="row" style="gap:8px;align-items:center"><input type="checkbox" id="s-todo" ${l.auto_todo ? 'checked' : ''}> The Writer adds what you have to do to your To-do</label>
      <p class="small muted" style="margin:0">They look every 3 minutes while the mailbox is linked and Outlook is open on the server. Mail from ${fmtDate(l.since)} on. Never sent, deleted or forwarded.</p></div></div>`;
    body.querySelector('#s-drafts').onchange = async (e) => { await api.patch('/api/mymail', { auto_drafts: e.target.checked }); toast('Saved'); };
    body.querySelector('#s-todo').onchange = async (e) => { await api.patch('/api/mymail', { auto_todo: e.target.checked }); toast('Saved'); };
  }
  show();
}
