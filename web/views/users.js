import { api } from '../api.js';
import { esc, toast, modal, pill, fmtDate, avatar, el, aiAvatar } from '../ui.js';
import rolesTab from './roles.js';

// Fallback wording for the four built-ins, used only if the server sends no description. Every
// other role — including ones invented on the Roles tab — is described by its own row.
const ROLE_TEXT = {
  owner: 'Full control. Can make other owners, admins, managers and users. Sees C-EXA, sandboxes and the audit trail.',
  admin: 'Builds tools from requests, manages the warehouse. Can add and manage <b>managers and users</b> — not admins or owners.',
  manager: 'Oversees their team: Entity Compliance, users, audit. Can add and manage <b>users on their own team</b>, and grant them the tools the manager holds.',
  user: 'Sees their tools, their requests, their results — and the Entity Compliance documents they file. Nothing else.',
};
// 1.0.83: master sheets live inside PDF Studio, so the grant says so
const toolName = (t) => (t.slug === 'master-sheets' ? 'PDF Studio · master sheets' : t.name);
const cap = (r) => String(r || '').replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

export default {
  title: 'Users & roles',
  async render(root, ctx) {
    let data = await api.get('/api/users');
    // R8: anyone the server limits to their team (`scope`), not only the role called manager
    const isManager = ctx.me.role === 'manager' || !!data.scope;
    const teamName = data.scope && data.scope.team;
    const info = (r) => (data.role_info || {})[r] || {};
    const roleName = (r) => info(r).name || cap(r);
    const roleText = (r) => info(r).description || ROLE_TEXT[r] || '';
    const rolePill = (u) => (u.is_axl ? pill('Super admin (AI)', 'ai') : pill(roleName(u.role), u.role === 'manager' ? 'manager' : 'neutral') + (u.team_limited ? ' ' + pill('Team only', 'neutral') : ''));
    // 1.0.83: each part is its own tab of People & access (no tabs inside tabs)
    const HUB_TAB = { users: 'people', access: 'access', roles: 'roles', rules: 'rules' };
    const inHub = !!HUB_TAB[ctx.tab];
    let tab = HUB_TAB[ctx.tab] || 'people';
    const TITLES = { people: 'People', access: 'Tool access', roles: 'Roles &amp; permissions', rules: 'Row-level rules' };
    const reload = async () => { data = await api.get('/api/users'); };
    const draw = () => {
      const lead = tab !== 'people' ? '' : isManager ? `Your team${teamName ? ` — <b>${esc(teamName)}</b>` : ''}. You can add people to it, reset their passwords and grant them the tools you hold.` : 'Deny by default. Every person sees only the tools and rows they\'ve been granted.';
      root.innerHTML = `
      <div class="head"><div><h1>${TITLES[tab]}</h1>${lead ? `<p>${lead}</p>` : ''}</div>
        ${tab === 'people' ? `<div class="row"><button class="btn primary" id="add-user">Add user</button>${isManager ? '' : '<button class="btn" id="add-team">New team</button>'}</div>` : ''}</div>
      <div id="codes-waiting"></div>
      ${isManager && !teamName ? '<div class="card" style="margin-bottom:16px"><div class="bd err-box">You are not on a team yet, so there is nobody to manage. Ask an admin to put you on one.</div></div>' : ''}
      ${inHub ? '' : `<div class="tabs">${tabs().map((t) => `<button aria-selected="${tab === t}" data-tab="${t}">${TITLES[t]}</button>`).join('')}</div>`}
      <div id="pane"></div>`;
      root.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { tab = b.dataset.tab; draw(); }));
      if (canViewAs) codesWaiting(root.querySelector('#codes-waiting'));
      const au = root.querySelector('#add-user'); if (au) au.onclick = addUser;
      const at = root.querySelector('#add-team'); if (at) at.onclick = addTeam;
      if (!tabs().includes(tab)) { root.querySelector('#pane').innerHTML = '<div class="card"><div class="bd muted">This part is not for you.</div></div>'; return; }
      ({ people, access, roles: rolesPane, rules })[tab]();
    };

    function tabs() {
      const t = ['people', 'access'];
      if (data.can_manage_roles) t.push('roles');
      if (!isManager) t.push('rules');
      return t;
    }

    // The Roles tab lives in its own file — it is a screen in its own right, not a table.
    function rolesPane() {
      rolesTab(root.querySelector('#pane'), ctx, { onChange: async () => { await reload(); draw(); } });
    }

    // View as: owners and admins only (the server decides who may be viewed — people they manage)
    const canViewAs = ctx.me.role === 'owner' || ctx.me.role === 'admin';
    const twoStep = (u) => u.mfa_method === 'totp' ? 'Two-step: app' : u.mfa_method === 'email' ? 'Two-step: email' : 'Two-step: not set up';
    const statusOf = (u) => u.locked ? pill('Locked', 'crit') : !u.is_active ? pill('Disabled', 'crit') : u.reset_requested_at ? pill('Reset requested', 'warn') : u.must_change_password ? pill('Temp password · not yet signed in', 'neutral') : pill('Active', 'good');

    function people() {
      const rows = data.users.map((u) => `<tr>
        <td><div class="row">${u.is_axl ? aiAvatar() : avatar(u.name)}<div><b>${esc(u.name)}</b><div class="small muted">${esc(u.is_axl ? u.title : (u.title || u.email))}${u.team && !u.is_axl ? ' · ' + esc(u.team) : ''}</div></div></div></td>
        <td>${rolePill(u)}</td>
        <td class="num">${u.tool_count}</td><td>${statusOf(u)}${u.is_axl ? '' : `<div class="small muted" style="margin-top:4px">${twoStep(u)}${u.expires_at ? ` · ends ${fmtDate(u.expires_at, { year: true })}` : ''}</div>`}</td><td class="muted small">${fmtDate(u.last_seen, { rel: true })}</td>
        <td>${u.is_axl ? (ctx.me.role === 'owner' ? `<button class="btn sm" data-go="cexa">Open C-EXA</button>` : '') : u.can_edit ? `<div class="row" style="gap:6px;flex-wrap:nowrap;justify-content:flex-end">${u.reset_requested_at || u.locked ? `<button class="btn sm primary" data-temp="${u.id}">Temp password</button>` : ''}${canViewAs && u.id !== ctx.me.id && u.is_active ? `<button class="btn sm ghost" data-viewas="${u.id}" title="See the app exactly as ${esc(u.name)} sees it — read-only">View as</button>` : ''}<button class="btn sm" data-edit="${u.id}">Edit</button></div>` : ''}</td></tr>`).join('');
      root.querySelector('#pane').innerHTML = `<div class="card"><div class="tbl"><table><thead><tr><th>User</th><th>Role</th><th>Tools</th><th>Status</th><th>Last active</th><th></th></tr></thead><tbody>${rows || '<tr><td colspan="6" class="empty">Nobody on your team yet. Add the first person.</td></tr>'}</tbody></table></div></div>`;
      root.querySelectorAll('[data-edit]').forEach((b) => (b.onclick = () => editUser(data.users.find((u) => u.id == b.dataset.edit))));
      root.querySelectorAll('[data-temp]').forEach((b) => (b.onclick = () => tempPassword(data.users.find((u) => u.id == b.dataset.temp))));
      root.querySelectorAll('[data-viewas]').forEach((b) => (b.onclick = async () => {
        const u = data.users.find((x) => x.id == b.dataset.viewas);
        try { await api.post(`/api/view-as/${u.id}`); history.replaceState(null, '', '#/home'); location.reload(); }  // 1.0.106: no hashchange — the old page drew Home as the owner with the new person's session (a 403 in the console)
        catch (e) { toast(e.detail || 'Could not start viewing.', 'err'); }
      }));
      root.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => ctx.go(b.dataset.go)));
    }

    // Anyone who is not treated as an admin needs grants — managers, users, and any role invented on
    // the Roles tab. The server already says which: `tools` comes back as "all" for an admin.
    const grantable = (u) => !u.is_axl && u.tools !== 'all';

    function access() {
      const users = data.users.filter((u) => grantable(u) && u.can_edit);
      const tools = data.tools.filter((t) => !t.admin_only);
      const rows = users.map((u) => `<tr><td><b>${esc(u.name)}</b><div class="small muted">${esc(u.team || '')}${u.role !== 'user' ? ' · ' + esc(roleName(u.role).toLowerCase()) : ''}</div></td>${tools.map((t) => `<td><span class="check ${u.tools.includes(t.id) ? 'on' : ''}" data-u="${u.id}" data-t="${t.id}" role="checkbox" aria-checked="${u.tools.includes(t.id)}" tabindex="0"></span></td>`).join('')}</tr>`).join('');
      const adminOnly = data.tools.filter((t) => t.admin_only);
      root.querySelector('#pane').innerHTML = `<div class="card matrix"><div class="tbl"><table><thead><tr><th>User</th>${tools.map((t) => `<th>${esc(toolName(t))}</th>`).join('')}</tr></thead><tbody>${rows || `<tr><td colspan="9" class="empty">${isManager ? 'Nobody on your team to grant tools to yet.' : 'No users yet. Owners and admins already have every tool.'}</td></tr>`}</tbody></table></div><div class="bd small muted">Click a box to grant or remove. Changes take effect immediately and are written to the audit trail. ${isManager ? 'You can grant the tools you hold yourself.' : `${adminOnly.map((t) => esc(t.name)).join(', ')} ${adminOnly.length === 1 ? 'is' : 'are'} admin-only.`}</div></div>`;
      root.querySelectorAll('.check').forEach((c) => (c.onclick = async () => {
        const on = !c.classList.contains('on');
        try { await api.post('/api/users/access', { user_id: +c.dataset.u, tool_id: +c.dataset.t, granted: on }); c.classList.toggle('on', on); c.setAttribute('aria-checked', on); toast('Access updated · logged to audit trail'); data = await api.get('/api/users'); }
        catch (e) { toast(e.detail, 'err'); }
      }));
    }

    async function rules() {
      const r = await api.get('/api/users/rules');
      const rows = r.rules.map((x) => `<tr><td>${esc(x.who)}</td><td class="mono">${esc(x.table)}</td><td class="mono">${esc(x.field)} ${esc(x.op)} '${esc(x.value)}'</td><td class="small muted">${esc(x.note)}</td><td><button class="btn sm ghost" data-del="${x.id}">Remove</button></td></tr>`).join('');
      root.querySelector('#pane').innerHTML = `<div class="card"><div class="hd"><h2>Row-level rules</h2><button class="btn sm" id="add-rule">Add rule</button></div><div class="tbl"><table><thead><tr><th>Applies to</th><th>Table</th><th>Rule</th><th>Note</th><th></th></tr></thead><tbody>${rows}
        <tr><td>Everyone (User role)</td><td class="mono">*</td><td class="mono">owner = current user</td><td class="small muted">Default — a user with no rule sees only what they created</td><td></td></tr>
        <tr><td>Managers</td><td class="mono">*</td><td class="mono">team = own team</td><td class="small muted">A manager sees their team's rows where the table has a team (Entity Compliance)</td><td></td></tr>
        <tr><td>C-EXA</td><td class="mono">*</td><td class="mono">no restriction · every read logged</td><td></td><td>${pill('Super admin', 'ai')}</td></tr></tbody></table></div></div>`;
      root.querySelector('#add-rule').onclick = () => addRule(r.tables);
      root.querySelectorAll('[data-del]').forEach((b) => (b.onclick = async () => { await api.del('/api/users/rules/' + b.dataset.del); toast('Rule removed · logged'); rules(); }));
    }

    function addRule(tables) {
      const people = data.users.filter(grantable);
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Applies to</label><select id="who">${people.map((u) => `<option value="u:${u.id}">${esc(u.name)}</option>`).join('')}${data.teams.map((t) => `<option value="t:${t.id}">${esc(t.name)} (team)</option>`).join('')}</select></div>
        <div class="field"><label>Table</label><select id="table">${Object.keys(tables).map((t) => `<option>${t}</option>`).join('')}</select></div>
        <div class="row" style="gap:8px"><div class="field" style="flex:1"><label>Field</label><select id="field"></select></div><div class="field"><label>Op</label><select id="op"><option value="eq">equals</option><option value="in">is one of</option><option value="startswith">starts with</option></select></div><div class="field" style="flex:1"><label>Value</label><input id="value" type="text" placeholder="Absa"></div></div>
        <div class="field"><label>Note</label><input id="note" type="text" placeholder="Why this rule exists"></div></div>`);
      const fill = () => (body.querySelector('#field').innerHTML = tables[body.querySelector('#table').value].map((f) => `<option>${f}</option>`).join(''));
      body.querySelector('#table').onchange = fill; fill();
      const m = modal({ title: 'Add a row-level rule', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Add rule</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        const [k, id] = body.querySelector('#who').value.split(':');
        try { await api.post('/api/users/rules', { table: body.querySelector('#table').value, field: body.querySelector('#field').value, op: body.querySelector('#op').value, value: body.querySelector('#value').value, note: body.querySelector('#note').value, user_id: k === 'u' ? +id : null, team_id: k === 't' ? +id : null }); m.close(); toast('Rule added · logged'); rules(); }
        catch (e) { toast(e.detail, 'err'); }
      };
    }

    // kind: 'user' (the big button) or 'senior' (owner / admin / manager — whatever this person may create)
    function addUser() {
      // one form for everyone; it starts on User, the least a person can have
      const roles = ['user', ...data.me_can_create.filter((r) => r !== 'user')];
      const tools = data.tools.filter((t) => !t.admin_only);
      const teamField = isManager
        ? `<div class="field"><label>Team</label><input type="text" value="${esc(teamName || '')}" disabled><span class="small muted">People you add join your team and see only your team.</span></div>`
        : `<div class="field"><label>Team</label><input id="team" type="text" list="teams" placeholder="Type a team — a new name creates a team folder"><datalist id="teams">${data.teams.map((t) => `<option value="${esc(t.name)}">`).join('')}</datalist></div>`;
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Full name</label><input id="name" type="text" autocomplete="off"></div>
        <div class="field"><label>Work email</label><input id="email" type="email" autocomplete="off"></div>
        <div class="field"><label>Job title</label><input id="title" type="text" placeholder="Utilities analyst"></div>
        <div class="field"><label>Role</label><select id="role">${roles.map((r) => `<option value="${r}">${esc(roleName(r))}</option>`).join('')}</select><span class="small muted" id="role-help"></span></div>
        ${teamField}
        <div class="field"><label>Access ends on <span class="muted">(optional)</span></label><input id="expires" type="date"><span class="small muted">For someone outside the company — a supplier, a client. Their sign-in stops at the end of that day.</span></div>
        <div class="field" id="tools-field"><label>Tools</label><div class="cols" style="grid-template-columns:1fr 1fr">${tools.map((t) => `<label class="colbox"><input type="checkbox" value="${t.id}" style="width:auto"> ${esc(toolName(t))}</label>`).join('') || '<span class="small muted">You hold no tools to pass on.</span>'}</div>${data.tools.some((t) => t.admin_only) ? `<span class="small muted">${data.tools.filter((t) => t.admin_only).map((t) => esc(t.name)).join(', ')}: admins only.</span>` : ''}</div>
        <label class="row" style="gap:8px"><input type="checkbox" id="welcome" checked style="width:auto"> Send them the welcome mail (how to sign in, with this password)</label>
      </div>`);
      // owners and admins hold every tool; managers and users get them by grant
      const sync = () => { const r = body.querySelector('#role').value; body.querySelector('#tools-field').style.display = (r === 'owner' || r === 'admin') ? 'none' : ''; body.querySelector('#role-help').innerHTML = roleText(r); };
      body.querySelector('#role').onchange = sync; sync();
      const m = modal({ title: 'Add a user', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Create account</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        const teamIn = body.querySelector('#team');
        const team = teamIn ? teamIn.value.trim() : '';
        const tid = data.teams.find((t) => t.name.toLowerCase() === team.toLowerCase());
        try {
          const r = await api.post('/api/users', { name: body.querySelector('#name').value, email: body.querySelector('#email').value, title: body.querySelector('#title').value, role: body.querySelector('#role').value, team_id: tid ? tid.id : null, team_name: tid ? null : team || null, tools: [...body.querySelectorAll('#tools-field input[type=checkbox]:checked')].map((c) => +c.value).filter((n) => Number.isInteger(n)), expires_at: body.querySelector('#expires').value || null, welcome_mail: body.querySelector('#welcome').checked });
          m.close(); showTemp(r.user, r.temp_password, r.welcome_mail); data = await api.get('/api/users'); draw();
        } catch (e) { toast(e.detail, 'err'); }
      };
    }

    function showTemp(u, pw, wm) {
      const mailLine = !wm || wm.detail === 'not sent (unticked)' ? '' : wm.held ? `<p class="small">The welcome mail is waiting in Mailer → Drafts for you to check and send.</p>`
        : wm.ok ? `<p class="small" style="color:var(--good)">Welcome mail sent to ${esc(u.email)}.</p>` : `<p class="small" style="color:var(--crit)">The welcome mail did not go: ${esc(wm.detail)} Give them the password yourself.</p>`;
      const m = modal({ title: 'Temporary password', body: `<p>Give this to <b>${esc(u.name)}</b> (${esc(u.email)}). It is shown <b>once</b> — it is not stored anywhere readable. They'll be asked to choose their own password on first sign-in.</p><div class="copybox"><span id="pw">${esc(pw)}</span><button class="btn sm" id="cp">Copy</button></div>${mailLine}`, footer: `<button class="btn primary" data-close>Done</button>` });
      m.root.querySelector('#cp').onclick = async () => {
        // navigator.clipboard only exists on https/localhost; the LAN install is plain http, so fall back to a selection copy
        try {
          if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(pw); toast('Copied'); return; }
          const span = m.root.querySelector('#pw'); const sel = window.getSelection(); const range = document.createRange();
          range.selectNodeContents(span); sel.removeAllRanges(); sel.addRange(range);
          toast(document.execCommand && document.execCommand('copy') ? 'Copied' : 'Select the password and press Ctrl+C');
        } catch (e) { toast('Select the password and press Ctrl+C', 'err'); }
      };
    }

    async function tempPassword(u) {
      try { const r = await api.post(`/api/users/${u.id}/temp-password`); showTemp(u, r.temp_password); data = await api.get('/api/users'); draw(); } catch (e) { toast(e.detail, 'err'); }
    }

    function editUser(u) {
      // a manager changes neither role nor team; the fields show but stay put
      const roles = isManager ? [u.role] : data.me_can_create.includes(u.role) ? data.me_can_create : [u.role, ...data.me_can_create];
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Full name</label><input id="name" type="text" value="${esc(u.name)}"></div>
        <div class="field"><label>Job title</label><input id="title" type="text" value="${esc(u.title)}"></div>
        <div class="field"><label>Role</label><select id="role" ${isManager ? 'disabled' : ''}>${roles.map((r) => `<option value="${r}" ${u.role === r ? 'selected' : ''}>${esc(roleName(r))}</option>`).join('')}</select><span class="small muted" id="role-help2"></span></div>
        <div class="field"><label>Team</label><select id="team" ${isManager ? 'disabled' : ''}><option value="">—</option>${data.teams.map((t) => `<option value="${t.id}" ${u.team_id === t.id ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select></div>
        <div class="field"><label>Access ends on</label><input id="expires" type="date" value="${esc((u.expires_at || '').slice(0, 10))}"><span class="small muted">Empty means no end date. For people outside the company.</span></div>
        ${data.can_set_team_limit ? `<div class="field"><label class="row" style="gap:6px"><input type="checkbox" id="team-limited" ${u.team_limited ? 'checked' : ''} style="width:auto">Limited to their team</label><span class="small muted">Sees only their own team's people and audit trail, whatever their role. Set for everyone a manager adds.</span></div>` : ''}
        <div class="row" style="gap:16px"><label class="row" style="gap:6px"><input type="checkbox" id="active" ${u.is_active ? 'checked' : ''} style="width:auto">Account enabled</label>${u.locked ? '<label class="row" style="gap:6px"><input type="checkbox" id="unlock" style="width:auto">Unlock</label>' : ''}</div>
        <div class="row between" style="border-top:1px solid var(--line);padding-top:12px"><span class="small muted">Lost their password?</span><button class="btn sm" id="temp">Issue temporary password</button></div>
        <div class="row between"><span class="small muted">${esc(twoStep(u))}${u.mfa_method ? ' · lost or replaced the phone?' : ''}</span>${u.mfa_method ? '<button class="btn sm" id="mfa-reset">Reset two-step</button>' : ''}</div></div>`);
      const m = modal({ title: 'Edit ' + u.name, body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Save</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        const unlock = body.querySelector('#unlock');
        const patch = { name: body.querySelector('#name').value, title: body.querySelector('#title').value, is_active: body.querySelector('#active').checked, unlock: unlock ? unlock.checked : false, expires_at: body.querySelector('#expires').value || '' };
        if (!isManager) { patch.role = body.querySelector('#role').value; patch.team_id = +body.querySelector('#team').value || null; }
        const lim = body.querySelector('#team-limited'); if (lim) patch.team_limited = lim.checked;
        try { await api.patch('/api/users/' + u.id, patch); m.close(); toast('Saved · logged'); data = await api.get('/api/users'); draw(); }
        catch (e) { toast(e.detail, 'err'); }
      };
      m.root.querySelector('#temp').onclick = () => { m.close(); tempPassword(u); };
      const mr = m.root.querySelector('#mfa-reset'); if (mr) mr.onclick = () => { m.close(); resetMfa(u); };
    }

    // Sign-in codes the mailer could not deliver (CBX_CODE_FALLBACK=terminal): shown here as well as in the
    // server's window, for the people you manage, so you can read one to someone over the phone.
    async function codesWaiting(box) {
      if (!box) return;
      let r; try { r = await api.get('/api/users/codes-waiting', { quiet: true }); } catch { return; }
      if (!box.isConnected || !(r.codes || []).length) { box.innerHTML = ''; return; }
      const mins = (iso) => Math.max(0, Math.round((new Date(iso) - Date.now()) / 60000));
      box.innerHTML = `<div class="card" style="margin-bottom:16px;border-color:var(--warn)"><div class="hd"><h2>Sign-in codes waiting</h2><span class="small muted">email could not send them — give each code only to that person</span></div><div class="tbl"><table><thead><tr><th>Person</th><th>Code</th><th>From</th><th>Runs out</th></tr></thead><tbody>${r.codes.map((c) => `<tr><td><b>${esc(c.name)}</b><div class="small muted">${esc(c.email)}</div></td><td class="mono" style="font-size:18px;letter-spacing:3px">${esc(c.code.slice(0, 3))} ${esc(c.code.slice(3))}</td><td class="small">${esc(c.where)}${c.ip ? ' · ' + esc(c.ip) : ''}</td><td class="small">in ${mins(c.until)} min</td></tr>`).join('')}</tbody></table></div><div class="bd small muted">Why: ${esc(String(r.codes[0].why || '').replace(/\.+$/, ''))}. Fix it under Settings → Email; until then codes also show in the CBX window on the server.</div></div>`;
    }

    function resetMfa(u) {
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px"><p style="margin:0">${esc(u.name)} will set up two-step verification again at their next sign-in. Their remembered browsers and other sessions are signed out now — whoever holds the old phone holds nothing.</p><div class="field"><label>Why</label><input id="why" type="text" placeholder="Lost phone / new phone / asked by IT" autocomplete="off"><span class="small muted">Goes in the audit trail with your name.</span></div></div>`);
      const m = modal({ title: 'Reset two-step verification', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Reset</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        try { await api.post(`/api/users/${u.id}/mfa-reset`, { reason: body.querySelector('#why').value }); m.close(); toast('Two-step verification reset · logged'); data = await api.get('/api/users'); draw(); }
        catch (e) { toast(e.detail, 'err'); }
      };
    }

    function addTeam() {
      const body = el(`<div class="field"><label>Team or department name</label><input id="tname" type="text" placeholder="Procurement"></div><p class="small muted">A folder is created for the team on the warehouse (and OneDrive when connected). Each person you add to it gets their own folder inside.</p>`);
      const m = modal({ title: 'New team', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Create team</button>` });
      m.root.querySelector('#ok').onclick = async () => { try { await api.post('/api/users/teams', { name: body.querySelector('#tname').value }); m.close(); toast('Team folder created · logged'); data = await api.get('/api/users'); draw(); } catch (e) { toast(e.detail, 'err'); } };
    }

    draw();
  },
};
