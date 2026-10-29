// Messages — one page for every conversation: colleagues (direct messages, your team's room, rooms
// an admin made) and C-EXA (the interviews about your requests, and her notes to you).
//
// People-to-people chat is deliberately small: text, newest at the bottom, nothing edited or
// deleted. Address C-EXA by name ("@C-EXA what's expiring this week") and she answers in the room,
// seeing exactly what you may see. The page polls every few seconds while it is open; nothing here
// needs a socket.
import { api } from '../api.js';
import { esc, toast, pill, fmtDate, aiAvatar, avatar, modal, el } from '../ui.js';

const br = (s) => esc(s).replace(/\n/g, '<br>');
const POLL_MS = 4000;

export default {
  title: 'Messages',
  async render(root, ctx) {
    const isAdmin = ctx.me.role !== 'user';
    let [chat, data] = await Promise.all([api.get('/api/chat'), api.get('/api/interviews')]);
    // what to open first: a chat from the URL, else the first unread chat, else the first C-EXA thread
    let sel = ctx.params.chat ? `c:${ctx.params.chat}`
      : ctx.params.interview ? `iv:${ctx.params.interview}`
        : (chat.conversations.find((c) => c.unread) || {}).id ? `c:${chat.conversations.find((c) => c.unread).id}`
          : data.interviews[0] ? `iv:${data.interviews[0].id}` : chat.conversations[0] ? `c:${chat.conversations[0].id}` : data.notes.length ? 'notes' : '';
    let poll = null;        // timer for the open chat
    let lastId = 0;         // newest message id we have drawn
    let disposed = false;
    const stopPoll = () => { if (poll) { clearInterval(poll); poll = null; } };

    const chatListHtml = () => chat.conversations.map((c) => {
        const who = c.kind === 'team' ? `<div class="avatar" title="Team room">${esc((c.name || 'T').slice(0, 2).toUpperCase())}</div>` : c.kind === 'room' ? `<div class="avatar" title="Room">#</div>` : avatar(c.name);
        const last = c.last ? (c.last.kind === 'axl' ? `${esc(chat.ai)}: ` : c.last.sender_id === chat.me ? 'You: ' : '') + esc(c.last.text) : (c.kind === 'team' ? 'Your team\'s room. Nothing said yet.' : 'No messages yet.');
        return `<li data-sel="c:${c.id}" style="${sel === `c:${c.id}` ? 'background:var(--accent-soft)' : ''};cursor:pointer">${who}<div class="t"><b>${esc(c.name)}${c.unread ? ` <span class="unread">${c.unread}</span>` : ''}</b><span>${last}</span></div>${c.last ? `<em class="small muted">${fmtDate(c.last.at, { rel: true })}</em>` : ''}</li>`;
      }).join('') || '<li class="empty">Nobody yet. "New message" starts one with a colleague.</li>';
    const wireList = (scope) => scope.querySelectorAll('[data-sel]').forEach((li) => (li.onclick = () => { sel = li.dataset.sel; draw(); }));

    const draw = () => {
      const chats = chatListHtml();
      const items = data.interviews.map((c) => {
        const mineOrOther = isAdmin && c.requester.id !== ctx.me.id ? `${esc(c.requester.name.split(' ')[0])} · ` : '';
        return `<li data-sel="iv:${c.id}" style="${sel === `iv:${c.id}` ? 'background:var(--accent-soft)' : ''};cursor:pointer">${aiAvatar()}<div class="t"><b>${esc(chat.ai)} · ${mineOrOther}request #${c.number}</b><span>${esc(c.last ? c.last.text.slice(0, 80) : c.title)}</span></div>${['cancelled', 'declined'].includes(c.request_status) ? pill(c.request_status === 'declined' ? 'Declined' : 'Cancelled', 'neutral') : c.status === 'done' ? pill(c.request_status === 'ready' ? 'Live' : 'Ticket created', 'good') : c.mode === 'human' ? pill('Meeting', 'neutral') : pill('Interview', 'info', true)}</li>`;
      }).join('');
      const notes = data.notes.length ? `<li data-sel="notes" style="${sel === 'notes' ? 'background:var(--accent-soft)' : ''};cursor:pointer">${aiAvatar()}<div class="t"><b>${esc(chat.ai)} · notes to you</b><span>${esc(data.notes[data.notes.length - 1].title)}</span></div></li>` : '';
      root.innerHTML = `
      <div class="head"><div><h1>Messages</h1><p>Colleagues on the left, ${esc(chat.ai)} below them. Everything here is kept.</p></div>
        <div class="row" style="gap:8px"><button class="btn primary sm" id="new-dm">New message</button>${chat.can_make_rooms ? '<button class="btn sm" id="new-room">New room</button>' : ''}</div></div>
      <div class="grid g12">
        <div class="card">
          <div class="hd"><h2>People</h2></div>
          <ul class="list" id="chat-list">${chats}</ul>
          <div class="hd" style="border-top:1px solid var(--line)"><h2>${esc(chat.ai)}</h2></div>
          <ul class="list">${items}${notes}${!items && !notes ? `<li class="empty">No conversations with ${esc(chat.ai)} yet. When a request is approved, she starts one here.</li>` : ''}</ul>
        </div>
        <div class="card" id="thread"></div>
      </div>`;
      wireList(root);
      root.querySelector('#new-dm').onclick = newDm;
      const nr = root.querySelector('#new-room'); if (nr) nr.onclick = newRoom;
      pane();
    };

    // ---- the right-hand pane ----------------------------------------------------------------
    async function pane() {
      stopPoll();
      const box = root.querySelector('#thread');
      if (!sel) { box.innerHTML = '<div class="bd empty">Pick a conversation on the left, or start one.</div>'; return; }
      if (sel.startsWith('c:')) return chatPane(box, Number(sel.slice(2)));
      if (sel === 'notes') {
        box.innerHTML = `<div class="hd"><div><h2>${esc(chat.ai)} · notes to you</h2><span class="small muted">Short notes when something about your requests changes</span></div></div>
          <div class="dm">${data.notes.map((n) => `<div class="msg ai"><b>${esc(n.title)}</b><br>${br(n.text)}${n.link ? `<br><a href="${esc(n.link)}" style="color:var(--accent)">open</a>` : ''}<span class="meta">${fmtDate(n.at, { time: true })}</span></div>`).join('')}</div>`;
        api.post('/api/home/messages/read').then(ctx.refreshCounts).catch(() => {});
        return;
      }
      return interviewPane(box, sel.split(':')[1]);
    }

    async function chatPane(box, cid) {
      let t;
      try { t = await api.get(`/api/chat/${cid}`); } catch (e) { box.innerHTML = `<div class="bd err-box">${esc(e.detail)}</div>`; return; }
      const c = t.conversation;
      const who = c.kind === 'dm' ? '' : `<span class="small muted">${t.members.map((m) => esc(m.name.split(' ')[0])).join(', ')}</span>`;
      box.innerHTML = `
        <div class="hd"><div><h2>${esc(c.name)}</h2>${who || `<span class="small muted">${esc(c.sub || '')}</span>`}</div><span class="small muted">${c.kind === 'team' ? 'Team room' : c.kind === 'room' ? 'Room' : 'Direct message'}</span></div>
        <div class="dm" id="dm"></div>
        <div class="compose"><input type="text" id="in" placeholder="Message… (@${esc(chat.ai)} asks her)" maxlength="4000"><button class="btn primary" id="send">Send</button></div>`;
      const dm = box.querySelector('#dm');
      lastId = 0;
      appendMessages(dm, t.messages, true);
      const input = box.querySelector('#in');
      const send = async () => {
        const v = input.value.trim(); if (!v) return;
        input.value = ''; input.disabled = true;
        try {
          const r = await api.post(`/api/chat/${cid}`, { text: v });
          appendMessages(dm, [r.message], true);
          if (r.asked_axl) appendThinking(dm);
          chat.conversations.forEach((x) => { if (x.id === cid) x.last = { text: v, at: r.message.at, sender_id: chat.me, kind: 'text' }; });
        } catch (e) { toast(e.detail || 'Could not send', 'err'); input.value = v; }
        finally { input.disabled = false; input.focus(); }
      };
      box.querySelector('#send').onclick = send;
      input.onkeydown = (e) => { if (e.key === 'Enter') send(); };
      input.focus();
      chat.conversations.forEach((x) => { if (x.id === cid) x.unread = 0; });
      ctx.refreshCounts && ctx.refreshCounts();
      // poll for new lines while this conversation is open
      poll = setInterval(async () => {
        if (disposed || !dm.isConnected) { stopPoll(); return; }
        if (document.visibilityState !== 'visible') return;
        try {
          const r = await api.get(`/api/chat/${cid}?after=${lastId}`, { quiet: true });
          if (r.messages.length) {
            const th = dm.querySelector('.thinking-msg'); if (th && r.messages.some((m) => m.kind === 'axl')) th.remove();
            appendMessages(dm, r.messages, true);
            ctx.refreshCounts && ctx.refreshCounts();
          }
        } catch { /* next tick */ }
      }, POLL_MS);
    }

    function appendMessages(dm, msgs, scroll) {
      const atBottom = dm.scrollHeight - dm.scrollTop - dm.clientHeight < 60;
      let day = dm.dataset.day || '';
      for (const m of msgs) {
        if (m.id <= lastId) continue;
        lastId = Math.max(lastId, m.id);
        const d = new Date(m.at).toDateString();
        if (d !== day) { day = d; dm.insertAdjacentHTML('beforeend', `<span class="day">${new Date(m.at).toLocaleDateString('en-ZA', { weekday: 'long' })} ${fmtDate(m.at)}</span>`); }
        const mine = m.sender_id === chat.me;
        const cls = mine ? 'me' : 'ai';
        const name = mine ? '' : `<b>${esc(m.sender || (m.kind === 'axl' ? chat.ai : ''))}</b><br>`;
        dm.insertAdjacentHTML('beforeend', `<div class="msg ${cls} ${m.kind === 'axl' ? 'from-cexa' : ''}" data-id="${m.id}">${name}${br(m.text)}<span class="meta">${fmtDate(m.at, { time: true })}</span></div>`);
      }
      dm.dataset.day = day;
      if (scroll && (atBottom || msgs.some((m) => m.sender_id === chat.me))) dm.scrollTop = dm.scrollHeight;
    }
    function appendThinking(dm) {
      if (!dm.querySelector('.thinking-msg')) dm.insertAdjacentHTML('beforeend', `<div class="msg ai thinking-msg" title="${esc(chat.ai)} is answering"><span class="spin"></span></div>`);
      dm.scrollTop = dm.scrollHeight;
    }

    // ---- starting conversations ------------------------------------------------------------
    function newDm() {
      if (!chat.people.length) { toast('Nobody to message yet — your team and the admins appear here once they have accounts.'); return; }
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Who</label><input id="who-q" type="text" placeholder="Type a name…" autocomplete="off"></div>
        <ul class="list" id="who-list" style="max-height:260px;overflow:auto"></ul>
        <p class="small muted">You can message people on your own team and the admins. A direct message is between the two of you — nobody else can read it.</p></div>`);
      const list = body.querySelector('#who-list');
      const fill = (q) => {
        const rows = chat.people.filter((p) => !q || p.name.toLowerCase().includes(q) || (p.team || '').toLowerCase().includes(q)).slice(0, 30);
        list.innerHTML = rows.map((p) => `<li data-p="${p.id}" style="cursor:pointer">${avatar(p.name)}<div class="t"><b>${esc(p.name)}</b><span>${esc(p.title || '')}${p.team ? (p.title ? ' · ' : '') + esc(p.team) : ''}</span></div></li>`).join('') || '<li class="empty">Nobody matches.</li>';
        list.querySelectorAll('[data-p]').forEach((li) => (li.onclick = async () => {
          try { const c = await api.post('/api/chat/dm', { user_id: +li.dataset.p }); m.close(); chat = await api.get('/api/chat'); sel = `c:${c.id}`; draw(); }
          catch (e) { toast(e.detail || 'Could not start that conversation', 'err'); }
        }));
      };
      const m = modal({ title: 'New message', body, footer: `<button class="btn" data-close>Cancel</button>` });
      body.querySelector('#who-q').oninput = (e) => fill(e.target.value.trim().toLowerCase());
      fill('');
    }

    function newRoom() {
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Room name</label><input id="r-name" type="text" placeholder="Capitec month-end" maxlength="120"></div>
        <div class="field"><label>Who is in it</label><div class="cols" style="grid-template-columns:1fr 1fr;max-height:260px;overflow:auto">${chat.people.map((p) => `<label class="colbox"><input type="checkbox" value="${p.id}" style="width:auto"> ${esc(p.name)}</label>`).join('')}</div></div>
        <p class="small muted">A room is whoever you put in it. You are in it too.</p></div>`);
      const m = modal({ title: 'New room', body, footer: `<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Create room</button>` });
      m.root.querySelector('#ok').onclick = async () => {
        const ids = [...body.querySelectorAll('input[type=checkbox]:checked')].map((c) => +c.value);
        try { const c = await api.post('/api/chat/rooms', { name: body.querySelector('#r-name').value, member_ids: ids }); m.close(); chat = await api.get('/api/chat'); sel = `c:${c.id}`; draw(); }
        catch (e) { toast(e.detail || 'Could not create the room', 'err'); }
      };
    }

    // ---- C-EXA interview thread (unchanged) -----------------------------------------------------
    async function interviewPane(box, id) {
      let thread;
      try { thread = (await api.get(`/api/interviews/${id}`)).interview; } catch (e) { box.innerHTML = `<div class="bd err-box">${esc(e.detail)}</div>`; return; }
      const done = thread.status === 'done';
      const closed = ['cancelled', 'declined'].includes(thread.request_status);
      const canType = !closed && !(thread.mode === 'human' && !done);
      const isMe = (m) => m.sender_id === ctx.me.id;
      let day = '';
      const msgs = thread.messages.map((m) => {
        const d = new Date(m.at).toDateString(); let sep = '';
        if (d !== day) { day = d; sep = `<span class="day">${new Date(m.at).toLocaleDateString('en-ZA', { weekday: 'long' })} ${fmtDate(m.at)}</span>`; }
        const rules = m.rule_ids.length ? `<span class="meta">${m.rule_ids.length === 1 ? 'rule captured' : m.rule_ids.length + ' rules captured'}</span>` : '';
        return sep + `<div class="msg ${isMe(m) ? 'me' : 'ai'}">${m.sender !== 'axl' && !isMe(m) ? `<b>${esc(m.sender === 'admin' ? (thread.lead ? thread.lead.name : 'Admin') : thread.requester.name)}</b><br>` : ''}${br(m.text)}${rules}</div>`;
      }).join('');
      const status = closed ? pill(thread.request_status === 'declined' ? 'Declined' : 'Cancelled', 'neutral') : done ? pill(thread.request_status === 'ready' ? 'Live' : 'Done · ticket created', 'good') : thread.mode === 'human' ? pill('Meeting with ' + (thread.lead ? thread.lead.name.split(' ')[0] : 'the team'), 'neutral') : pill('Interview', 'info', true);
      const openTicket = isAdmin && thread.ticket_id ? `<button class="btn sm" id="open-ticket">Open build</button>` : '';
      box.innerHTML = `
        <div class="hd"><div><h2>${esc(chat.ai)} · ${esc(thread.title)}</h2><span class="small muted">Request #${thread.number} · ${thread.mode === 'axl' ? 'interview' : 'meeting'} · ${fmtDate(thread.started_at, { time: true })}${thread.finished_at ? '–' + fmtDate(thread.finished_at) : ''}</span></div><div class="row" style="gap:8px">${openTicket}${status}</div></div>
        <div class="dm" id="dm">${msgs}</div>
        <div class="compose"><input type="text" id="in" placeholder="${canType ? `Message ${esc(chat.ai)} about this request…` : closed ? `This request was ${thread.request_status} — the conversation is closed.` : 'The team will come back to you after the meeting.'}" ${canType ? '' : 'disabled'}><button class="btn primary" id="send" ${canType ? '' : 'disabled'}>Send</button></div>`;
      const dm = box.querySelector('#dm'); dm.scrollTop = dm.scrollHeight;
      if (openTicket) box.querySelector('#open-ticket').onclick = () => ctx.go('build', { id: thread.ticket_id });
      const input = box.querySelector('#in');
      const send = async () => {
        const v = input.value.trim(); if (!v) return;
        input.value = ''; input.disabled = true;
        dm.insertAdjacentHTML('beforeend', `<div class="msg me">${br(v)}</div><div class="msg ai thinking-msg"><span class="spin"></span></div>`); dm.scrollTop = dm.scrollHeight;
        try {
          const r = await api.post(`/api/interviews/${id}/reply`, { text: v });
          if (r.done && thread.status !== 'done') toast('Ticket created · your request is now being built');
          if (sel !== `iv:${id}`) return;
          data = await api.get('/api/interviews'); ctx.refreshCounts && ctx.refreshCounts(); draw();
        } catch (e) { toast(e.detail || 'Could not send', 'err'); input.disabled = false; dm.querySelector('.thinking-msg')?.remove(); }
      };
      box.querySelector('#send').onclick = send; input.onkeydown = (e) => { if (e.key === 'Enter') send(); };
      if (canType) input.focus();
    }

    draw();
    // the left list follows new activity too, at a gentler pace
    const listPoll = setInterval(async () => {
      if (disposed) return;
      if (document.visibilityState !== 'visible') return;
      try {
        const fresh = await api.get('/api/chat', { quiet: true });
        const changed = JSON.stringify(fresh.conversations.map((c) => [c.id, c.unread, c.last && c.last.at])) !== JSON.stringify(chat.conversations.map((c) => [c.id, c.unread, c.last && c.last.at]));
        chat = fresh;
        if (changed) {
          // repaint the list only — the open thread has its own poll, and the composer keeps its text
          const list = root.querySelector('#chat-list');
          if (list) { list.innerHTML = chatListHtml(); wireList(list); }
        }
      } catch { /* next tick */ }
    }, POLL_MS * 3);
    return () => { disposed = true; stopPoll(); clearInterval(listPoll); };
  },
};
