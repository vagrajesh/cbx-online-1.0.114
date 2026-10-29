// Microsoft sign-in (1.0.97): the owner's card. Company staff sign in with their Microsoft work account and the
// company's MFA; their CBX password still works. Only people who already have a CBX account get in. The values
// come from IT's app registration in Entra; the secret is sealed on the server and never shown again.
import { api } from '../api.js';
import { esc, toast } from '../ui.js';

export default {
  title: 'Microsoft sign-in',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner') { root.innerHTML = '<div class="head"><div><h1>Microsoft sign-in</h1><p>This page is for the owner.</p></div></div>'; return; }
    root.innerHTML = `
      <div class="head"><div><h1>Microsoft sign-in</h1><p>Company staff sign in with their Microsoft work account and the company's MFA. Their CBX password still works. Only people who already have a CBX account get in.</p></div></div>
      <div class="card" style="padding:18px 20px;max-width:760px;display:flex;flex-direction:column;gap:14px">
        <div class="field"><label for="ms-tenant">Tenant id</label><input id="ms-tenant" placeholder="GUID or company.onmicrosoft.com" autocomplete="off"></div>
        <div class="field"><label for="ms-client">Client id (application id)</label><input id="ms-client" placeholder="GUID" autocomplete="off"></div>
        <div class="field"><label for="ms-secret">Client secret</label><input id="ms-secret" type="password" autocomplete="new-password"></div>
        <label class="row" style="gap:8px"><input type="checkbox" id="ms-on" style="width:auto"> Show "Sign in with Microsoft" on the sign-in page</label>
        <div class="row" style="gap:8px"><button class="btn primary" id="ms-save">Save</button><button class="btn" id="ms-test">Test</button><span class="small muted" id="ms-state"></span></div>
        <div class="small">Redirect address to give IT: <span class="mono" id="ms-redirect"></span></div>
        <div class="small" id="ms-out"></div>
      </div>`;
    const $ = (id) => root.querySelector('#' + id);
    const draw = (c) => {
      $('ms-tenant').value = c.tenant || ''; $('ms-client').value = c.client_id || ''; $('ms-secret').value = ''; $('ms-on').checked = !!c.enabled;
      $('ms-secret').placeholder = c.has_secret ? `saved (…${c.secret_hint || ''}), type to replace` : 'kept sealed, never shown again';
      $('ms-redirect').textContent = c.redirect_uri;
      $('ms-state').textContent = c.working ? 'On' : c.enabled ? 'Waiting' : 'Off';
      $('ms-out').textContent = c.unreadable ? "The saved secret can't be read on this server any more: type it again." : (c.ready ? '' : c.why);
    };
    try { draw(await api.get('/api/auth/microsoft/settings')); } catch (e) { $('ms-out').innerHTML = esc(e.detail || e.message); }
    $('ms-save').onclick = async () => {
      const body = { tenant: $('ms-tenant').value.trim(), client_id: $('ms-client').value.trim(), enabled: $('ms-on').checked };
      if ($('ms-secret').value) body.secret = $('ms-secret').value;
      try { draw(await api.put('/api/auth/microsoft/settings', body)); toast('Saved · logged'); } catch (e) { toast(e.detail || e.message, 'err'); }
    };
    $('ms-test').onclick = async () => { try { const r = await api.post('/api/auth/microsoft/test', {}); $('ms-out').textContent = r.detail; } catch (e) { toast(e.detail || e.message, 'err'); } };
  },
};
