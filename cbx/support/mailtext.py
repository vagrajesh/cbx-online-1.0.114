"""Reading a support mail the way a person does (1.0.84, from the real Chase mailbox).

Real support mail is rarely one clean message. It is a forward ("Kindly assist with the below"), a chain of
replies with the actual request four messages down, a reminder ("A polite reminder…"), or a thank-you. It is
wrapped in an external-sender banner, a signature with phone numbers, a page of disclaimer and links that are
three hundred characters of tracking. This module turns that into what the support team needs to read:

  * clean()      the banner, disclaimers, tracking links and contact lines removed
  * messages()   the chain split into its messages, newest first, each with who and when
  * kind()       request · follow_up (a reminder or "see below") · thanks (nothing asked) — plain rules
  * readable()   the new words, plus the earlier messages when the new words alone don't say what is wanted
  * redact()     passwords, PINs and one-time codes in the text are removed before anything is kept or read
  * hints()      countries, sites, PIDs and reference numbers named in it, for the summary and the model

Nothing here acts on a mail: it is text in, text out. A mail is the sender's words, never instructions.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# ---- noise ---------------------------------------------------------------------------------------------

BANNERS = [
    r"CAUTION:\s*This (e-?mail|message) originated from outside (of )?the organi[sz]ation[^\n]*",
    r"You don'?t often get (e-?mail|messages?) from [^\n]*?(Learn why this is important[^\n]*)?",
    r"Learn why this is important\s*<[^>]*>",
    r"^\s*\[?External\]?\s*$",
    r"^\s*(Classified|Classification)\s*-\s*[A-Za-z ]+$",
    r"This e-?mail is confidential and intended[\s\S]{0,900}?(?:e-?mailed to you\.|\n\s*\n)",
    r"NOTICE: This e-?mail and any files transmitted[\s\S]{0,900}?(?:from your system\.|\n\s*\n)",
    r"(?:Disclaimer|DISCLAIMER)[:\s][\s\S]{0,900}?\n\s*\n",
    r"Please consider the environment before printing[^\n]*",
    r"Details about the personal data [\s\S]{0,300}?Privacy Policy\.?",
    r"^[^\n]*(?:proper judg(?:e)?ment|caution when opening|be aware of attachments|report suspicious e-?mails?|phishing)[^\n]*$",
]
BANNER = re.compile("|".join(f"(?:{b})" for b in BANNERS), re.I | re.M)
LINK = re.compile(r"<(?:https?|mailto):[^>\s]*>|https?://\S{60,}", re.I)
# signature lines only: "T: +27 …", "Mobile: +971 …", "www.…", "Follow CBRE" — an "Email: …" or "Cellphone number: …" the
# person wrote in the body (the details of someone who needs a login) stays
CONTACT = re.compile(r"^\s*(?:[TMFC]|Tel|Phone|Mobile|Mob|Fax)\s*[:.]\s*\+?\(?\d[^\n]*$|^\s*(?:www\.|Follow (?:us|CBRE)|LinkedIn\b|Website\s*:)[^\n]*$"
                     # 1.0.98: from CBX-Connect 2.10.3 — "Cell:", "Direct:" and "Office:" numbers too (an invisible lead's number)
                     r"|^[ \t]*(?:Cell|Direct|Office)\s*[:.]\s*\+?\(?\d[\d ()./-]{6,}\d\b[^\n]*$"
                     r"|^\s*\+?\d[\d ()-]{7,}\s*$", re.I | re.M)

# where an earlier message starts (Outlook, Gmail, the "____" line some clients draw)
HEAD = re.compile(r"^\s*(?:From|Van|De|Von)\s*:\s*(?P<who>.+?)\s*$\s*^\s*(?:Sent|Date|Datum|Envoyé|Gesendet)\s*:\s*(?P<when>.+?)\s*$", re.I | re.M)
GMAIL = re.compile(r"^\s*On (?P<when>[^\n]{4,80}?),? (?P<who>[^\n<]{2,80}?)\s*(?:<[^\n]*)?\s*wrote:\s*$", re.I | re.M)
RULE = re.compile(r"^\s*(?:_{10,}|-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,})\s*$", re.I | re.M)
FIELDS = re.compile(r"^\s*(?:To|Cc|Bcc|Subject|Importance|Onderwerp|Aan|Objet|À|Betreff|An)\s*:.*$", re.I | re.M)


def clean(text: str) -> str:
    """The words without the banner, the disclaimer, tracking links, image placeholders and contact lines."""
    t = (text or "").replace("\r", "").replace("\xa0", " ").replace("​", "")
    t = BANNER.sub("", t)
    t = re.sub(r"\[cid:[^\]\s]{1,200}\]", "", t)  # 1.0.109: where a picture was pasted — the picture itself is shown under the message
    t = LINK.sub("", t)
    t = CONTACT.sub("", t)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)  # plain-text quoting
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"(?m)^ +| +$", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def messages(body: str) -> list[dict]:
    """The chain, newest first: [{who, when, text}] — the first one is the sender's own new words (who/when empty)."""
    b = (body or "").replace("\r", "")
    cuts = sorted({m.start() for m in HEAD.finditer(b)} | {m.start() for m in GMAIL.finditer(b)} | {m.start() for m in RULE.finditer(b)})
    parts, last = [], 0
    for c in cuts:
        parts.append(b[last:c])
        last = c
    parts.append(b[last:])
    out = []
    for i, p in enumerate(parts):
        who = when = ""
        if i:
            p = RULE.sub("", p, count=1)
            h = HEAD.search(p) or GMAIL.search(p)
            if h and h.start() < 40:
                who, when = _name(h.group("who")), h.group("when").strip()[:60]
                p = p[h.end():]
            p = FIELDS.sub("", p)
        text = unsigned(clean(p))
        if i == 0 or text:
            out.append({"who": who, "when": when, "text": text})
    return out


def _name(who: str) -> str:
    who = re.sub(r"<.*|\[[^\]]*\]|\([^)]*\)|'|\"", "", who).strip()
    if "," in who and not re.search(r"\S@\S", who):  # "Otieno, Brian @ Nairobi" → "Brian Otieno"
        last, first = who.split(",", 1)
        who = f"{first.split('@')[0].strip()} {last.strip()}"
    return re.sub(r"\s+", " ", who).strip()[:80]


# ---- what kind of mail ----------------------------------------------------------------------------------

GREET = re.compile(r"^\s*(?:hi|hello|dear|good (?:day|morning|afternoon|evening)|morning|afternoon|greetings|hey)\b[^\n]*$", re.I | re.M)
SIGNOFF = re.compile(r"^\s*(?:(?:kind(?:est)?|best|warm)?\s*regards|br|thanks(?: a lot)?|thank you(?: very much)?|many thanks|cheers|sincerely|yours[^\n]*)\s*[,.!]*\s*$", re.I | re.M)
POINTER = re.compile(r"\b(?:see (?:the )?(?:e-?\s?mail|mail|request|message|note)?\s*(?:below|attached|trail)|(?:the )?below (?:request|mail|e-?mail)|"
                     r"(?:with|on|to) the below|as requested|for your (?:info|information|attention|action)|fyi|"
                     r"(?:polite |gentle |kind |friendly )?reminder|follow(?:ing)?[- ]up|any (?:update|feedback|news)|provide (?:an? )?(?:update|feedback)|"
                     r"(?:please |kindly )?(?:advise|update) (?:on|me)|still (?:waiting|awaiting|outstanding))", re.I)
NUDGE = re.compile(r"\b(?:(?:please|kindly) (?:urgently )?(?:assist|support|help|action)|urgently assist|please urgently)\b", re.I)  # only a nudge when it is all they wrote
ASKING = re.compile(r"\?|\b(?:please|kindly|could you|can you|would you|i need|we need|need(?:ed)? to|require|requesting|request|unable|cannot|can'?t|not able|"
                    r"doesn'?t|does not|isn'?t|is not|error|issue|problem|urgent|assist|help|missing|incorrect|wrong)\b", re.I)
THANKS = re.compile(r"\b(?:thank(?:s| you)|much appreciated|noted|received|great|perfect|ok(?:ay)?|sorted|resolved|works now|all good)\b", re.I)


# 1.0.98: from CBX-Connect 2.10.3 cbx/support/mailtext.py (signoff, _goes_on, unlink)
def _goes_on(line: str) -> bool:
    """The line is more of the message (a question, or a sentence of five words or more), not a name or a title."""
    s = line.strip()
    return len(s) >= 300 or "?" in s or (s[-1:] in ".!" and "|" not in s and len(re.findall(r"[A-Za-z]+", s)) >= 5)


def signoff(text: str, rx: "re.Pattern | None" = None):
    """The sign-off that ends the message: the first one not followed straight away by more of the message.
    None when there is none (a "Thanks." above the question no longer ends the message)."""
    for m in (rx or SIGNOFF).finditer(text or ""):
        if not _goes_on(text[m.end():m.end() + 600].lstrip().split("\n", 1)[0]):
            return m
    return None


# 1.0.98: the lead's own links (Accept, Mark as done, My settings) live only in the mails CBX sends them: what CBX keeps
# and shows of a mail has "[link]" in their place (anyone with the desk's view could accept or close for the lead)
PRIVATE_LINK = re.compile(r"(?:https?://[^\s<>\"']*?)?/(?:api/)?support/(?:accept|me|done)/[A-Za-z0-9_\-=]{16,}", re.I)


def unlink(text: str) -> str:
    return PRIVATE_LINK.sub("[link]", text or "")


def core_words(text: str) -> str:
    """The message without greeting lines and sign-off lines (and what follows a sign-off: the name, the title)."""
    t = GREET.sub("", text or "")
    m = signoff(t)  # 1.0.98: a "Thanks." above the question no longer ends the message
    if m:
        t = t[: m.start()]
    return re.sub(r"\s+", " ", t).strip()


def unsigned(text: str) -> str:
    """The message up to its sign-off and the name under it — the title, address and numbers dropped."""
    m = SIGNOFF.search(text or "")
    if not m:
        return (text or "").strip()
    rest = [l for l in text[m.end():].splitlines() if l.strip()]
    name = rest[0].split("|")[0].strip() if rest and len(rest[0]) < 60 else ""
    return (text[: m.end()].rstrip() + ("\n" + name if name else "")).strip()


def kind(text: str) -> str:
    """request · follow_up · thanks — for the sender's NEW words only."""
    w = core_words(text)
    n = len(re.findall(r"\w+", w))
    if n == 0:
        return "follow_up"  # nothing but a greeting and a name above a forward
    if n <= 40 and THANKS.search(w) and not re.search(r"\?|\b(?:please|kindly|could you|can you|need|urgent|still|not|unable|update|advise)\b", w, re.I):
        return "thanks"
    if n <= 45 and POINTER.search(w) or n <= 12 and NUDGE.search(w):
        return "follow_up"
    return "request"


def thin(text: str) -> bool:
    """The new words don't carry the request on their own: read the earlier messages too."""
    return kind(text) != "request" or len(re.findall(r"\w+", core_words(text))) < 25


def readable(body: str, limit: int = 6000) -> str:
    """What goes on the ticket: the new words, and — when they only point at it — the earlier messages, newest
    first, each headed with who wrote it. Credentials are removed. At most `limit` characters."""
    ms = messages(body)
    if not ms:
        return ""
    top = ms[0]["text"]
    if not thin(top) or len(ms) == 1:
        return redact(top)[:limit]
    out, seen = [top] if top else [], set()
    for m in ms[1:]:
        w = core_words(m["text"])
        if not w or w.lower() in seen:
            continue
        seen.add(w.lower())
        out.append(f"— Earlier, from {m['who'] or 'someone'}{' (' + m['when'] + ')' if m['when'] else ''}:\n{m['text']}")
        if sum(len(x) for x in out) > limit:
            break
    return redact("\n\n".join(out))[:limit]


def same_text(a: str, b: str) -> float:
    """How alike two mails' words are, 0..1 — the same request sent twice reads 0.9+."""
    a, b = core_words(a).lower(), core_words(b).lower()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a[:2000], b[:2000]).ratio()


# ---- what must never be kept ---------------------------------------------------------------------------

SECRET_LINE = re.compile(r"(?im)^(?P<k>[^\n:]{0,40}?\b(?:pass ?word|passcode|pwd|pin(?: code)?|otp|one[- ]time (?:pin|code|password)|verification code|security code|temporary password)\b[^\n:=]{0,30})"
                         r"\s*(?::|=|\bis\b)\s*(?P<v>\S[^\n]*)$")
SECRET_INLINE = re.compile(r"(?i)\b(?P<k>(?:new |temporary |temp )?(?:pass ?word|passcode|pwd|otp|pin))\s*(?::|=|\bis\b)\s*(?P<v>[^\s,;\[]{3,})")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
ACCOUNT = re.compile(r"(?i)\b(?P<k>(?:bank )?(?:acc(?:ount)?\.?\s*(?:no|number|#)|iban|swift|card number|credit card|id number|identity number|passport(?: number)?))\b\s*[:.#]?\s*(?P<v>[A-Z]{0,8}\d[\dA-Z -]{5,})")


def redact(text: str) -> str:
    """Passwords, PINs, one-time codes, bank, card and ID numbers out — the label stays, the value does not."""
    t = SECRET_LINE.sub(lambda m: f"{m.group('k').rstrip()}: [removed — CBX never keeps passwords or codes]", text or "")
    t = SECRET_INLINE.sub(lambda m: f"{m.group('k')}: [removed]", t)
    t = ACCOUNT.sub(lambda m: f"{m.group('k')}: [removed]", t)
    return CARD.sub(lambda m: m.group() if _not_card(m.group()) else "[number removed]", t)


def _not_card(s: str) -> bool:
    d = re.sub(r"\D", "", s)
    if not 13 <= len(d) <= 19:
        return True
    total, alt = 0, False
    for ch in reversed(d):  # Luhn: only a real card number is removed; PO and order numbers stay
        n = int(ch) * (2 if alt else 1)
        total += n - 9 if n > 9 else n
        alt = not alt
    return total % 10 != 0


def had_secret(text: str) -> bool:
    return redact(text) != (text or "")


# ---- what it names ---------------------------------------------------------------------------------------

COUNTRIES = ("Algeria", "Angola", "Bahrain", "Botswana", "Burundi", "Cameroon", "DRC", "Congo", "Egypt", "Eswatini", "Ethiopia", "Ghana", "Ivory Coast",
             "Kenya", "Kuwait", "Lesotho", "Madagascar", "Malawi", "Mauritius", "Morocco", "Mozambique", "Namibia", "Nigeria", "Oman", "Qatar", "Rwanda",
             "Saudi Arabia", "Senegal", "South Africa", "Sudan", "Tanzania", "Tunisia", "UAE", "United Arab Emirates", "Dubai", "Abu Dhabi", "Uganda",
             "Zambia", "Zimbabwe", "United Kingdom")


def hints(text: str) -> dict:
    """Countries, sites, PIDs and reference numbers the mail names — what the lead will look for first."""
    t = text or ""
    found = [c for c in COUNTRIES if re.search(rf"\b{re.escape(c)}\b", t, re.I)]
    sites = [m.strip(" .") for m in re.findall(r"(?im)^\s*site(?: name)?\s*[:\-]\s*([^\n]{2,60})$", t)]
    sites += [m.strip() for m in re.findall(r"\b(?:for|at|on) the ([A-Z][\w&'-]+(?: [A-Z][\w&'-]+){0,3}) site\b", t)]
    pids = re.findall(r"(?i)\bPID\s*[:#]?\s*(\d{2,8})\b", t)
    refs = re.findall(r"(?i)\b(?:PO|WO|JC|quote|qtn|invoice|inv)\s*(?:no\.?|number|#)?\s*[:#]?\s*([A-Z]{0,4}\d{5,12})\b", t)
    codes = re.findall(r"\b(X[A-Z0-9]\d{2}|Z\d{3})\b", t)
    found = ["UAE" if c in ("United Arab Emirates",) else c for c in found]
    return {"countries": list(dict.fromkeys(found))[:20], "sites": list(dict.fromkeys(sites))[:10], "pids": list(dict.fromkeys(pids))[:10],
            "refs": list(dict.fromkeys(refs))[:20], "company_codes": list(dict.fromkeys(codes))[:30]}


def where_of(h: dict) -> str:
    bits = []
    if h.get("sites"):
        bits.append("site " + ", ".join(h["sites"][:4]))
    if h.get("pids"):
        bits.append("PID " + ", ".join(h["pids"][:4]))
    if h.get("countries"):
        bits.append(", ".join(h["countries"][:12]))
    return "; ".join(bits)[:300]
