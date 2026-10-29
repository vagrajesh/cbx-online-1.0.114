// C-EXA rings you inside CBX (1.0.64). While the app is open it looks every few seconds for a call; one
// rings in the corner. Answer and she talks: she says who asked and why, asks her questions one at a time
// and listens. Speak, or type in the box. Only the words are kept — never the sound.
import { api } from './api.js';
import { esc } from './ui.js';
import { voice } from './voice.js';

const EVERY = 8000;
let timer = null, busy = false, seen = new Set(), box = null;

export function startCalls(getMe) {
  if (timer) return;
  const look = async () => {
    if (busy || document.visibilityState !== 'visible' || !getMe()) return;
    try {
      const r = await api.get('/api/voice/ringing', { quiet: true });
      if (r && r.call && !seen.has(r.call.id)) { seen.add(r.call.id); ring(r.call); }
    } catch { /* a call is a convenience; the next look tries again */ }
  };
  timer = setInterval(look, EVERY);
  document.addEventListener('visibilitychange', look);
  setTimeout(look, 1500);
}

// ---- the ring ---------------------------------------------------------------------------------------

let ringCtx = null;
function tone(on) {
  try {
    if (!on) { if (ringCtx) { ringCtx.close(); ringCtx = null; } return; }
    const ctx = ringCtx = new (window.AudioContext || window.webkitAudioContext)();
    const g = ctx.createGain(); g.gain.value = 0; g.connect(ctx.destination);
    [440, 480].forEach((f) => { const o = ctx.createOscillator(); o.frequency.value = f; o.connect(g); o.start(); });
    let t = ctx.currentTime;
    for (let i = 0; i < 30; i++) { g.gain.setValueAtTime(0.06, t); g.gain.setValueAtTime(0, t + 1.0); t += 3; } // ring… ring…
  } catch { /* no sound: the card still shows */ }
}

function shell(html) {
  if (box) box.remove();
  box = document.createElement('div');
  box.className = 'call-card'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-live', 'polite');
  box.innerHTML = html;
  document.body.append(box);
  return box;
}
function close() { tone(false); voice.hush(); if (box) box.remove(); box = null; busy = false; }

function ring(c) {
  busy = true;
  const b = shell(`
    <div class="cc-top"><span class="cc-av">${esc((c.ai || 'C').slice(0, 1))}</span><div><b>${esc(c.ai)} is calling</b><span>${esc(c.by ? `${c.by} asked her to call: ` : '')}${esc(c.purpose)}</span></div></div>
    <div class="cc-acts"><button class="btn primary" data-a="answer">Answer</button><button class="btn ghost" data-a="decline">Not now</button></div>
    <p class="cc-note">Only the words are kept — never the sound.</p>`);
  tone(true);
  const giveUp = setTimeout(() => { if (box === b) close(); }, Math.max(15, c.ring_seconds || 60) * 1000);
  b.querySelector('[data-a="decline"]').onclick = async () => { clearTimeout(giveUp); try { await api.post(`/api/voice/calls/${c.id}/decline`); } catch { /* it rings out */ } close(); };
  b.querySelector('[data-a="answer"]').onclick = async () => {
    clearTimeout(giveUp); tone(false);
    try { const r = await api.post(`/api/voice/calls/${c.id}/answer`); live(c, r.say); }
    catch (e) { b.querySelector('.cc-note').textContent = e.detail || 'That call has ended.'; setTimeout(close, 2500); }
  };
}

// ---- the call ---------------------------------------------------------------------------------------

async function live(c, first) {
  const b = shell(`
    <div class="cc-top"><span class="cc-av on">${esc((c.ai || 'C').slice(0, 1))}</span><div><b>${esc(c.ai)}</b><span class="cc-state">On the call</span></div><button class="btn sm danger" data-a="hang">Hang up</button></div>
    <div class="cc-lines"></div>
    <div class="cc-type"><input type="text" placeholder="Or type your answer…" aria-label="Your answer"><button class="btn sm">Send</button></div>
    <p class="cc-note">Only the words are kept — never the sound.</p>`);
  const lines = b.querySelector('.cc-lines'), state = b.querySelector('.cc-state'), inp = b.querySelector('input');
  const add = (who, text) => { lines.insertAdjacentHTML('beforeend', `<div class="cc-l ${who}">${esc(text)}</div>`); lines.scrollTop = lines.scrollHeight; };
  let over = false, mic = null, ctl = null, typed = null;
  const end = (why) => { over = true; if (ctl) ctl.abort(); if (mic) mic.close(); state.textContent = why || 'Call ended'; b.querySelector('.cc-type').remove(); b.querySelector('[data-a="hang"]').textContent = 'Close'; b.querySelector('[data-a="hang"]').onclick = close; setTimeout(() => { if (box === b) close(); }, 6000); };
  b.querySelector('[data-a="hang"]').onclick = async () => { if (over) { close(); return; } voice.hush(); try { await api.post(`/api/voice/calls/${c.id}/hangup`); } catch { /* ended anyway */ } end('You hung up'); };
  const sendTyped = () => { const v = inp.value.trim(); if (!v || !typed) return; inp.value = ''; const t = typed; typed = null; t(v); };
  b.querySelector('.cc-type button').onclick = sendTyped;
  inp.onkeydown = (e) => { if (e.key === 'Enter') sendTyped(); };

  const canHear = voice.canListen() && (await voice.status()).ears?.ready;
  if (canHear) { try { mic = await voice.openMic(); } catch { mic = null; } }

  // what they say next: spoken (and written down on the server) or typed — whichever comes first
  const next = () => new Promise((resolve) => {
    typed = (t) => { if (ctl) ctl.abort(); resolve(t); };
    if (!mic) { state.textContent = 'Type your answer'; inp.focus(); return; }
    state.textContent = 'Listening…';
    ctl = new AbortController();
    voice.recordOnce(mic, { signal: ctl.signal, waitMs: 20000, silenceMs: 1100 }).then(async (blob) => {
      if (!typed) return; // they typed instead
      if (!blob) { resolve(''); return; }
      state.textContent = 'Writing it down…';
      try { const t = await voice.transcribe(blob); typed = null; resolve(t); } catch { typed = null; resolve(''); }
    });
  });

  let say = first;
  while (!over) {
    add('cexa', say);
    state.textContent = 'Speaking…';
    await voice.say(say);
    if (over) break;
    let heard = await next();
    if (over) break;
    if (!heard) { heard = await next(); if (over) break; }
    if (heard) add('them', heard);
    state.textContent = 'Thinking…';
    try {
      const r = await api.post(`/api/voice/calls/${c.id}/turn`, { text: heard });
      say = r.say;
      if (r.done) { add('cexa', say); state.textContent = 'Saying goodbye…'; await voice.say(say); end('Call ended — thank you'); break; }
    } catch (e) { end(e.detail || 'The call ended'); break; }
  }
}
