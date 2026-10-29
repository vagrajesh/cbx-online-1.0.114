import { api } from '../api.js';
import { esc, toast, modal, pill, fmtDate, fmtNum, el } from '../ui.js';

const FICO = { pdf: ['pdf', 'PDF'], xlsx: ['xls', 'XLS'], csv: ['csv', 'CSV'], docx: ['doc', 'DOC'], flow: ['csv', 'FLW'], other: ['doc', 'FILE'] };
const fico = (k) => { const [c, t] = FICO[k] || FICO.other; return `<span class="fico ${c}">${t}</span>`; };
const KIND_LABEL = { pdf: 'PDF', xlsx: 'Workbook', csv: 'CSV', docx: 'Word', flow: 'Workflow', other: 'File' };
const PIPE = { queued: ['Queued', 'neutral'], '': ['Queued', 'neutral'], reading: ['Reading', 'info', true], ocr: ['OCR', 'neutral', true], detected: ['Detected', 'info'], added: ['Added', 'good'], merged: ['Merged', 'good'], check: ['VAT check', 'warn'], unknown: ['Unknown', 'neutral'] };
const pipePill = (s) => { const [t, k, r] = PIPE[s] || [s, 'neutral']; return pill(t, k, r); };

export default {
  title: 'Data warehouse',
  async render(root, ctx) {
    if (ctx.me.role === 'user') return userView(root, ctx);
    return adminView(root, ctx);
  },
};

// ---------------------------------------------------------------- admin ---------------------------------------------------------------
async function adminView(root, ctx) {
  let tree, stats, selected = null, open = new Set();
  const load = async () => { [tree, stats] = await Promise.all([api.get('/api/warehouse/tree'), api.get('/api/warehouse/stats')]); };
  await load();
  const od = tree.onedrive;
  const odPill = () => od.connected
    ? `<span class="nr"><i style="background:#0F6CBD"></i>OneDrive · ${od.last_sync ? 'synced ' + fmtDate(od.last_sync) : 'connected'}${od.pending ? ` · ${od.pending} pending` : ''}</span>`
    : `<span class="nr"><i style="background:var(--line-2)"></i>OneDrive · not connected</span>`;

  root.innerHTML = `
  <div class="head"><div><h1>Data warehouse</h1><p>${od.connected ? 'Lives on OneDrive.' : 'Lives on this server' + (od.enabled ? ' — OneDrive is switched on but not set up yet.' : '; switch on OneDrive to mirror it.')} Every team gets a folder, every person gets a folder inside it, and every input is also kept in the shared library.</p></div>
    <div class="row">${odPill()}${ctx.me.role === 'owner' ? '<button class="btn sm" id="od-test">Test OneDrive</button>' : ''}</div></div>
  <div class="grid g3" style="margin-bottom:16px">
    <div class="card stat"><div class="eyebrow">Stored</div><div class="v num">${fmtNum(stats.files)}</div><div class="d">files · ${esc(stats.size_label || stats.gb + ' GB')} ${od.connected ? 'on OneDrive' : 'on this server'}</div></div>
    <div class="card stat"><div class="eyebrow">Teams</div><div class="v num">${fmtNum(stats.teams)}</div><div class="d">${stats.people} people folder${stats.people === 1 ? '' : 's'}${stats.teams_this_month ? ` · ${stats.teams_this_month} created this month` : ''}</div></div>
    <div class="card stat"><div class="eyebrow">Inbox</div><div class="v num">${fmtNum(stats.inbox_waiting)}</div><div class="d">waiting to be read</div></div>
  </div>
  <div class="grid g12">
    <div>
      <div class="card" style="margin-bottom:16px"><div class="hd"><h2>Folders</h2><span class="small muted">${esc(tree.display_root.replace('/', ' › '))}</span></div>
        <ul class="tree" id="tree"></ul>
        <div class="bd small muted" style="border-top:1px solid var(--line)">A new team (People &amp; access → New team) gets its folder here${od.connected ? ' and on OneDrive' : ''}. New user → their folder appears under their team. Only they, their admins and C-EXA can see inside it.</div>
      </div>
      <div id="drop"></div>
    </div>
    <div style="display:flex;flex-direction:column;gap:16px">
      <div class="card" id="folder-card"><div class="hd"><h2>Pick a folder</h2></div><div class="bd small muted">Click a folder on the left to see what's inside.</div></div>
      <div class="card"><div class="hd"><h2>Inbox → master sheet</h2><span class="small muted">Live pipeline · every 30 s</span></div><div id="pipe"></div></div>
    </div>
  </div>`;

  // ---- tree
  const treeEl = root.querySelector('#tree');
  const drawTree = () => {
    const lib = tree.roots.find((r) => r.path === 'Shared library');
    const teams = tree.roots.find((r) => r.path === 'Teams');
    const ico = { PDFs: 'pdf|PDF', Workflows: 'csv|FLW', 'Master sheets': 'xls|XLS' };
    let html = '';
    if (lib) {
      html += `<li class="grp" data-f="${lib.id}"><span class="fold">▾</span><b>Shared library</b><span class="hint">everything, from everyone</span></li>`;
      html += lib.children.map((c) => { const [k, t] = (ico[c.name] || 'doc|DIR').split('|'); return `<li class="lvl1 ${selected === c.id ? 'sel' : ''}" data-f="${c.id}"><span class="fico ${k}">${t}</span>${esc(c.name)}<span class="n">${fmtNum(c.total)}</span></li>`; }).join('');
    }
    if (teams) {
      html += `<li class="grp" data-f="${teams.id}"><span class="fold">▾</span><b>Teams</b><span class="hint">one folder per department</span></li>`;
      const today = new Date().toDateString();
      for (const t of teams.children) {
        const isOpen = open.has(t.id);
        const fresh = t.created_at && new Date(t.created_at).toDateString() === today;
        html += `<li class="lvl1 ${isOpen ? 'open' : ''} ${selected === t.id ? 'sel' : ''}" data-f="${t.id}" data-toggle="${t.id}"><span class="fold">${isOpen ? '▾' : '▸'}</span>${esc(t.name)}${fresh ? `${pill('Created today', 'good')}` : `<span class="n">${t.children.length} ${t.children.length === 1 ? 'person' : 'people'}</span>`}</li>`;
        if (isOpen) {
          for (const p of t.children) html += `<li class="lvl2 ${selected === p.id ? 'sel' : ''}" data-f="${p.id}">${esc(p.name)}<span class="n">${p.children.map((c) => c.name).join(' · ') || ''}</span></li>`;
          if (!t.children.length) html += `<li class="lvl2 muted">No people yet</li>`;
        }
      }
    }
    treeEl.innerHTML = html;
    treeEl.querySelectorAll('li[data-f]').forEach((li) => (li.onclick = (e) => {
      const id = +li.dataset.f;
      if (li.dataset.toggle && (e.target.classList.contains('fold') || selected === id)) { open.has(id) ? open.delete(id) : open.add(id); }
      else if (li.dataset.toggle) open.add(id);
      selected = id; drawTree(); showFolder(id);
    }));
  };
  drawTree();

  // ---- folder contents
  async function showFolder(id) {
    const card = root.querySelector('#folder-card');
    let d;
    try { d = await api.get(`/api/warehouse/folders/${id}/files`); } catch (e) { toast(e.detail, 'err'); return; }
    const rows = d.rows.map((r) => r.row === 'folder'
      ? `<tr data-open="${r.id}" style="cursor:pointer"><td><b>${esc(r.name)}</b></td><td>Folder</td><td class="num">${r.items}</td><td>${r.library ? pill(r.library, 'good') : r.own_only ? pill('Theirs only', 'neutral') : '<span class="muted">—</span>'}</td><td class="muted">${fmtDate(r.modified)}</td></tr>`
      : `<tr><td class="mono"><a href="/api/warehouse/files/${r.id}/download" style="color:inherit">${esc(r.name)}</a></td><td>${r.source === 'result' ? 'Result' : KIND_LABEL[r.kind] || 'File'}</td><td class="num">${r.pages || '—'}</td><td>${r.source === 'result' ? pill('Theirs only', 'neutral') : r.library ? pill(r.library, 'good') : '<span class="muted">—</span>'}</td><td class="muted">${fmtDate(r.modified)}</td></tr>`).join('');
    card.innerHTML = `<div class="hd"><h2>${esc(d.folder.crumbs.join(' › '))}</h2><span class="small muted mono">${esc(d.display_path)}</span></div>
      <div class="tbl"><table><thead><tr><th>Name</th><th>Kind</th><th>Items</th><th>Also in shared library</th><th>Modified</th></tr></thead><tbody>${rows || '<tr><td colspan="5" class="empty">Nothing here yet.</td></tr>'}</tbody></table></div>`;
    card.querySelectorAll('[data-open]').forEach((tr) => (tr.onclick = () => { selected = +tr.dataset.open; drawTree(); showFolder(selected); }));
  }

  // ---- pipeline
  async function drawPipe() {
    let p;
    try { p = await api.get('/api/warehouse/pipeline'); } catch (e) { return; } // polled every 15 s; a blip must not throw an unhandled error
    const pipe = root.querySelector('#pipe');
    if (!pipe) return; // view was navigated away between ticks
    pipe.innerHTML = `<div class="tbl"><table><thead><tr><th>File</th><th>From</th><th>Detected as</th><th>Goes to</th><th>Status</th></tr></thead><tbody>
      ${p.rows.length ? p.rows.slice(0, 12).map((r) => `<tr><td class="mono">${esc(r.file)}</td><td>${esc(r.from)}</td><td class="${r.detected_as ? '' : 'muted'}">${esc(r.detected_as || (r.status === 'ocr' ? 'Unknown · scanned' : '—'))}</td><td class="${r.goes_to ? '' : 'muted'}">${esc(r.goes_to || '—')}</td><td>${pipePill(r.status)}</td></tr>`).join('') : '<tr><td colspan="5" class="empty">Nothing in anyone\'s Inbox. Drop a PDF to see the pipeline work.</td></tr>'}
    </tbody></table></div>`;
  }
  drawPipe();
  const timer = setInterval(drawPipe, 15000);

  // ---- drop zone
  root.querySelector('#drop').append(dropZone('Drop anything here', 'Goes to your folder and the shared library', async (files) => {
    const r = await api.upload('/api/warehouse/upload', files);
    toast(r.message + (r.duplicates ? ` ${r.duplicates} look${r.duplicates === 1 ? 's' : ''} like a copy.` : ''), r.duplicates ? 'warn' : '');
    if (r.duplicates) dupModal(r.files.filter((f) => f.duplicates.length));
    await load(); drawTree(); drawPipe(); if (selected) showFolder(selected);
  }));

  const t = root.querySelector('#od-test');
  if (t) t.onclick = async () => { t.disabled = true; try { const r = await api.post('/api/warehouse/onedrive/test'); modal({ title: r.ok ? 'OneDrive is connected' : 'OneDrive is not connected', body: `<p>${esc(r.message)}</p>${r.ok ? '' : '<p class="small muted">Setup steps are in docs/onedrive-setup.md.</p>'}`, footer: '<button class="btn primary" data-close>OK</button>' }); } catch (e) { toast(e.detail, 'err'); } t.disabled = false; };

  return () => clearInterval(timer);
}

// ---------------------------------------------------------------- user ----------------------------------------------------------------
async function userView(root, ctx) {
  const draw = async () => {
    const m = await api.get('/api/warehouse/mine');
    root.innerHTML = `
    <div class="head"><div><h1>My folder</h1><p>Anything you drop here is kept safe and also goes into the shared library, so C-EXA can work on it.</p></div></div>
    <div class="grid g21">
      <div class="card"><div class="hd"><h2>${esc(m.folder.name)}</h2><span class="small muted mono">${esc(m.display_root)}/${esc(m.folder.path)}</span></div>
        <div class="tbl"><table><thead><tr><th>Folder</th><th>Items</th><th>Also in shared library</th><th>Last added</th><th></th></tr></thead><tbody>
          ${m.subs.map((s) => `<tr><td><b>${esc(s.name)}</b><div class="small muted">${{ Inbox: 'Drop PDFs and files here', Work: 'Files you are working on', Results: 'What C-EXA made for you' }[s.name] || ''}</div></td><td class="num">${s.files}</td><td>${s.library ? pill(s.library, 'good') : pill('Yours only', 'neutral')}</td><td class="muted">${fmtDate(s.modified)}</td><td><button class="btn sm" data-open="${s.id}">Open</button></td></tr>`).join('')}
        </tbody></table></div>
        <div id="sub"></div></div>
      <div id="drop"></div>
    </div>`;
    root.querySelector('#drop').append(dropZone('Drop anything here', 'Goes to your folder and the shared library', async (files) => {
      const r = await api.upload('/api/warehouse/upload', files);
      toast(r.message, r.duplicates ? 'warn' : '');
      if (r.duplicates) dupModal(r.files.filter((f) => f.duplicates.length));
      draw();
    }));
    root.querySelectorAll('[data-open]').forEach((b) => (b.onclick = async () => {
      const d = await api.get(`/api/warehouse/folders/${b.dataset.open}/files`);
      root.querySelector('#sub').innerHTML = `<div class="hd" style="border-top:1px solid var(--line)"><h2>${esc(d.folder.name)}</h2><span class="small muted">${d.rows.length} file${d.rows.length === 1 ? '' : 's'}</span></div>
        <ul class="list">${d.rows.length ? d.rows.map((f) => `<li>${fico(f.kind)}<div class="t"><b>${esc(f.name)}</b><span>${fmtDate(f.modified, { rel: true })}${f.detected_as ? ' · ' + esc(f.detected_as) : ''}</span></div><a class="btn sm" href="/api/warehouse/files/${f.id}/download">Open</a></li>`).join('') : '<li><div class="t"><span class="muted">Nothing here yet.</span></div></li>'}</ul>`;
    }));
  };
  await draw();
}

// ---------------------------------------------------------------- shared --------------------------------------------------------------
export function dropZone(label, hint, onFiles) {
  const box = el(`<label class="drop wh-drop"><b>${esc(label)}</b>${esc(hint)}<input type="file" multiple style="display:none"></label>`);
  const input = box.querySelector('input');
  const handle = async (files) => {
    if (!files.length) return;
    box.classList.add('busy'); box.querySelector('b').textContent = `Saving ${files.length} file${files.length === 1 ? '' : 's'}…`;
    try { await onFiles([...files]); } catch (e) { toast(e.detail || e.message, 'err'); }
    box.classList.remove('busy'); box.querySelector('b').textContent = label;
  };
  input.onchange = () => handle(input.files);
  box.ondragover = (e) => { e.preventDefault(); box.classList.add('over'); };
  box.ondragleave = () => box.classList.remove('over');
  box.ondrop = (e) => { e.preventDefault(); box.classList.remove('over'); handle(e.dataTransfer.files); };
  return box;
}

function dupModal(files) {
  modal({ title: 'This looks familiar', body: `<p>${files.length === 1 ? 'One file looks' : files.length + ' files look'} like something already in the warehouse. ${files.length === 1 ? 'It was' : 'They were'} saved anyway — delete it if it's a mistake.</p><ul class="list">${files.map((f) => `<li>${fico(f.kind)}<div class="t"><b>${esc(f.name)}</b><span>${esc(f.duplicate_note)}</span></div></li>`).join('')}</ul>`, footer: '<button class="btn primary" data-close>OK</button>' });
}
