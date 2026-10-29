"""1.0.114 (Look): CBX-Connect 2's look in CBX 1 — themes, side/top menu, text size, the wordmark, tool colours, the new Home.
The browser walk (every view in Classic, Fresh, Night and High contrast, owner and plain user, desktop and phone) is in the
release notes; these tests pin what must not drift: the same ten themes in the server, the browser and the CSS; a person's
look saved with them and only known words accepted; the files the shell loads; the shell's parts present."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ._node import check_module, run_module

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def _read(p: str) -> str:
    return (WEB / p).read_text(encoding="utf-8")


def test_the_shell_loads_the_theme_first_and_the_boot_script_in_the_head():
    html = _read("index.html")
    head = html.split("</head>")[0]
    assert '<script src="/static/theme-boot.js"></script>' in head  # an external script: the CSP allows no inline one
    assert head.index("theme-boot.js") < head.index("theme.css") < head.index("styles.css") < head.index("app.css")
    assert "light only" not in html  # dark themes need dark form controls
    assert 'href="/static/img/cbx-x.png"' in head


def test_the_served_shell_versions_the_new_files(client):
    r = client.get("/")
    from cbx.main import VERSION

    for f in ("theme-boot.js", "theme.css", "img/cbx-x.png"):
        assert f'"/static/{VERSION}/{f}"' in r.text, f
    for f in ("fonts/geist-latin-400-normal.woff2", "fonts/geist-mono-latin-400-normal.woff2", "fonts/instrument-serif-latin-400-normal.woff2",
              "img/cbx-wordmark-light.png", "img/cbx-wordmark.png", "img/cbx-x.png", "theme.css", "theme-boot.js"):
        assert client.get(f"/static/{VERSION}/{f}").status_code == 200, f


def test_the_same_ten_themes_everywhere():
    from cbx.api.settings_api import THEMES

    assert THEMES[0] == "classic" and len(THEMES) == 10
    boot = _read("theme-boot.js")
    assert re.findall(r"^    \['([a-z-]+)', '", boot, re.M) == list(THEMES)
    css = _read("theme.css")
    for k in THEMES:
        assert f":root.th-{k},.xs-thc.th-{k}{{" in css, k
    # the bridge: this app's names read CBX 2's per theme
    for pair in ("--surface:var(--surf)", "--line:var(--rule)", "--line-2:var(--strong)", "--accent:var(--acc)", "--muted:var(--mute)"):
        assert pair in css, pair
    # square themes: no corners, and the training tour's spotlight (a shadow) survives the no-shadow rule
    assert ".sharp *{border-radius:0!important}" in css
    assert re.search(r"\.sharp \*:not\(\.tour-spot\)[^{]*\{box-shadow:none!important\}", css)
    # Classic still takes the owner's Branding accent
    assert "--acc:var(--brand-accent,#0B8A63)" in css


def test_the_boot_script_only_ever_puts_known_classes_on_the_page():
    boot = _read("theme-boot.js")
    out = run_module("""
const store = {};
const cls = () => { const s = new Set(); return { add: (k) => s.add(k), remove: (k) => s.delete(k), all: () => [...s].sort() }; };
const html = { classList: cls() };
globalThis.document = { documentElement: html, body: null, addEventListener() {} };
globalThis.localStorage = { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
globalThis.window = globalThis;
""" + boot + """
const L = window.CBX_LOOK;
const r = {
  first: html.classList.all(),
  night: L.classes({ theme: 'night', menu: 'top', size: 'large' }),
  forest: L.classes({ theme: 'forest' }),
  junk: L.classes({ theme: 'x" onload="alert(1)', menu: 'sideways', size: 'huge' }),
};
L.apply({ theme: 'fresh', menu: 'top' });
r.after = html.classList.all();
r.saved = JSON.parse(store['cbx.look']);
console.log(JSON.stringify(r));
""")
    r = json.loads(out)
    assert r["first"] == ["menu-side", "round", "th-classic"]  # nothing stored yet: Classic with the side menu
    assert r["night"] == ["th-night", "sharp", "dark", "sidedark", "menu-top", "size-large"]
    assert r["forest"] == ["th-forest", "round", "sidedark", "menu-side"]
    assert r["junk"] == ["th-classic", "round", "menu-side"]
    assert r["after"] == ["menu-top", "sharp", "th-fresh"]
    assert r["saved"] == {"theme": "fresh", "menu": "top", "size": "standard"}


def test_a_persons_look_is_saved_with_them_and_only_known_words(user):
    r = user.patch("/api/settings", json={"prefs": {"theme": "night", "menu": "top", "size": "large"}})
    assert r.status_code == 200, r.text
    me = user.get("/api/auth/me").json()["user"]
    assert {k: me["prefs"][k] for k in ("theme", "menu", "size")} == {"theme": "night", "menu": "top", "size": "large"}
    for bad in ({"theme": "neon"}, {"menu": "sideways"}, {"size": "huge"}, {"theme": 'classic" onload="x'}):
        r = user.patch("/api/settings", json={"prefs": bad})
        assert r.status_code == 400, (bad, r.text)
    assert user.get("/api/settings").json()["prefs"]["theme"] == "night"  # a refused change changes nothing


def test_the_microsoft_account_a_person_is_pinned_to_is_not_theirs_to_set(user, db):
    from cbx.models import User

    r = user.patch("/api/settings", json={"prefs": {"ms_oid": "someone-else", "theme": "ocean"}})
    assert r.status_code == 200
    assert "ms_oid" not in r.json()["prefs"] and r.json()["prefs"]["theme"] == "ocean"
    db.expire_all()
    u = db.query(User).filter(User.email == "thandi@test.local").one()
    assert "ms_oid" not in (u.prefs or {})


def test_the_shell_has_both_menus_the_wordmark_and_the_tool_colours():
    js = _read("app.js")
    assert "function topMenu(items, drawer)" in js and 'class="side x-side"' in js
    assert "window.CBX_LOOK.apply(state.me.prefs || {}, !state.viewing)" in js  # viewing as someone leaves nothing behind
    assert "cbx-wordmark-light.png" in js and "--brand-accent" in js
    colours = {"ap": "#1F3A5F", "'pdf-studio'": "#2E7D57", "ifrs16": "#2E5E7E", "'doc-watch'": "#B8731A", "'site-schedules'": "#6B4E9E", "support": "#A8432F", "todo": "#3E7C8C"}
    for slug, c in colours.items():
        assert f"{slug}: '{c}'" in js, slug
    # what CBX 1 had and CBX 2 didn't stays: folding sections, counts every minute, view-as bar, the drawer, the tour
    for keep in ("NAV_FOLD_KEY", "60000", "viewas-bar", "setupDrawer()", "tour.resume()", "canOpen(route, view)", "ALIAS[route]"):
        assert keep in js, keep


def test_home_is_top_down_and_the_old_dashboard_stays_behind_customise():
    js = _read("views/home.js")
    # owner/admin, in order
    order = [js.index(s) for s in ("'xh-needs', 'Needs you'", "'xh-ask'", "'xh-today', 'Today'", "'xh-goals', 'Goals in progress'", "'xh-now', 'Happening now'", "'xh-tools', 'Tools'")]
    assert order == sorted(order)
    order = [js.index(s) for s in ("'xh-work', 'Your work'", "'xh-tools', 'Your tools'", "'xh-prog', 'Your progress'")]
    assert order == sorted(order)
    assert "if (ctx.params.numbers) return dashboard(root, ctx);" in js and "PUT" in js and "/api/engine/home/mine" in js
    # a plain user's Home never asks for the company or the teams
    user_part = js[js.index("async function userHome"):]
    assert "/api/cexa" not in user_part
    assert "/api/cexa/goals/summary" in js


def test_the_scripts_parse():
    for f in ("theme-boot.js", "app.js", "views/home.js", "views/settings.js", "training/lessons.js"):
        assert check_module(WEB / f) == "", f
