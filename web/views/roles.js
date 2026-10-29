// Roles & permissions — the screen behind "I want to add or change role permissions".
//
// A role is a name and a list of ticks. The four built-ins are shown but can't be changed: 152
// checks in the app assume them, and an owner who accidentally unticks the wrong box on "Owner"
// would lock the whole company out with no way back in. Copy one instead — that is what the Copy
// button is for.
//
// Two rules are enforced on the server as well as here, because a screen is not security:
//   * you cannot tick a capability you do not hold yourself;
//   * you cannot touch a role that ranks at or above your own.
import { api } from '../api.js';
import { esc, toast, el, pill, confirmBox, modal } from '../ui.js';

export default async function roles(pane, ctx, { onChange } = {}) {
  pane.innerHTML = '<div class="card"><div class="bd muted">Loading roles…</div></div>';
  let d;
  try { d = await api.get('/api/roles'); } catch (e) { pane.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || 'Could not load roles')}</div></div>`; return; }

  const capsByKey = Object.fromEntries(d.capabilities.map((c) => [c.key, c]));
  const mine = new Set(d.my_caps);
  // A narrowing flag ("their own team only") takes access away, so it is never greyed out — the
  // owner does not hold it himself and must still be able to put it on a role he builds.
  const iHold = (k) => mine.has(k) || !!(capsByKey[k] || {}).narrowing;
  const changed = async () => { d = await api.get('/api/roles'); draw(); onChange && onChange(); };

  const capSummary = (r) => {
    if ((r.caps || []).includes('everything')) return 'Everything';
    if (!r.caps || !r.caps.length) return 'Nothing beyond their own work';
    return r.caps.slice(0, 3).map((k) => (capsByKey[k] || {}).label || k).join(', ') + (r.caps.length > 3 ? ` +${r.caps.length - 3} more` : '');
  };

  function draw() {
    const rows = d.roles.map((r) => `<tr>
      <td><div><b>${esc(r.name)}</b>${r.locked ? ' ' + pill('Built-in', 'neutral') : ''}</div><div class="small muted">${esc(r.description || '—')}</div></td>
      <td class="small">${esc(capSummary(r))}</td>
      <td class="num">${r.people}</td>
      <td class="num">${r.rank}</td>
      <td><div class="row" style="gap:6px;justify-content:flex-end">
        <button class="btn sm" data-view="${r.id}">${r.editable ? 'Edit' : 'View'}</button>
        <button class="btn sm ghost" data-copy="${r.id}">Copy</button>
        ${r.editable && !r.people ? `<button class="btn sm ghost" data-del="${r.id}">Delete</button>` : ''}
      </div></td></tr>`).join('');
    pane.innerHTML = `
      <div class="card"><div class="hd"><h2>Roles &amp; permissions</h2><button class="btn sm primary" id="new-role">New role</button></div>
        <div class="tbl"><table><thead><tr><th>Role</th><th>Can do</th><th>People</th><th>Rank</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
        <div class="bd small muted">Rank decides who may hand a role out: you can only give someone a role ranked below your own${d.my_rank ? ` (yours is <b>${d.my_rank}</b>)` : ''}. Built-in roles can be read and copied but not changed — the app's own checks rely on them.${d.team_limited ? ' Roles you build stay limited to your team, and you can change only the roles you built.' : ''} Every change here is written to the audit trail.</div>
      </div>`;
    pane.querySelector('#new-role').onclick = () => editor(null);
    pane.querySelectorAll('[data-view]').forEach((b) => (b.onclick = () => editor(d.roles.find((r) => r.id == b.dataset.view))));
    pane.querySelectorAll('[data-copy]').forEach((b) => (b.onclick = () => editor(null, d.roles.find((r) => r.id == b.dataset.copy))));
    pane.querySelectorAll('[data-del]').forEach((b) => (b.onclick = () => remove(d.roles.find((r) => r.id == b.dataset.del))));
  }

  async function remove(r) {
    if (!(await confirmBox(`Delete the role '${r.name}'? Nobody holds it, so nothing changes for anyone — but it disappears from the list you pick from when adding people.`, 'Delete role'))) return;
    try { const out = await api.del('/api/roles/' + r.id); toast(out.message || 'Deleted'); await changed(); }
    catch (e) { toast(e.detail || 'Could not delete', 'err'); }
  }

  // role === null → creating. `from` pre-fills the ticks from an existing role (Copy).
  function editor(role, from) {
    const src = role || from || { name: '', description: '', caps: [], rank: Math.max(1, Math.min(10, (d.my_rank || 20) - 1)) };
    const creating = !role;
    const readOnly = !creating && !role.editable;
    const held = new Set(src.caps || []);

    const capRow = (c) => {
      const can = iHold(c.key);
      // R8: someone limited to their team builds roles limited to it too; the server adds the tick anyway
      const forced = !readOnly && d.team_limited && c.key === 'team.scope';
      const on = held.has(c.key) || forced;
      const lock = readOnly || !can || forced;
      return `<label class="colbox" style="align-items:flex-start;${lock ? 'opacity:.55' : ''}" title="${esc(forced ? 'You are limited to your team, so the roles you build are too.' : can ? '' : 'You do not have this yourself, so you cannot give it away.')}">
        <input type="checkbox" value="${esc(c.key)}" style="width:auto;margin-top:3px" ${on ? 'checked' : ''} ${lock ? 'disabled' : ''}>
        <span><b>${esc(c.label)}</b><br><span class="small muted">${esc(c.meaning)}</span>${forced ? '<br><span class="small muted">Always on for roles you build.</span>' : can ? '' : '<br><span class="small muted">You don\'t have this yourself.</span>'}</span></label>`;
    };
    const groups = d.groups.filter((g) => d.capabilities.some((c) => c.group === g)).map((g) => `
      <div class="field"><label>${esc(g)}</label><div class="cols" style="grid-template-columns:1fr 1fr;gap:10px">${d.capabilities.filter((c) => c.group === g).map(capRow).join('')}</div></div>`).join('');

    const body = el(`<div style="display:flex;flex-direction:column;gap:14px">
      ${readOnly ? `<div class="err-box">${esc(role.why_not)}</div>` : ''}
      ${from ? `<div class="small muted">Starting from <b>${esc(from.name)}</b>. Give it a new name and change whatever you like — the original is untouched.</div>` : ''}
      <div class="row" style="gap:8px;align-items:flex-start">
        <div class="field" style="flex:2"><label>Name</label><input id="r-name" type="text" value="${esc(from ? from.name + ' (copy)' : src.name)}" placeholder="Reporting admin" ${readOnly ? 'disabled' : ''}></div>
        <div class="field" style="width:110px"><label>Rank</label><input id="r-rank" type="number" min="1" max="${d.my_rank ? d.my_rank - 1 : 99}" value="${src.rank}" ${readOnly ? 'disabled' : ''}></div></div>
      <div class="field"><label>What this role is for</label><input id="r-desc" type="text" value="${esc(from ? from.description : src.description || '')}" placeholder="One line the next person will read" ${readOnly ? 'disabled' : ''}></div>
      <div id="r-caps" style="display:flex;flex-direction:column;gap:14px">${groups}</div>
      <p class="small muted">Ticking <b>Everything</b> makes every other tick irrelevant — only the Owner role should have it.</p>
    </div>`);

    const boxes = () => [...body.querySelectorAll('#r-caps input[type=checkbox]')];
    const syncEverything = () => {
      const ev = boxes().find((b) => b.value === 'everything');
      const all = ev && ev.checked;
      boxes().forEach((b) => { if (b.value !== 'everything') { b.closest('label').style.opacity = all ? '.4' : (b.disabled ? '.55' : ''); } });
    };
    boxes().forEach((b) => (b.onchange = syncEverything));
    syncEverything();

    const title = creating ? (from ? `New role from ${from.name}` : 'New role') : readOnly ? role.name : `Edit ${role.name}`;
    const m = modal({
      title,
      body,
      footer: readOnly ? `<button class="btn" data-close>Close</button><button class="btn primary" id="cp">Copy into a new role</button>`
                       : `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">${creating ? 'Create role' : 'Save'}</button>`,
    });

    if (readOnly) { m.root.querySelector('#cp').onclick = () => { m.close(); editor(null, role); }; return; }

    m.root.querySelector('#ok').onclick = async () => {
      const picked = boxes().filter((b) => b.checked).map((b) => b.value);
      // A tick the caller cannot hold is disabled, so it never reaches here — but a role being
      // edited may already carry one. Keep those rather than silently stripping them.
      const keep = (src.caps || []).filter((k) => !iHold(k));
      const caps = [...new Set([...picked, ...keep])];
      const payload = { name: body.querySelector('#r-name').value, description: body.querySelector('#r-desc').value, caps, rank: +body.querySelector('#r-rank').value || 10 };
      try {
        const out = creating ? await api.post('/api/roles', payload) : await api.patch('/api/roles/' + role.id, payload);
        m.close(); toast(out.message || 'Saved'); await changed();
      } catch (e) { toast(e.detail || 'Could not save', 'err'); }
    };
  }

  draw();
}
