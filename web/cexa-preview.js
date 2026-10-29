// C-EXA's designer takes her screenshots of this page (cbx/cexa/eyes.py): a draft page drawn by the app's
// own renderer and stylesheet, with made-up rows and numbers from the page itself — it asks the server for
// nothing. When it has drawn, window.__cexaDone tells the headless browser to take the picture.
import { renderLayout, applyTheme } from './views/tool.js';
import { esc } from './ui.js';

const d = JSON.parse(document.getElementById('cexa-data').textContent || '{}');
const S = (k) => (k && d.strings && d.strings[k]) || k || '';
const root = document.getElementById('root');
root.classList.add('eng');
applyTheme(root, d.theme || {});
const tabs = (d.tabs || []).length > 1 ? `<div class="eng-tabs">${d.tabs.map((t) => `<button class="${t.name === d.page ? 'on' : ''}">${esc(t.title)}</button>`).join('')}</div>` : '';
root.innerHTML = `<div class="head"><div><h1>${esc(d.tool)}${(d.tabs || []).length > 1 ? ` <span class="muted" style="font-weight:400">· ${esc(d.title)}</span>` : ''}</h1>${d.intro ? `<p>${esc(d.intro)}</p>` : ''}</div><div class="row" style="gap:8px;align-items:center">${tabs}</div></div><div id="lay" class="eng-page w-${esc(d.width || 'normal')}"></div>`;
const env = {
  slug: 'preview', page: d.page, S, strings: d.strings || {}, me: { id: 0 }, info: d.info || {},
  go: () => {}, block: d.info || {},
  rows: async (w) => (d.rows || {})[w.id] || { columns: [], rows: [], total: 0 },
  assetUrl: () => '', codeUrl: () => 'about:blank', write: null,
  metrics: async (keys) => ({ values: Object.fromEntries(keys.map((k) => [k, (d.metrics || {})[k] || { value: 0, label: k }])) }),
};
renderLayout(root.querySelector('#lay'), d.layout || { sections: [] }, env);
setTimeout(() => { window.__cexaDone = true; document.body.dataset.done = '1'; }, 700);
