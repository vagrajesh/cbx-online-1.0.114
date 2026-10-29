// Two-step verification, the person's side of it. One panel, two homes: the set-up screen that
// stands in front of the app at the first sign-in (web/app.js renderEnrol) and the Security card in
// Settings. The server holds the rules (cbx/api/auth.py: who must have it, whether email codes are
// allowed, how long a browser is trusted); this file only asks and shows.
import { api } from './api.js';
import { esc, el, toast, modal, fmtDate } from './ui.js';

const METHOD = { totp: 'Authenticator app', email: 'Codes by email', '': 'Not set up' };

async function copyText(text, node) {
  // navigator.clipboard only exists on https/localhost; the LAN install is plain http, so fall back
  // to selecting the node and asking the browser to copy that
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); toast('Copied'); return; }
    if (node) { const sel = window.getSelection(); const range = document.createRange(); range.selectNodeContents(node); sel.removeAllRanges(); sel.addRange(range); }
    toast(document.execCommand && document.execCommand('copy') ? 'Copied' : 'Select the text and press Ctrl+C');
  } catch { toast('Select the text and press Ctrl+C', 'err'); }
}

function download(name, text) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
  a.download = name; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/** The recovery codes, shown once. `next` runs when the person says they have kept them. */
export function recoveryCodes(host, codes, { product = 'CBX-Connect', email = '', next, nextLabel = 'I have saved them — continue' } = {}) {
  const text = `${product} recovery codes for ${email}\n${new Date().toISOString().slice(0, 10)}\n\nEach code works once, in place of the app.\n\n${codes.join('\n')}\n`;
  host.innerHTML = `
    <div><h1>Your recovery codes</h1><p class="lead">Ten one-time codes for the day the phone is lost or replaced. <b>This is the only time they are shown.</b> Keep them somewhere that is not the phone — a password manager, or printed.</p></div>
    <div class="rc-grid" id="rc">${codes.map((c) => `<code>${esc(c)}</code>`).join('')}</div>
    <div class="row" style="gap:8px"><button class="btn sm" id="rc-copy">Copy</button><button class="btn sm" id="rc-dl">Download</button><button class="btn sm" id="rc-print">Print</button></div>
    <label class="row small" style="gap:8px;align-items:flex-start"><input type="checkbox" id="rc-kept" style="width:auto;margin-top:3px"><span>I have kept these codes somewhere safe. A lost phone without them means asking an owner or admin to reset my two-step verification.</span></label>
    <button class="btn primary" style="justify-content:center;padding:11px" id="rc-next" disabled>${esc(nextLabel)}</button>`;
  host.querySelector('#rc-copy').onclick = () => copyText(codes.join('\n'), host.querySelector('#rc'));
  host.querySelector('#rc-dl').onclick = () => download(`${product.replace(/[^\w-]+/g, '-')}-recovery-codes.txt`, text);
  host.querySelector('#rc-print').onclick = () => {
    const w = window.open('', '_blank', 'width=520,height=640'); if (!w) { toast('Allow pop-ups to print.', 'err'); return; }
    w.document.write(`<pre style="font:14px/1.6 ui-monospace,Consolas,monospace;padding:24px">${esc(text)}</pre>`); w.document.close(); w.focus(); w.print();
  };
  const kept = host.querySelector('#rc-kept'), nextBtn = host.querySelector('#rc-next');
  kept.onchange = () => { nextBtn.disabled = !kept.checked; };
  nextBtn.onclick = () => next && next();
}

/**
 * The panel. `who` is the signed-in person (name, email), `mfa` the status from /api/auth/me or
 * /api/auth/mfa. `first` is the set-up screen (no status card, a sign-out link); otherwise it is the
 * Security card's body. `done()` is called when the second step is in place; `changed(mfa)` after
 * anything else that alters the status.
 */
export function mfaPanel(host, { who, mfa, first = false, done, changed, signOut, product = 'CBX-Connect' } = {}) {
  let status = mfa || {};
  const product_ = product;

  const view = (html) => { host.innerHTML = `<div class="mfa" style="display:flex;flex-direction:column;gap:16px">${html}</div>`; return host.firstElementChild; };
  const busy = (btn, on) => { if (!btn) return; btn.disabled = on; btn.style.opacity = on ? '.6' : ''; };

  // ---- 1. choose ----------------------------------------------------------------------------
  const choose = () => {
    const emailOk = status.email_allowed && status.method !== 'totp';
    const box = view(`
      <div><h1>${first ? 'Set up two-step verification' : 'Two-step verification'}</h1><p class="lead">${first
        ? (status.method === 'email' && status.proven === false
          ? `Hello ${esc((who && who.name) || '')}. Your email codes were never confirmed with a code, and this server can't send one right now. Set up an authenticator app to carry on.`
          : `Hello ${esc((who && who.name) || '')}. Before anything else opens, add a second step to your sign-in. It takes two minutes and you only do it once.`)
        : 'A second step for sign-ins from a browser I have not seen.'}</p></div>
      <div class="mfa-opt" id="opt-app"><b>Authenticator app</b><span>Microsoft Authenticator, Google Authenticator, Authy, 1Password, Bitwarden — any of them. A code that changes every 30 seconds, no email involved, works without signal.</span><span class="small" style="color:var(--accent)">Recommended</span></div>
      ${emailOk ? `<div class="mfa-opt" id="opt-email"><b>Codes by email</b><span>A 6-digit code to ${esc((who && who.email) || 'your inbox')} each time you sign in from a new browser. Slower, and only as safe as that inbox.</span></div>` : ''}
      ${first && signOut ? '<div class="row between small"><span class="muted">Not now?</span><a href="#" id="mfa-out" style="color:var(--accent)">Sign out</a></div>' : ''}`);
    box.querySelector('#opt-app').onclick = () => setup();
    const oe = box.querySelector('#opt-email'); if (oe) oe.onclick = () => useEmail();
    const so = box.querySelector('#mfa-out'); if (so) so.onclick = (e) => { e.preventDefault(); signOut(); };
  };

  // ---- 2. the QR code and the first code ------------------------------------------------------
  const setup = async (currentPassword) => {
    let r;
    try { r = await api.post('/api/auth/mfa/setup', { current_password: currentPassword || null }, { quiet: true }); }
    catch (e) {
      if (e.status === 400 && /current password/i.test(e.detail || '') && !currentPassword) { askPassword(); return; }
      toast(e.detail || 'Could not start the set-up.', 'err'); return;
    }
    const box = view(`
      <div><h1>Scan, then type the code</h1><p class="lead">In your authenticator app choose <b>Add account</b> (or the + sign) and scan this. The app then shows a 6-digit code for <b>${esc(r.issuer || product_)}</b> — type it below to prove the phone has it.</p></div>
      <div class="mfa-qr">${r.qr_svg || '<div class="small muted">QR code unavailable — type the key instead.</div>'}</div>
      <details class="small"><summary style="cursor:pointer;color:var(--accent)">Can't scan? Type the key instead</summary><div class="copybox" style="margin-top:8px"><span class="mono" id="mfa-secret">${esc(r.secret)}</span><button class="btn sm" id="mfa-copy">Copy</button></div><p class="muted" style="margin:6px 0 0">In the app pick <i>Enter a setup key</i>, name it ${esc(r.issuer || product_)}, and choose <i>time based</i>.</p></details>
      <form id="mfa-f" style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label for="mfa-code">Code from the app</label><input id="mfa-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" style="font-size:22px;letter-spacing:6px;text-align:center" required></div>
        <div class="err-box hidden" id="mfa-err"></div>
        <div class="small muted hidden" id="mfa-time">Is your phone's time set automatically?</div>
        <button class="btn primary" style="justify-content:center;padding:11px" type="submit">Confirm</button>
        <div class="row between small"><a href="#" id="mfa-back" class="muted">Back</a><span class="muted">Codes change every 30 seconds</span></div>
      </form>`);
    box.querySelector('#mfa-copy').onclick = () => copyText(r.secret.replace(/\s+/g, ''), box.querySelector('#mfa-secret'));
    box.querySelector('#mfa-back').onclick = (e) => { e.preventDefault(); first || !status.method ? choose() : card(); };
    const f = box.querySelector('#mfa-f'); setTimeout(() => f.querySelector('#mfa-code').focus(), 0);
    let misses = 0; // 1.0.113: after two wrong app codes, one quiet line about the phone's clock
    f.onsubmit = async (e) => {
      e.preventDefault();
      const err = box.querySelector('#mfa-err'); err.classList.add('hidden');
      busy(f.querySelector('button[type=submit]'), true);
      try {
        const c = await api.post('/api/auth/mfa/confirm', { code: f.querySelector('#mfa-code').value.trim() }, { quiet: true });
        status = c.mfa || status;
        recoveryCodes(view(''), c.recovery_codes || [], { product: product_, email: who && who.email, next: () => (first ? done && done() : (changed && changed(status), card())), nextLabel: first ? 'I have saved them — open ' + product_ : 'I have saved them — done' });
      } catch (ex) {
        err.textContent = ex.detail || 'That code did not work.'; err.classList.remove('hidden');
        misses += 1; if (misses >= 2) box.querySelector('#mfa-time').classList.remove('hidden');
        const c = f.querySelector('#mfa-code'); c.value = ''; c.focus();
      } finally { if (f.isConnected) busy(f.querySelector('button[type=submit]'), false); }
    };
  };

  const askPassword = () => {
    const body = el('<div class="field"><label>Current password</label><input id="cur" type="password" autocomplete="current-password"><span class="small muted">You already have an app set up. Your password proves it is you replacing it.</span></div>');
    const m = modal({ title: 'Replace the authenticator app', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Continue</button>' });
    m.root.querySelector('#ok').onclick = () => { const pw = body.querySelector('#cur').value; m.close(); setup(pw); };
  };

  // ---- 3. email codes instead -----------------------------------------------------------------
  // 1.0.113: a code is sent first and the switch happens only when it comes back
  const useEmail = async () => {
    let sent;
    try { sent = await api.post('/api/auth/mfa/use-email', {}, { quiet: true }); }
    catch (e) { toast(e.detail || 'Could not send a code.', 'err'); return; }
    const box = view(`
      <div><h1>Check your email</h1><p class="lead">${esc(sent.message || `A 6-digit code went to ${sent.sent_to || 'your inbox'}.`)}</p></div>
      <form id="em-f" style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label for="em-code">Code</label><input id="em-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" style="font-size:22px;letter-spacing:6px;text-align:center" required></div>
        <div class="err-box hidden" id="em-err"></div>
        <button class="btn primary" style="justify-content:center;padding:11px" type="submit">Confirm</button>
        <div class="row between small"><a href="#" id="em-back" class="muted">Back</a><a href="#" id="em-again" class="muted">Send a new code</a></div>
      </form>`);
    box.querySelector('#em-back').onclick = (e) => { e.preventDefault(); first || !status.method ? choose() : card(); };
    box.querySelector('#em-again').onclick = (e) => { e.preventDefault(); useEmail(); };
    const f = box.querySelector('#em-f'); setTimeout(() => f.querySelector('#em-code').focus(), 0);
    f.onsubmit = async (e) => {
      e.preventDefault();
      const err = box.querySelector('#em-err'); err.classList.add('hidden');
      busy(f.querySelector('button[type=submit]'), true);
      try {
        const r = await api.post('/api/auth/mfa/use-email/confirm', { code: f.querySelector('#em-code').value.trim() }, { quiet: true });
        status = r.mfa || status;
        toast('Codes will come by email when you sign in from a new browser.');
        if (first) done && done(); else { changed && changed(status); card(); }
      } catch (ex) {
        err.textContent = ex.detail || 'That code did not work.'; err.classList.remove('hidden');
        const c = f.querySelector('#em-code'); c.value = ''; c.focus();
      } finally { if (f.isConnected) busy(f.querySelector('button[type=submit]'), false); }
    };
  };

  // ---- 4. the status card (Settings) ----------------------------------------------------------
  const askCode = (title, hint) => new Promise((res) => {
    const body = el(`<div class="field"><label>Code from your app</label><input id="c" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="000000" style="font-size:20px;letter-spacing:6px;text-align:center"><span class="small muted">${esc(hint)}</span></div>`);
    const m = modal({ title, body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Continue</button>', onClose: () => res(null) });
    const go = () => { const v = body.querySelector('#c').value.trim(); if (!v) return; m.root.remove(); res(v); };
    m.root.querySelector('#ok').onclick = go;
    body.querySelector('#c').onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); go(); } };
  });

  const card = () => {
    const m = status.method || '';
    const lines = [];
    if (m === 'totp') lines.push(`Set up ${fmtDate(status.enrolled_at, { rel: true })}`, `${status.recovery_left ?? 0} recovery code${status.recovery_left === 1 ? '' : 's'} left`);
    if (m === 'email') lines.push(status.proven === false ? 'Not confirmed yet' : `Since ${fmtDate(status.enrolled_at, { rel: true })}`);
    lines.push(status.trust_days > 0 ? `A browser that passed is remembered for ${status.trust_days} days` : 'Asked at every sign-in (your role)');
    if (status.required) lines.push('Required for your account');
    const box = view(`
      <div class="mfa-status">
        <b>${esc(METHOD[m])}</b>
        <div class="small muted">${lines.map(esc).join(' · ')}</div>
        <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:10px">
          ${m !== 'totp' ? '<button class="btn sm primary" id="m-app">Set up an app</button>' : ''}
          ${m === 'totp' ? '<button class="btn sm" id="m-codes">New recovery codes</button><button class="btn sm" id="m-replace">Replace the app</button>' : ''}
          ${(m !== 'email' || status.proven === false) && status.email_allowed && m !== 'totp' ? `<button class="btn sm" id="m-email">${m === 'email' ? 'Confirm email codes' : 'Use email codes'}</button>` : ''}
          ${m && !status.required ? '<button class="btn sm ghost" id="m-remove">Remove</button>' : ''}
        </div></div>`);
    const q = (id) => box.querySelector(id);
    if (q('#m-app')) q('#m-app').onclick = () => setup();
    if (q('#m-replace')) q('#m-replace').onclick = () => askPassword();
    if (q('#m-email')) q('#m-email').onclick = () => useEmail();
    if (q('#m-codes')) q('#m-codes').onclick = async () => {
      const code = await askCode('New recovery codes', 'The ten old codes stop working the moment the new ones are made.'); if (!code) return;
      try {
        const r = await api.post('/api/auth/mfa/recovery', { code }, { quiet: true });
        status.recovery_left = (r.recovery_codes || []).length;
        recoveryCodes(view(''), r.recovery_codes || [], { product: product_, email: who && who.email, next: () => { changed && changed(status); card(); }, nextLabel: 'I have saved them — done' });
      } catch (e) { toast(e.detail || 'Could not make new codes.', 'err'); }
    };
    if (q('#m-remove')) q('#m-remove').onclick = async () => {
      const code = m === 'totp' ? await askCode('Remove two-step verification', 'The current code from your app confirms it is you.') : '';
      if (m === 'totp' && !code) return;
      try { const r = await api.post('/api/auth/mfa/remove', { code }, { quiet: true }); status = r.mfa || { method: '' }; toast('Two-step verification removed'); changed && changed(status); card(); }
      catch (e) { toast(e.detail || 'Could not remove it.', 'err'); }
    };
  };

  if (first) choose(); else card();
  return { refresh: (s) => { status = s || status; card(); } };
}
