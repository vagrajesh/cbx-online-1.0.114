// Talk to C-EXA: the owner's 1-on-1. Thread with thinking blocks, today's plan, the journal (reviewable memory), ideas parked.
import { api } from '../api.js';
import { voice } from '../voice.js';
import { esc, toast, pill, fmtDate, confirmBox, md } from '../ui.js';

const KIND_PILL = { learned: ['Learned', 'info'], team: ['About the team', 'neutral'], self: ['Note to self', 'ai'], idea: ['Idea', 'warn'] };
const MSG_TITLE = { brief: 'Overnight', needs_you: 'Needs you', team: 'About the team', checkin: 'Check-in', aside: 'And' };

export default {
  title: 'Talk to C-EXA',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner') { root.innerHTML = '<div class="head"><div><h1>Talk to C-EXA</h1><p>This page is for the owner.</p></div></div>'; return; }
    let health = { model: {} }, thread = { messages: [] }, plan = { items: [] }, journal = { entries: [] }, props = { noticed: [] };
    const safe = (p, fb) => p.catch(() => fb);
    async function loadAll() {
      [health, thread, plan, journal, props] = await Promise.all([
        safe(api.get('/api/health'), { model: {} }), safe(api.get('/api/cexa/thread?limit=80'), { messages: [] }), safe(api.get('/api/cexa/plan'), { items: [] }), safe(api.get('/api/cexa/journal'), { entries: [] }), safe(api.get('/api/cexa/proposals'), { noticed: [] }),
      ]);
    }
    await loadAll();

    const modelPill = () => {
      const m = health.model || {};
      const name = (m.models && m.models[0]) || 'model';
      if (m.online && m.mode === 'live') return pill(`${name} · online`, 'ai', true);
      return pill('offline — stub replies', 'warn');
    };

    const msgHtml = (m) => {
      if (!m.from_axl) return `<div class="msg me">${esc(m.text).replace(/\n/g, '<br>')}<span class="meta">${fmtDate(m.at, { rel: true })}</span></div>`;
      const think = m.thinking ? `<details class="think"><summary class="think-toggle">Show her reasoning</summary><div class="thinking">${esc(m.thinking)}</div></details>` : '';
      const title = m.kind !== 'chat' ? `<b>${esc(m.title || MSG_TITLE[m.kind] || '')}</b><br>` : '';
      const link = m.link ? ` · <a href="${esc(m.link)}" style="color:var(--accent)">open</a>` : '';
      return `<div class="msg ai">${title}${md(m.text)}<span class="meta">${fmtDate(m.at, { time: true })}${link}</span></div>${think}`;
    };

    // hands-free: her answer is spoken by the conversation, the thread just shows both sides
    const talk = voice.talkButton(async (text) => {
      const msgs = root.querySelector('#msgs');
      msgs.querySelector('.empty')?.remove();
      msgs.insertAdjacentHTML('beforeend', `<div class="msg me">${esc(text)}</div><div class="msg ai thinking-msg"><span class="spin"></span></div>`); msgs.scrollTop = msgs.scrollHeight;
      try {
        const r = await api.post('/api/cexa/chat', { text });
        const said = (r && (r.answer || (r.reply && r.reply.text))) || '';
        msgs.querySelector('.thinking-msg')?.remove();
        msgs.insertAdjacentHTML('beforeend', `<div class="msg ai">${md(said)}</div>`); msgs.scrollTop = msgs.scrollHeight;
        return said;
      } catch (e) { msgs.querySelector('.thinking-msg')?.remove(); return e.detail || 'I could not answer that.'; }
    });
    function draw() {
      root.innerHTML = `
      <div class="head"><div><h1>Talk to C-EXA</h1><p>Your 1-on-1 with the controller — her thinking, and everything she remembers, which you can remove.</p></div>
        <div class="row" id="model-pill">${modelPill()}<span class="small muted">${esc(health.version ? 'CBX ' + health.version : '')}</span></div></div>
      <div class="chat">
        <div class="card thread">
          <div class="msgs" id="msgs">${thread.messages.length ? thread.messages.map(msgHtml).join('') : '<div class="empty">Nothing yet. Say hello — or ask what needs you today.</div>'}</div>
          <div class="compose"><input type="text" id="in" placeholder="Ask or give her a job…"><button class="btn primary" id="send">Send</button><button class="btn ghost" id="clear-chat" title="Clears this conversation. The audit trail keeps it; C-EXA starts fresh.">Clear chat</button></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:16px">
          <div class="card"><div class="hd"><h2>Today's plan</h2><span class="small muted num">${plan.done || 0} / ${plan.total || 0}</span></div>
            <ul class="list plan">${plan.items.map((i) => `<li><div class="row"><span class="check ${i.done ? 'on' : ''}"></span><span ${i.done ? 'class="muted"' : ''}>${esc(i.text)}</span></div></li>`).join('') || '<li class="empty">No plan yet today.</li>'}</ul></div>
          <div class="card"><div class="hd"><h2>C-EXA's journal</h2><span class="small muted">what she's keeping · ${journal.entries.length}</span></div>
            <ul class="list journal" id="journal">${journal.entries.map((e) => { const [t, k] = KIND_PILL[e.kind] || [e.kind, 'neutral']; return `<li style="align-items:flex-start"><div class="t"><div class="row" style="gap:6px;margin-bottom:4px">${pill(t, k)}<span class="small muted">${fmtDate(e.at)}${e.pinned ? ' · pinned' : ''}</span></div><p>${esc(e.text)}</p></div><button class="btn sm ghost" data-forget="${e.id}" title="Remove from C-EXA's memory">Forget</button></li>`; }).join('') || '<li class="empty">Empty. C-EXA writes here as she learns how the team works — work things only, never personal.</li>'}</ul></div>
          <div class="card"><div class="hd"><h2>Ideas parked</h2><button class="btn sm ghost" id="to-dev">Open board</button></div>
            <ul class="list">${(props.noticed || []).map((p) => `<li><div class="t"><b>${esc(p.title)}</b><span>${esc(p.evidence_kind === 'requests' ? 'from requests' : p.evidence_kind === 'audit' ? 'from the audit trail' : p.evidence_kind === 'errors' ? 'from errors' : p.evidence_kind === 'corrections' ? 'from corrections' : 'suggested to you')} · needs your OK</span></div></li>`).join('') || '<li class="empty">Nothing parked. When C-EXA notices a pattern it lands here first.</li>'}</ul></div>
        </div>
      </div>`;
      const msgs = root.querySelector('#msgs'); msgs.scrollTop = msgs.scrollHeight;
      root.querySelector('#to-dev').onclick = () => ctx.go('cexa-dev');
      root.querySelector('#clear-chat').onclick = async () => {
        if (!(await confirmBox('Clear this conversation? C-EXA starts fresh; the audit trail keeps a copy.', 'Clear'))) return;
        try { await api.post('/api/cexa/thread/clear'); toast('Chat cleared'); await loadAll(); draw(); } catch (e) { toast(e.detail || 'Could not clear', 'err'); }
      };
      root.querySelectorAll('[data-forget]').forEach((b) => (b.onclick = async () => {
        if (!(await confirmBox('Remove this from C-EXA\'s memory? She will not know it any more. This is logged.', 'Forget it'))) return;
        try { await api.del(`/api/cexa/journal/${b.dataset.forget}`); toast('Forgotten · logged'); journal = await api.get('/api/cexa/journal'); draw(); } catch (e) { toast(e.detail, 'err'); }
      }));
      const input = root.querySelector('#in');
      const compose = input.closest('.compose');
      if (compose && !compose.querySelector('.mic')) {
        compose.insertBefore(voice.micButton(input), root.querySelector('#send'));
        compose.insertBefore(talk, root.querySelector('#send'));
        compose.append(voice.speakToggle());
      }
      const send = async () => {
        const v = input.value.trim(); if (!v) return;
        input.value = ''; input.disabled = true;
        msgs.querySelector('.empty')?.remove();
        msgs.insertAdjacentHTML('beforeend', `<div class="msg me">${esc(v).replace(/\n/g, '<br>')}</div><div class="msg ai thinking-msg"><span class="spin"></span></div>`); msgs.scrollTop = msgs.scrollHeight;
        try {
          const r = await api.post('/api/cexa/chat', { text: v });
          voice.speak((r && (r.answer || (r.reply && r.reply.text))) || '');
          await loadAll(); draw(); root.querySelector('#in').focus({ preventScroll: true });
        } catch (e) { toast(e.detail || 'Could not send', 'err'); input.disabled = false; msgs.querySelector('.thinking-msg')?.remove(); }
      };
      root.querySelector('#send').onclick = send; input.onkeydown = (e) => { if (e.key === 'Enter') send(); };
      input.focus({ preventScroll: true });
    }
    draw();
  },
};
