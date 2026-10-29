// 1.0.114: each person's look — theme, menu (side or top) and text size — as classes on <html> and <body>.
// Loaded in <head> before the styles paint (the CSP allows no inline script), it puts back the classes this
// browser saw last, so a reload does not flash Classic first. app.js calls CBX_LOOK.apply(prefs) once
// /api/auth/me has answered, and Settings calls it when the person picks something — no reload.
// The themes are CBX-Connect 2's (cbx/web/themes.py): key, name, one line, square corners?, dark page?, dark side panel?
(function () {
  var THEMES = [
    ['classic', 'Classic', 'The CBX look: white, green, soft corners', false, false, false],
    ['classic-dark', 'Classic dark', 'The CBX look after dark', false, true, true],
    ['ocean', 'Ocean', 'White with a calm blue', false, false, false],
    ['sand', 'Sand', 'Warm paper and terracotta', false, false, false],
    ['forest', 'Forest', 'A deep green panel, a light page', false, false, true],
    ['fresh', 'Fresh', 'Square corners, serif headings', true, false, false],
    ['paper', 'Paper', 'Plain white, square', true, false, false],
    ['slate', 'Slate', 'Cool grey, square', true, false, false],
    ['night', 'Night', 'Dark and square', true, true, true],
    ['contrast', 'High contrast', 'Black on white — easiest to read', true, false, false],
  ];
  var KEY = 'cbx.look';
  var DEFAULT = 'classic';
  var byKey = {};
  THEMES.forEach(function (t) { byKey[t[0]] = t; });
  // only known words ever become class names: a stored or typed value is never put on the page as-is
  function clean(p) {
    p = p || {};
    return { theme: byKey[p.theme] ? p.theme : DEFAULT, menu: p.menu === 'top' ? 'top' : 'side', size: p.size === 'large' ? 'large' : 'standard' };
  }
  function classes(p) {
    var c = clean(p), t = byKey[c.theme];
    var out = ['th-' + c.theme, t[3] ? 'sharp' : 'round'];
    if (t[4]) out.push('dark');
    if (t[5]) out.push('sidedark');
    out.push(c.menu === 'top' ? 'menu-top' : 'menu-side');
    if (c.size === 'large') out.push('size-large');
    return out;
  }
  var ALL = ['sharp', 'round', 'dark', 'sidedark', 'menu-side', 'menu-top', 'size-large'].concat(THEMES.map(function (t) { return 'th-' + t[0]; }));
  function put(el, cls) {
    if (!el) return;
    ALL.forEach(function (k) { el.classList.remove(k); });
    cls.forEach(function (k) { el.classList.add(k); });
  }
  var current = null;
  function apply(p, remember) {
    var c = clean(p), cls = classes(c);
    current = c;
    put(document.documentElement, cls);
    put(document.body, cls);
    if (remember !== false) { try { localStorage.setItem(KEY, JSON.stringify(c)); } catch (e) { /* private window: Classic until the server answers */ } }
    return c;
  }
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(KEY) || 'null'); } catch (e) { saved = null; }
  apply(saved, false);
  // <body> does not exist yet while <head> runs: give it the same classes as soon as it does
  if (!document.body) document.addEventListener('DOMContentLoaded', function () { put(document.body, classes(current)); });
  window.CBX_LOOK = {
    THEMES: THEMES.map(function (t) { return { key: t[0], name: t[1], line: t[2], sharp: t[3], dark: t[4], sidedark: t[5] }; }),
    DEFAULT: DEFAULT, clean: clean, classes: classes, apply: apply,
  };
})();
