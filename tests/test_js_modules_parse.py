"""1.0.104: every page script parses as an ES module (the browser loads them as modules: one stray ')' in one sub-view
blanked the whole app). Needs Node on the PATH; skipped where there is none. 1.0.110: works on any Node (tests/_node.py)."""
from pathlib import Path

from ._node import check_module

WEB = Path(__file__).resolve().parent.parent / "web"


def test_every_page_script_parses_as_a_module():
    bad = []
    for f in sorted(WEB.rglob("*.js")):
        err = check_module(f)
        if err:
            bad.append(f"{f.relative_to(WEB)}: {err}")
    assert not bad, "\n".join(bad)
