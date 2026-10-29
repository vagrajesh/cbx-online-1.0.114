# How C-EXA learns a layout

The goal is not a good profile for Eskom. It is a C-EXA who can be handed a municipality she has
never seen and build a working profile herself — and who is better at municipality #10 than she was
at #1. This document is the design; `cbx/pdfstudio/profile.py` is the implementation.

## What was wrong (measured on 31 real Telkom municipal bills, Sept 2026)

Trained on 6 Abaqulusi bills, tested on 26 held-out bills from Abaqulusi, Bela-Bela and Alfred Duma
(one shared billing-system layout), against a hand-read gold set:

    correct 28.6% · silently wrong 52.7% · blank 18.8%

Not random errors — confident ones:

* `account_number` was the municipality's own ABSA bank account, read out of the banking details.
* `meter_number` was the literal string `METER TYPE` — a printed column heading.
* Six columns (start/end reading, total/peak/standard/off-peak kWh) all claimed the *same* span.
* The app's own accuracy said 90%, because it scored "did my rule fire consistently", never
  "is the answer right". A consistently wrong rule scored perfectly.

Four root causes:

1. **Rules were invented, not verified.** The model proposed `label_right "Supplier"`,
   `label_below "Your Account With Us"` and dozens more that appear nowhere in the documents. They
   were written into the ruleset untested and kept as fallbacks.
2. **No exclusivity.** Nothing stopped six columns binding to one number.
3. **Identity was a guess.** The family name was "the first two long words on page 1", so the live
   system grew 21 Capitec profiles — `capitec.cape-town`, `capitec.teplonull-anne`,
   `capitec.website-httpwwwpishoninfrastructurecoza` — for what is perhaps eight real layouts, and a
   match rule of `{all: ["ltd","pty"]}` that matches every South African invoice ever printed.
4. **No memory.** Every new municipality started from zero. 201 profile versions across 21 profiles,
   one municipality at a time, learning nothing transferable.

## The design

### 0. Identity before anything else

`identity(spans)` returns the things that are actually stable:

* **VAT number** (`VAT No.: 4000791881`) — the strongest identity a South African invoice carries.
* **Issuer line** — the organisation on the letterhead, not the first long words.
* **Layout skeleton** — the set of text lines that appear *identically in ≥ 80 % of the samples*.
  Those are the printed labels; everything else is data. The hash of that skeleton is the layout.

Family = layout skeleton, not issuer. This matters: Abaqulusi, Bela-Bela and Alfred Duma are three
municipalities on **one** billing system and must share **one** profile. Conversely two layouts from
the same issuer must split. The family carries a readable name and the list of issuers seen in it.

Match rules are *tested before they are saved*: a match rule must match its own family's samples and
must not match any other known family's samples. If it can't be made discriminative, the profile is
not saved — better no profile than one that hijacks another layout's documents.

### 1. Candidates — generate widely

For each column, propose candidates from:

* every static label in the skeleton × placement (value right of it / below it / left of it),
* the column's own label and hint, and synonyms,
* the regex library,
* **the lexicon** — every label that has ever worked for this column on any other layout,
* the model, when one is available.

The model is a *candidate generator*, never an authority. Its suggestions go through the same gate as
everything else. This is why a weaker local model is acceptable: it can only propose.

### 2. The evidence gate — the core fix

Every candidate is run against every sample and kept only if it earns its place:

| test | why |
|---|---|
| **fires** on ≥ 80 % of samples | a rule that doesn't fire is not a fallback, it is noise |
| **parses** as the column's type | `deposit = "1999/01/01"` is not money |
| **is not a heading** | the value must not itself be a line in the layout skeleton — this alone kills `meter_number = "METER TYPE"` |
| **variance matches expectation** | see below |
| **proximity** | of two labels that both work, the closer one wins |

**Variance is the signal that needs no model.** Across samples of one layout:

* identity-like columns (account number, meter number, issuer, VAT, deposit) should be **constant**
  per account — a candidate that changes every month is wrong;
* period columns (dates, readings, consumption, amounts) should **vary** — a candidate that returns
  the same number on every bill is wrong. `350000` on all six kWh columns dies here.

Anything below the floor is **discarded**, never written as a lower-ranked fallback.

### 3. Exclusive assignment

Columns compete for spans. Candidate (column, span) pairs are scored and assigned so that no span
serves two columns, unless the template itself says they are the same figure. Ties go to the column
whose label is nearer and whose type fits better.

### 4. Derivations must be acyclic and true

`total = excl + vat` is only written if it *holds arithmetically on the samples*. The dependency
graph is checked for cycles. The live system had
`labour_excl_vat = invoiced_total − vat`, `invoiced_total = labour_excl_vat + vat` and
`vat = labour_excl_vat × 0.15` all at once — three rules defining each other.

### 5. Stop, and say what is missing

Authoring converges: if two rounds bring no improvement it stops. Columns with no candidate that
passed the gate are reported as **not found** and go to a person. A blank cell costs a minute; a
wrong account number in a billing master sheet costs far more.

### 6. The lexicon — what makes #10 easier than #1

`cbx/pdfstudio/lexicon.py` + the `pdf_lexicon` table. One row per
(**column meaning**, label text, placement), with how many layouts it has been **confirmed** on, how
many it failed on, how many of those confirmations came from a person's fix, and an example value.

The row is keyed on the column's *meaning*, not its key: `lexicon.group_for()` maps
`account_number`, `acc_no`, `account_no` and `customer_account` all to `account`, so a label learned
under one key is offered under every other. Unrecognised columns fall back to their own key and
still learn. Ranking is confirmations minus failures, then anything learned before anything shipped,
then recency.

```python
candidates(db, column, *, limit=40) -> [{"label", "placement": "right"|"below"|"left",
                                         "confirmed", "failed", ...}]
confirm(db, *, column_key, label, placement, layout, example="")   # this worked here
reject(db,  *, column_key, label, placement, layout)               # this did not
```

`db=None` returns the shipped vocabulary. Nothing in the module raises: DB work runs inside a
savepoint and every failure falls back to the seeds, because a person's batch must never fail over
C-EXA's memory.

* **Written** at the end of every batch for each rule that read its column on ≥ 80 % of the layout's
  documents *and* whose column passed the 97 % gate on documents the profile was not built from —
  and whenever a person corrects a cell and the corrected value is found next to a printed label
  (a correction teaches the label, not just the answer). The label the failed rule used is marked
  **failed** in the same breath, so a wrong phrase stops being offered.
* Four things are refused before anything is written, all of them cheap and needing no model: a
  "value" printed on nearly every document of the layout (this is what stops
  `meter_number = "METER TYPE"` from being taught); a date, reading or amount that is identical on
  every bill; a clean date read into a text column; and a label that is not a phrase (a two-letter
  table code like `EL` is data sitting next to the value, not the heading that names it).
* **Read** at candidate generation for every new layout, ranked by confirmations.
* **Shipped seeded** with 135 entries in 23 groups — the printed label text of the real statements
  (`Account Number`, `Account Date`, `Tax Invoice No`, `Meter No`, `Old Reading`, `New Reading`,
  `Consumption`, `KL / Units`, `Total Due`, `Closing Balance`, `Deposit`, `VAT No`, `Tariff Code`,
  `Total Due On Or Before`…) plus the conventions South African vendor invoices use (`Invoice Date`,
  `Balance Due`, `Work Order`, `Purchase Order`). Each carries 1 or 2 starting confirmations — a
  prior, not a rival: a learned row inherits the seed's count, so the first real confirmation puts
  it in front of every shipped guess. Labels from a statement's banking-details block are
  deliberately absent.

This is the harness. It is the part that turns "C-EXA got Eskom right" into "C-EXA knows how municipal
bills work".

### 7. Honest accuracy

A profile's accuracy is measured on documents it was **not** authored from. Authoring on six bills
and scoring on those same six is how 52.7 % wrong was reported as 90 % right.

`repair.split()` holds about a third of each layout's documents back (at least one, evenly spread
through the batch rather than taken off the end). Authoring, the repair loop and every new profile
version see the training split only; the held-out documents are read once, at the end, with the
rules as they stand, and nothing about them changes the profile.

`repair.measure()` writes both numbers onto the version:

```
backtest = {docs, passed, accuracy, holdout_accuracy, by_column,
            trained_on, held_out, training_by_column, note}
```

`accuracy` is the flattering one and is kept only so the two can be shown side by side.
`holdout_accuracy` is what `Profile.accuracy` stores, what `SC.gates` is given, and what the
Profiles page shows ("97 % on bills it had not seen"). Under four documents nothing can be held
back: `holdout_accuracy` is `None`, `Profile.accuracy` is `None`, and every surface says **"too few
to check"** rather than quoting a number that only proves C-EXA remembers these.

A profile authored before held-out scoring existed keeps its old figure but is labelled
`training_only` — "never checked on bills it hadn't seen" — until the next batch re-measures it.

Note what this does **not** fix: the held-out score is still computed with `scoring.py`, which marks
"did my rule fire and parse", not "is the answer right". Splitting train from test removes the leak;
making the score mean correctness is the evidence gate's job (§2).

## The bench

`/home/claude/bench` (development only) trains on N real bills of one layout and scores the authored
ruleset against a hand-read gold set on held-out bills, including two municipalities never seen in
training. `python3 bench.py` prints correct / wrong / blank. Every change to authoring is judged by
that number, and by wrong going down faster than blank goes up.
