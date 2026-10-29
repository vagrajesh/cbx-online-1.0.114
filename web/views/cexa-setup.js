// C-EXA's voice and the AI models (1.0.83): set up once, so they live in Settings — not on the Now page.
import { aiName } from '../ui.js';
import { makeNow } from './agents-now.js';

export default {
  title: 'C-EXA voice & models',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner') { root.innerHTML = '<div class="head"><div><h1>Voice &amp; models</h1><p>For the owner.</p></div></div>'; return undefined; }
    const ai = aiName();
    root.innerHTML = `<div class="head"><div><h1>${ai}'s voice &amp; models</h1><p>How she sounds, and which AI model each kind of agent runs on. Both stay on this server.</p></div></div><div id="cx-setup"></div>`;
    const part = makeNow(root.querySelector('#cx-setup'), { canAct: true, ai, org: () => null, part: 'setup' });
    await part.refresh();
    let timer = null;
    const tick = async () => { if (!root.isConnected) return; if (part.busy()) await part.refresh(); timer = setTimeout(tick, 5000); };
    timer = setTimeout(tick, 5000);
    return () => { if (timer) clearTimeout(timer); };
  },
};
