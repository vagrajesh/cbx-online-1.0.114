// Work areas (1.0.80): Dev → Test → Live. Your own Dev area (the live app with your Builder drafts, your own
// throwaway data), the shared Test area (a released version everyone who tests tries first), and Live.
import { api } from '../api.js';
import { esc, toast, pill, confirmBox } from '../ui.js';

const login = (a) => (a.login || []).map((l) => `<div class="small" style="margin-top:6px">Sign in there as <b>${esc(l.email)}</b> with <span class="mono">${esc(l.password)}</span> <span class="muted">— this area only, new at every start</span></div>`).join('');
const state = (a) => (a.running ? pill(a.healthy ? 'Running' : 'Starting', a.healthy ? 'good' : 'warn') : pill('Stopped', 'neutral'));

export default {
  title: 'Work areas',
  async render(root, ctx) {
    let d;
    const load = async () => { d = await api.get('/api/areas'); };
    try { await load(); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Work areas</h1><p>${esc(e.detail || 'Could not load.')}</p></div></div>`; return; }

    const busy = (b, text) => { b.disabled = true; b.innerHTML = `<span class="spin"></span> ${esc(text)}`; };
    async function act(b, text, path, done) {
      busy(b, text);
      try { const r = await api.post(path); if (r && r.note && r.ok === false) toast(r.note, 'err'); else if (done) toast(done); } catch (e) { toast(e.detail || 'That did not work', 'err'); }
      await load(); draw();
    }

    function draw() {
      const dev = d.dev, test = d.test;
      const devCard = dev ? `<div class="card"><div class="hd"><h2>My Dev area</h2>${state(dev)}</div><div class="bd">
          <p class="small muted" style="margin:0 0 10px">The live app (v${esc(d.live.version)}) with <b>your</b> Builder drafts${dev.drafts.length ? `: ${dev.drafts.map(esc).join(', ')}` : ' (none yet — change a tool in the Builder)'}. Its data is yours alone and stays between starts. Nothing here touches production.</p>
          <div class="row" style="gap:8px;flex-wrap:wrap">
            <button class="btn sm primary" id="dev-start">${dev.running ? 'Start again with my latest drafts' : 'Start'}</button>
            ${dev.running ? `<a class="btn sm" href="${esc(dev.door)}">Open</a><button class="btn sm ghost" id="dev-stop">Stop</button>` : ''}
            <button class="btn sm ghost" id="dev-reset" title="Throw away the rows you made in your Dev area">Empty its data</button>
          </div>${dev.running ? login(dev) : ''}</div></div>` : '';
      const ready = (d.ready || []).map((v) => `<li><div class="t"><b>v${esc(v.tag)}</b><span>${esc(v.notes || '')}</span></div>${v.in_test ? pill('Tried in Test', 'info') : ''}<button class="btn sm" data-test="${v.id}">${test && test.version === v.tag && test.running ? 'Restart in Test' : 'Put in Test'}</button></li>`).join('');
      const testCard = test ? `<div class="card"><div class="hd"><h2>Test</h2>${state(test)}</div><div class="bd">
          <p class="small muted" style="margin:0 0 10px">${test.version && test.running ? `<b>v${esc(test.version)}</b> is in Test — try it before it goes live.` : 'Nothing in Test. A released version that passed its tests goes here for everyone who tests to try, before the owner promotes it.'}</p>
          <div class="row" style="gap:8px">${test.running ? `<a class="btn sm primary" href="${esc(test.door)}">Open</a>` : ''}${test.running && ctx.me.role !== 'user' && d.can_build ? '<button class="btn sm ghost" id="test-stop">Take it out of Test</button>' : ''}</div>
          ${test.running ? login(test) : ''}
          ${d.can_build && ready ? `<div class="eyebrow" style="margin-top:14px">Ready to try</div><ul class="list">${ready}</ul>` : ''}</div></div>` : '';
      const liveCard = `<div class="card"><div class="hd"><h2>Live</h2>${pill('Production', 'good')}</div><div class="bd"><p class="small muted" style="margin:0">v${esc(d.live.version)} — what everyone uses. ${d.can_promote ? 'You promote a version from <a href="#/versions">Versions</a> once it has been in Test.' : 'The owner promotes a version once it has been in Test.'}</p></div></div>`;
      const taken = (d.drafts_taken || []).length ? `<p class="small muted" style="margin-top:12px">Being changed by others: ${d.drafts_taken.map((x) => `${esc(x.slug)} (${esc(x.who)})`).join(', ')}.</p>` : '';
      const others = (d.others || []).length ? `<div class="card" style="margin-top:16px"><div class="hd"><h2>Everyone's Dev areas</h2></div><ul class="list">${d.others.map((a) => `<li><div class="t"><b>${esc(a.who)}</b><span>${esc(a.title || '—')}</span></div>${state(a)}${a.running ? `<button class="btn sm ghost" data-stop="${esc(a.slot)}">Stop</button>` : ''}</li>`).join('')}</ul></div>` : '';
      root.innerHTML = `<div class="head"><div><h1>Work areas</h1><p>Dev → Test → Live. Build in your Dev area, try it in Test, the owner puts it live.</p></div></div>
        <div class="grid g3" style="align-items:start">${devCard}${testCard}${liveCard}</div>${taken}${others}`;
      const on = (id, fn) => { const b = root.querySelector(id); if (b) b.onclick = () => fn(b); };
      on('#dev-start', (b) => act(b, 'Starting your Dev area…', '/api/areas/dev/start', 'Your Dev area is running — press Open'));
      on('#dev-stop', (b) => act(b, 'Stopping…', '/api/areas/dev/stop'));
      on('#dev-reset', async (b) => { if (await confirmBox('Throw away every row you made in your Dev area? Your drafts are not touched.', 'Empty it')) act(b, 'Emptying…', '/api/areas/dev/reset', 'Your Dev area is empty'); });
      on('#test-stop', (b) => act(b, 'Stopping…', '/api/areas/test/stop'));
      root.querySelectorAll('[data-test]').forEach((b) => (b.onclick = () => act(b, 'Starting Test…', `/api/areas/test/${b.dataset.test}`, 'In Test — everyone who tests can open it')));
      root.querySelectorAll('[data-stop]').forEach((b) => (b.onclick = () => act(b, 'Stopping…', `/api/areas/${b.dataset.stop}/stop`)));
    }
    draw();
  },
};
