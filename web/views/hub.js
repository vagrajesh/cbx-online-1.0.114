// A hub: one menu item holding pages that belong together (1.0.52). The pages stay what they were — each
// is drawn into the hub under a short row of names — so a link to an old page (#/new-request, #/users …)
// still works: app.js maps it to its hub and tab (ALIAS). Tabs a person may not open are not drawn.
import { esc } from '../ui.js';

export function hub({ title, tabs }) {
  const allowed = (t, me) => (!t.roles || t.roles.includes(me.role) || (t.also && me[t.also]));
  return {
    title,
    async render(root, ctx) {
      const mine = tabs.filter((t) => allowed(t, ctx.me));
      if (!mine.length) { root.innerHTML = `<div class="head"><div><h1>${esc(title)}</h1><p>Nothing here is for you.</p></div></div>`; return undefined; }
      const cur = mine.find((t) => t.key === ctx.params.tab) || mine[0];
      root.innerHTML = `${mine.length > 1 ? `<div class="hub-bar" role="tablist" aria-label="${esc(title)}">${mine.map((t) => `<button role="tab" aria-selected="${t === cur}" class="${t === cur ? 'on' : ''}" data-tab="${esc(t.key)}">${esc(t.label)}${t.count ? `<span class="count" data-count="${esc(t.count)}" data-label=""></span>` : ''}</button>`).join('')}</div>` : ''}<div class="hub-body"></div>`;
      root.querySelectorAll('[data-tab]').forEach((b) => (b.onclick = () => { if (b.dataset.tab !== cur.key) ctx.go(ctx.route, { tab: b.dataset.tab }); }));
      const body = root.querySelector('.hub-body');
      const { tab, ...rest } = ctx.params; // eslint-disable-line no-unused-vars
      return cur.view.render(body, { ...ctx, params: rest, tab: cur.key });
    },
  };
}
