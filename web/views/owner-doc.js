// The owner's document (1.0.58): compiled by the server from what it holds; the data backups beside it.
import { api } from '../api.js';
import { esc, toast } from '../ui.js';

const mb = (n) => `${(n / 1048576).toFixed(1)} MB`;

export default {
  title: "The owner's document",
  navRoute: 'versions',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner') { root.innerHTML = '<div class="head"><div><h1>The owner’s document</h1><p>This page is for the owner.</p></div></div>'; return; }
    root.innerHTML = '<div class="loading"><span class="spin"></span></div>';
    let doc, bk;
    try { [doc, bk] = await Promise.all([api.get('/api/owner/document'), api.get('/api/owner/backups')]); }
    catch (e) { root.innerHTML = `<div class="head"><div><h1>The owner's document</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }
    const last = (bk.snapshots || [])[0];
    root.innerHTML = `
      <div class="head"><div><h1>The owner's document</h1><p>Where is what, who owns what, how to change, release, roll back and restore — compiled from what this server holds. A copy is sealed into every release zip.</p></div>
        <div class="row" style="gap:8px"><a class="btn sm" href="/api/owner/document.html" download>Download (HTML, prints well)</a><a class="btn sm ghost" href="/api/owner/document.md" download>Markdown</a><a class="btn sm ghost" href="#/versions">Versions</a></div></div>
      <div class="card" style="margin-bottom:16px"><div class="hd"><h2>Backups of the data</h2><button class="btn sm primary" id="bk-now">Back up now</button></div>
        <div class="bd small">${bk.on ? `Every night at <b>${esc(bk.at)}</b>, ${esc(bk.keep_days)} days kept` : '<b style="color:var(--crit)">Nightly backups are switched off</b> (CBX_BACKUPS in .env)'} · into <span class="mono">${esc(bk.folder)}</span>
          · ${last ? `last: <b>${esc(last.name)}</b> — ${last.ok ? `${esc(last.databases)} database(s), ${mb(last.bytes)}` : `<span style="color:var(--crit)">did not finish: ${esc(last.note)}</span>`}` : '<b>no backup yet</b>'}</div></div>
      <div class="card"><div class="bd od-doc">${doc.html}</div></div>`;
    root.querySelector('#bk-now').onclick = async (ev) => {
      ev.target.disabled = true;
      try { await api.post('/api/owner/backups/run'); toast('Backing up — it takes a moment; this page shows it when you open it again'); } catch (e) { toast(e.detail || e.message, 'err'); ev.target.disabled = false; }
    };
  },
};
