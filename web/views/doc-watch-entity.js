import { api } from '../api.js';
import { esc, toast, confirmBox, pill, modal } from '../ui.js';
import { openDoc, statusPill, fmtDay, daysCell, dot, whoCell, archiveDialog, bringBack, pickAccountDialog, accessDialog, recordCounts } from './doc-watch.js';

// Entity Compliance — one entity's dashboard: what needs attention, every document (one row per column),
// other documents that predate the columns, and (for admins and the team's manager) correcting the entity.
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const shortName = (s, n = 28) => (s && s.length > n ? s.slice(0, n - 1) + '…' : s || '');

// Delete refused: the entity has filed something. The server's sentence is shown as it wrote it, and
// the only thing offered next to "Close" is the thing it recommends — there is no force button here.
function refusedDelete(detail, e, counts, draw, ctx) {
  const m = modal({
    title: 'Not deleted',
    body: `<p>${esc(detail || 'That entity has documents on file.')}</p>`,
    footer: '<button class="btn" data-close>Close</button><button class="btn caution" id="d-arch">Archive instead</button>',
  });
  m.root.querySelector('#d-arch').onclick = () => { m.close(); archiveDialog(e, counts, async () => { ctx.refreshCounts && ctx.refreshCounts(); await draw(); }); };
}

export default {
  title: 'Entity Compliance',
  navRoute: 'doc-watch',
  async render(root, ctx) {
    const id = ctx.params.id;
    if (!id) { ctx.go('doc-watch'); return; }
    let page;
    let opened = false;

    const draw = async () => {
      try { page = await api.get(`/api/docwatch/entities/${encodeURIComponent(id)}`); }
      catch (e) {
        root.innerHTML = `<div class="head"><div><a class="dw-back" href="#/doc-watch">← All entities</a><h1>Entity</h1><p>${esc(e.detail || 'That entity could not be opened.')}</p></div></div>`;
        return;
      }
      const e = page.entity;
      const s = page.summary;
      const manage = !!page.can_manage;
      const isAdmin = !!page.is_admin;
      const docRow = (d, name, label) => `<tr>
        <td style="text-align:center;width:34px">${dot(d)}</td>
        <td><a href="#" data-doc="${d.id}" style="color:var(--ink);font-weight:600;text-decoration:none">${esc(name)}</a>${label ? `<span class="small muted"> · ${esc(label)}</span>` : ''}${d.notes ? `<div class="small muted">${esc(shortName(d.notes, 60))}</div>` : ''}</td>
        <td>${d.file_id ? `<a class="mono small" href="/api/docwatch/${d.id}/download?inline=1" target="_blank" rel="noopener" style="color:var(--accent)" title="${esc(d.file_name)}">${esc(shortName(d.file_name))}</a>` : '<span class="muted">—</span>'}</td>
        <td>${d.no_expiry ? '<span class="muted">never</span>' : fmtDay(d.expiry)}</td>
        <td class="mono">${daysCell(d)}</td>
        <td>${statusPill(d.status)}</td>
        <td style="text-align:right"><button class="btn sm ghost" data-doc="${d.id}">${d.file_id ? 'Open' : 'Upload'}</button></td>
      </tr>`;

      const counts = recordCounts(page.cells, page.extra);

      root.innerHTML = `
      <div class="head"><div><a class="dw-back" href="#/doc-watch">← All entities</a><h1>${esc(e.name)}</h1><p>${e.team ? `Visible to ${esc(e.team)} only` : 'Visible to everyone with the tool'}${e.notes ? ` · ${esc(e.notes)}` : ''}</p><div class="row" style="gap:8px;margin-top:8px"><span class="small muted">Responsible person</span>${e.responsible ? whoCell(e) : '<span class="small muted">not set</span>'}</div></div><div class="row" style="gap:8px">${e.archived ? pill('Archived', 'warn') : ''}${s.worst === 'expired' ? pill(plural(s.expired, 'expired', 'expired'), 'crit') : s.worst === 'expiring' ? pill(plural(s.expiring, 'expiring soon', 'expiring soon'), 'warn') : s.worst === 'missing' ? pill(plural(s.outstanding, 'outstanding', 'outstanding'), 'neutral') : s.total ? pill('All in date', 'good') : ''}</div></div>
      ${e.archived ? `<div class="card dw-archived-note"><div class="bd">
        <div><b>This entity is archived.</b> It came off the grid${e.archived_at ? ` on ${fmtDay(String(e.archived_at).slice(0, 10))}` : ''}, and everything below was kept: ${plural(counts.records, 'document record', 'document records')}${counts.filed ? `, ${counts.filed} with a file attached` : ''}.${e.archived_reason ? `<div class="small" style="margin-top:5px">Reason given: “${esc(e.archived_reason)}”</div>` : ''}</div>
        ${manage ? '<button class="btn primary" id="e-back">Bring it back</button>' : ''}
      </div></div>` : ''}
      <div class="grid dw-stats five" style="margin-bottom:16px">
        <div class="card stat"><div class="eyebrow">Expired</div><div class="v" style="color:${s.expired ? 'var(--crit)' : 'inherit'}">${s.expired}</div><div class="d">past their date</div></div>
        <div class="card stat"><div class="eyebrow">Expiring soon</div><div class="v" style="color:${s.expiring ? 'var(--warn)' : 'inherit'}">${s.expiring}</div><div class="d">inside the warning window</div></div>
        <div class="card stat"><div class="eyebrow">Outstanding</div><div class="v" style="color:var(--muted)">${s.outstanding}</div><div class="d">nothing filed or no date yet</div></div>
        <div class="card stat"><div class="eyebrow">In date</div><div class="v" style="color:${s.in_date ? 'var(--good)' : 'inherit'}">${s.in_date}</div><div class="d">${s.no_expiry ? `${s.no_expiry} never expire` : 'with a date'}</div></div>
        <div class="card stat"><div class="eyebrow">Not required</div><div class="v" style="color:var(--muted)">${s.not_required}</div><div class="d">grey on the grid</div></div>
      </div>
      ${page.attention.length ? `<div class="card dw-attn" style="margin-bottom:16px">
        <div class="hd"><h2>Needs attention</h2>${pill(plural(page.attention.length, 'document', 'documents'), page.attention.some((a) => a.status === 'expired') ? 'crit' : 'warn')}</div>
        <div class="tbl"><table><thead><tr><th>Document</th><th>Expiry</th><th>Days</th><th>Status</th><th></th></tr></thead><tbody>
          ${page.attention.map((a) => `<tr><td><b>${esc(a.title)}</b></td><td>${fmtDay(a.expiry)}</td><td class="mono">${daysCell(a)}</td><td>${statusPill(a.status)}</td><td style="text-align:right"><button class="btn sm" data-doc="${a.id}">Open</button></td></tr>`).join('')}
        </tbody></table></div></div>` : ''}
      <div class="card" style="margin-bottom:16px">
        <div class="hd"><h2>Documents</h2><span class="small muted">Click a document to open it — that is where you upload or renew its file, and type its expiry date.</span></div>
        ${page.cells.length ? `<div class="tbl"><table><thead><tr><th></th><th>Document</th><th>File</th><th>Expiry</th><th>Days</th><th>Status</th><th></th></tr></thead><tbody>
          ${page.cells.map((c) => (c.doc ? docRow(c.doc, c.type.name, c.type.label) : `<tr><td style="text-align:center">${dot(null)}</td><td><b>${esc(c.type.name)}</b><span class="small muted"> · ${esc(c.type.label)}</span></td><td colspan="5" class="muted small">not set up yet</td></tr>`)).join('')}
        </tbody></table></div>` : `<div class="bd empty">No document types have been set up yet — an admin adds the columns on the <a href="#/doc-watch" style="color:var(--accent)">grid</a>.</div>`}
        <div class="bd small muted" style="border-top:1px solid var(--line)">Need a document that isn't listed? Ask an administrator to add the column — it then appears for every entity.</div>
      </div>
      ${page.extra.length ? `<div class="card" style="margin-bottom:16px">
        <div class="hd"><h2>Other documents</h2><span class="small muted">Kept from before the columns existed.</span></div>
        <div class="tbl"><table><thead><tr><th></th><th>Document</th><th>File</th><th>Expiry</th><th>Days</th><th>Status</th><th></th></tr></thead><tbody>
          ${page.extra.map((d) => docRow(d, d.title, d.doc_type)).join('')}
        </tbody></table></div></div>` : ''}
      ${manage ? `<div class="card" style="margin-bottom:16px">
        <div class="hd"><h2>Correct this entity</h2></div>
        <div class="bd">
          <form class="dw-form" id="f-fix">
            <div class="field"><label>Entity name</label><input id="e-name" type="text" required maxlength="160" value="${esc(e.name)}"></div>
            <div class="field"><label>Responsible person</label><input id="e-resp" type="text" maxlength="80" value="${esc(e.responsible)}" placeholder="Who answers for these documents"></div>
            <div class="field"><label>Notes</label><input id="e-notes" type="text" maxlength="300" value="${esc(e.notes)}"></div>
            ${isAdmin ? `<div class="field sm"><label>Visible to</label><select id="e-team"><option value="" ${e.team_id ? '' : 'selected'}>Everyone with the tool</option>${(page.teams || []).map((t) => `<option value="${t.id}" ${t.id === e.team_id ? 'selected' : ''}>${esc(t.name)} only</option>`).join('')}</select></div>` : ''}
            <button class="btn primary" type="submit">Save</button>
          </form>
          <div class="dw-linkrow">
            ${e.responsible_user_id
              ? `${whoCell(e)}<span class="small">has an account here: reminders and messages about these documents reach them, and they can open this company.</span>`
              : `<span class="small">${e.responsible ? `<b>${esc(e.responsible)}</b> is a name written down. Nothing sent from here reaches them` : 'Nobody is responsible for this entity yet'} — set the person as an account.</span>`}
            <span class="sp"></span>
            <button class="btn sm" id="e-link">${e.responsible_user_id ? 'Change the person' : 'Set the responsible person'}</button>
          </div>
          <div class="dw-linkrow" style="margin-top:8px">
            <span class="small">Who can see this company by name — give someone just <b>${esc(e.name)}</b>, and nothing else if you choose.</span>
            <span class="sp"></span>
            <button class="btn sm" id="e-access">Who can see it</button>
          </div>
          <div class="row between" style="margin-top:16px;padding-top:14px;border-top:1px solid var(--line);flex-wrap:wrap;gap:8px">
            <span class="small muted">${e.archived ? 'This entity is archived — bring it back to put it on the grid again.' : `Stopped working with them? Archive keeps the record and takes ${esc(e.name)} off the grid.`}</span>
            ${e.archived ? '<button class="btn" id="e-back2">Bring it back</button>' : '<button class="btn caution" id="e-arch">Archive this entity</button>'}
          </div>
          ${isAdmin ? `<div class="row between" style="margin-top:12px;padding-top:14px;border-top:1px solid var(--line);flex-wrap:wrap;gap:8px"><span class="small muted">Deleting is for an entity added by mistake: it removes the entity and its document records. Uploaded files stay in the warehouse.</span><button class="btn sm ghost" id="e-del" style="color:var(--crit)">Delete this entity</button></div>` : ''}
        </div>
      </div>` : ''}`;

      root.querySelectorAll('[data-doc]').forEach((b) => (b.onclick = (ev) => { ev.preventDefault(); openDoc(b.dataset.doc, ctx, draw); }));
      const fix = root.querySelector('#f-fix');
      if (fix) fix.onsubmit = async (ev) => {
        ev.preventDefault();
        const body = { name: root.querySelector('#e-name').value, notes: root.querySelector('#e-notes').value, responsible: root.querySelector('#e-resp').value };
        const team = root.querySelector('#e-team');
        if (team) { if (team.value) body.team_id = +team.value; else body.everyone = true; }
        try { await api.patch(`/api/docwatch/entities/${e.id}`, body); toast('Entity updated'); draw(); } catch (er) { toast(er.detail || er.message, 'err'); }
      };
      const link = root.querySelector('#e-link');
      if (link) link.onclick = () => pickAccountDialog(e, draw);
      const acc = root.querySelector('#e-access');
      if (acc) acc.onclick = () => accessDialog(e, draw);
      // a link from an email lands on the document itself: ?doc=<id> opens it once
      if (ctx.params.doc && !opened) { opened = true; openDoc(ctx.params.doc, ctx, draw); }
      const arch = root.querySelector('#e-arch');
      if (arch) arch.onclick = () => archiveDialog(e, counts, async () => { ctx.refreshCounts && ctx.refreshCounts(); await draw(); });
      root.querySelectorAll('#e-back, #e-back2').forEach((b) => (b.onclick = () => bringBack(e, async () => { ctx.refreshCounts && ctx.refreshCounts(); await draw(); })));
      const del = root.querySelector('#e-del');
      if (del) del.onclick = async () => {
        if (!(await confirmBox(`Delete ${e.name} and its document records? Uploaded files stay in the warehouse.`, 'Delete entity'))) return;
        try { const r = await api.del(`/api/docwatch/entities/${e.id}`); toast(`'${e.name}' deleted (${plural(r.removed, 'record', 'records')}). Files remain in the warehouse.`); ctx.refreshCounts && ctx.refreshCounts(); ctx.go('doc-watch'); }
        catch (er) {
          // The server refuses to delete an entity that has filed something, and says why in a full
          // sentence. It is shown as written — with the way out it names, and nothing that forces it.
          if (er.status === 400) refusedDelete(er.detail, e, counts, draw, ctx);
          else toast(er.detail || er.message, 'err');
        }
      };
    };

    await draw();
  },
};
