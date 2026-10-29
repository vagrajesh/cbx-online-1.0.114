// API connections: the outside services (address + key) that tool pages and flows call. The key goes in
// here once, is stored sealed, and is never shown again — tools use it through CBX, which adds it on
// the way out. Owners and admins; every change is logged.
import { api } from '../api.js';
import { esc, toast, modal, confirmBox, pill, el, fmtDate } from '../ui.js';

const KIND = { none: 'No key', bearer: 'Bearer token', header: 'Header', basic: 'Basic (user + key)', query: 'Query parameter' };
const KIND_HELP = {
  none: 'The service needs no key (or is open on the network).',
  bearer: 'Sent as  Authorization: Bearer <key>  — most modern APIs.',
  header: 'Sent in a header of your choosing, e.g.  X-API-Key: <key>.',
  basic: 'HTTP basic auth: a username and the key as its password.',
  query: 'Appended to every call as  ?<name>=<key>  — older APIs.',
};
const NAME_LABEL = { header: 'Header name', basic: 'Username', query: 'Parameter name' };
const NAME_PH = { header: 'X-API-Key', basic: 'apiuser', query: 'api_key' };

export default {
  title: 'API connections',
  async render(root, ctx) {
    if (!['owner', 'admin'].includes(ctx.me.role)) { root.innerHTML = '<div class="head"><div><h1>API connections</h1><p>This page is for admins.</p></div></div>'; return; }
    let d, k;
    const load = async () => { [d, k] = await Promise.all([api.get('/api/connections'), api.get('/api/data-keys')]); };
    try { await load(); } catch (e) { root.innerHTML = `<div class="head"><div><h1>API connections</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }

    const toolName = (slug) => (d.tools.find((t) => t.slug === slug) || { name: slug }).name;
    const row = (c) => {
      const t = c.last_test || {};
      const test = t.at ? `<span class="${t.ok ? 'good' : 'crit'}" style="color:var(--${t.ok ? 'good' : 'crit'})">${esc(t.note || (t.ok ? 'answered' : 'no answer'))}</span> <span class="muted">· ${fmtDate(t.at, { rel: true })}</span>` : '<span class="muted">not tested yet</span>';
      const key = c.auth_kind === 'none' ? 'no key' : !c.has_key ? '<span style="color:var(--crit)">no key stored</span>' : !c.key_readable ? '<span style="color:var(--crit)">key unreadable — enter it again</span>' : `key <span class="mono">${esc(c.key_hint)}</span>`;
      return `<div class="conn ${c.is_active ? '' : 'off'}">
        <div><b>${esc(c.name)}</b>${c.description ? `<div class="small muted">${esc(c.description)}</div>` : ''}
          <div class="small" style="margin-top:4px"><span class="mono">${esc(c.base_url)}</span></div>
          <div class="small muted" style="margin-top:4px">${esc(KIND[c.auth_kind] || c.auth_kind)}${c.header_name && c.auth_kind !== 'bearer' ? ` (${esc(c.header_name)})` : ''} · ${key}${Object.keys(c.extra_headers || {}).length ? ` · ${Object.keys(c.extra_headers).length} extra header${Object.keys(c.extra_headers).length === 1 ? '' : 's'}` : ''}</div>
          <div class="small" style="margin-top:6px">${c.tools.length ? c.tools.map((s) => pill(toolName(s), 'info')).join(' ') : '<span class="muted">No tool may use it yet — tick one under Edit.</span>'}</div>
          <div class="small" style="margin-top:6px">${test}</div></div>
        <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">${pill(c.is_active ? 'on' : 'off', c.is_active ? 'good' : 'neutral')}
          <div class="row" style="gap:6px;flex-wrap:wrap;justify-content:flex-end">
            <button class="btn sm" data-test="${c.id}">Test</button>
            <button class="btn sm ghost" data-edit="${c.id}">Edit</button>
            <button class="btn sm ghost" data-toggle="${c.id}">${c.is_active ? 'Switch off' : 'Switch on'}</button>
            <button class="btn sm ghost danger" data-del="${c.id}">Delete</button>
          </div></div></div>`;
    };

    function draw() {
      const list = d.connections;
      root.innerHTML = `
      <div class="head"><div><h1>API connections</h1><p>Outside services your tools call — from a page (an API source) or a flow (Call an API). The key lives here, once; no page or flow ever holds it. Every change is logged.</p></div>
        <div class="row" style="gap:8px"><button class="btn primary" id="add">Add connection</button></div></div>
      <div class="card" style="margin-bottom:16px">
        ${list.map(row).join('') || '<div class="empty">No connections yet. Add the first: a name, the service\'s address and its key — then tick the tools that may use it.</div>'}
      </div>
      ${keysCard()}
      `;
      root.querySelector('#add').onclick = () => form();
      wireKeys();
      const find = (id) => d.connections.find((c) => c.id === +id);
      root.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => form(find(b.dataset.edit))));
      root.querySelectorAll('[data-test]').forEach((b) => (b.onclick = async () => {
        const c = find(b.dataset.test);
        const body = el(`<div><p class="small muted" style="margin:0 0 10px">One GET at the address, with the key. Add a path if the root of the service doesn't answer (e.g. <span class="mono">/v1/ping</span>).</p><div class="field"><label>Path (optional)</label><input id="tp" type="text" placeholder="/"></div></div>`);
        const m = modal({ title: `Test ${c.name}`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="go">Test now</button>' });
        m.root.querySelector('#go').onclick = async () => {
          const g = m.root.querySelector('#go'); g.disabled = true; g.innerHTML = '<span class="spin"></span> Calling…';
          try { const r = await api.post(`/api/connections/${c.id}/test`, { path: body.querySelector('#tp').value.trim() }); m.close(); toast(r.result.note, r.result.ok ? undefined : 'err'); await load(); draw(); }
          catch (e) { toast(e.detail || 'Could not test', 'err'); g.disabled = false; g.textContent = 'Test now'; }
        };
      }));
      root.querySelectorAll('[data-toggle]').forEach((b) => (b.onclick = async () => { const c = find(b.dataset.toggle); try { await api.patch(`/api/connections/${c.id}`, { is_active: !c.is_active }); toast(c.is_active ? 'Switched off · logged' : 'Switched on · logged'); await load(); draw(); } catch (e) { toast(e.detail, 'err'); } }));
      root.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => {
        const c = find(b.dataset.del);
        if (!(await confirmBox(`Delete the connection “${c.name}”? Tools that use it will fail on their next call. Logged under your name.`, 'Delete'))) return;
        try { await api.del(`/api/connections/${c.id}`); toast('Deleted · logged'); await load(); draw(); } catch (e) { toast(e.detail, 'err'); }
      }));
    }

    // ---- keys for our data (1.0.59): another system reads one table, with a key ----------------------------
    const STATE = { active: 'good', expired: 'neutral', revoked: 'neutral' };
    const tableName = (x) => { const t = k.tables.find((y) => y.tool === x.tool && y.table === x.table); return t ? `${t.tool_name} · ${x.table}` : `${x.tool} · ${x.table} (not open any more)`; };
    function keysCard() {
      const rows = k.keys.map((x) => `<div class="conn ${x.state === 'active' ? '' : 'off'}">
        <div><b>${esc(x.name)}</b> <span class="small muted mono">${esc(x.prefix)}…</span>
          <div class="small" style="margin-top:4px">${esc(tableName(x))}</div>
          <div class="small muted" style="margin-top:4px">${x.rate_per_min} calls a minute · ${x.allowed_ips.length ? 'from ' + esc(x.allowed_ips.join(', ')) : 'from any address'} · ${x.state === 'active' ? 'until ' + fmtDate(x.expires_at) : x.state}</div>
          <div class="small muted" style="margin-top:4px">${x.calls ? `${x.calls} call${x.calls === 1 ? '' : 's'} · last ${fmtDate(x.last_used_at, { rel: true })}${x.last_ip ? ' from ' + esc(x.last_ip) : ''}` : 'not used yet'} · made by ${esc(x.created_by || '—')}</div></div>
        <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">${pill(x.state, STATE[x.state])}
          ${x.state === 'active' ? `<button class="btn sm ghost danger" data-revoke="${x.id}">Revoke</button>` : ''}</div></div>`).join('');
      const open = k.tables.length ? k.tables.map((t) => pill(`${t.tool_name} · ${t.table}`, 'info')).join(' ') : '<span class="muted">No table is open to other systems. In the Builder, open a table and tick the columns under “Other systems may read it”.</span>';
      return `<div class="head" style="margin-top:8px"><div><h2 style="margin:0">Keys for our data</h2><p>Another system reads one table — the columns the Builder opened, reading only — with a key made here. The key is shown once; only its fingerprint is kept. Every key has an end date and a limit of calls a minute.${k.https_only ? ' Keys are taken over HTTPS only.' : ''}</p></div>
        ${k.can_make ? '<div class="row" style="gap:8px"><button class="btn" id="kadd"' + (k.tables.length ? '' : ' disabled') + '>Make a key</button></div>' : ''}</div>
        <div class="card"><div class="small" style="padding:12px 16px 4px">Open to other systems: ${open}</div>
        ${rows || '<div class="empty">No keys yet.</div>'}</div>`;
    }
    function wireKeys() {
      const add = root.querySelector('#kadd');
      if (add) add.onclick = makeKey;
      root.querySelectorAll('[data-revoke]').forEach((b) => (b.onclick = async () => {
        const x = k.keys.find((y) => y.id === +b.dataset.revoke);
        if (!(await confirmBox(`Revoke the key for “${x.name}”? It stops working at once, for good. Logged under your name.`, 'Revoke'))) return;
        try { await api.post(`/api/data-keys/${x.id}/revoke`, {}); toast('Revoked · logged'); await load(); draw(); } catch (e) { toast(e.detail, 'err'); }
      }));
    }
    function makeKey() {
      const body = el(`<div style="display:grid;gap:12px">
        <div class="field"><label>Who it is for</label><input id="k-name" type="text" maxlength="120" placeholder="e.g. Finance system"></div>
        <div class="field"><label>The table it reads</label><select id="k-table">${k.tables.map((t, i) => `<option value="${i}">${esc(t.tool_name)} · ${esc(t.table)} — ${esc(t.columns.map((c) => c.label).join(', '))}</option>`).join('')}</select></div>
        <div class="row" style="gap:8px;align-items:flex-start">
          <div class="field" style="flex:1"><label>Works for</label><select id="k-days"><option value="30">30 days</option><option value="90" selected>90 days</option><option value="180">180 days</option><option value="365">a year</option></select></div>
          <div class="field" style="flex:1"><label>Calls a minute</label><input id="k-rate" type="number" min="1" max="600" value="60"></div></div>
        <div class="field"><label>Only from these addresses <span class="muted" style="font-weight:500">— optional, one per line</span></label><textarea id="k-ips" rows="2" placeholder="10.0.0.5&#10;10.0.1.0/24"></textarea><span class="small muted">Empty: from any address that reaches the server.</span></div></div>`);
      const m = modal({ title: 'Make a key', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Make it</button>' });
      m.root.querySelector('#ok').onclick = async () => {
        const b = m.root.querySelector('#ok'); b.disabled = true;
        const t = k.tables[+body.querySelector('#k-table').value];
        try {
          const r = await api.post('/api/data-keys', { name: body.querySelector('#k-name').value, tool: t.tool, table: t.table, days: +body.querySelector('#k-days').value,
            rate_per_min: +body.querySelector('#k-rate').value, allowed_ips: body.querySelector('#k-ips').value.split('\n').map((x) => x.trim()).filter(Boolean) });
          m.close(); await load(); draw(); showKey(r.key, t);
        } catch (e) { toast(e.detail || 'Could not make the key', 'err'); b.disabled = false; }
      };
      body.querySelector('#k-name').focus();
    }
    function showKey(key, t) {
      const addr = `${location.origin}/api/shared/${t.tool}/${t.table}`;
      const body = el(`<div style="display:grid;gap:10px"><p class="small" style="margin:0"><b>Copy it now — it is not shown again.</b> Give it to the other system's owner the way you would give a password. If it is lost, revoke it and make a new one.</p>
        <div class="field"><label>The key</label><input id="k-key" type="text" readonly class="mono" value="${esc(key)}"></div>
        <div class="field"><label>How the other system reads</label><textarea readonly rows="3" class="mono small">GET ${esc(addr)}?limit=100&amp;after_id=0
Authorization: Bearer &lt;the key&gt;</textarea><span class="small muted">after_id: only the rows added after that one (each answer says next_after_id).</span></div></div>`);
      const m = modal({ title: 'The new key', body, footer: '<button class="btn" id="cp">Copy the key</button><button class="btn primary" data-close>Done</button>' });
      m.root.querySelector('#cp').onclick = async () => { try { await navigator.clipboard.writeText(key); toast('Copied'); } catch { body.querySelector('#k-key').select(); } };
    }

    function form(c) {
      const isNew = !c;
      const wfTools = d.tools;
      c = c || { name: '', description: '', base_url: '', auth_kind: 'bearer', header_name: '', extra_headers: {}, tools: [] };
      const hdrs = Object.entries(c.extra_headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
      const body = el(`<div style="display:grid;gap:12px">
        <div class="row" style="gap:8px;align-items:flex-start"><div class="field" style="flex:1"><label>Name</label><input id="f-name" type="text" value="${esc(c.name)}" placeholder="e.g. Sage accounting" maxlength="120"></div></div>
        <div class="field"><label>Address (base URL)</label><input id="f-url" type="text" inputmode="url" value="${esc(c.base_url)}" placeholder="https://api.example.com/v1"><span class="small muted">Calls are made to paths under this address — a flow can't point it anywhere else.</span></div>
        <div class="row" style="gap:8px;align-items:flex-start">
          <div class="field" style="flex:1"><label>How the key is sent</label><select id="f-kind">${Object.entries(KIND).map(([k, v]) => `<option value="${k}" ${k === c.auth_kind ? 'selected' : ''}>${v}</option>`).join('')}</select><span class="small muted" id="f-kind-help">${KIND_HELP[c.auth_kind]}</span></div>
          <div class="field" style="flex:1" id="f-hn-box"><label id="f-hn-label">${NAME_LABEL[c.auth_kind] || 'Name'}</label><input id="f-hn" type="text" value="${esc(c.header_name || '')}" placeholder="${NAME_PH[c.auth_kind] || ''}"></div>
        </div>
        <div class="field" id="f-key-box"><label>Key${isNew ? '' : ' <span class="muted" style="font-weight:500">— leave empty to keep the current one' + (c.key_hint ? ` (${esc(c.key_hint)})` : '') + '</span>'}</label><input id="f-key" type="password" autocomplete="new-password" placeholder="${isNew ? 'Paste the key or token' : 'Paste a new key to replace it'}"><span class="small muted">Stored sealed on the server. Not shown again — only its last four characters.</span></div>
        <div class="field"><label>Extra headers <span class="muted" style="font-weight:500">— optional, one per line</span></label><textarea id="f-hdrs" rows="2" placeholder="Accept: application/json">${esc(hdrs)}</textarea></div>
        <div class="field"><label>Tools that may use it</label>${wfTools.length ? `<div class="cols" style="grid-template-columns:1fr 1fr;max-height:200px;overflow:auto">${wfTools.map((t) => `<label class="colbox" style="cursor:pointer"><input type="checkbox" value="${esc(t.slug)}" style="width:auto" ${c.tools.includes(t.slug) ? 'checked' : ''}> <span>${esc(t.name)}<span class="small muted mono" style="display:block">${esc(t.slug)}</span></span></label>`).join('')}</div>` : '<div class="cols"></div><p class="small muted" style="margin:0">No tool folders yet.</p>'}<span class="small muted">Only the tools ticked here may use this connection — from their pages or their flows. Nothing else can.</span></div>
        <div class="field"><label>What it is for <span class="muted" style="font-weight:500">— optional</span></label><input id="f-desc" type="text" value="${esc(c.description || '')}" placeholder="Invoices and customer balances"></div>
      </div>`);
      const kindSel = body.querySelector('#f-kind');
      const paintKind = () => { const k = kindSel.value; body.querySelector('#f-kind-help').textContent = KIND_HELP[k]; body.querySelector('#f-hn-box').style.display = NAME_LABEL[k] ? '' : 'none'; body.querySelector('#f-hn-label').textContent = NAME_LABEL[k] || 'Name'; body.querySelector('#f-hn').placeholder = NAME_PH[k] || ''; body.querySelector('#f-key-box').style.display = k === 'none' ? 'none' : ''; };
      kindSel.onchange = paintKind; paintKind();
      const m = modal({ title: isNew ? 'Add connection' : `Edit ${c.name}`, body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">${isNew ? 'Add' : 'Save'}</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        const b = m.root.querySelector('#ok'); b.disabled = true;
        const extra = {}; let bad = '';
        body.querySelector('#f-hdrs').value.split('\n').map((l) => l.trim()).filter(Boolean).forEach((l) => { const i = l.indexOf(':'); if (i < 1) { bad = l; return; } extra[l.slice(0, i).trim()] = l.slice(i + 1).trim(); });
        if (bad) { toast(`Header lines look like  Name: value  — check “${bad}”`, 'err'); b.disabled = false; return; }
        const payload = { name: body.querySelector('#f-name').value, base_url: body.querySelector('#f-url').value, auth_kind: kindSel.value, header_name: body.querySelector('#f-hn').value, description: body.querySelector('#f-desc').value, extra_headers: extra, tools: [...body.querySelectorAll('.cols input:checked')].map((x) => x.value) };
        const key = body.querySelector('#f-key').value;
        if (key) payload.secret = key;
        try {
          if (isNew) await api.post('/api/connections', payload); else await api.patch(`/api/connections/${c.id}`, payload);
          m.close(); toast(isNew ? 'Connection added · logged' : 'Saved · logged'); await load(); draw();
        } catch (e) { toast(e.detail || 'Could not save', 'err'); b.disabled = false; }
      };
      body.querySelector('#f-name').focus();
    }
    draw();
  },
};
