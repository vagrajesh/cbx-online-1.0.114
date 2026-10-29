// Develop board: To do / Working / Done for admins and owners. C-EXA reads every card and comments on it.
import { api } from '../api.js';
import { esc, toast, modal, confirmBox, pill, fmtDate, initials, el, aiInitials } from '../ui.js';

const POLL_MS = 20000;
const COLS = [['todo', 'To do'], ['working', 'Working'], ['done', 'Done this week']];
const NEXT = { todo: 'working', working: 'done' };
const PREV = { working: 'todo', done: 'working' };

const av = (t) => (t.assignee_axl ? `<span class="av" style="background:var(--ai-soft);color:var(--ai)">${esc(aiInitials())}</span>` : t.assignee ? `<span class="av">${esc(initials(t.assignee))}</span>` : '');
const dueText = (t) => {
  if (!t.due) return '<span>no date</span>';
  if (t.overdue) return `<span style="color:var(--crit)">due ${fmtDate(t.due)}</span>`;
  return `<span>due ${fmtDate(t.due)}</span>`;
};
const dateInput = (iso) => (iso ? new Date(iso).toISOString().slice(0, 10) : '');
const noteHtml = (n) => (n.by === 'axl' ? `<div class="note cexa"><b>C-EXA ·</b> ${esc(n.text)}</div>` : `<div class="note">${esc(n.by_name)}: ${esc(n.text)}</div>`);

export default {
  title: 'Develop',
  async render(root, ctx) {
    if (ctx.me.role === 'user') { root.innerHTML = '<div class="head"><div><h1>Develop</h1><p>This page is for the team who build CBX.</p></div></div>'; return; }
    let data;
    let timer = null;
    let open = null; // task id open in the modal
    const dispose = () => { if (timer) clearInterval(timer); timer = null; };

    async function load() { data = await api.get('/api/develop/tasks'); }
    try { await load(); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Develop</h1><p>${esc(e.detail || e.message)}</p></div></div>`; return dispose; }

    function card(t) {
      const origin = t.origin === 'proposal' ? pill('C-EXA proposal', 'ai') : t.origin === 'request' ? pill('from request', 'neutral') : '';
      const notes = t.notes.slice(-2).map(noteHtml).join('');
      const more = t.notes.length > 2 ? `<div class="small muted" style="margin-top:6px">+ ${t.notes.length - 2} earlier note${t.notes.length - 2 === 1 ? '' : 's'}</div>` : '';
      const moves = `<div class="acts row" style="gap:6px;margin-top:10px">${PREV[t.status] ? `<button class="btn sm ghost" data-move="${t.id}:${PREV[t.status]}" title="Move back">←</button>` : ''}${NEXT[t.status] ? `<button class="btn sm" data-move="${t.id}:${NEXT[t.status]}">${NEXT[t.status] === 'working' ? 'Start' : 'Done'}</button>` : ''}</div>`;
      return `<div class="task${t.overdue ? ' over' : ''}" draggable="true" data-task="${t.id}" ${t.status === 'done' ? 'style="opacity:.8"' : ''}>
        <b data-open="${t.id}" style="cursor:pointer">${esc(t.title)}</b>${t.detail ? `<span class="small muted">${esc(t.detail.length > 90 ? t.detail.slice(0, 90) + '…' : t.detail)}</span>` : ''}
        <div class="m">${av(t)}<span>${esc(t.assignee || 'unassigned')}</span><span>·</span>${t.status === 'done' ? `<span>${fmtDate(t.done_at)}</span>` : dueText(t)}${origin}</div>
        ${notes}${more}${moves}</div>`;
    }

    function draw() {
      const c = data.columns;
      root.innerHTML = `
      <div class="head"><div><h1>Develop</h1><p>The team's board. Add tasks, move them along, leave notes. C-EXA reads every card, fixes what she can, and says so on the card.</p></div>
        <div class="row">${pill('C-EXA watching', 'ai')}<button class="btn primary sm" id="add">Add task</button></div></div>
      <div class="kan">${COLS.map(([k, label]) => `<div class="col" data-col="${k}"><h3>${label} <span>${c[k].length}</span></h3>${c[k].map(card).join('')}${c[k].length ? '' : '<div class="empty small">Nothing here.</div>'}${k === 'done' && data.done_total > c.done.length ? `<div class="task muted" style="text-align:center;border-style:dashed;cursor:default">+ ${data.done_total - c.done.length} more, older</div>` : ''}</div>`).join('')}</div>`;
      root.querySelector('#add').onclick = addTask;
      root.querySelectorAll('[data-open]').forEach((b) => (b.onclick = () => openTask(+b.dataset.open)));
      root.querySelectorAll('[data-move]').forEach((b) => (b.onclick = () => { const [id, to] = b.dataset.move.split(':'); move(+id, to); }));
      // drag between columns
      root.querySelectorAll('[data-task]').forEach((t) => { t.ondragstart = (e) => { e.dataTransfer.setData('text/plain', t.dataset.task); e.dataTransfer.effectAllowed = 'move'; }; });
      root.querySelectorAll('[data-col]').forEach((col) => {
        col.ondragover = (e) => { e.preventDefault(); col.classList.add('over'); };
        col.ondragleave = () => col.classList.remove('over');
        col.ondrop = (e) => { e.preventDefault(); col.classList.remove('over'); const id = +e.dataTransfer.getData('text/plain'); if (id) move(id, col.dataset.col); };
      });
    }

    async function refresh() { if (open) return; try { await load(); draw(); } catch { /* keep the last good board */ } }

    async function move(id, to) {
      try { await api.patch(`/api/develop/tasks/${id}`, { status: to }); await load(); draw(); ctx.refreshCounts && ctx.refreshCounts(); } catch (e) { toast(e.detail || 'Could not move', 'err'); }
    }

    const peopleOptions = (sel, axl) => `<option value="" ${!sel && !axl ? 'selected' : ''}>Unassigned</option><option value="axl" ${axl ? 'selected' : ''}>C-EXA</option>${data.people.map((p) => `<option value="${p.id}" ${sel === p.id && !axl ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}`;
    const assignee = (v) => (v === 'axl' ? { assignee_axl: true, assignee_id: null } : { assignee_axl: false, assignee_id: v ? +v : 0 });

    function addTask() {
      const body = el(`<div style="display:flex;flex-direction:column;gap:12px">
        <div class="field"><label>Title</label><input id="title" type="text" placeholder="What needs doing"></div>
        <div class="field"><label>Detail</label><textarea id="detail" rows="3" placeholder="Context, links, what done looks like"></textarea></div>
        <div class="row" style="gap:8px"><div class="field" style="flex:1"><label>Assignee</label><select id="who">${peopleOptions(null, false)}</select></div><div class="field"><label>Due</label><input id="due" type="date"></div></div></div>`);
      const m = modal({ title: 'Add a task', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn primary" id="ok">Add to board</button>' });
      body.querySelector('#title').focus();
      m.root.querySelector('#ok').onclick = async () => {
        try {
          const who = body.querySelector('#who').value;
          await api.post('/api/develop/tasks', { title: body.querySelector('#title').value, detail: body.querySelector('#detail').value, due: body.querySelector('#due').value || null, assignee_axl: who === 'axl', assignee_id: who && who !== 'axl' ? +who : null });
          m.close(); toast('Task added · logged'); await load(); draw(); ctx.refreshCounts && ctx.refreshCounts();
        } catch (e) { toast(e.detail || 'Could not add', 'err'); }
      };
    }

    async function openTask(id) {
      let t;
      try { t = await api.get(`/api/develop/tasks/${id}`); } catch (e) { toast(e.detail, 'err'); return; }
      open = id;
      const body = el('<div style="display:flex;flex-direction:column;gap:14px"></div>');
      const m = modal({ title: 'Task', body, footer: `<button class="btn ghost sm" id="del" style="margin-right:auto;color:var(--crit)">Delete</button><button class="btn" data-close>Close</button>`, onClose: () => { open = null; refresh(); } });
      const paint = () => {
        body.innerHTML = `
          <div class="row between" style="gap:8px">${pill(COLS.find(([k]) => k === t.status)[1], t.status === 'done' ? 'good' : t.status === 'working' ? 'info' : 'neutral')}<div class="row" style="gap:6px">${COLS.filter(([k]) => k !== t.status).map(([k, l]) => `<button class="btn sm" data-st="${k}">Move to ${l.replace(' this week', '')}</button>`).join('')}</div></div>
          <div class="field"><label>Title</label><input id="title" type="text" value="${esc(t.title)}"></div>
          <div class="field"><label>Detail</label><textarea id="detail" rows="3">${esc(t.detail)}</textarea></div>
          <div class="row" style="gap:8px"><div class="field" style="flex:1"><label>Assignee</label><select id="who">${peopleOptions(t.assignee_id, t.assignee_axl)}</select></div><div class="field"><label>Due</label><input id="due" type="date" value="${dateInput(t.due)}"></div><button class="btn sm primary" id="save" style="align-self:flex-end">Save</button></div>
          ${t.overdue ? `<div class="err-box">Overdue since ${fmtDate(t.due)}.</div>` : ''}
          <div><div class="eyebrow" style="margin-bottom:8px">Notes</div><div class="task" style="cursor:default;margin:0;padding:4px 12px 10px">${t.notes.length ? t.notes.map((n) => noteHtml(n) + `<div class="small muted" style="margin-top:2px;padding-left:10px">${fmtDate(n.at, { time: true })}</div>`).join('') : '<div class="small muted" style="padding-top:8px">No notes yet.</div>'}</div></div>
          <div class="row" style="gap:8px"><input id="note" type="text" placeholder="Add a note for the team (C-EXA reads it too)" style="flex:1"><button class="btn sm" id="addnote">Add note</button></div>
          <div class="small muted">Created ${fmtDate(t.created_at, { time: true })}${t.started_at ? ` · started ${fmtDate(t.started_at)}` : ''}${t.done_at ? ` · done ${fmtDate(t.done_at)}` : ''}${t.origin_ref ? ` · ${esc(t.origin_ref)}` : ''}</div>`;
        body.querySelectorAll('[data-st]').forEach((b) => (b.onclick = async () => { try { t = await api.patch(`/api/develop/tasks/${id}`, { status: b.dataset.st }); paint(); toast('Moved'); } catch (e) { toast(e.detail, 'err'); } }));
        body.querySelector('#save').onclick = async () => {
          try { t = await api.patch(`/api/develop/tasks/${id}`, { title: body.querySelector('#title').value, detail: body.querySelector('#detail').value, due: body.querySelector('#due').value || '', ...assignee(body.querySelector('#who').value) }); paint(); toast('Saved · logged'); } catch (e) { toast(e.detail, 'err'); }
        };
        const noteIn = body.querySelector('#note');
        const addNote = async () => { const v = noteIn.value.trim(); if (!v) return; try { t = await api.post(`/api/develop/tasks/${id}/notes`, { text: v }); paint(); } catch (e) { toast(e.detail, 'err'); } };
        body.querySelector('#addnote').onclick = addNote; noteIn.onkeydown = (e) => { if (e.key === 'Enter') addNote(); };
      };
      paint();
      m.root.querySelector('#del').onclick = async () => { if (!(await confirmBox(`Delete “${t.title}”? This is logged.`, 'Delete'))) return; try { await api.del(`/api/develop/tasks/${id}`); m.close(); toast('Task deleted · logged'); } catch (e) { toast(e.detail, 'err'); } };
    }

    draw();
    timer = setInterval(refresh, POLL_MS);
    return dispose;
  },
};
