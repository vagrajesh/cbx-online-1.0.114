// Voice for C-EXA (1.0.64): talk to her — one sentence at a time or hands-free — and hear her answer.
//
// The private path, when the owner has set it up (Agents → Now → Voice): the microphone is recorded in
// the browser, sent to this server, and Whisper on the server writes down what was said; Kokoro on the
// server speaks her answers. No audio goes anywhere else and none is kept — only the words.
// Until then the browser's own speech is used (Chrome/Edge recognition, speechSynthesis), as before.
//
// Browsers only open the microphone on a secure page: https, or localhost. On the plain LAN address
// the mic button says so instead of failing quietly.
import { api } from './api.js';
import { esc } from './ui.js';
import { lang } from './lang.js';

const KEY_OUT = 'cbx_voice_out';        // "1" when replies should be read aloud (per browser)
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;
const canRecord = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder) && window.isSecureContext;

let _status = null, _statusAt = 0;
async function server() {
  if (_status && Date.now() - _statusAt < 60000) return _status;
  try { _status = await api.get('/api/voice/status', { quiet: true }); } catch { _status = { ears: {}, voice: {} }; }
  _statusAt = Date.now();
  return _status;
}
const ears = async () => canRecord() && !!(await server()).ears?.ready && (await server()).enabled !== false;
const mouth = async () => !!(await server()).voice?.ready && (await server()).enabled !== false;

// ---- the microphone ---------------------------------------------------------------------------------
// One open stream while a conversation lasts; a level meter decides when someone starts and stops talking.

async function openMic() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const src = ctx.createMediaStreamSource(stream), an = ctx.createAnalyser();
  an.fftSize = 1024; src.connect(an);
  const buf = new Float32Array(an.fftSize);
  const level = () => { an.getFloatTimeDomainData(buf); let s = 0; for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i]; return Math.sqrt(s / buf.length); };
  return { stream, level, close() { stream.getTracks().forEach((t) => t.stop()); try { ctx.close(); } catch { /* closed */ } } };
}

// Record one utterance: waits for speech (up to `waitMs`), stops after `silenceMs` of quiet or `maxMs`.
// Resolves with a Blob, or null when nobody spoke. `abort()` on the returned handle stops it.
function recordOnce(mic, { waitMs = 9000, silenceMs = 900, maxMs = 30000, onLevel, signal } = {}) {
  return new Promise((resolve) => {
    const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    const type = types.find((t) => window.MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(t)) || '';
    const rec = new MediaRecorder(mic.stream, type ? { mimeType: type } : undefined);
    const parts = [];
    let spoke = false, quietSince = 0, done = false, floor = 0.006;
    const t0 = performance.now();
    rec.ondataavailable = (e) => { if (e.data && e.data.size) parts.push(e.data); };
    rec.onstop = () => resolve(spoke && parts.length ? new Blob(parts, { type: rec.mimeType || 'audio/webm' }) : null);
    const finish = () => { if (done) return; done = true; clearInterval(tick); try { rec.stop(); } catch { resolve(null); } };
    // the room's own noise for the first 300 ms sets the bar a voice has to clear
    const samples = [];
    const tick = setInterval(() => {
      if (signal && signal.aborted) { spoke = false; finish(); return; }
      const lv = mic.level(), now = performance.now();
      if (onLevel) onLevel(lv);
      if (now - t0 < 300) { samples.push(lv); floor = Math.max(0.006, (samples.reduce((a, b) => a + b, 0) / samples.length) * 2.2); return; }
      if (lv > floor * 1.6) { spoke = true; quietSince = 0; } else if (spoke) { quietSince = quietSince || now; if (now - quietSince > silenceMs) finish(); }
      if ((!spoke && now - t0 > waitMs) || now - t0 > maxMs) finish();
    }, 50);
    rec.start(250);
  });
}

async function transcribe(blob) {
  const fd = new FormData();
  fd.append('audio', blob, 'speech.' + ((blob.type.split('/')[1] || 'webm').split(';')[0]));
  fd.append('lang', lang.get());
  const r = await api.post('/api/voice/listen', fd, { quiet: true });
  return (r && r.text || '').trim();
}

// ---- her voice ---------------------------------------------------------------------------------------

let _audio = null;
function browserSay(text) {
  return new Promise((resolve) => {
    if (!('speechSynthesis' in window)) { resolve(); return; }
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(plain(text).slice(0, 1500));
      const v = pickVoice(); if (v) u.voice = v;
      u.lang = lang.locale(); u.rate = 1.02;
      u.onend = u.onerror = () => resolve();
      window.speechSynthesis.speak(u);
    } catch { resolve(); }
  });
}

// Say it and resolve when she has finished (or was interrupted). Long answers go sentence by sentence:
// the next one is made while the current one plays, so she starts talking at once.
let _gen = 0;
function chunks(words) {
  const parts = words.match(/[^.!?…]+[.!?…]+["')\]]*\s*|[^.!?…]+$/g) || [words];
  const out = [];
  for (const p of parts) {
    const t = p.trim(); if (!t) continue;
    if (out.length && (out[out.length - 1] + ' ' + t).length <= 220) out[out.length - 1] += ' ' + t;
    else if (t.length > 300) out.push(...t.match(/.{1,280}(\s|$)/g).map((x) => x.trim()).filter(Boolean));
    else out.push(t);
  }
  return out;
}
async function fetchSay(text, voiceId) {
  try {
    const r = await fetch(voiceId ? '/api/voice/sample' : '/api/voice/say', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(voiceId ? { id: voiceId } : { text, lang: lang.get() }) });
    return r.status === 200 ? await r.blob() : null;
  } catch { return null; }
}
function play(blob, gen) {
  return new Promise((resolve) => {
    if (gen !== _gen) { resolve(); return; }
    const url = URL.createObjectURL(blob);
    const a = new Audio(url); _audio = a;
    const done = () => { URL.revokeObjectURL(url); if (_audio === a) _audio = null; resolve(); };
    a.onended = a.onerror = a.onpause = done;
    a.play().catch(done);
  });
}
async function say(text, { voiceId } = {}) {
  const words = plain(text) || (voiceId ? 'a sample' : '');
  if (!words) return;
  hush();
  const gen = ++_gen;
  if (voiceId || await mouth()) {
    const list = voiceId ? [words] : chunks(words);
    let next = fetchSay(list[0], voiceId);
    for (let i = 0; i < list.length; i++) {
      const blob = await next;
      if (gen !== _gen) return;
      if (!blob) { if (i === 0) break; return; } // the server can't: the browser says it (below)
      next = i + 1 < list.length ? fetchSay(list[i + 1]) : null;
      await play(blob, gen);
      if (gen !== _gen) return;
      if (i === list.length - 1) return;
    }
    if (gen !== _gen || voiceId) return; // a sample the server couldn't make: nothing to say instead
  }
  await browserSay(words);
}

function hush() {
  _gen++;
  try { if (_audio) { _audio.pause(); _audio = null; } } catch { /* ignore */ }
  try { window.speechSynthesis && window.speechSynthesis.cancel(); } catch { /* ignore */ }
}

function plain(text) {
  return String(text || '').replace(/```[\s\S]*?```/g, ' ').replace(/\[(.*?)\]\(.*?\)/g, '$1').replace(/https?:\/\/\S+/g, 'a link').replace(/[*_`#>|]/g, '').replace(/\s+/g, ' ').trim().slice(0, 3000);
}

// ---- a conversation: listen → think → speak → listen, until stopped --------------------------------------
// `ask(text)` returns her answer. While she speaks the mic stays open: start talking and she stops (barge-in).

function conversation({ ask, onState }) {
  let mic = null, stopped = false;
  const ctl = new AbortController();
  const set = (s, extra) => { if (!stopped || s === 'off') onState && onState(s, extra); };
  async function speakListening(text) {
    // speak, but stop her if the person starts talking over her
    let over = 0;
    const watch = setInterval(() => { if (!mic) return; if (mic.level() > 0.06) { over += 50; if (over > 300) hush(); } else over = 0; }, 50);
    try { await say(text); } finally { clearInterval(watch); }
  }
  (async () => {
    try { mic = await openMic(); } catch (e) { set('error', e && e.name === 'NotAllowedError' ? 'Microphone blocked — allow it in the address bar.' : 'No microphone found.'); set('off'); return; }
    let quiet = 0;
    while (!stopped) {
      set('listening');
      const blob = await recordOnce(mic, { signal: ctl.signal, onLevel: (lv) => onState && onState('level', lv) });
      if (stopped) break;
      if (!blob) { if (++quiet >= 3) { set('error', 'I stopped listening — press Talk to start again.'); break; } continue; }
      quiet = 0;
      set('thinking');
      let text = '';
      try { text = await transcribe(blob); } catch (e) { set('error', e.detail || 'I could not hear that.'); continue; }
      if (!text) continue;
      if (/^(stop|bye|goodbye|that'?s all|thank you,? that'?s all)\W*$/i.test(text)) { set('heard', text); break; }
      set('heard', text);
      let reply = '';
      try { reply = await ask(text); } catch (e) { reply = (e && e.detail) || 'Something went wrong.'; }
      if (stopped) break;
      set('speaking', reply);
      await speakListening(reply);
    }
    stopped = true;
    if (mic) mic.close();
    set('off');
  })();
  return { stop() { stopped = true; ctl.abort(); hush(); } };
}

export const voice = {
  canListen: () => canRecord() || (!!Recognition && window.isSecureContext),
  whyNotListen: () => (!window.isSecureContext ? 'The microphone only opens on a secure (https) address. Use the tunnel link, or open the app on this machine as localhost.' : !canRecord() && !Recognition ? 'This browser cannot use the microphone — use Chrome or Edge.' : ''),
  speakEnabled: () => { try { return localStorage.getItem(KEY_OUT) === '1'; } catch { return false; } },
  setSpeak(on) { try { on ? localStorage.setItem(KEY_OUT, '1') : localStorage.removeItem(KEY_OUT); } catch { /* per-browser convenience only */ } if (!on) hush(); },
  status: server,
  refresh() { _status = null; return server(); },
  say, hush, transcribe, openMic, recordOnce, plain,

  // A microphone button bound to a text input: click, speak, and what you said lands in the box.
  micButton(input, { onFinal } = {}) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'btn ghost mic'; b.setAttribute('aria-label', 'Dictate');
    b.innerHTML = MIC;
    if (!this.canListen()) { b.disabled = true; b.title = this.whyNotListen(); b.classList.add('off'); return b; }
    b.title = 'Dictate — what you say is typed into the box; press Send when it is right';
    let live = null;
    const done = () => { live = null; b.classList.remove('live'); b.title = 'Speak'; };
    b.onclick = async () => {
      if (live) { live.stop(); return; }
      const base = input.value ? input.value.replace(/\s+$/, '') + ' ' : '';
      if (await ears()) {
        // the private path: record here, Whisper on the server writes it down
        const ctl = new AbortController(); let mic = null;
        live = { stop: () => ctl.abort() };
        b.classList.add('live'); b.title = 'Listening… click to stop';
        try {
          mic = await openMic();
          const blob = await recordOnce(mic, { signal: ctl.signal, waitMs: 8000, silenceMs: 1200 });
          mic.close(); mic = null;
          if (blob) { b.title = 'Writing it down…'; const t = await transcribe(blob); if (t) { input.value = (base + t).trimStart(); if (onFinal) onFinal(input.value.trim()); } }
        } catch (e) { input.placeholder = e && e.name === 'NotAllowedError' ? 'Microphone blocked — allow it in the address bar and try again' : (e && e.detail) || input.placeholder; }
        finally { if (mic) mic.close(); done(); input.focus(); }
        return;
      }
      if (!Recognition) { b.title = this.whyNotListen(); return; }
      const rec = new Recognition();
      rec.lang = lang.locale(); rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
      let finalText = '';
      live = { stop: () => { try { rec.stop(); } catch { /* stopped */ } } };
      rec.onresult = (ev) => {
        let interim = '';
        for (let i = ev.resultIndex; i < ev.results.length; i++) { const r = ev.results[i]; if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript; }
        input.value = (base + finalText + interim).replace(/\s+/g, ' ').trimStart();
      };
      rec.onerror = (ev) => { done(); input.placeholder = ev.error === 'not-allowed' ? 'Microphone blocked — allow it in the address bar and try again' : input.placeholder; };
      rec.onend = () => { done(); if (finalText.trim() && onFinal) onFinal(input.value.trim()); input.focus(); };
      try { rec.start(); b.classList.add('live'); b.title = 'Listening… click to stop'; } catch { done(); }
    };
    return b;
  },

  // Hands-free: press Talk, speak, she answers out loud, and listens again. Press again (or say "stop") to end.
  // ask(text) → her answer (the caller also shows both in its thread).
  talkButton(ask, { label = 'Hands-free' } = {}) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'btn ghost talk'; b.innerHTML = `${WAVE}<span>${esc(label)}</span>`;
    const span = () => b.querySelector('span');
    let conv = null;
    const idle = () => { conv = null; b.classList.remove('live', 'thinking', 'speaking'); span().textContent = label; b.title = 'Talk hands-free — she answers out loud and listens again. Only the words are kept.'; };
    idle();
    if (!canRecord()) { b.disabled = true; b.classList.add('off'); b.title = window.isSecureContext ? 'This browser cannot record — use Chrome or Edge.' : this.whyNotListen(); return b; }
    b.onclick = async () => {
      if (conv) { conv.stop(); return; }
      if (!(await ears())) { b.title = 'Voice is not set up on this server yet — the owner sets it up in Settings → C-EXA voice & models.'; span().textContent = 'Not set up'; setTimeout(idle, 2500); return; }
      b.classList.add('live');
      conv = conversation({
        ask,
        onState: (s, x) => {
          if (s === 'level') { b.style.setProperty('--lv', Math.min(1, x * 12).toFixed(2)); return; }
          b.classList.toggle('thinking', s === 'thinking'); b.classList.toggle('speaking', s === 'speaking');
          if (s === 'listening') span().textContent = 'Listening…';
          if (s === 'thinking') span().textContent = 'Thinking…';
          if (s === 'speaking') span().textContent = 'Speaking… (talk to interrupt)';
          if (s === 'error') { span().textContent = x || 'Stopped'; }
          if (s === 'off') setTimeout(idle, 1200);
        },
      });
    };
    return b;
  },

  // A small speaker toggle: read C-EXA's replies aloud, or not. Remembered per browser.
  speakToggle() {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'btn ghost spk'; b.setAttribute('aria-label', 'Read replies aloud');
    const paint = () => { const on = this.speakEnabled(); b.innerHTML = `${on ? SPK_ON : SPK_OFF}<span>${on ? 'Speak: on' : 'Speak: off'}</span>`; b.classList.toggle('live', on); b.title = on ? 'She says her answers out loud — click to stop' : 'Type, and she says her answers out loud'; };
    b.onclick = () => { this.setSpeak(!this.speakEnabled()); paint(); if (this.speakEnabled()) say('Reading replies aloud.'); };
    paint();
    return b;
  },

  // Say a reply, if that is switched on.
  speak(text) { if (this.speakEnabled() && text) say(text); },
};

function pickVoice() {
  try {
    const vs = window.speechSynthesis.getVoices() || [], loc = lang.locale(), l2 = loc.slice(0, 2);
    return vs.find((v) => v.lang === loc) || (l2 === 'en' && vs.find((v) => /en-GB/i.test(v.lang))) || vs.find((v) => v.lang.slice(0, 2) === l2) || null;
  } catch { return null; }
}
// Chrome loads voices lazily; ask once so the first reply has one.
try { window.speechSynthesis && window.speechSynthesis.getVoices(); } catch { /* ignore */ }

const MIC = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/></svg>';
const WAVE = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/></svg>';
const SPK_ON = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16 9a4 4 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/></svg>';
const SPK_OFF = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4h4l5 4V6L8 10z"/><path d="M17 9l4 6M21 9l-4 6"/></svg>';
