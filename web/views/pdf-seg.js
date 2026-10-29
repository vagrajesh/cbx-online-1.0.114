// The three parts of PDF Studio, one row of tabs: Read PDFs (a batch), Harvest (folders watched all
// the time), Master sheets (where the rows end up). Each tab shows only for people who hold the tool
// behind it: pdf-studio for the first two, master-sheets for the third. Owners and admins hold all.
import { esc } from '../ui.js';

const SEGS = [
  { route: 'pdf-studio', label: 'Read PDFs', tool: 'pdf-studio' },
  { route: 'pdf-harvest', label: 'Harvest', tool: 'pdf-studio' },
  { route: 'master-sheets', label: 'Master sheets', tool: 'master-sheets' },
  { route: 'results', label: 'Results', tool: 'pdf-studio' },
];

export function pdfSegs(ctx) {
  const has = (slug) => (ctx.tools || []).some((t) => t.slug === slug);
  return SEGS.filter((s) => has(s.tool));
}

/** The tab row as HTML. `active` is the route of the page drawing it. */
export function pdfSegHtml(ctx, active) {
  const segs = pdfSegs(ctx);
  if (segs.length < 2) return '';
  return `<div class="hub-bar pdf-tabs" role="tablist">${segs.map((s) => `<button role="tab" aria-selected="${s.route === active}" class="${s.route === active ? 'on' : ''}" data-seg="${s.route}">${esc(s.label)}</button>`).join('')}</div>`;
}

/** Wire the tabs. `leave()` runs before switching (stop polling, drop timers). */
export function pdfSegWire(root, ctx, active, leave) {
  root.querySelectorAll('[data-seg]').forEach((b) => (b.onclick = () => {
    if (b.dataset.seg === active) return;
    if (leave) leave();
    ctx.go(b.dataset.seg, b.dataset.seg === 'pdf-studio' ? { new: 1 } : {});
  }));
}
