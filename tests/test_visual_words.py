"""1.0.83: the visual pass — titles cut at a word, plain words in the audit trail, the Mailer saying when it
cannot send, and the "My requests" status shown as a tag."""
from cbx.api.audit import action_words
from cbx.engine.providers import PROVIDERS
from cbx.util import clip


def test_clip_cuts_at_a_word_and_says_so():
    t = "The municipal account for site 4471 shows a duplicated water charge and the PDF extract stops"
    c = clip(t, 40)
    assert len(c) <= 40 and c.endswith("…")
    assert t.startswith(c[:-1].rstrip())
    assert c[:-1].rstrip().split(" ")[-1] in t.split(" ")  # never half a word
    assert clip("short", 40) == "short"
    assert clip("  many   spaces  ", 40) == "many spaces"


def test_audit_codes_read_as_words():
    assert action_words("tool_row_added") == "Tool item added"
    assert action_words("cexa_team_created").startswith("C-EXA team")
    assert action_words("") == ""


def test_audit_rows_carry_a_name_and_plain_words(owner):
    owner.post("/api/requests", json={"problem": "Lease end dates live in three spreadsheets.", "impact": "Missed notices.", "wish": "One list."})
    d = owner.get("/api/audit").json()
    assert d["rows"]
    r = d["rows"][0]
    assert "actor_name" in r and "what" in r
    assert r["what"] and "_" not in r["what"]
    assert isinstance(d.get("action_words"), dict)


def test_drafts_say_whether_mail_can_go_out(owner):
    d = owner.get("/api/mailer/drafts").json()
    assert "ready" in d and "why" in d
    if not d["ready"]:
        assert d["why"]


def test_my_requests_status_is_a_tag():
    cols = {c["key"]: c for c in PROVIDERS["core.my_requests"].columns}
    assert cols["status"].get("format") == "status"
