// Carry-over: what a person was doing survives a refresh. Before the page reloads for a new version
// (web/update.js) — or because the person pressed F5 with something half-typed — the route, the scroll
// position and the value of every visible field on the page are written to this browser's storage.
// After the reload the same person is put back on that page, the fields are filled in again and a
// toast says so. Nothing leaves the browser; passwords and file pickers are never kept; a snapshot older
// than half an hour, or made by a different person, is thrown away unread.
const KEY = 'cbx.carry';
const MAX_AGE_MS = 30 * 60 * 1000;
const SKIP_TYPES = new Set(['password', 'file', 'hidden', 'button', 'submit', 'reset', 'image']);

let dirty = false;

// A field is worth carrying when a person can see it and it can be found again after the reload:
// by id, by name, or by its position among the page's fields as a last resort.
function fieldKey(f, index) {
  if (f.id) return '#' + f.id;
  if (f.name) return '@' + f.name;
  return '~' + index;
}
function fields(main) {
  return [...(main || document).querySelectorAll('input, textarea, select')].filter((f) => !SKIP_TYPES.has((f.type || '').toLowerCase()) && !f.disabled && !f.readOnly && !f.closest('[data-no-carry]') && f.offsetParent !== null);
}

export const carry = {
  /** Something on the page changed since it was drawn (the banner says "unsaved changes here are carried over"). */
  isDirty: () => dirty,
  markClean() { dirty = false; },
  /** Wire the page's main element once: any typing marks the page dirty until the next navigation. */
  watch(main) {
    if (!main) return;
    main.addEventListener('input', () => { dirty = true; }, true);
    main.addEventListener('change', () => { dirty = true; }, true);
  },
  /** Write the snapshot. `meId` scopes it to the signed-in person. Returns what was saved (or null). */
  snapshot(meId, version) {
    try {
      const main = document.getElementById('main');
      const values = fields(main).map((f, i) => {
        const k = fieldKey(f, i);
        if (f.type === 'checkbox' || f.type === 'radio') return { k, c: !!f.checked };
        return { k, v: f.value };
      }).filter((x) => x.v !== '' || x.c !== undefined);
      const snap = { me: meId, from: version || '', at: Date.now(), hash: location.hash || '#/home', scroll: window.scrollY || 0, values, dirty };
      localStorage.setItem(KEY, JSON.stringify(snap));
      return snap;
    } catch { return null; } // private window or storage full: nothing to carry, nothing to break
  },
  /** Read and remove the snapshot if it is fresh and belongs to this person. */
  take(meId) {
    let snap = null;
    try { snap = JSON.parse(localStorage.getItem(KEY) || 'null'); localStorage.removeItem(KEY); } catch { /* unreadable → nothing */ }
    if (!snap || typeof snap !== 'object') return null;
    if (snap.me !== meId) return null;
    if (!snap.at || Date.now() - snap.at > MAX_AGE_MS) return null;
    return snap;
  },
  /** Put the values back into a freshly drawn page. Returns how many fields were filled. */
  apply(snap, main) {
    if (!snap || !Array.isArray(snap.values)) return 0;
    const fs = fields(main);
    const byKey = new Map(fs.map((f, i) => [fieldKey(f, i), f]));
    const changed = [];
    for (const x of snap.values) {
      const f = byKey.get(x.k);
      if (!f) continue;
      if (x.c !== undefined) { if (f.checked !== x.c) { f.checked = x.c; changed.push(f); } continue; }
      if (typeof x.v === 'string' && f.value !== x.v) { f.value = x.v; changed.push(f); }
    }
    // the page's own listeners (a live search, a preview) see the value as if it had been typed
    changed.forEach((f) => f.dispatchEvent(new Event('input', { bubbles: true })));
    if (snap.scroll) window.scrollTo({ top: snap.scroll });
    dirty = changed.length > 0; // it is unsaved again, on the new version
    return changed.length;
  },
};
