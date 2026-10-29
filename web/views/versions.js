// Versions & sandboxes: the version injector. Upload a zip, it is unpacked and tested in a sandbox slot; a person promotes; rollback is one button.
import { api } from '../api.js';
import { esc, toast, pill, modal, confirmBox, fmtDate, fileDrop, el } from '../ui.js';
import { diffHtml } from './cexa-dev.js';

const STATUS = { staged: ['Staged · not tested', 'neutral'], sandbox: ['Tested · ready', 'good'], live: ['Live', 'good'], failed: ['Tests failed', 'crit'], rolled_back: ['Rolled back', 'neutral'], superseded: ['Replaced', 'neutral'] };
const sp = (s) => { const [t, k] = STATUS[s] || [s, 'neutral']; return pill(t, k); };

export default {
  title: 'Versions',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner') { root.innerHTML = '<div class="head"><div><h1>Versions</h1><p>This page is for the owner.</p></div></div>'; return; }
    let data, health = {};
    async function load() { [data, health] = await Promise.all([api.get('/api/versions'), api.get('/api/health').catch(() => ({}))]); }
    try { await load(); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Versions</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return; }

    // After a promote/rollback the server exits and run.bat starts it again: wait for it instead of
    // failing the very next request (which would leave the page stale and log an error).
    async function waitForRestart(label) {
      toast(`${label} · the server is restarting, hold on…`);
      await new Promise((r) => setTimeout(r, 4000));
      for (let i = 0; i < 30; i++) {
        try { await api.get('/api/health', { quiet: true }); return true; } catch (e) { /* not back yet */ }
        await new Promise((r) => setTimeout(r, 2000));
      }
      return false;
    }
    async function reloadAfter(r, label) {
      if (r.restart) {
        const back = await waitForRestart(label);
        if (!back) { root.innerHTML = `<div class="head"><div><h1>Versions</h1><p>${esc(label)}, but the server has not come back yet. Check the run.bat window on the server, then reload this page.</p></div></div>`; return; }
        toast(`${label} · back online`);
      }
      try { await load(); draw(); } catch (e) { toast(e.detail || 'Please sign in again.', 'err'); }
    }

    const testsLine = (v) => {
      const t = v.tests || {};
      if (t.skipped) return 'tests skipped (nested run)';
      if (t.ok === undefined) return t.installed_by_hand ? 'applied outside this page' : 'not tested yet';
      const c = t.council;
      const council = c ? ` · <a href="#/agents?job=${c.job}" class="${c.verdict === 'block' ? 'crit-text' : ''}">Council: ${esc(c.verdict === 'pass' ? 'passed' : c.verdict === 'block' ? 'blocked' : 'concerns')}</a>` : '';
      return `${t.passed || 0} passed · ${t.failed || 0} failed${t.new ? ` · ${t.new} new` : ''}${t.seconds ? ` · ${t.seconds} s` : ''}${t.forced ? ' · promoted with failing tests' : ''}${t.installed_by_hand ? ' · applied outside this page' : ''}${council}`;
    };
    // Which tests failed, on the row — "1 failed" on its own told nobody anything.
    const failLines = (v) => {
      const f = (v.tests || {}).failures || [];
      if (!f.length) return '';
      const shown = f.slice(0, 3).map((x) => `<div class="ver-fail mono">${esc(x)}</div>`).join('');
      return `<div class="ver-fails">${shown}${f.length > 3 ? `<div class="small muted">… and ${f.length - 3} more — see the test log</div>` : ''}</div>`;
    };

    const row = (v) => {
      const acts = [];
      if (v.status === 'sandbox') acts.push(`<button class="btn sm${(v.tests || {}).in_test ? ' ghost' : ' primary'}" data-totest="${v.id}" title="Run it in the Test area for everyone who tests (Work areas)">${(v.tests || {}).in_test ? 'Back in Test' : 'Put in Test'}</button>`);
      if (['sandbox', 'rolled_back', 'superseded'].includes(v.status)) acts.push(`<button class="btn sm ${v.status === 'sandbox' && !(v.tests || {}).in_test ? '' : 'primary'}" data-promote="${v.id}">Promote to production</button>`);
      if (v.status === 'live' && v.has_backup) acts.push(`<button class="btn sm" data-rollback="${v.id}">Roll back</button>`);
      if (v.status === 'staged') acts.push(`<button class="btn sm primary" data-retest="${v.id}">Test in sandbox</button>`);
      if (v.status === 'sandbox' || v.status === 'failed') acts.push(`<button class="btn sm ghost" data-retest="${v.id}">Re-test</button>`);
      if (v.status === 'failed') acts.push(`<button class="btn sm ghost" data-force="${v.id}" title="Promote although its tests failed — you will be shown which, and the audit trail says it was forced">Promote anyway</button>`);
      if (v.status !== 'live') acts.push(`<button class="btn sm ghost" data-diff="${v.id}">Open diff</button>`);
      if (v.test_output) acts.push(`<button class="btn sm ghost" data-log="${v.id}">Test log</button>`);
      if (v.status !== 'live') acts.push(`<button class="btn sm ghost danger" data-delete="${v.id}" title="Remove this version, its unpacked files and its zip">Delete</button>`);
      const who = v.proposal ? `C-EXA: ${esc(v.proposal.title)}` : esc(v.notes || 'Uploaded version');
      return `<div class="ver"><div><div class="tag">v${esc(v.tag)}</div><div class="env">${esc(v.env || (v.status === 'rolled_back' ? 'backup kept' : '—'))}</div></div>
        <div><b>${who}</b><div class="small muted">${fmtDate(v.created_at, { time: true })} · ${testsLine(v)}${v.promoted_at ? ` · promoted ${fmtDate(v.promoted_at, { time: true })}` : ''}${v.has_backup ? ` · backup of what it replaced kept${v.data_backup ? ' (code and data)' : ''}` : ''}</div>${failLines(v)}${v.proposal && v.proposal.diff && v.status !== 'live' ? diffHtml(v.proposal.diff, 4) : ''}</div>
        <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end"><div class="row" style="gap:6px">${(v.tests || {}).in_test && v.status !== 'live' ? pill('Tried in Test', 'info') : ''}${sp(v.status)}</div><div class="row" style="gap:6px;flex-wrap:wrap;justify-content:flex-end">${acts.join('')}</div></div></div>`;
    };

    function draw() {
      const live = data.versions.find((v) => v.status === 'live');
      const sb = data.sandboxes || [];
      // 1.0.83: a sandbox card only while it holds something; trying versions is Work areas' job
      const sbShown = sb.filter((s) => s.busy || ((data.runtime || []).find((x) => x.slot === s.slot) || {}).running);
      // Only what you can still act on stays in view: the live version, anything newer than it, and
      // anything still being tested. Older uploads that were promoted and replaced, or never made
      // it, fold into "older uploads" at the foot — the list used to grow by one row per release.
      const num = (t) => String(t || '').split('.').map((x) => parseInt(x, 10) || 0);
      const newer = (a, b) => { const x = num(a), y = num(b); for (let i = 0; i < Math.max(x.length, y.length); i++) { if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) > (y[i] || 0); } return false; };
      const isCurrent = (v) => v.status === 'live' || v.status === 'staged' || v.status === 'sandbox' || (live && newer(v.tag, live.tag));
      const current = data.versions.filter(isCurrent);
      const archived = data.versions.filter((v) => !isCurrent(v));
      const running = health.version || data.code_version;
      const liveNote = live
        ? (live.promoted_at ? `Promoted ${fmtDate(live.promoted_at, { time: true })}` : 'Applied outside this page (update.bat or copied files)') + (live.has_backup ? ' · rollback available' : '')
        : 'Installed by hand — no upload matches the running code';
      const pend = data.pending;
      const pendCard = pend ? `<div class="card" style="margin-bottom:16px;border-color:var(--info)"><div class="ver"><div><div class="tag">v${esc(pend.tag)}</div><div class="env">announced</div></div>
          <div><b>Switches at ${new Date(pend.switch_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} — <span id="pend-left" class="mono">${pend.seconds_left}</span> s to go</b><div class="small muted">Every open page shows the countdown; what people are typing is carried over the refresh. Announced by ${esc(pend.by || 'you')}. Nothing has changed on disk yet.</div></div>
          <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end">${pill('Announced', 'info')}<button class="btn sm ghost" id="pend-cancel">Cancel the update</button></div></div></div>` : '';
      root.innerHTML = `
      <div class="head"><div><h1>Versions</h1><p>Nothing reaches production without you. Promote is yours; rollback is one button.</p></div><div class="row" style="gap:8px"><a class="btn sm" href="#/owner-doc">The owner's document &amp; backups</a></div></div>
      ${pendCard}
      <div class="card" style="margin-bottom:16px">
        <div class="ver"><div><div class="tag">v${esc(running)}</div><div class="env">production</div></div><div><b>Running now</b><div class="small muted">${liveNote}${data.current && data.current !== running ? ` · VERSION file says ${esc(data.current)} — restart pending` : ''}${live && live.tag !== running ? ` · the page thought v${esc(live.tag)} was live; it is corrected at the next restart` : ''}</div></div>${pill('Live', 'good')}</div>
        ${current.map(row).join('')}
        ${!data.versions.length ? '<div class="empty">No versions uploaded yet. The running code is what was installed by hand.</div>' : ''}
        ${archived.length ? `<details class="ver-arch"><summary>${archived.length} older upload${archived.length === 1 ? '' : 's'} — superseded, kept for the record</summary>${archived.map(row).join('')}</details>` : ''}
      </div>
      ${sbShown.length ? '' : '<p class="small muted" style="margin:0 0 16px">To try a version, put it in Test from its row above; your own copy of the app is your Dev area on <a href="#/areas">Work areas</a>.</p>'}
      <div class="grid g2" style="margin-bottom:16px">
        ${sbShown.map((s) => {
          const rt = (data.runtime || []).find((x) => x.slot === s.slot) || {};
          const openable = s.busy && s.occupant.path !== undefined;
          const logins = rt.login || [];
          const playground = !s.busy && rt.running && rt.kind === 'playground';
          const title = s.busy ? (s.occupant.kind === 'version' ? 'v' : '') + esc(s.occupant.title) : (playground ? 'Playground' : 'Free');
          const sub = s.busy ? `${s.occupant.kind === 'proposal' ? 'C-EXA building' : 'Version'} · ${esc(s.occupant.status)}` : (playground ? `${esc(rt.title || 'copy of the live app')} — nothing to review, just to look and try` : 'Ready for the next build — or a playground');
          return `<div class="card stat sbx"><div class="eyebrow">${esc(s.label)} · port ${rt.port || ''}</div><div class="v">${title}</div><div class="d">${sub}</div>
          <div class="row" style="gap:6px;margin-top:10px;flex-wrap:wrap">
            ${openable ? `<button class="btn sm" data-sb-open="${s.slot}">${rt.running ? 'Restart' : 'Start'}</button>` : ''}
            ${rt.running ? `<a class="btn sm primary" href="${rt.door || `/sandbox/${s.slot}/enter`}">Go in (${rt.healthy ? 'healthy' : 'starting…'})</a><button class="btn sm ghost" data-sb-stop="${s.slot}">Stop</button>` : ''}
          </div>
          ${rt.running ? `<div class="small" style="margin-top:8px">${logins.length ? logins.map((l) => `Sign in as <b>${esc(l.email)}</b> · <span class="mono" style="user-select:all">${esc(l.password)}</span>`).join('<br>') : '<span class="muted">No sign-in made — press Restart.</span>'}</div>
          <div class="small muted" style="margin-top:4px">Sandbox password, new at every start. Opens at this address with a strip on top; <b>Back to live</b> brings you out.</div>` : ''}</div>`;
        }).join('')}
      </div>
      <div class="card"><div class="hd"><h2>Upload a version</h2><span class="small muted">zip of cbx/, web/, tests/, requirements.txt, VERSION</span></div>
        <div class="bd" style="display:flex;flex-direction:column;gap:12px">
          <div id="drop"></div>
          <div class="row" style="gap:8px"><div class="field" style="flex:1"><label>Notes</label><input id="notes" type="text" placeholder="What changed, in one line"></div><button class="btn primary" id="up" disabled style="align-self:flex-end">Upload &amp; test</button></div>
          <p class="small muted">The zip is unpacked to a sandbox and its tests run there (a few minutes); only a tested version can be promoted.</p>
          <p class="small"><b>Only upload versions you trust:</b> their tests run on this server with the server's own Windows account.</p>
        </div></div>`;
      let files = [];
      const up = root.querySelector('#up');
      root.querySelector('#drop').append(fileDrop({ label: 'Drop a version zip here or click to choose', hint: 'One .zip, up to 200 MB', multiple: false, accept: '.zip,application/zip', onFiles: (f) => { files = f.slice(0, 1); up.disabled = !files.length; } }));
      up.onclick = async () => {
        up.disabled = true; up.textContent = 'Uploading & testing…';
        try { const v = await api.upload('/api/versions/upload', files, { notes: root.querySelector('#notes').value }); toast(`v${v.tag} staged · ${(STATUS[v.status] || [v.status])[0]}`); await load(); draw(); }
        catch (e) { toast(e.detail || 'Upload failed', 'err'); up.disabled = false; up.textContent = 'Upload & test'; }
      };
      const find = (id) => data.versions.find((v) => v.id === +id);
      if (pend) {
        const leftEl = root.querySelector('#pend-left');
        const end = Date.now() + (pend.seconds_left || 0) * 1000;
        const t = setInterval(() => { if (!leftEl.isConnected) { clearInterval(t); return; } const l = Math.max(0, Math.round((end - Date.now()) / 1000)); leftEl.textContent = l; if (l === 0) { clearInterval(t); load().then(draw).catch(() => {}); } }, 1000);
        root.querySelector('#pend-cancel').onclick = async () => {
          if (!(await confirmBox(`Call off the update to v${pend.tag}? Nothing has changed on disk; the countdown disappears from everyone's screen. Logged under your name.`, 'Cancel the update'))) return;
          try { await api.post('/api/versions/pending/cancel'); toast('Update cancelled · logged'); await load(); draw(); } catch (e) { toast(e.detail || 'Could not cancel', 'err'); }
        };
      }
      // How much notice people get. The default is ten minutes: the bar on every page counts down, what
      // is being typed is carried over, and the switch runs when the timer ends. Zero switches now.
      async function askNotice(v, { forced = false } = {}) {
        return new Promise((resolve) => {
          const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
            <p>${forced ? `v${esc(v.tag)} failed its tests; you are promoting it anyway. ` : ''}The live tree is backed up first, then replaced, and the server restarts. Logged under your name.</p>
            <label class="row" style="gap:10px;align-items:center;cursor:pointer"><input type="radio" name="notice" value="later" checked> <span>Give people time to save: <input id="notice-min" type="number" min="1" max="240" value="10" style="width:64px;margin:0 6px"> minutes. Every open page shows the countdown and their work is carried over the refresh.</span></label>
            <label class="row" style="gap:10px;align-items:center;cursor:pointer"><input type="radio" name="notice" value="now"> <span>Switch now — for a security fix or an empty office. Open pages get two minutes' notice after the restart.</span></label>
          </div>`);
          const m = modal({ title: `Promote v${v.tag} to production`, body, footer: `<button class="btn" data-close>Back</button><button class="btn ${forced ? 'danger' : 'primary'}" id="notice-ok">${forced ? 'Promote anyway' : 'Promote'}</button>`, onClose: () => resolve(null) });
          m.root.querySelector('#notice-ok').onclick = () => {
            const now = m.root.querySelector('input[name=notice]:checked').value === 'now';
            const min = Math.max(1, Math.min(240, parseInt(m.root.querySelector('#notice-min').value, 10) || 10));
            resolve(now ? 0 : min); m.close();
          };
        });
      }
      async function doPromote(v, minutes, forced, override, skipTest) {
        let r;
        try { r = await api.post(`/api/versions/${v.id}/promote?minutes=${minutes}${forced ? '&force=1' : ''}${override ? '&override_council=1' : ''}${skipTest ? '&skip_test=1' : ''}`); }
        catch (e) {
          if (e.status === 409 && String(e.detail || '').startsWith('test:')) {
            // 1.0.80: Dev → Test → Live — straight to Live only on purpose
            if (await confirmBox(`v${v.tag} has not been tried in Test. Promote it to production without Test? Logged as promoted without Test.`, 'Promote without Test')) return doPromote(v, minutes, forced, override, true);
            return;
          }
          if (e.status === 409 && String(e.detail || '').startsWith('council:')) {
            // C-EXA's Governance council blocked it: say why, and promote only if the owner means it
            if (await confirmBox(`The council blocked v${v.tag}. ${String(e.detail).slice(8).trim()} Promote it anyway? This is logged as overriding the council.`, 'Promote anyway')) return doPromote(v, minutes, forced, true, skipTest);
            return;
          }
          throw e;
        }
        if (r.announced) { toast(`v${v.tag} announced — switches in ${r.announced.minutes} min · logged`); await load(); draw(); return; }
        toast(`v${v.tag} is live · logged${forced ? ' as forced' : ''}`);
        await reloadAfter(r, `v${v.tag} promoted`);
      }
      root.querySelectorAll('[data-totest]').forEach((b) => (b.onclick = async () => {
        b.disabled = true; b.innerHTML = '<span class="spin"></span> Starting Test…';
        try { const r = await api.post(`/api/areas/test/${b.dataset.totest}`); toast(r.ok ? 'In Test — everyone who tests has a sign-in on Work areas' : (r.note || 'Not started'), r.ok ? '' : 'err'); await load(); draw(); }
        catch (e) { toast(e.detail || 'Could not start Test', 'err'); b.disabled = false; }
      }));
      root.querySelectorAll('[data-promote]').forEach((b) => (b.onclick = async () => {
        const v = find(b.dataset.promote);
        const minutes = await askNotice(v);
        if (minutes === null) return;
        try { await doPromote(v, minutes, false); } catch (e) { toast(e.detail, 'err'); }
      }));
      root.querySelectorAll('[data-force]').forEach((b) => (b.onclick = async () => {
        const v = find(b.dataset.force);
        const f = (v.tests || {}).failures || [];
        const list = f.length ? `<ul class="small mono" style="margin:8px 0 0;padding-left:18px">${f.slice(0, 8).map((x) => `<li>${esc(x)}</li>`).join('')}${f.length > 8 ? `<li>… and ${f.length - 8} more</li>` : ''}</ul>` : '<p class="small muted">The log has the detail — the failures could not be read out by name.</p>';
        const body = el(`<div><p>v${esc(v.tag)} failed ${f.length || (v.tests || {}).failed || ''} test${f.length === 1 ? '' : 's'} in the sandbox. Promoting it anyway puts code live that its own checks did not pass. Do this only when you know the failure is about this server, not the code.</p>${list}<p class="small" style="margin-top:10px">The live tree is backed up first, the server restarts, and the audit trail records that you promoted with failing tests.</p></div>`);
        const m = modal({ title: `Promote v${v.tag} anyway?`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn danger" id="force-ok">Promote anyway</button>' });
        m.root.querySelector('#force-ok').onclick = async () => {
          m.close();
          const minutes = await askNotice(v, { forced: true });
          if (minutes === null) return;
          try { await doPromote(v, minutes, true); } catch (e) { toast(e.detail, 'err'); }
        };
      }));
      root.querySelectorAll('[data-delete]').forEach((b) => (b.onclick = async () => {
        const v = find(b.dataset.delete);
        if (!(await confirmBox(`Delete v${v.tag}? Its unpacked files and zip are removed from the server and it leaves this list. ${v.has_backup ? 'The backup it made when it was promoted is kept. ' : ''}Logged under your name.`, 'Delete'))) return;
        try { await api.del(`/api/versions/${v.id}`); toast(`v${v.tag} deleted · logged`); await load(); draw(); } catch (e) { toast(e.detail || 'Could not delete', 'err'); }
      }));
      root.querySelectorAll('[data-rollback]').forEach((b) => (b.onclick = async () => {
        const v = find(b.dataset.rollback);
        // 1.0.114: the database was backed up beside the code before the promote — offered here, off by default
        const dbk = v.data_backup;
        const withData = await new Promise((resolve) => {
          const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
            <p>The version v${esc(v.tag)} replaced is restored from its backup and the server restarts. Logged under your name.</p>
            ${dbk ? `<label class="row" style="gap:10px;align-items:flex-start;cursor:pointer"><input type="checkbox" id="rb-data" style="margin-top:3px"> <span>Also put the data back as it was at ${esc(fmtDate(dbk.at, { time: true }))}, just before v${esc(v.tag)} went live. <b>Everything saved since then is replaced</b> — people added since are gone, and access or roles given since are undone. Kept as they are now: anyone disabled, locked or whose access was removed stays that way, and passwords and two-step settings stay current. The audit trail since is copied back. <b>Everyone is signed out</b> and signs in again. What is replaced is kept beside the backup.</span></label>` : '<p class="small muted">Only the code goes back — this version\'s backup has no copy of the database.</p>'}
          </div>`);
          const m = modal({ title: `Roll back v${v.tag}`, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="rb-ok">Roll back</button>', onClose: () => resolve(null) });
          m.root.querySelector('#rb-ok').onclick = () => { const c = m.root.querySelector('#rb-data'); resolve(!!(c && c.checked)); m.close(); };
        });
        if (withData === null) return;
        try {
          const r = await api.post(`/api/versions/${v.id}/rollback${withData ? '?data=1' : ''}`);
          if (withData && !r.data_restored) toast(r.note || 'The code was rolled back; the data was not.', 'err');
          else toast(withData ? 'Rolled back with the data · logged' : 'Rolled back · logged');
          // 1.0.114: putting the data back signs everyone out — after the restart, the sign-in page
          if (r.signed_out) { if (r.restart) await waitForRestart(`v${v.tag} rolled back`); location.reload(); return; }
          await reloadAfter(r, `v${v.tag} rolled back`);
        } catch (e) { toast(e.detail, 'err'); }
      }));
      root.querySelectorAll('[data-retest]').forEach((b) => (b.onclick = async () => { b.disabled = true; b.textContent = 'Testing…'; try { const v = await api.post(`/api/versions/${b.dataset.retest}/retest`); toast(`v${v.tag}: ${testsLine(v)}`); await load(); draw(); } catch (e) { toast(e.detail, 'err'); await load(); draw(); } }));
      root.querySelectorAll('[data-diff]').forEach((b) => (b.onclick = async () => {
        const v = find(b.dataset.diff);
        const m = modal({ title: `v${v.tag} against live`, body: '<div class="loading"><span class="spin"></span></div>', footer: '<button class="btn" data-close>Close</button>' });
        try { const r = await api.get(`/api/versions/${v.id}/diff`); m.body.innerHTML = r.diff ? diffHtml(r.diff, 600) : '<p class="small muted">No differences from the live tree.</p>'; } catch (e) { m.body.innerHTML = `<div class="err-box">${esc(e.detail)}</div>`; }
      }));
      root.querySelectorAll('[data-log]').forEach((b) => (b.onclick = () => { const v = find(b.dataset.log); modal({ title: `v${v.tag} · test output`, body: `<div class="diff">${esc(v.test_output)}</div>`, footer: '<button class="btn" data-close>Close</button>' }); }));
      root.querySelectorAll('[data-sb-open]').forEach((b) => (b.onclick = async () => {
        b.disabled = true; b.textContent = 'Starting…';
        try { const r = await api.post(`/api/versions/sandboxes/${b.dataset.sbOpen}/open`); if (r.ok) toast(r.note ? `Sandbox running — ${r.note}` : 'Sandbox running. Sign in with the password on its card.'); else toast(r.note || 'Could not start the sandbox', 'err'); await load(); draw(); }
        catch (e) { toast(e.detail || e.message, 'err'); b.disabled = false; b.textContent = 'Start'; }
      }));
      root.querySelectorAll('[data-sb-stop]').forEach((b) => (b.onclick = async () => {
        try { await api.post(`/api/versions/sandboxes/${b.dataset.sbStop}/stop`); toast('Sandbox stopped'); await load(); draw(); } catch (e) { toast(e.detail || e.message, 'err'); }
      }));
    }
    draw();
  },
};
