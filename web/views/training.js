// Training — lessons for the tools you hold, played on the real screens (web/training/tour.js).
import { api } from '../api.js';
import { esc, toast, pill } from '../ui.js';
import { tour } from '../training/tour.js';
import { LESSONS } from '../training/lessons.js';

export default {
  title: 'Help',
  async render(root, ctx) {
    let d;
    try { d = await api.get('/api/training'); } catch (e) { root.innerHTML = `<div class="head"><div><h1>Training</h1><p>${esc(e.detail || 'Could not load the lessons')}</p></div></div>`; return; }

    const draw = () => {
      const pct = d.available ? Math.round((d.done / d.available) * 100) : 0;
      const groups = d.groups.map((g) => {
        const rows = d.lessons.filter((l) => l.group === g).map((l) => {
          const p = l.progress;
          const steps = (LESSONS[l.id] || { steps: [] }).steps.length;
          const state = p.done ? pill('Done', 'good') : p.started && p.step > 0 ? pill(`Step ${Math.min(p.step + 1, steps)} of ${steps}`, 'info') : '';
          const btn = !l.available
            ? `<span class="small muted">${esc(l.why_not)}</span>`
            : p.done
              ? `<button class="btn sm" data-start="${esc(l.id)}">Take again</button>`
              : p.started && p.step > 0
                ? `<button class="btn sm primary" data-start="${esc(l.id)}" data-step="${p.step}">Continue</button><button class="btn sm ghost" data-start="${esc(l.id)}" data-step="0">Restart</button>`
                : `<button class="btn sm primary" data-start="${esc(l.id)}">Start</button>`;
          return `<li class="${l.available ? '' : 'off'}"><div class="t"><b>${esc(l.title)} ${state}</b><span>${esc(l.blurb)}</span><em class="small muted">${steps} step${steps === 1 ? '' : 's'} · about ${l.minutes} min</em></div><div class="row tr-act" style="gap:6px">${btn}</div></li>`; // 1.0.106: flex lives in app.css (.tr-act) — a long "you don't have the tool" line squeezed the lesson to one word a line on a phone
        }).join('');
        return `<div class="card" style="margin-bottom:16px"><div class="hd"><h2>${esc(g)}</h2></div><ul class="list tr-list">${rows}</ul></div>`;
      }).join('');

      root.innerHTML = `
      <div class="head"><div><h1>Help</h1><p>Short lessons on the real screens, with practice PDFs — nothing real is touched.</p></div></div>
      <div class="card" style="margin-bottom:16px"><div class="bd tr-summary">
        <div><div class="eyebrow">Your progress</div><div class="v">${d.done} <span class="muted">of ${d.available}</span></div><div class="small muted">lessons done</div></div>
        <div class="tr-bar"><i style="width:${pct}%"></i></div>
        <div class="small muted" style="max-width:420px">A lesson ends when you have done the thing once for real. Take any of them again whenever you like — nothing is marked, nothing is timed.</div>
      </div></div>
      ${groups}`;
      root.querySelectorAll('[data-start]').forEach((b) => (b.onclick = () => {
        const id = b.dataset.start;
        const from = b.dataset.step !== undefined ? Number(b.dataset.step) : 0;
        if (!LESSONS[id]) { toast('That lesson is not in this version yet.', 'err'); return; }
        tour.start(id, from);
      }));
    };
    draw();

    // When a lesson ends (done or left), refresh the list so the state is right.
    const onTour = async (ev) => { if (ev.detail && ev.detail.ended && root.isConnected) { try { d = await api.get('/api/training'); draw(); } catch { /* keep what we have */ } } };
    window.addEventListener('cbx:tour', onTour);
    return () => window.removeEventListener('cbx:tour', onTour);
  },
};
