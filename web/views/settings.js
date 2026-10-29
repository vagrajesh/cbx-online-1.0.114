import { api } from '../api.js';
import { esc, toast, modal, el, fmtDate } from '../ui.js';
import { lang, LANGS } from '../lang.js';
import { mfaPanel } from '../mfa.js';

export default {
  title: 'Settings',
  async render(root, ctx) {
    const isOwner = ctx.me.role === 'owner';
    if (ctx.tab === 'branding') {
      // 1.0.83: the product's name and look apply to everyone — their own tab, not under "My settings"
      root.innerHTML = `<div class="head"><div><h1>Branding</h1><p>The product's name, logo, colour and the AI's name — everyone sees these.</p></div></div><div id="branding" style="max-width:720px"></div>`;
      if (isOwner) branding(root.querySelector('#branding')); else root.querySelector('#branding').innerHTML = '<div class="card"><div class="bd muted">For the owner.</div></div>';
      return;
    }
    const d = await api.get('/api/settings');
    // 1.0.114: the person's look — theme, menu, text size (web/theme-boot.js knows the themes)
    const LOOK = window.CBX_LOOK || { THEMES: [], clean: (p) => p || {}, classes: () => [] };
    let look = LOOK.clean(d.prefs || {});
    root.innerHTML = `
    <div class="head"><div><h1>My settings</h1><p>Only yours. Who may use what is set under People &amp; access.</p></div></div>
    <div class="grid g2">
      <div style="display:flex;flex-direction:column;gap:16px">
      <div class="card"><div class="hd"><h2>Profile</h2></div><div class="bd grid" style="gap:14px">
        <div class="field"><label>Name</label><input id="name" type="text" value="${esc(d.user.name)}"></div>
        <div class="field"><label>Email</label><input type="email" value="${esc(d.user.email)}" disabled></div>
        <div class="field"><label>Team</label><input type="text" value="${esc(d.user.team || '—')}" disabled></div>
        <div class="row"><button class="btn primary sm" id="save">Save</button></div></div></div>
      </div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="card"><div class="hd"><h2>Appearance</h2><span class="small muted">only yours</span></div><ul class="list">
          <li><div class="t"><b>Menu</b><span>A panel on the left, or along the top.</span></div><div class="seg" id="look-menu" role="group" aria-label="Menu">${[['side', 'Side'], ['top', 'Top']].map(([k, v]) => `<button type="button" data-menu="${k}" aria-pressed="${look.menu === k}">${v}</button>`).join('')}</div></li>
          <li><div class="t"><b>Text size</b><span>Larger draws the pages and the menu a little bigger.</span></div><div class="seg" id="look-size" role="group" aria-label="Text size">${[['standard', 'Standard'], ['large', 'Larger']].map(([k, v]) => `<button type="button" data-size="${k}" aria-pressed="${look.size === k}">${v}</button>`).join('')}</div></li>
          <li><div class="t"><b>Language</b><span>Tool pages, their menu names and dates and numbers. Arabic reads right to left.</span></div><select id="lang" style="width:auto">${Object.entries(LANGS).map(([k, v]) => `<option value="${k}" ${lang.get() === k ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select></li></ul></div>
        <div class="card"><div class="hd"><h2>Security</h2><span class="small muted">Two-step verification</span></div><div id="mfa-host"></div><ul class="list" style="border-top:1px solid var(--line)">
          <li><div class="t"><b>Password</b><span>Temporary passwords must be replaced on first sign-in</span></div><button class="btn sm" id="chpw">Change</button></li>
          <li><div class="t"><b>Active sessions</b><span>${d.sessions.map((s) => `${esc(s.agent.split(')')[0].split('(').pop() || 'device')} · ${esc(s.ip)} · ${fmtDate(s.last_seen, { rel: true })}`).join('<br>')}</span></div><button class="btn sm" id="others">Sign out others</button></li></ul></div>
      </div></div>
    <div class="card" style="margin-top:16px"><div class="hd"><h2>Theme</h2><span class="small muted">Classic is what everyone starts with</span></div>
      <div class="xs-themes" id="look-themes">${LOOK.THEMES.map((t) => `<button type="button" class="xs-thc ${LOOK.classes({ theme: t.key }).filter((c) => !c.startsWith('menu-')).join(' ')}" data-theme="${esc(t.key)}" aria-pressed="${look.theme === t.key}">
        <span class="xs-thp" style="background:var(--bg)"><span class="xs-thp-s" style="background:var(--side);border-right:1px solid var(--sideRule)"><i style="background:var(--ai)"></i><i style="background:var(--sideOn)"></i><i style="background:var(--sideRule)"></i></span><span class="xs-thp-m"><i style="background:var(--ink);width:46%"></i><i class="c" style="background:var(--surf);border:1px solid var(--rule)"></i><i style="background:var(--acc);width:30%;height:8px"></i></span></span>
        <span class="xs-thn">${t.key === LOOK.DEFAULT ? '<em>Default</em>' : ''}<b>${esc(t.name)}</b><span>${esc(t.line)}</span></span></button>`).join('')}</div></div>`;
    // saved for this person on the server (any browser they sign in on), applied at once — no reload
    const saveLook = async (change) => {
      const before = look;
      look = LOOK.clean({ ...look, ...change });
      window.dispatchEvent(new CustomEvent('cbx:look', { detail: look }));
      paintLook();
      try { await api.patch('/api/settings', { prefs: look }); }
      catch (e) { look = before; window.dispatchEvent(new CustomEvent('cbx:look', { detail: look })); paintLook(); toast(e.detail || 'Could not save that.', 'err'); }
    };
    const paintLook = () => {
      root.querySelectorAll('[data-theme]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.theme === look.theme)));
      root.querySelectorAll('[data-menu]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.menu === look.menu)));
      root.querySelectorAll('[data-size]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.size === look.size)));
    };
    root.querySelectorAll('[data-theme]').forEach((b) => (b.onclick = () => saveLook({ theme: b.dataset.theme })));
    root.querySelectorAll('[data-menu]').forEach((b) => (b.onclick = () => saveLook({ menu: b.dataset.menu })));
    root.querySelectorAll('[data-size]').forEach((b) => (b.onclick = () => saveLook({ size: b.dataset.size })));
    if (ctx.viewing) root.querySelectorAll('[data-theme],[data-menu],[data-size]').forEach((b) => { b.disabled = true; });
    root.querySelector('#lang').onchange = (e) => { lang.set(e.target.value); toast(`Language: ${LANGS[e.target.value]}`); };
    root.querySelector('#save').onclick = async () => {
      const name = root.querySelector('#name').value.trim();
      if (!name) { toast('Your name can\'t be empty.', 'err'); return; }
      try { await api.patch('/api/settings', { name }); toast('Saved'); } catch (e) { toast(e.detail || 'Could not save', 'err'); }
    };
    root.querySelector('#others').onclick = async () => { try { const r = await api.post('/api/auth/sign-out-others'); toast(`Signed out ${r.revoked} other session${r.revoked === 1 ? '' : 's'}`); } catch (e) { toast(e.detail || 'Could not sign out other sessions', 'err'); } };
    const chpw = () => {
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px"><div class="field"><label>Current password</label><input id="cur" type="password"></div><div class="field"><label>New password</label><input id="n1" type="password" placeholder="At least 12 characters"></div><div class="field"><label>Repeat</label><input id="n2" type="password"></div></div>`);
      const m = modal({ title: 'Change password', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Change</button>` });
      m.root.querySelector('#ok').onclick = async () => { try { await api.post('/api/auth/change-password', { current_password: body.querySelector('#cur').value, new_password: body.querySelector('#n1').value, repeat: body.querySelector('#n2').value }); m.close(); toast('Password changed'); } catch (e) { toast(e.detail, 'err'); } };
    };
    root.querySelector('#chpw').onclick = chpw;
    try { const st = await api.get('/api/auth/mfa', { quiet: true }); mfaPanel(root.querySelector('#mfa-host'), { who: d.user, mfa: st, product: ctx.product }); }
    catch (e) { root.querySelector('#mfa-host').innerHTML = `<div class="bd small muted">${esc(e.detail || 'Two-step verification is not available right now.')}</div>`; }
  },
};

// ---- Branding (owner) ----------------------------------------------------------------------
// Product name, organisation, tagline, the AI's name, logo, favicon, accent. Saved on the server
// (data\branding), so it survives updates; the .env values are only the defaults.
async function branding(box) {
  let b;
  try { b = await api.get('/api/branding'); } catch (e) { box.innerHTML = `<div class="card"><div class="bd err-box">${esc(e.detail || 'Could not load branding')}</div></div>`; return; }
  const draw = () => {
    const mark = b.has_logo ? `<div class="mark"><img src="/api/branding/logo?v=${b.logo_v || 0}" alt=""></div>` : `<div class="mark x-word"><img src="${new URL('../img/cbx-wordmark-light.png', import.meta.url).href}" alt="CBX"></div>`; // 1.0.114: the CBX wordmark until a logo is uploaded
    box.innerHTML = `
    <div class="card"><div class="hd"><h2>Branding</h2><span class="small muted">applies to everyone</span></div><div class="bd grid" style="gap:14px">
      <div class="brand-preview no-ai-rename">${mark}<div class="t"><b>${esc(b.product)}</b><span>${esc(b.org)}${b.tagline ? ' · ' + esc(b.tagline) : ''}</span></div>${b.has_favicon ? `<img class="fav" src="/api/branding/favicon?v=${b.favicon_v || 0}" alt="" title="Browser tab icon">` : ''}</div>
      <div class="row" style="gap:8px;align-items:flex-start"><div class="field" style="flex:1"><label>Product name</label><input id="b-product" type="text" value="${esc(b.product)}" placeholder="CBX-Connect"></div><div class="field" style="flex:1"><label>Organisation</label><input id="b-org" type="text" value="${esc(b.org)}" placeholder="CBRE Excellerate"></div></div>
      <div class="row" style="gap:8px;align-items:flex-start"><div class="field" style="flex:1"><label>Tagline</label><input id="b-tagline" type="text" value="${esc(b.tagline)}" placeholder="Digital & Transformation"></div><div class="field" style="flex:1"><label>The AI's name</label><input id="b-ai" type="text" value="${esc(b.ai_name)}" placeholder="C-EXA" class="no-ai-rename"></div></div>
      <div class="field"><label>Accent colour</label><div class="row" style="gap:8px"><input id="b-accent-pick" type="color" value="${/^#[0-9a-fA-F]{6}$/.test(b.accent || '') ? esc(b.accent) : '#0B8A63'}" style="width:44px;height:36px;padding:2px;border:1px solid var(--line-2);border-radius:var(--r-sm);background:var(--surface)"><input id="b-accent" type="text" value="${esc(b.accent || '')}" placeholder="#0B8A63 (empty = default)" style="flex:1"></div></div>
      <div class="row" style="gap:8px;flex-wrap:wrap"><button class="btn primary sm" id="b-save">Save branding</button>
        <button class="btn sm" id="b-logo">${b.has_logo ? 'Replace logo' : 'Upload logo'}</button>${b.has_logo ? '<button class="btn sm ghost" id="b-logo-x">Remove logo</button>' : ''}
        <button class="btn sm" id="b-fav">${b.has_favicon ? 'Replace tab icon' : 'Upload tab icon'}</button>${b.has_favicon ? '<button class="btn sm ghost" id="b-fav-x">Remove tab icon</button>' : ''}
        <input type="file" id="b-logo-file" accept=".png,.svg,.jpg,.jpeg,.gif,.webp" style="display:none"><input type="file" id="b-fav-file" accept=".png,.ico,.svg" style="display:none"></div>
      <p class="small muted">The logo sits inside the dark green tile at the top of the sidebar and on the sign-in page — white lettering on a transparent background works best (PNG or SVG, under 2 MB). The tab icon is a small square PNG or ICO. Renaming the AI renames her everywhere on screen and in her own messages.</p>
    </div></div>`;
    const apply = (nb) => { b = nb; window.dispatchEvent(new CustomEvent('cbx:branding', { detail: b })); draw(); };
    box.querySelector('#b-accent-pick').oninput = (e) => { box.querySelector('#b-accent').value = e.target.value.toUpperCase(); };
    box.querySelector('#b-save').onclick = async () => {
      const body = { product: box.querySelector('#b-product').value, org: box.querySelector('#b-org').value, tagline: box.querySelector('#b-tagline').value, ai_name: box.querySelector('#b-ai').value, accent: box.querySelector('#b-accent').value.trim() };
      try { apply(await api.patch('/api/branding', body)); toast('Branding saved — everyone sees it on their next page.'); } catch (e) { toast(e.detail || 'Could not save', 'err'); }
    };
    const upload = (kind, input) => async () => {
      if (!input.files.length) return;
      try { apply(await api.upload(`/api/branding/${kind}`, [input.files[0]])); toast(kind === 'logo' ? 'Logo updated' : 'Tab icon updated'); } catch (e) { toast(e.detail || 'Could not upload', 'err'); }
      input.value = '';
    };
    const lf = box.querySelector('#b-logo-file'), ff = box.querySelector('#b-fav-file');
    box.querySelector('#b-logo').onclick = () => lf.click(); lf.onchange = upload('logo', lf);
    box.querySelector('#b-fav').onclick = () => ff.click(); ff.onchange = upload('favicon', ff);
    const lx = box.querySelector('#b-logo-x'); if (lx) lx.onclick = async () => { try { apply(await api.del('/api/branding/logo')); toast('Logo removed'); } catch (e) { toast(e.detail, 'err'); } };
    const fx = box.querySelector('#b-fav-x'); if (fx) fx.onclick = async () => { try { apply(await api.del('/api/branding/favicon')); toast('Tab icon removed'); } catch (e) { toast(e.detail, 'err'); } };
  };
  draw();
}
