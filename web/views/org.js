// Org chart & access (admin/owner). One canvas, two kinds of line, and they mean different things:
//
//   * a REPORTING LINE runs down the page, manager → report. It is structure. It grants the
//     ordinary kinds of work upward on its own (see cbx/org/access.py rule 4).
//   * a GRANT WIRE runs across the page, one person's right-hand pin to another's left-hand pin.
//     It is permission, drawn on purpose by a person, and it is the ONLY thing that grants a
//     kind marked sensitive.
//
// The two are never drawn alike — different axis, different anchor, different weight, different
// colour — because this is the page where someone decides who sees what, and a picture that is
// pretty but ambiguous is worse here than no picture at all.
//
// ~1 900 people: nothing is fetched or drawn for the whole company. We load the roots, then two
// levels at a time, and only ever draw the nodes that are currently expanded.
import { api } from '../api.js';
import { esc, toast, pill, modal, fmtDate } from '../ui.js';

// ---- geometry -------------------------------------------------------------------------------
const NW = 244;            // node width
const HEAD_H = 46;         // header band inside a node
const PIN_TOP = 64;        // y of the first pin row
const PIN_GAP = 18;        // between pin rows
const FOOT_H = 24;         // footer band
const LEVEL_GAP = 96;      // vertical space between one level of the tree and the next
const SIB_GAP = 30;        // between siblings
const TREE_GAP = 72;       // between two separate root trees
const MIN_K = 0.3, MAX_K = 2.2;

// Each kind of access gets one colour, used for its pins and for every wire that carries it.
// All four come from the app palette; the sensitive one is the red so that "be careful who you
// hand this to" reads before anyone has read the legend.
const KIND_COLOUR = { folders: '#2F5D8A', pdf: '#0B8A63', compliance: '#965D0F', todo: '#B3403A' };
const FALLBACK_COLOUR = ['#2F5D8A', '#0B8A63', '#965D0F', '#B3403A', '#5B4B9E', '#6B7683'];

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const cap = (s) => String(s || '').charAt(0).toUpperCase() + String(s || '').slice(1);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + 's'}`;

export default {
  title: 'Org chart & access',
  async render(root, ctx) {
    if (ctx.me.role !== 'owner' && ctx.me.role !== 'admin') {
      root.innerHTML = '<div class="head"><div><h1>Not for you</h1><p>The org chart and who-sees-what is an owner and admin page.</p></div></div>';
      return;
    }

    // ---- state ------------------------------------------------------------------------------
    const S = {
      nodes: new Map(),          // id → node as the API returned it
      grants: new Map(),         // grant id → grant
      kinds: [],                 // [{key,label,sensitive,hint}]
      rootIds: [],               // the trees currently on the canvas
      expanded: new Set(),       // nodes whose children are drawn
      loaded: new Set(),         // nodes whose direct reports we have actually fetched
      manual: new Map(),         // id → {x,y} a person dragged this node here
      pos: new Map(),            // id → {x,y} where it is drawn right now
      canEdit: false,
      sel: null,                 // {kind:'node', id} | {kind:'grant', id} | {kind:'report', from, to}
      teams: null,               // lazily fetched
    };
    const V = { x: 0, y: 0, k: 0.85 };
    let disposed = false;

    const kindOf = (key) => S.kinds.find((k) => k.key === key) || { key, label: key, sensitive: false, hint: '' };
    const kindColour = (key) => KIND_COLOUR[key] || FALLBACK_COLOUR[Math.max(0, S.kinds.findIndex((k) => k.key === key)) % FALLBACK_COLOUR.length];
    const nodeH = () => PIN_TOP + Math.max(0, S.kinds.length - 1) * PIN_GAP + 14 + FOOT_H;
    const pinY = (key) => PIN_TOP + Math.max(0, S.kinds.findIndex((k) => k.key === key)) * PIN_GAP;
    const levelH = () => nodeH() + LEVEL_GAP;

    // ---- shell ------------------------------------------------------------------------------
    root.innerHTML = `
      <div class="head">
        <div><h1>Org chart &amp; access</h1>
          <p>Down the page is the reporting line — that is structure. Across the page is a grant — that is permission, drawn on purpose. Changing one never changes the other.</p></div>
        <div class="row" style="gap:8px">
          <button class="btn sm" id="og-tidy" title="Put every node back where the automatic layout wants it">Re-tidy</button>
          <button class="btn sm" id="og-fit">Fit to view</button>
        </div>
      </div>
      <div class="org-wrap">
        <div class="org-stage" id="og-stage">
          <div class="org-bar">
            <div class="org-find">
              <input type="text" id="og-q" placeholder="Find a person…" autocomplete="off" spellcheck="false" aria-label="Find a person">
              <div class="org-hits" id="og-hits" hidden></div>
            </div>
            <span class="org-count" id="og-count"></span>
            <span class="sp" style="flex:1"></span>
            <div class="org-zoom">
              <button id="og-out" title="Zoom out" aria-label="Zoom out">−</button>
              <span id="og-pct">85%</span>
              <button id="og-in" title="Zoom in" aria-label="Zoom in">+</button>
            </div>
          </div>
          <div class="org-canvas" id="og-canvas"></div>
          <div class="org-legend" id="og-legend"></div>
        </div>
        <aside class="org-side card" id="og-side"></aside>
      </div>`;

    const canvas = root.querySelector('#og-canvas');
    const side = root.querySelector('#og-side');
    const qInput = root.querySelector('#og-q');
    const hits = root.querySelector('#og-hits');

    // ---- loading ----------------------------------------------------------------------------
    // Merge one graph payload. `depth` is what we asked for: every node nearer the root than that
    // came back with its children, so we can mark it loaded and never ask again.
    function absorb(g, askedDepth) {
      for (const n of g.nodes || []) S.nodes.set(n.id, { ...(S.nodes.get(n.id) || {}), ...n });
      for (const gr of g.grants || []) S.grants.set(gr.id, gr);
      if (Array.isArray(g.kinds) && g.kinds.length) S.kinds = g.kinds;
      if (typeof g.can_edit === 'boolean') S.canEdit = g.can_edit;
      for (const n of g.nodes || []) {
        if (n.x || n.y) S.manual.set(n.id, { x: n.x, y: n.y });
        if (n.collapsed) S.expanded.delete(n.id);
      }
      // breadth-first depth from the root of THIS payload
      const rootId = g.root_id;
      if (rootId == null) return;
      const d = new Map([[rootId, 0]]);
      let queue = [rootId];
      while (queue.length) {
        const id = queue.shift();
        const dd = d.get(id);
        if (dd >= askedDepth) continue;
        for (const c of [...S.nodes.values()].filter((n) => n.reports_to_id === id)) {
          if (d.has(c.id)) continue;
          d.set(c.id, dd + 1); queue.push(c.id);
        }
        S.loaded.add(id);
      }
      S.loaded.add(rootId);
    }

    async function loadGraph(rootRef, depth = 2) {
      const g = await api.get(`/api/org/graph?root=${encodeURIComponent(rootRef)}&depth=${depth}`);
      absorb(g, depth);
      return g;
    }

    // Everything we know about this person's direct reports, in a stable order.
    const childrenOf = (id) => [...S.nodes.values()].filter((n) => n.reports_to_id === id).sort((a, b) => a.name.localeCompare(b.name));
    const reportCount = (id) => {
      const n = S.nodes.get(id);
      const dr = n && n.direct_reports;
      if (Array.isArray(dr)) return dr.length;
      if (typeof dr === 'number') return dr;
      return childrenOf(id).length;
    };
    const visibleChildren = (id) => (S.expanded.has(id) ? childrenOf(id) : []);

    function visibleIds() {
      const out = [];
      const walk = (id, guard) => {
        if (guard.has(id)) return; guard.add(id);
        out.push(id);
        for (const c of visibleChildren(id)) walk(c.id, guard);
      };
      const guard = new Set();
      for (const r of S.rootIds) if (S.nodes.has(r)) walk(r, guard);
      return out;
    }

    async function expand(id) {
      if (!S.loaded.has(id) && reportCount(id) > 0) {
        try { await loadGraph(id, 2); } catch (e) { toast(e.detail || 'Could not load their reports', 'err'); return; }
      }
      S.expanded.add(id);
      queueSave(id);
    }

    // ---- layout -----------------------------------------------------------------------------
    // A tidy layered tree: depth sets y, a left-to-right sweep over the leaves sets x, and a
    // parent sits over the middle of its children. A node someone has dragged keeps its own
    // position but still reserves its slot, so the rest of the tree does not jump around it.
    function computeLayout() {
      const pos = new Map();
      const LH = levelH();
      let cursor = 0;
      let deepest = 0;
      const place = (id, depth, guard) => {
        if (guard.has(id)) return cursor;
        guard.add(id);
        deepest = Math.max(deepest, depth);
        const kids = visibleChildren(id);
        let cx;
        if (!kids.length) { cx = cursor + NW / 2; cursor += NW + SIB_GAP; }
        else {
          const xs = kids.map((c) => place(c.id, depth + 1, guard));
          cx = (xs[0] + xs[xs.length - 1]) / 2;
        }
        const man = S.manual.get(id);
        pos.set(id, man ? { x: man.x, y: man.y } : { x: cx - NW / 2, y: depth * LH });
        return cx;
      };
      const guard = new Set();
      // 1.0.83: separate trees (people who report to nobody yet) wrap onto rows, so the whole chart
      // fits a screen instead of one long line that runs off both sides
      const rowLimit = Math.max(4 * (NW + SIB_GAP), ((canvas && canvas.getBoundingClientRect().width) || 900) * 1.4);
      let rowY = 0, rowDeep = 0;
      for (const r of S.rootIds) {
        if (!S.nodes.has(r)) continue;
        const start = cursor, before = new Set(pos.keys());
        deepest = 0;
        place(r, 0, guard);
        if (start > 0 && cursor - SIB_GAP > rowLimit) {
          rowY += (rowDeep + 1) * LH + TREE_GAP / 2; rowDeep = 0;
          for (const [id, p] of pos) if (!before.has(id) && !S.manual.has(id)) pos.set(id, { x: p.x - start, y: p.y + rowY });
          cursor = cursor - start;
        } else if (rowY) {
          for (const [id, p] of pos) if (!before.has(id) && !S.manual.has(id)) pos.set(id, { x: p.x, y: p.y + rowY });
        }
        rowDeep = Math.max(rowDeep, deepest);
        cursor += TREE_GAP;
      }
      S.pos = pos;
    }

    // ---- saving the layout ------------------------------------------------------------------
    // A node that has never been dragged is saved at 0,0 — that is the sentinel for "let the
    // automatic layout decide", which is also what an untouched OrgLayout row looks like.
    const pending = new Map();
    let saveTimer = null;
    function queueSave(id) {
      const m = S.manual.get(id) || { x: 0, y: 0 };
      pending.set(id, { user_id: id, x: Math.round(m.x), y: Math.round(m.y), collapsed: !S.expanded.has(id) });
      clearTimeout(saveTimer);
      saveTimer = setTimeout(flushSave, 700);
    }
    async function flushSave() {
      clearTimeout(saveTimer); saveTimer = null;
      if (!pending.size) return;
      const positions = [...pending.values()];
      pending.clear();
      try { await api.put('/api/org/layout', { positions }); }
      catch (e) { if (!disposed) toast(e.detail || 'Could not save the layout', 'err'); }
    }

    // ---- drawing ----------------------------------------------------------------------------
    const cx = (p) => p.x + NW / 2;

    function reportPath(a, b) {
      const H = nodeH();
      const x1 = cx(a), y1 = a.y + H, x2 = cx(b), y2 = b.y;
      const dy = Math.max(34, Math.abs(y2 - y1) * 0.45);
      return `M${x1} ${y1}C${x1} ${y1 + dy} ${x2} ${y2 - dy} ${x2} ${y2}`;
    }
    function grantPath(o, v, key) {
      const y = pinY(key);
      const x1 = o.x + NW, y1 = o.y + y, x2 = v.x, y2 = v.y + y;
      const d = Math.max(64, Math.abs(x2 - x1) * 0.45);
      return `M${x1} ${y1}C${x1 + d} ${y1} ${x2 - d} ${y2} ${x2} ${y2}`;
    }
    // Midpoint and heading of a cubic, for the little chevron that says which way a grant flows.
    function cubicMid(x1, y1, c1x, c1y, c2x, c2y, x2, y2) {
      const mx = (x1 + 3 * c1x + 3 * c2x + x2) / 8, my = (y1 + 3 * c1y + 3 * c2y + y2) / 8;
      const tx = 3 * (c1x - x1) * 0.25 + 6 * (c2x - c1x) * 0.25 + 3 * (x2 - c2x) * 0.25;
      const ty = 3 * (c1y - y1) * 0.25 + 6 * (c2y - c1y) * 0.25 + 3 * (y2 - c2y) * 0.25;
      return { x: mx, y: my, a: (Math.atan2(ty, tx) * 180) / Math.PI };
    }
    function grantChevron(o, v, key) {
      const y = pinY(key);
      const x1 = o.x + NW, y1 = o.y + y, x2 = v.x, y2 = v.y + y;
      const d = Math.max(64, Math.abs(x2 - x1) * 0.45);
      return cubicMid(x1, y1, x1 + d, y1, x2 - d, y2, x2, y2);
    }

    const grantsList = () => [...S.grants.values()];
    const pinHasWire = (id, key, side) => grantsList().some((g) => g.kind === key && (side === 'out' ? g.owner_id === id : g.viewer_id === id));

    function nodeSvg(n) {
      const p = S.pos.get(n.id);
      if (!p) return '';
      const H = nodeH();
      const sel = S.sel && S.sel.kind === 'node' && S.sel.id === n.id;
      const kids = reportCount(n.id);
      const open = S.expanded.has(n.id);
      const sub = [n.title, n.team && n.team.name].filter(Boolean).join(' · ') || n.email || '';
      const pins = S.kinds.map((k) => {
        const y = pinY(k.key), col = kindColour(k.key);
        const tint = k.sensitive ? `<rect class="og-sens" x="12" y="${y - 8}" width="${NW - 24}" height="16" rx="4" fill="${col}" opacity=".07"></rect>` : '';
        const shape = (x, filled) => (k.sensitive
          ? `<path d="M${x} ${y - 5.4}L${x + 5.4} ${y}L${x} ${y + 5.4}L${x - 5.4} ${y}Z" fill="${filled ? col : 'var(--surface)'}" stroke="${col}" stroke-width="1.6"></path>`
          : `<circle cx="${x}" cy="${y}" r="4.6" fill="${filled ? col : 'var(--surface)'}" stroke="${col}" stroke-width="1.6"></circle>`);
        const tip = `${k.label}${k.sensitive ? ' — private. Only a wire grants this; being someone\'s manager is not enough.' : ''}${k.hint ? ' — ' + k.hint : ''}`;
        return `${tint}
          <text class="og-pin-lbl${k.sensitive ? ' sens' : ''}" x="${NW / 2}" y="${y + 3.6}" text-anchor="middle" ${k.sensitive ? `fill="${col}"` : ''}>${esc(k.label)}</text>
          <g class="og-pin" data-pin="in" data-kind="${esc(k.key)}" data-id="${n.id}"><title>${esc('In · ' + tip + ' Drop a wire here to let them see it.')}</title><circle cx="0" cy="${y}" r="10" fill="transparent"></circle>${shape(0, pinHasWire(n.id, k.key, 'in'))}</g>
          <g class="og-pin" data-pin="out" data-kind="${esc(k.key)}" data-id="${n.id}"><title>${esc('Out · ' + tip + ' Drag from here to someone\'s left pin.')}</title><circle cx="${NW}" cy="${y}" r="10" fill="transparent"></circle>${shape(NW, pinHasWire(n.id, k.key, 'out'))}</g>`;
      }).join('');
      const roleTxt = n.is_axl ? 'AI' : cap(n.role || '');
      const roleW = Math.max(30, roleTxt.length * 6.4 + 14);
      return `<g class="og-node${sel ? ' sel' : ''}${n.is_axl ? ' cexa' : ''}" data-node="${n.id}" transform="translate(${p.x} ${p.y})">
        <rect class="og-card" x="0" y="0" width="${NW}" height="${H}" rx="10"></rect>
        <rect class="og-head" x="0" y="0" width="${NW}" height="${HEAD_H}" rx="10"></rect>
        <rect class="og-head-sq" x="0" y="${HEAD_H - 10}" width="${NW}" height="10"></rect>
        <line class="og-rule" x1="0" y1="${HEAD_H}" x2="${NW}" y2="${HEAD_H}"></line>
        <circle class="og-av" cx="26" cy="23" r="14"></circle>
        <text class="og-av-t" x="26" y="27" text-anchor="middle">${esc(n.initials || '?')}</text>
        <text class="og-name" x="48" y="21" data-max="${NW - 60}">${esc(n.name)}</text>
        <text class="og-sub" x="48" y="36" data-max="${NW - 60}">${esc(sub)}</text>
        ${pins}
        <line class="og-rule" x1="0" y1="${H - FOOT_H}" x2="${NW}" y2="${H - FOOT_H}"></line>
        <g class="og-foot${kids ? ' act' : ''}" ${kids ? `data-toggle="${n.id}"` : ''}>
          <rect x="0" y="${H - FOOT_H}" width="${NW - roleW - 14}" height="${FOOT_H}" fill="transparent"></rect>
          ${kids ? `<path class="og-chev" d="M12 ${H - 15}l4 4 4-4" transform="${open ? `rotate(180 16 ${H - 13})` : ''}"></path>` : ''}
          <text class="og-foot-t" x="${kids ? 28 : 12}" y="${H - 8}">${kids ? `${open ? 'Hide' : 'Show'} ${plural(kids, 'report')}` : 'No reports'}</text>
        </g>
        <rect class="og-role" x="${NW - roleW - 10}" y="${H - FOOT_H + 4}" width="${roleW}" height="${FOOT_H - 9}" rx="7"></rect>
        <text class="og-role-t" x="${NW - roleW / 2 - 10}" y="${H - 9}" text-anchor="middle">${esc(roleTxt)}</text>
      </g>`;
    }

    function draw() {
      computeLayout();
      const ids = visibleIds();
      const vis = new Set(ids);
      const H = nodeH();

      // reporting lines: only between two nodes that are both on the canvas
      const reps = [];
      for (const id of ids) {
        const n = S.nodes.get(id);
        if (!n || n.reports_to_id == null || !vis.has(n.reports_to_id)) continue;
        reps.push({ from: n.reports_to_id, to: id });
      }
      const repSvg = reps.map((r) => {
        const a = S.pos.get(r.from), b = S.pos.get(r.to);
        const d = reportPath(a, b);
        const on = S.sel && S.sel.kind === 'report' && S.sel.from === r.from && S.sel.to === r.to;
        return `<g class="og-rep${on ? ' sel' : ''}" data-rep="${r.from}-${r.to}" data-from="${r.from}" data-to="${r.to}">
          <path class="hit" d="${d}"></path><path class="ln" d="${d}"></path>
          <circle class="og-rep-dot" cx="${cx(a)}" cy="${a.y + H}" r="2.6"></circle></g>`;
      }).join('');

      // grant wires: both ends on the canvas
      const wires = grantsList().filter((g) => vis.has(g.owner_id) && vis.has(g.viewer_id) && S.kinds.some((k) => k.key === g.kind));
      const wireSvg = wires.map((g) => {
        const o = S.pos.get(g.owner_id), v = S.pos.get(g.viewer_id);
        const d = grantPath(o, v, g.kind);
        const col = kindColour(g.kind);
        const k = kindOf(g.kind);
        const m = grantChevron(o, v, g.kind);
        const on = S.sel && S.sel.kind === 'grant' && S.sel.id === g.id;
        return `<g class="og-wire${on ? ' sel' : ''}${k.sensitive ? ' sens' : ''}" data-wire="${g.id}">
          <path class="hit" d="${d}"></path>
          <path class="case" d="${d}"></path>
          <path class="ln" d="${d}" stroke="${col}"></path>
          <path class="chev" d="M-3.4 -3.6L3.4 0L-3.4 3.6Z" fill="${col}" transform="translate(${m.x.toFixed(1)} ${m.y.toFixed(1)}) rotate(${m.a.toFixed(1)})"></path>
        </g>`;
      }).join('');

      const nodesSvg = ids.map((id) => nodeSvg(S.nodes.get(id))).join('');

      canvas.innerHTML = `<svg class="og-svg" id="og-svg" xmlns="http://www.w3.org/2000/svg">
        <defs><pattern id="og-dots" width="22" height="22" patternUnits="userSpaceOnUse" patternTransform="translate(${V.x} ${V.y}) scale(${V.k})">
          <circle cx="1.2" cy="1.2" r="1.2" fill="var(--line)"></circle></pattern></defs>
        <rect class="og-bg" x="0" y="0" width="100%" height="100%" fill="url(#og-dots)"></rect>
        <g id="og-view" transform="translate(${V.x} ${V.y}) scale(${V.k})">
          <g id="og-l-rep">${repSvg}</g>
          <g id="og-l-wire">${wireSvg}</g>
          <g id="og-l-node">${nodesSvg}</g>
          <g id="og-l-live"></g>
        </g>
      </svg>`;
      fitText(canvas);
      root.querySelector('#og-count').textContent = `${plural(ids.length, 'person', 'people')} shown · ${plural(wires.length, 'grant')}${extraRoots ? ` · ${extraRoots} more top-level ${extraRoots === 1 ? 'person' : 'people'} — search for them` : ''}`;
      root.querySelector('#og-pct').textContent = Math.round(V.k * 100) + '%';
      wireCanvas();
      drawLegend();
    }

    // SVG has no text-overflow, so a name that will not fit is trimmed to the width it has.
    function fitText(scope) {
      scope.querySelectorAll('text[data-max]').forEach((t) => {
        const max = Number(t.dataset.max);
        const full = t.textContent;
        if (!max || !full) return;
        let len = 0;
        try { len = t.getComputedTextLength(); } catch { return; }
        if (len <= max) return;
        let lo = 0, hi = full.length;
        while (lo < hi) {
          const mid = (lo + hi + 1) >> 1;
          t.textContent = full.slice(0, mid) + '…';
          if (t.getComputedTextLength() <= max) lo = mid; else hi = mid - 1;
        }
        t.textContent = full.slice(0, lo).trimEnd() + '…';
        const tip = document.createElementNS('http://www.w3.org/2000/svg', 'title');
        tip.textContent = full;
        t.append(tip);
      });
    }

    function applyView() {
      const g = canvas.querySelector('#og-view');
      const pat = canvas.querySelector('#og-dots');
      if (g) g.setAttribute('transform', `translate(${V.x} ${V.y}) scale(${V.k})`);
      if (pat) pat.setAttribute('patternTransform', `translate(${V.x} ${V.y}) scale(${V.k})`);
      const pct = root.querySelector('#og-pct');
      if (pct) pct.textContent = Math.round(V.k * 100) + '%';
    }

    function bbox() {
      const ids = visibleIds();
      if (!ids.length) return null;
      const H = nodeH();
      let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;
      for (const id of ids) {
        const p = S.pos.get(id); if (!p) continue;
        x1 = Math.min(x1, p.x); y1 = Math.min(y1, p.y); x2 = Math.max(x2, p.x + NW); y2 = Math.max(y2, p.y + H);
      }
      return Number.isFinite(x1) ? { x1, y1, x2, y2 } : null;
    }
    // The first view stops zooming out at 70%: below that the names stop being readable and the
    // chart becomes a diagram of itself, so we centre on the person the page opened on instead.
    // "Fit to view" is someone asking for the whole thing, so that one goes all the way down.
    const OPEN_FLOOR = 0.6;
    function fit(focusId, floor = MIN_K) {
      const b = bbox(); if (!b) return;
      const r = canvas.getBoundingClientRect();
      const pad = 48;
      const raw = Math.min((r.width - pad * 2) / (b.x2 - b.x1 || 1), (r.height - pad * 2) / (b.y2 - b.y1 || 1));
      const k = clamp(raw, floor, 1.05);
      V.k = k;
      if (raw < floor && focusId != null && S.pos.has(focusId)) return centreOn(focusId);
      V.x = r.width / 2 - ((b.x1 + b.x2) / 2) * k;
      V.y = r.height / 2 - ((b.y1 + b.y2) / 2) * k;
      applyView();
    }
    function centreOn(id, k) {
      const p = S.pos.get(id); if (!p) return;
      const r = canvas.getBoundingClientRect();
      if (k) V.k = clamp(k, MIN_K, MAX_K);
      V.x = r.width / 2 - (p.x + NW / 2) * V.k;
      V.y = r.height / 2 - (p.y + nodeH() / 2) * V.k;
      applyView();
    }
    function zoomAt(px, py, factor) {
      const nk = clamp(V.k * factor, MIN_K, MAX_K);
      if (nk === V.k) return;
      V.x = px - (px - V.x) * (nk / V.k);
      V.y = py - (py - V.y) * (nk / V.k);
      V.k = nk;
      applyView();
    }

    // ---- canvas interaction -----------------------------------------------------------------
    // One pointerdown handler decides what a drag is: a pin drag makes a wire, a node drag moves
    // a node, anything else pans. A drag under 4px is treated as a click so selecting is easy.
    function wireCanvas() {
      const svg = canvas.querySelector('#og-svg');
      if (!svg) return;
      const toWorld = (e) => {
        const r = svg.getBoundingClientRect();
        return { x: (e.clientX - r.left - V.x) / V.k, y: (e.clientY - r.top - V.y) / V.k };
      };
      svg.addEventListener('wheel', (e) => { e.preventDefault(); const r = svg.getBoundingClientRect(); zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * 0.0016)); }, { passive: false });

      svg.addEventListener('pointerdown', (e) => {
        if (e.button !== 0) return;
        const pinEl = e.target.closest('[data-pin]');
        const foot = e.target.closest('[data-toggle]');
        const nodeEl = e.target.closest('[data-node]');
        const wireEl = e.target.closest('[data-wire]');
        const repEl = e.target.closest('[data-rep]');
        closeHits();

        if (pinEl && S.canEdit) return startLink(e, pinEl, svg, toWorld);
        if (pinEl && !S.canEdit) { toast('You can look, but only an owner or admin can draw a wire here.', 'err'); return; }
        if (foot) { e.preventDefault(); return toggleNode(Number(foot.dataset.toggle)); }
        if (nodeEl) return startNodeDrag(e, nodeEl, svg, toWorld);
        if (wireEl) { e.preventDefault(); return selectGrant(Number(wireEl.dataset.wire)); }
        if (repEl) { e.preventDefault(); return selectReport(Number(repEl.dataset.from), Number(repEl.dataset.to)); }
        return startPan(e, svg);
      });
    }

    function startPan(e, svg) {
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      svg.classList.add('panning');
      const sx = e.clientX, sy = e.clientY, ox = V.x, oy = V.y;
      let moved = false;
      const move = (ev) => { V.x = ox + (ev.clientX - sx); V.y = oy + (ev.clientY - sy); if (Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) > 4) moved = true; applyView(); };
      const up = () => {
        svg.removeEventListener('pointermove', move); svg.removeEventListener('pointerup', up); svg.removeEventListener('pointercancel', up);
        svg.classList.remove('panning');
        if (!moved) { S.sel = null; draw(); drawSide(); }
      };
      svg.addEventListener('pointermove', move); svg.addEventListener('pointerup', up); svg.addEventListener('pointercancel', up);
    }

    function startNodeDrag(e, nodeEl, svg, toWorld) {
      e.preventDefault();
      const id = Number(nodeEl.dataset.node);
      svg.setPointerCapture(e.pointerId);
      nodeEl.parentNode.append(nodeEl); // the node you are holding belongs on top of the others
      const p0 = S.pos.get(id) || { x: 0, y: 0 };
      const w0 = toWorld(e);
      const ox = p0.x, oy = p0.y;
      let moved = false;
      const move = (ev) => {
        const w = toWorld(ev);
        const dx = w.x - w0.x, dy = w.y - w0.y;
        if (!moved && Math.abs(dx) * V.k + Math.abs(dy) * V.k < 4) return;
        moved = true;
        if (!S.canEdit) return;
        S.manual.set(id, { x: ox + dx, y: oy + dy });
        S.pos.set(id, { x: ox + dx, y: oy + dy });
        nodeEl.setAttribute('transform', `translate(${ox + dx} ${oy + dy})`);
        redrawLinesFor(id);
      };
      const up = () => {
        svg.removeEventListener('pointermove', move); svg.removeEventListener('pointerup', up); svg.removeEventListener('pointercancel', up);
        if (!moved) { selectNode(id); return; }
        if (!S.canEdit) { toast('Only an owner or admin can move nodes here.', 'err'); return; }
        queueSave(id);
      };
      svg.addEventListener('pointermove', move); svg.addEventListener('pointerup', up); svg.addEventListener('pointercancel', up);
    }

    // Moving one node only changes the lines touching it, so a drag stays smooth on a big tree.
    function redrawLinesFor(id) {
      const H = nodeH();
      canvas.querySelectorAll('[data-rep]').forEach((g) => {
        const f = Number(g.dataset.from), t = Number(g.dataset.to);
        if (f !== id && t !== id) return;
        const a = S.pos.get(f), b = S.pos.get(t); if (!a || !b) return;
        const d = reportPath(a, b);
        g.querySelectorAll('path').forEach((p) => p.setAttribute('d', d));
        const dot = g.querySelector('circle'); if (dot) { dot.setAttribute('cx', cx(a)); dot.setAttribute('cy', a.y + H); }
      });
      canvas.querySelectorAll('[data-wire]').forEach((g) => {
        const gr = S.grants.get(Number(g.dataset.wire)); if (!gr) return;
        if (gr.owner_id !== id && gr.viewer_id !== id) return;
        const o = S.pos.get(gr.owner_id), v = S.pos.get(gr.viewer_id); if (!o || !v) return;
        const d = grantPath(o, v, gr.kind);
        g.querySelectorAll('path:not(.chev)').forEach((p) => p.setAttribute('d', d));
        const m = grantChevron(o, v, gr.kind);
        const ch = g.querySelector('.chev'); if (ch) ch.setAttribute('transform', `translate(${m.x.toFixed(1)} ${m.y.toFixed(1)}) rotate(${m.a.toFixed(1)})`);
      });
    }

    // ---- drawing a grant --------------------------------------------------------------------
    function startLink(e, pinEl, svg, toWorld) {
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      const from = { id: Number(pinEl.dataset.id), kind: pinEl.dataset.kind, side: pinEl.dataset.pin };
      const col = kindColour(from.kind);
      const k = kindOf(from.kind);
      const live = canvas.querySelector('#og-l-live');
      // dim every pin this one cannot legally reach — the shape of the answer before the attempt
      canvas.querySelectorAll('[data-pin]').forEach((p) => {
        const ok = p.dataset.kind === from.kind && p.dataset.pin !== from.side && Number(p.dataset.id) !== from.id;
        p.classList.add(ok ? 'ok' : 'no');
      });
      svg.classList.add('linking');
      const anchor = () => {
        const p = S.pos.get(from.id);
        return { x: p.x + (from.side === 'out' ? NW : 0), y: p.y + pinY(from.kind) };
      };
      const move = (ev) => {
        const a = anchor(), w = toWorld(ev);
        const d = Math.max(50, Math.abs(w.x - a.x) * 0.45) * (from.side === 'out' ? 1 : -1);
        live.innerHTML = `<path class="og-ghost${k.sensitive ? ' sens' : ''}" stroke="${col}" d="M${a.x} ${a.y}C${a.x + d} ${a.y} ${w.x - d} ${w.y} ${w.x} ${w.y}"></path>
          <circle class="og-ghost-end" cx="${w.x}" cy="${w.y}" r="4" fill="${col}"></circle>`;
      };
      const up = (ev) => {
        svg.removeEventListener('pointermove', move); svg.removeEventListener('pointerup', up); svg.removeEventListener('pointercancel', up);
        svg.classList.remove('linking');
        canvas.querySelectorAll('[data-pin]').forEach((p) => p.classList.remove('ok', 'no'));
        live.innerHTML = '';
        const drop = document.elementFromPoint(ev.clientX, ev.clientY);
        const target = drop && drop.closest ? drop.closest('[data-pin]') : null;
        if (!target) return;
        finishLink(from, { id: Number(target.dataset.id), kind: target.dataset.kind, side: target.dataset.pin });
      };
      svg.addEventListener('pointermove', move); svg.addEventListener('pointerup', up); svg.addEventListener('pointercancel', up);
      move(e);
    }

    async function finishLink(a, b) {
      if (a.id === b.id) return toast('That is their own work — they can already see it.', 'err');
      if (a.kind !== b.kind) {
        return toast(`A ${kindOf(a.kind).label} pin only joins another ${kindOf(a.kind).label} pin. ${kindOf(b.kind).label} is a different kind of access — drop it on the matching pin instead.`, 'err');
      }
      if (a.side === b.side) {
        return toast(a.side === 'out'
          ? 'Both of those are out pins. A wire runs from the right-hand pin of the person whose work it is to the left-hand pin of the person who may see it.'
          : 'Both of those are in pins. Start the wire on the right-hand pin of the person whose work it is.', 'err');
      }
      const ownerId = a.side === 'out' ? a.id : b.id;
      const viewerId = a.side === 'out' ? b.id : a.id;
      const k = kindOf(a.kind);
      const owner = S.nodes.get(ownerId), viewer = S.nodes.get(viewerId);
      if (grantsList().some((g) => g.owner_id === ownerId && g.viewer_id === viewerId && g.kind === a.kind && !g.resource_id)) {
        return toast(`${viewer.name} already has a wire for ${k.label} from ${owner.name}.`, 'err');
      }
      try {
        const r = await api.post('/api/org/grants', { owner_id: ownerId, viewer_id: viewerId, kind: a.kind });
        if (r && r.grant) S.grants.set(r.grant.id, r.grant);
        toast(`${viewer.name} can now see ${owner.name}'s ${k.label.toLowerCase()}.`);
        personCache.delete(ownerId); personCache.delete(viewerId); // their grant lists just changed
        if (r && r.grant) await selectGrant(r.grant.id); // selecting it also asks WHY, so the new wire explains itself
        else { S.sel = null; draw(); drawSide(); }
      } catch (e) { toast(e.detail || 'Could not draw that wire', 'err'); }
    }

    async function toggleNode(id) {
      if (S.expanded.has(id)) { S.expanded.delete(id); queueSave(id); }
      else await expand(id);
      draw();
    }

    // ---- selection --------------------------------------------------------------------------
    const personCache = new Map();
    // A person's grants name the other end by id only, and that person is often not on the canvas.
    // "Someone off the canvas" is honest but useless on a page about who sees what, so the few
    // names a panel is missing are fetched one by one and the panel redrawn.
    const hydrating = new Set();
    async function hydrateNames(ids, forId) {
      const todo = ids.filter((i) => !hydrating.has(i)).slice(0, 12);
      if (!todo.length) return;
      todo.forEach((i) => hydrating.add(i));
      await Promise.all(todo.map(async (i) => {
        try {
          const d = await api.get(`/api/org/people/${i}`);
          personCache.set(i, d);
          if (d.person) S.nodes.set(i, { ...(S.nodes.get(i) || {}), ...d.person });
        } catch { /* they may sit outside what this person is allowed to open */ }
      }));
      if (S.sel && S.sel.kind === 'node' && S.sel.id === forId) drawSide();
    }

    async function selectNode(id) {
      S.sel = { kind: 'node', id };
      draw(); drawSide();
      if (!personCache.has(id)) {
        try { personCache.set(id, await api.get(`/api/org/people/${id}`)); }
        catch (e) { toast(e.detail || 'Could not load that person', 'err'); return; }
      }
      if (S.sel && S.sel.kind === 'node' && S.sel.id === id) drawSide();
    }
    async function selectGrant(id) {
      S.sel = { kind: 'grant', id, why: null };
      draw(); drawSide();
      const g = S.grants.get(id); if (!g) return;
      try {
        const r = await api.get(`/api/org/explain?viewer_id=${g.viewer_id}&owner_id=${g.owner_id}&kind=${encodeURIComponent(g.kind)}`);
        if (S.sel && S.sel.kind === 'grant' && S.sel.id === id) { S.sel.why = r; drawSide(); }
      } catch (e) { toast(e.detail || 'Could not explain that wire', 'err'); }
    }
    async function selectReport(from, to) {
      S.sel = { kind: 'report', from, to, rows: null };
      draw(); drawSide();
      try {
        const rows = await Promise.all(S.kinds.map(async (k) => {
          const r = await api.get(`/api/org/explain?viewer_id=${from}&owner_id=${to}&kind=${encodeURIComponent(k.key)}`);
          return { kind: k, ...r };
        }));
        if (S.sel && S.sel.kind === 'report' && S.sel.from === from && S.sel.to === to) { S.sel.rows = rows; drawSide(); }
      } catch (e) { toast(e.detail || 'Could not explain that line', 'err'); }
    }

    // ---- the details panel ------------------------------------------------------------------
    const who = (id) => S.nodes.get(id) || { name: 'Someone off the canvas', initials: '?' };
    const kindDot = (key) => {
      const k = kindOf(key), c = kindColour(key);
      return `<span class="og-dot${k.sensitive ? ' sens' : ''}" style="--c:${c}"></span>`;
    };
    const kindTag = (key) => `<span class="og-tag">${kindDot(key)}${esc(kindOf(key).label)}${kindOf(key).sensitive ? '<i>private</i>' : ''}</span>`;
    const personRow = (p, note) => `<button class="og-person" data-go="${p.id}"><span class="avatar">${esc(p.initials || '?')}</span><span class="t"><b>${esc(p.name)}</b><span>${esc(note || p.title || p.email || '')}</span></span></button>`;

    function drawSide() {
      if (!S.sel) return drawSideEmpty();
      if (S.sel.kind === 'node') return drawSideNode();
      if (S.sel.kind === 'grant') return drawSideGrant();
      if (S.sel.kind === 'report') return drawSideReport();
    }

    function drawSideEmpty() {
      side.innerHTML = `
        <div class="hd"><h2>Nothing selected</h2></div>
        <div class="bd og-note">
          <p><b>Click a person</b> to see who they report to, who reports to them, and every grant in and out.</p>
          <p><b>Click a line</b> to be told why that access exists — and to take it away.</p>
          <p><b>Drag from a right-hand pin</b> to someone's matching left-hand pin to grant them that kind of access.</p>
        </div>
        <div class="bd og-rules">
          <div class="og-rule-row"><span class="og-swatch rep"></span><div><b>Reporting line</b><span>Structure. Runs down the page. On its own it lets a manager — and their manager — see the ordinary kinds of work below them.</span></div></div>
          <div class="og-rule-row"><span class="og-swatch grant"></span><div><b>Grant wire</b><span>Permission. Runs across the page, in the colour of the kind it carries. Someone drew it on purpose, and it is the only thing that grants a private kind.</span></div></div>
          ${S.kinds.some((k) => k.sensitive) ? `<div class="og-warn"><b>${S.kinds.filter((k) => k.sensitive).map((k) => k.label).join(', ')} is private.</b> The reporting line does not grant it. Do not assume a manager already has it — if they need it, the wire has to be drawn.</div>` : ''}
        </div>`;
      wireSide();
    }

    function drawSideNode() {
      const id = S.sel.id;
      const n = S.nodes.get(id) || {};
      const d = personCache.get(id);
      const outs = grantsList().filter((g) => g.owner_id === id);
      const ins = grantsList().filter((g) => g.viewer_id === id);
      // `manager` comes back as {id,name}; the canvas usually knows more about them than that
      let mgr = d ? d.manager : (n.reports_to_id != null ? S.nodes.get(n.reports_to_id) : null);
      if (mgr && S.nodes.has(mgr.id)) mgr = { ...S.nodes.get(mgr.id), ...mgr };
      const reports = d && Array.isArray(d.reports) ? d.reports : childrenOf(id);
      const tools = ((d && d.tools) || []).filter((t) => t.granted !== false);
      const canEdit = S.canEdit && (!d || d.can_edit !== false) && !n.is_axl;
      const sens = S.kinds.filter((k) => k.sensitive);
      const p = (d && d.person) || n;

      side.innerHTML = `
        <div class="hd og-who">
          <span class="avatar${n.is_axl ? ' ai' : ''}">${esc(p.initials || n.initials || '?')}</span>
          <div class="t"><b>${esc(p.name || n.name || '')}</b><span>${esc(p.title || n.title || '—')}${p.team || n.team ? ' · ' + esc((p.team && p.team.name) || (n.team && n.team.name) || p.team || '') : ''}</span></div>
          ${n.is_axl ? pill('AI', 'ai') : pill(cap(p.role || n.role || ''), (p.role || n.role) === 'manager' ? 'manager' : 'neutral')}
        </div>
        <div class="bd og-meta">
          <div><span class="eyebrow">Email</span><b class="mono">${esc(p.email || n.email || '—')}</b></div>
          <div><span class="eyebrow">Reports to</span>${mgr ? personRow(mgr, 'their manager') : '<b class="og-top">Top of the tree</b>'}</div>
        </div>
        ${canEdit ? `<div class="bd og-edit">
          <div class="field"><label for="og-title">Job title</label><input type="text" id="og-title" value="${esc(p.title || '')}" maxlength="120"></div>
          <div class="row" style="gap:10px;align-items:flex-end">
            <div class="field" style="flex:1"><label for="og-role">Role</label><select id="og-role">${['user', 'manager', 'admin'].concat(ctx.me.role === 'owner' ? ['owner'] : []).map((r) => `<option value="${r}" ${(p.role || n.role) === r ? 'selected' : ''}>${cap(r)}</option>`).join('')}</select></div>
            <div class="field" style="flex:1"><label for="og-team">Team</label><select id="og-team"><option value="">—</option></select></div>
          </div>
          <div class="row" style="gap:8px"><button class="btn sm" id="og-remgr">Change manager</button><button class="btn sm primary" id="og-save">Save</button></div>
        </div>` : ''}
        <div class="bd og-sec">
          <div class="eyebrow">Direct reports · ${reports.length}</div>
          ${reports.length ? `<div class="og-people">${reports.slice(0, 40).map((r) => personRow(r)).join('')}</div>${reports.length > 40 ? `<p class="small muted">…and ${reports.length - 40} more. Expand them on the canvas.</p>` : ''}` : '<p class="small muted">Nobody reports to them.</p>'}
        </div>
        <div class="bd og-sec">
          <div class="eyebrow">Who can see their work · ${outs.length}</div>
          <p class="small muted og-lead">Wires out of their right-hand pins. These were drawn by a person.</p>
          ${outs.length ? `<ul class="list og-grants">${outs.map((g) => grantRow(g, 'viewer')).join('')}</ul>` : '<p class="small muted">No wires out.</p>'}
          ${mgr ? `<p class="small muted og-lead">Separately, ${esc(mgr.name)} and anyone above them can see the kinds that are not private, because of the reporting line — no wire needed.${sens.length ? ` Not ${esc(sens.map((k) => k.label).join(', '))}.` : ''}</p>` : ''}
        </div>
        <div class="bd og-sec">
          <div class="eyebrow">Whose work they can see · ${ins.length}</div>
          <p class="small muted og-lead">Wires into their left-hand pins.</p>
          ${ins.length ? `<ul class="list og-grants">${ins.map((g) => grantRow(g, 'owner')).join('')}</ul>` : '<p class="small muted">No wires in.</p>'}
        </div>
        ${tools.length ? `<div class="bd og-sec"><div class="eyebrow">Tools</div><div class="og-chips">${tools.map((t) => `<span class="chip">${esc(t.name || t.label || t.slug || t)}</span>`).join('')}</div></div>` : ''}
        ${!d ? '<div class="bd small muted">Loading the rest…</div>' : ''}`;
      wireSide();
      if (canEdit) fillTeams(p);
      const missing = [...new Set([...outs.map((g) => g.viewer_id), ...ins.map((g) => g.owner_id)])].filter((x) => !S.nodes.has(x));
      if (missing.length) hydrateNames(missing, id);
    }

    function grantRow(g, showSide) {
      const other = who(showSide === 'viewer' ? g.viewer_id : g.owner_id);
      const by = g.granted_by && typeof g.granted_by === 'object' ? g.granted_by.name : g.granted_by;
      return `<li data-grant="${g.id}">
        <span class="avatar sm">${esc(other.initials || '?')}</span>
        <div class="t"><b>${esc(other.name)}</b><span>${esc(kindOf(g.kind).label)}${g.resource_id ? ' · just ' + esc(g.resource_id) : ''}${by ? ' · by ' + esc(by) : ''}${g.at ? ' · ' + fmtDate(g.at) : ''}${g.note ? ' · ' + esc(g.note) : ''}</span></div>
        ${kindDot(g.kind)}
        ${S.canEdit ? `<button class="btn sm ghost og-rm" data-rm="${g.id}" title="Remove this grant">Remove</button>` : ''}
      </li>`;
    }

    function drawSideGrant() {
      const g = S.grants.get(S.sel.id);
      if (!g) return drawSideEmpty();
      const k = kindOf(g.kind);
      const owner = who(g.owner_id), viewer = who(g.viewer_id);
      const why = S.sel.why;
      const by = g.granted_by && typeof g.granted_by === 'object' ? g.granted_by.name : g.granted_by;
      side.innerHTML = `
        <div class="hd"><h2>A grant wire</h2>${kindTag(g.kind)}</div>
        <div class="bd og-flow">
          ${personRow(owner, 'their work')}
          <div class="og-arrow" style="--c:${kindColour(g.kind)}"><span></span><b>${esc(k.label)}</b><span></span></div>
          ${personRow(viewer, 'may see it')}
        </div>
        <div class="bd og-sec">
          <div class="eyebrow">Why this access exists</div>
          ${why ? `<p class="og-why">${why.allowed === false ? '<b style="color:var(--crit)">Not allowed. </b>' : ''}${esc(why.why || '')}</p>` : '<p class="small muted"><span class="spin"></span> Asking the one place that decides…</p>'}
          <p class="small muted og-lead">This is a permission, not a reporting line. Removing it does not move anybody in the tree.</p>
          ${k.sensitive ? `<div class="og-warn"><b>${esc(k.label)} is private.</b> This wire is the only reason ${esc(viewer.name)} can see it. Nothing about the reporting line grants it.</div>` : ''}
        </div>
        <div class="bd og-sec">
          <div class="eyebrow">Details</div>
          <div class="og-meta">
            <div><span class="eyebrow">Covers</span><b>${g.resource_id ? esc(g.resource_id) + ' only' : 'Everything of this kind'}</b></div>
            <div><span class="eyebrow">Drawn by</span><b>${esc(by || 'Not recorded')}${g.at ? ' · ' + fmtDate(g.at, { time: true }) : ''}</b></div>
            ${g.note ? `<div><span class="eyebrow">Note</span><b>${esc(g.note)}</b></div>` : ''}
          </div>
        </div>
        ${S.canEdit ? `<div class="bd"><button class="btn sm ghost og-danger" data-rm="${g.id}">Remove this grant</button></div>` : ''}`;
      wireSide();
    }

    function drawSideReport() {
      const mgr = who(S.sel.from), rep = who(S.sel.to);
      const rows = S.sel.rows;
      side.innerHTML = `
        <div class="hd"><h2>A reporting line</h2><span class="og-tag"><span class="og-dot rep"></span>Structure</span></div>
        <div class="bd og-flow">
          ${personRow(rep, rep.title || rep.email || '')}
          <div class="og-arrow rep"><span></span><b>reports to</b><span></span></div>
          ${personRow(mgr, mgr.title || mgr.email || '')}
        </div>
        <div class="bd og-sec">
          <div class="eyebrow">This is not a grant</div>
          <p class="small">A reporting line says who someone answers to. It is not a wire and nobody drew it to hand over access. On its own it lets ${esc(mgr.name)} — and everyone above them — see the kinds below that are not private.</p>
        </div>
        <div class="bd og-sec">
          <div class="eyebrow">What ${esc(mgr.name)} can actually see of ${esc(rep.name)}'s</div>
          ${rows ? `<ul class="list og-explain">${rows.map((r) => `<li>${kindDot(r.kind.key)}<div class="t"><b>${esc(r.kind.label)}${r.kind.sensitive ? ' <i class="og-priv">private</i>' : ''}</b><span>${esc(r.why || '')}</span></div>${r.allowed ? pill('Yes', 'good') : pill('No', 'neutral')}</li>`).join('')}</ul>`
            : '<p class="small muted"><span class="spin"></span> Asking the one place that decides…</p>'}
          <p class="small muted og-lead">Each answer comes from the same check the rest of the app runs, so the picture and what they actually get back cannot drift apart.</p>
        </div>
        <div class="bd small muted">A reporting line has no Remove button here — move the person under a different manager from their details panel instead.</div>`;
      wireSide();
    }

    function wireSide() {
      side.querySelectorAll('[data-go]').forEach((b) => (b.onclick = () => revealPerson(Number(b.dataset.go))));
      side.querySelectorAll('[data-rm]').forEach((b) => (b.onclick = () => removeGrant(Number(b.dataset.rm))));
      const save = side.querySelector('#og-save');
      if (save) save.onclick = () => savePerson();
      const re = side.querySelector('#og-remgr');
      if (re) re.onclick = () => changeManager();
    }

    async function fillTeams(p) {
      const sel = side.querySelector('#og-team');
      if (!sel) return;
      if (!S.teams) {
        try { const r = await api.get('/api/org/teams'); S.teams = Array.isArray(r) ? r : (r.teams || []); }
        catch { S.teams = []; }
      }
      if (!sel.isConnected) return;
      const cur = (p.team && p.team.id) || p.team_id || '';
      sel.innerHTML = '<option value="">—</option>' + S.teams.map((t) => `<option value="${t.id}" ${String(t.id) === String(cur) ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
    }

    async function savePerson() {
      const id = S.sel && S.sel.kind === 'node' ? S.sel.id : null;
      if (!id) return;
      const team = side.querySelector('#og-team').value;
      const body = {
        title: side.querySelector('#og-title').value.trim(),
        role: side.querySelector('#og-role').value,
        team_id: team ? Number(team) : null, // sent explicitly so "—" really does clear the team
      };
      const btn = side.querySelector('#og-save');
      btn.disabled = true;
      try {
        const r = await api.patch(`/api/org/people/${id}`, body);
        if (r && r.person) {
          S.nodes.set(id, { ...S.nodes.get(id), ...r.person });
          const cached = personCache.get(id);
          if (cached) personCache.set(id, { ...cached, person: r.person });
        }
        toast('Saved');
        draw(); drawSide();
      } catch (e) { btn.disabled = false; toast(e.detail || 'Could not save', 'err'); }
    }

    // Moving someone in the tree is structure, so the dialog says plainly what it does and does
    // not do to permission — that is the whole point of keeping the two apart.
    function changeManager() {
      const id = S.sel.id;
      const n = S.nodes.get(id) || {};
      const sens = S.kinds.filter((k) => k.sensitive).map((k) => k.label).join(', ');
      const body = document.createElement('div');
      body.innerHTML = `
        <p>Pick who <b>${esc(n.name)}</b> reports to. This changes the shape of the organisation. It does not draw or remove a single wire.</p>
        <div class="field"><label for="og-mq">Search for their new manager</label><input type="text" id="og-mq" placeholder="Start typing a name…" autocomplete="off"></div>
        <div class="org-hits static" id="og-mhits"></div>
        <p class="small muted">Their new manager — and everyone above — will be able to see the kinds that are not private, because that is what a reporting line grants.${sens ? ` ${esc(sens)} stays private either way.` : ''}</p>`;
      const m = modal({ title: 'Change manager', body, footer: '<button class="btn" data-close>Cancel</button><button class="btn sm ghost" id="og-mtop">Make them top of the tree</button>' });
      const input = body.querySelector('#og-mq');
      const box = body.querySelector('#og-mhits');
      let t = null;
      input.oninput = () => {
        clearTimeout(t);
        t = setTimeout(async () => {
          const q = input.value.trim();
          if (q.length < 2) { box.innerHTML = ''; return; }
          try {
            const r = await api.get(`/api/org/search?q=${encodeURIComponent(q)}&limit=20`);
            const people = (r.people || []).filter((x) => x.id !== id);
            box.innerHTML = people.length ? people.map((x) => `<button data-pick="${x.id}"><b>${esc(x.name)}</b><span>${esc([x.title, x.team, x.email].filter(Boolean).join(' · '))}</span></button>`).join('') : '<div class="og-none">Nobody by that name.</div>';
            box.querySelectorAll('[data-pick]').forEach((b) => (b.onclick = () => apply(Number(b.dataset.pick))));
          } catch (e) { box.innerHTML = `<div class="og-none">${esc(e.detail || 'Could not search')}</div>`; }
        }, 220);
      };
      m.root.querySelector('#og-mtop').onclick = () => apply(null);
      async function apply(newMgr) {
        try {
          const r = await api.patch(`/api/org/people/${id}`, { reports_to_id: newMgr });
          if (r && r.person) S.nodes.set(id, { ...S.nodes.get(id), ...r.person });
          else S.nodes.set(id, { ...S.nodes.get(id), reports_to_id: newMgr });
          personCache.delete(id);
          if (newMgr != null) { S.loaded.delete(newMgr); if (!S.nodes.has(newMgr)) { try { await loadGraph(newMgr, 2); } catch { /* it will appear when expanded */ } } }
          m.close();
          toast(newMgr == null ? `${n.name} is now top of the tree. No wire changed.` : `${n.name} now reports to ${who(newMgr).name}. No wire changed.`);
          if (newMgr == null && !S.rootIds.includes(id)) S.rootIds.push(id);
          draw(); selectNode(id);
        } catch (e) { toast(e.detail || 'Could not move them', 'err'); }
      }
    }

    // Taking access away is a production decision, so the dialog says what it costs rather than
    // asking "are you sure?" — and the button is the app's red, like every other destructive one.
    function removeGrant(gid) {
      const g = S.grants.get(gid);
      if (!g) return;
      const k = kindOf(g.kind);
      const owner = who(g.owner_id), viewer = who(g.viewer_id);
      const m = modal({
        title: `Remove the ${k.label} wire?`,
        body: `<p><b>${esc(viewer.name)}</b> will stop seeing <b>${esc(owner.name)}</b>'s ${esc(k.label.toLowerCase())}.</p>
          ${k.sensitive
            ? `<p class="small" style="color:var(--crit)"><b>${esc(k.label)} is private.</b> Nothing gives it back — not the reporting line, not being their manager. Only another wire.</p>`
            : `<p class="small muted">If ${esc(viewer.name)} is above ${esc(owner.name)} on the reporting line, they keep seeing it anyway — that is what a reporting line grants. Click the reporting line to check.</p>`}
          <p class="small muted">Nobody moves in the tree. The removal is written to the audit trail.</p>`,
        footer: '<button class="btn" data-close>Keep it</button><button class="btn primary" id="og-rmok" style="background:var(--crit);border-color:var(--crit)">Remove the wire</button>',
      });
      m.root.querySelector('#og-rmok').onclick = async (e) => {
        e.target.disabled = true;
        try {
          await api.del(`/api/org/grants/${gid}`);
          S.grants.delete(gid);
          personCache.delete(g.owner_id); personCache.delete(g.viewer_id);
          if (S.sel && S.sel.kind === 'grant' && S.sel.id === gid) S.sel = { kind: 'node', id: g.owner_id };
          m.close();
          toast(`${viewer.name} can no longer see ${owner.name}'s ${k.label.toLowerCase()}.`);
          draw(); drawSide();
        } catch (err) { e.target.disabled = false; toast(err.detail || 'Could not remove that wire', 'err'); }
      };
    }

    // ---- search & reveal --------------------------------------------------------------------
    // Bring one person onto the canvas without ever loading the company. /graph already answers
    // with the chain of managers above the person it is asked about, so the path is known after a
    // single call; each manager on it then needs one cheap depth-1 call for their own row.
    const chainAbove = (id) => {
      const out = [];
      let cur = S.nodes.get(id), hops = 0;
      while (cur && cur.reports_to_id != null && hops++ < 24) {
        out.unshift(cur.reports_to_id);
        cur = S.nodes.get(cur.reports_to_id);
      }
      return out;
    };

    async function revealPerson(id) {
      if (!S.loaded.has(id)) {
        try { await loadGraph(id, 2); } catch (e) { return toast(e.detail || 'Could not open their part of the chart', 'err'); }
      }
      const chain = chainAbove(id);
      const top = chain.length ? chain[0] : id;
      if (!S.rootIds.includes(top)) S.rootIds.push(top);
      for (const a of chain) {
        if (!S.loaded.has(a)) { try { await loadGraph(a, 1); } catch { /* the path still draws with what we have */ } }
        S.expanded.add(a);
      }
      draw();
      if (!S.pos.has(id) && !S.rootIds.includes(id)) { S.rootIds.push(id); draw(); }
      await selectNode(id);
      centreOn(id, Math.max(V.k, 0.8));
    }

    let qTimer = null;
    const closeHits = () => { hits.hidden = true; hits.innerHTML = ''; };
    qInput.addEventListener('input', () => {
      clearTimeout(qTimer);
      const q = qInput.value.trim();
      if (q.length < 2) return closeHits();
      qTimer = setTimeout(async () => {
        try {
          const r = await api.get(`/api/org/search?q=${encodeURIComponent(q)}&limit=20`);
          const people = r.people || [];
          hits.innerHTML = people.length
            ? people.map((p) => `<button data-pick="${p.id}"><b>${esc(p.name)}</b><span>${esc([p.title, p.team, p.email].filter(Boolean).join(' · '))}</span></button>`).join('')
            : '<div class="og-none">Nobody by that name.</div>';
          hits.hidden = false;
          hits.querySelectorAll('[data-pick]').forEach((b) => (b.onclick = async () => { closeHits(); qInput.value = ''; await revealPerson(Number(b.dataset.pick)); }));
        } catch (e) { hits.innerHTML = `<div class="og-none">${esc(e.detail || 'Could not search')}</div>`; hits.hidden = false; }
      }, 220);
    });
    qInput.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeHits(); qInput.blur(); } });
    const onDocDown = (e) => { if (!e.target.closest('.org-find')) closeHits(); };
    document.addEventListener('pointerdown', onDocDown);

    // ---- legend -----------------------------------------------------------------------------
    // A strip under the canvas rather than a floating card: an overlay sat on top of whichever
    // node happened to be bottom-left, which is exactly the node someone was trying to read.
    let legendDone = false;
    function drawLegend() {
      if (legendDone) return;
      legendDone = true;
      root.querySelector('#og-legend').innerHTML = `
        <span class="og-lg-row"><svg width="30" height="14" aria-hidden="true"><path d="M3 1C3 9 27 5 27 13" fill="none" stroke="var(--line-2)" stroke-width="1.6"/></svg>Reports to — structure</span>
        <span class="og-lg-row"><svg width="30" height="14" aria-hidden="true"><path d="M2 7h26" fill="none" stroke="#2F5D8A" stroke-width="2.4" stroke-linecap="round"/></svg>Grant — permission</span>
        <span class="og-lg-sep"></span>
        ${S.kinds.map((k) => `<span class="og-lg-k" title="${esc(k.hint || '')}">${kindDot(k.key)}${esc(k.label)}${k.sensitive ? '<i>private</i>' : ''}</span>`).join('')}
        <span class="og-lg-foot">A filled pin already carries a wire.</span>`;
    }

    // ---- toolbar ----------------------------------------------------------------------------
    root.querySelector('#og-fit').onclick = () => fit(null);
    root.querySelector('#og-in').onclick = () => { const r = canvas.getBoundingClientRect(); zoomAt(r.width / 2, r.height / 2, 1.2); };
    root.querySelector('#og-out').onclick = () => { const r = canvas.getBoundingClientRect(); zoomAt(r.width / 2, r.height / 2, 1 / 1.2); };
    root.querySelector('#og-tidy').onclick = async () => {
      if (!S.canEdit) return toast('Only an owner or admin can change the layout.', 'err');
      const ids = visibleIds().filter((id) => S.manual.has(id));
      if (!ids.length) return toast('Everything is already where the automatic layout wants it.');
      for (const id of ids) { S.manual.delete(id); queueSave(id); }
      draw();
      toast(`${plural(ids.length, 'node')} put back.`);
    };

    // ---- boot -------------------------------------------------------------------------------
    // Roots first, so the tops of the organisation are on the canvas as closed nodes. Then two
    // levels around wherever we are starting. In a 1 900-person company a root list can still be
    // long, so only the first few trees are drawn — everyone else is a search away.
    const MAX_ROOT_TREES = 12;
    let extraRoots = 0;
    let startId = null;
    try {
      const rootsRes = await api.get('/api/org/roots');
      const roots = rootsRes.roots || [];
      for (const r of roots) S.nodes.set(r.id, { ...(S.nodes.get(r.id) || {}), ...r });
      S.rootIds = roots.slice(0, MAX_ROOT_TREES).map((r) => r.id);
      extraRoots = Math.max(0, roots.length - S.rootIds.length);

      const g = await loadGraph(ctx.params.person ? Number(ctx.params.person) : 'me', 2);
      const start = g.root_id;
      startId = start;
      if (start != null) {
        // the chain of managers comes back with the graph, so we can sit the start inside its tree
        const chain = chainAbove(start);
        // our own tree goes first, so the canvas opens on it rather than on whoever sorts first
        const top = chain.length ? chain[0] : start;
        S.rootIds = [top, ...S.rootIds.filter((r) => r !== top)].slice(0, MAX_ROOT_TREES);
        // the graph gives the managers above but not their other reports, so each one needs a
        // single depth-1 call — otherwise a node says "3 reports" and draws one of them
        for (const a of chain) {
          if (!S.loaded.has(a)) { try { await loadGraph(a, 1); } catch { /* the path still draws */ } }
          S.expanded.add(a);
        }
        S.expanded.add(start);
        // second level opens too, unless somebody closed that node last time they were here
        for (const c of childrenOf(start)) if (S.loaded.has(c.id) && !c.collapsed) S.expanded.add(c.id);
      }
      if (!S.rootIds.length && start != null) S.rootIds = [start];
    } catch (e) {
      root.innerHTML = `<div class="head"><div><h1>Org chart &amp; access</h1><p>${esc(e.detail || e.message || e)}</p></div></div>`;
      return;
    }

    draw();
    drawSide();
    requestAnimationFrame(() => fit(startId, OPEN_FLOOR));
    if (ctx.params.person && S.nodes.has(Number(ctx.params.person))) selectNode(Number(ctx.params.person));

    // Keep what is in the middle of the canvas in the middle of it when the window changes size,
    // rather than letting the whole chart slide off to one side.
    let lastSize = canvas.getBoundingClientRect();
    const onResize = () => {
      const now = canvas.getBoundingClientRect();
      V.x += (now.width - lastSize.width) / 2;
      V.y += (now.height - lastSize.height) / 2;
      lastSize = now;
      applyView();
    };
    window.addEventListener('resize', onResize);

    return () => {
      disposed = true;
      window.removeEventListener('resize', onResize);
      document.removeEventListener('pointerdown', onDocDown);
      flushSave();
    };
  },
};
