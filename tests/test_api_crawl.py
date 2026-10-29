"""1.0.79: every API address is called — signed out, as an ordinary user and as the owner — and none of them
may break (a 500), and none may answer a signed-out caller with data.

Path parts like {id} are filled with an id that does not exist, so a route is reached but finds nothing. Signed
in, a write is only called when it names such an id (it must answer "not found", never break); a write with no id
would change the shared test database, so it is called signed out only — where it must be refused. Addresses that
start real work on the server (restart, promote, backups, sandboxes, sending mail, model calls) are left out of the
write round — they have tests of their own."""
import re

import pytest

from cbx.main import app

# signed-out callers may reach these (sign-in, the sandbox door, mail links, the version check …)
OPEN = re.compile(r"^/api/(auth/|version$|branding|support/done/|lang|health|public|mfa/|devices/confirm|update/|i18n)")
# writes that start real work — tested on their own
HEAVY = re.compile(r"/(promote|rollback|restart|release|start|stop|backup|test-version|stage|upload|playground|sandbox|"
                   r"voice|studio|call|chat$|ask$|message$|missions|agents|teams|cexa|mailer/read|mailer/test|mail/|"
                   r"support/mail|warehouse/pipeline|onedrive|shutdown|logout|reset|import|harvest/sources/[^/]+/run|rerun|"
                   r"run$|runs$|send|flows|ledger|scan|month)")


def _routes():
    out = []

    def walk(rs, prefix=""):
        for r in rs:
            if hasattr(r, "original_router"):
                ctx = getattr(r, "include_context", None)
                walk(r.original_router.routes, prefix + (getattr(ctx, "prefix", "") or ""))
            elif hasattr(r, "methods") and (prefix + r.path).startswith("/api"):
                out.append((prefix + r.path, sorted(r.methods)))
    walk(app.routes)
    return out


ROUTES = _routes()


def _fill(path: str) -> str:
    path = path.replace("{fmt}", "md")
    return re.sub(r"\{[^}]+\}", "999999", path)


def _call(c, method, path):
    url = _fill(path)
    if method == "GET":
        return c.get(url)
    if method == "DELETE":
        return c.delete(url)
    return c.request(method, url, json={})


def test_there_are_many_addresses():
    assert len(ROUTES) > 300


def test_signed_out_nothing_breaks_and_nothing_answers(client):
    client.cookies.clear()
    bad, open_ = [], []
    for path, methods in ROUTES:
        for m in methods:
            if m in ("HEAD", "OPTIONS") or (m != "GET" and HEAVY.search(path)):
                continue
            r = _call(client, m, path)
            if r.status_code >= 500:
                bad.append((m, path, r.status_code))
            elif r.status_code < 300 and not OPEN.search(path):
                open_.append((m, path, r.status_code))
    assert not bad, bad
    assert not open_, open_


@pytest.mark.parametrize("who", ["user", "owner"])
def test_signed_in_nothing_breaks(who, request):
    c = request.getfixturevalue(who)
    bad = []
    for path, methods in ROUTES:
        for m in methods:
            if m in ("HEAD", "OPTIONS") or (m != "GET" and (HEAVY.search(path) or "{" not in path)):
                continue
            r = _call(c, m, path)
            if r.status_code >= 500:
                bad.append((m, path, r.status_code, r.text[:120]))
            elif m not in ("GET", "DELETE") and r.status_code < 300:  # deleting what is not there may say ok
                bad.append((m, path, r.status_code, "a write to a row that does not exist succeeded"))
    assert not bad, bad
