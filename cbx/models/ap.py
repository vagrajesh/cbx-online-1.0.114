# 1.0.98: from CBX-Connect 2.10.3 cbx/models/ap.py (the 2.10.0 capture and 2.10.3 SAP knowledge tables; all new tables)
"""The AP Tool (2.8.6): a folder of AP files in, the AP team reads, checks and sorts every file; what a bot can't
place goes up the chain (the bot → Paula → AXL → a person) and people clear what the checks stopped.

    ApBatch   one folder dropped (its files, its counts, its Excel report)
    ApItem    one file: what it is, which bot has it, the fields read, the findings, where it sits in the chain,
              who checked and who approved (two different people on payment runs and bank details)
    ApVendor  what the team knows about a supplier, learned only from items people signed off
    ApSeen    invoices seen and items paid, to catch duplicates and "paid already" across folders
    ApRule    what a person taught the team when they fixed an item ("files with X are Y", "X's VAT number is …")

The same tables as CBX-Connect 2 (1.0.95; 1.0.98 adds the 2.10.3 ones), so CBX-Connect 2 can take them as they are.

2.10.0 invoice capture (Edwin, 15 Oct): municipal/Eskom/Telkom bills and manual invoices in one folder → sorted into
buckets → read (PDF Studio's profiles, or the capture bots with vision) → matched to the master lists → the SAP upload.

    ApEntity          the company codes (Edwin's Stat Company Codes list): who a bill is addressed to
    ApUtilityAccount  the utilities list (the Harvester's vendor_mappings / Utilities_Master_List): account → vendor, PID/CC, site
    ApVendorCode      the vendors of manual invoices: vendor BP, GL, cost centre, tax code — kept by admins, learned from people
    ApDocType         SAP document types (KR invoice, KG credit memo, KZ payment …)
    ApSapValue        the Harvester's sap_config.xlsx: the Fixed values and the GL Codes
    ApCapture         one file's way through capture: its bucket, who has it, what was read, the match, its SAP rows and test

2.10.3 SAP knowledge (Edwin, 16 Oct: "here is some knowledge … it needs to know what to use where"):

    ApDocType         now the whole SAP Document Type List (75 types: account types, number range, reverse type), Edwin's
                      meanings kept; where his meaning and SAP's description disagree the row says "Check" until an admin confirms
    ApGlAccount       the chart of accounts (G/L account, long text): Tess refuses a G/L that is not in it
    ApVendorCompany   which company codes each SAP vendor is opened in (the Vendor Master - Comp Code export)
    ApCostCentre      SAP's cost centres per company code (the Cost Center List): currency, profit centre, description

1.0.102 (Edwin, 18 Oct: posting date, cost centre per site, G/L per line, due date from payment terms, SAP's vendor list):

    ApBatch.posting_date   the SAP rows' posting date ("" = today: the day of the download)
    ApVendorCode           + line_rules (words → G/L, cost centre, tax code) and payment_days
    ApSite                 a bill-to's site code → its cost centre (per company code, or any)
    ApSapVendor            SAP's vendor general data (LFA1): vendor number, name, VAT number, blocked, payment terms

1.0.103 (Edwin, 19 Oct: the Supplier Master, the EFM cost centres, two months of vendor and G/L line items; CBX-Connect 2.10.8):

    ApSapVendor.account_group  KTOKK
    ApPosted               a vendor document SAP posted (the duplicate check), with its expense lines
    ApVendorHistory        how SAP coded each vendor per company code (Pip codes an uncoded vendor from it)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, UTCDateTime, utcnow

STATUSES = ("new", "ok", "check", "exception", "unknown", "waiting", "cleared", "rejected")
LEVELS = {0: "Bot", 1: "Paula", 2: "AXL", 3: "Needs you"}


class ApBatch(Base):
    __tablename__ = "ap_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default="reading")  # reading | ready | failed
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    report_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # 1.0.102: the SAP rows' posting date — "" = today (the day of the download), "yyyy-mm-dd" = a day a person set
    posting_date: Mapped[str] = mapped_column(String(10), default="")
    posting_date_by: Mapped[str] = mapped_column(String(160), default="")
    # 1.0.104: the portfolio (cbx/ap/portfolio.py): picked in the drop form ("picked"), or "" / "auto" = each document's own
    # from its utility account or company code (EFM-SA when nothing matches)
    portfolio: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="")
    portfolio_how: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, default="")
    # 1.0.104 (Builder R, V05 cbx/ap/review.py): the seed of the batch's random review sample (kept so it can be repeated)
    review_seed: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApItem(Base):
    __tablename__ = "ap_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ap_batches.id"), index=True)
    file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stored_files.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    kind: Mapped[str] = mapped_column(String(24), default="unknown")
    bot: Mapped[str] = mapped_column(String(16), default="paula")
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    why: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="new")
    level: Mapped[int] = mapped_column(Integer, default=0)  # 0 bot · 1 Paula · 2 AXL · 3 needs you
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    findings: Mapped[list] = mapped_column(JSON, default=list)
    history: Mapped[list] = mapped_column(JSON, default=list)  # [{at, who, text}]
    suggestion: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # AXL's reading, for a person to confirm
    reply: Mapped[str] = mapped_column(Text, default="")  # Quinn's draft answer
    stops: Mapped[int] = mapped_column(Integer, default=0)
    checks: Mapped[int] = mapped_column(Integer, default=0)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    supplier: Mapped[str] = mapped_column(String(160), default="")
    ref: Mapped[str] = mapped_column(String(120), default="")
    checked_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    checked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    approved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # 1.0.103: which bot is on this file right now (cbx/busy.py): name, step, since, last sign of life (cbx/migrate.py adds them)
    busy_bot: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    busy_step: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    busy_since: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    busy_beat: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApVendor(Base):
    __tablename__ = "ap_vendors"
    id: Mapped[int] = mapped_column(primary_key=True)
    vkeys: Mapped[str] = mapped_column(String(500), default="", index=True)  # "|vat:…|v:…|n:…|"
    name: Mapped[str] = mapped_column(String(160), default="")
    vat: Mapped[str] = mapped_column(String(20), default="")
    vendor_no: Mapped[str] = mapped_column(String(40), default="")
    bank_account: Mapped[str] = mapped_column(String(40), default="")
    bank: Mapped[str] = mapped_column(String(60), default="")
    branch: Mapped[str] = mapped_column(String(12), default="")
    last_run_total: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    verified_by: Mapped[str] = mapped_column(String(160), default="")  # the two people who signed off the bank details
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApSeen(Base):
    __tablename__ = "ap_seen"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), default="invoice")  # invoice | paid
    vkeys: Mapped[str] = mapped_column(String(500), default="")
    ref: Mapped[str] = mapped_column(String(120), default="")
    ref_key: Mapped[str] = mapped_column(String(120), default="", index=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    on_date: Mapped[str] = mapped_column(String(10), default="")
    batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApRule(Base):
    __tablename__ = "ap_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    match: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(24), default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    by_name: Mapped[str] = mapped_column(String(160), default="")
    uses: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- 2.10.0: invoice capture -------------------------------------------------------------------------------------
BUCKETS = ("utility", "manual", "other", "odd", "ap", "skipped")
# where a file is in capture: Sol sorts, Uma reads utilities, the capture bots (Cara, Dex, Eli) and Seth read manual
# invoices, Mia matches, Pip builds the SAP rows, Tess tests them; "needs" = a person (see ApCapture.needs)
STAGES = ("sort", "read", "queued", "capturing", "senior", "senior_busy", "match", "sap", "test", "ready", "needs", "skipped", "ap")


class ApEntity(Base):
    __tablename__ = "ap_entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True)  # X143
    name: Mapped[str] = mapped_column(String(160), default="")  # Company Name
    entity: Mapped[str] = mapped_column(String(160), default="")  # Entity Name
    aliases: Mapped[str] = mapped_column(Text, default="")  # other names bills are addressed to, one per line (learned from people too)
    vat_no: Mapped[str] = mapped_column(String(20), default="")
    doc_type: Mapped[str] = mapped_column(String(4), default="")  # "" = the Fixed DocType (KR); an admin may set YR for this company
    doc_type_credit: Mapped[str] = mapped_column(String(4), default="")  # "" = the Fixed DocTypeCredit (KG); or YG
    source: Mapped[str] = mapped_column(String(16), default="seed")  # seed | excel | sap | person
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApUtilityAccount(Base):
    __tablename__ = "ap_utility_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(40), default="", index=True)  # digits only, leading zeros gone (the Harvester's _sap_norm_acct)
    account: Mapped[str] = mapped_column(String(60), default="")  # as the list gives it
    vendor: Mapped[str] = mapped_column(String(40), default="")  # VENDOR/BP NUMBER (Supplier)
    cost_centre: Mapped[str] = mapped_column(String(40), default="")  # PID/CC
    site: Mapped[str] = mapped_column(String(160), default="")  # NAME1 (Site Name)
    name3: Mapped[str] = mapped_column(String(80), default="")
    municipality: Mapped[str] = mapped_column(String(160), default="")  # MUNICIPALITY/PAYEE (Vendor Name)
    company_code: Mapped[str] = mapped_column(String(12), default="")  # "" = the Fixed Company, as the Harvester posted
    extra: Mapped[dict] = mapped_column(JSON, default=dict)  # the list's other columns (district, region …)
    source: Mapped[str] = mapped_column(String(16), default="excel")  # excel | sap | person | admin | seed (1.0.101: the Harvester's list, shipped)
    updated_by: Mapped[str] = mapped_column(String(160), default="")
    portfolio: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="")  # 1.0.104: "" = the portfolio of its company code
    # 1.0.104 (V10, cbx/ap/completeness.py): the account's status with its effective date and where it came from, Shell's
    # CO/DO, Telkom's cluster, a check meter on site, the day of the month its bill usually comes (None = learned from history)
    status: Mapped[Optional[str]] = mapped_column(String(12), nullable=True, default="")  # "" = active · active · suspended · terminated · disposed
    status_from: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default="")  # yyyy-mm-dd
    status_source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, default="")  # "Telkom Termination report 2026-10-19", "Jane Doe"
    co_do: Mapped[Optional[str]] = mapped_column(String(4), nullable=True, default="")  # CO company-operated · DO dealer-operated (Shell)
    cluster: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="")  # Telkom 1–3
    check_meter: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    bill_day: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApVendorCode(Base):
    __tablename__ = "ap_vendor_codes"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_no: Mapped[str] = mapped_column(String(40), default="")  # SAP vendor / BP number
    name: Mapped[str] = mapped_column(String(160), default="")
    name_key: Mapped[str] = mapped_column(String(160), default="", index=True)  # the name squeezed (no spaces, no Pty Ltd)
    vat_no: Mapped[str] = mapped_column(String(20), default="", index=True)
    aliases: Mapped[str] = mapped_column(Text, default="")
    gl: Mapped[str] = mapped_column(String(20), default="")
    cost_centre: Mapped[str] = mapped_column(String(40), default="")
    tax_code: Mapped[str] = mapped_column(String(8), default="")
    company_code: Mapped[str] = mapped_column(String(12), default="")  # "" = any company
    item_text: Mapped[str] = mapped_column(String(60), default="")  # 2.10.3: the G/L lines' text (SGTXT), e.g. "{site} {description}"; "" = the site goes in the header text
    # 1.0.102: line rules — [{words, gl, cost_centre, tax_code}]: a line whose description has the words posts to that G/L
    # (cost centre, tax code); lines no rule fits take the coding's G/L. payment_days: due = invoice date + these days
    # when the invoice prints no due date
    line_rules: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    payment_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    learned: Mapped[bool] = mapped_column(Boolean, default=False)  # a person coded it from Needs you (not from a list)
    learned_by: Mapped[str] = mapped_column(String(160), default="")
    learned_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="excel")  # excel | sap | person
    uses: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApDocType(Base):
    __tablename__ = "ap_doc_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(4), unique=True)  # KR
    label: Mapped[str] = mapped_column(String(80), default="")  # Invoice
    meaning: Mapped[str] = mapped_column(String(16), default="invoice")  # invoice | credit | payment | clearing | journal | migration | other
    note: Mapped[str] = mapped_column(String(200), default="")
    # 2.10.3: what SAP's Document Type List says (cbx/knowledge_data/sap_doc_types.csv, or an admin's upload)
    description: Mapped[str] = mapped_column(String(80), default="")  # SAP's Description: "Vendor Invoice"
    account_types: Mapped[str] = mapped_column(String(8), default="")  # A assets · D customer · K vendor · M material · S G/L
    number_range: Mapped[str] = mapped_column(String(8), default="")
    reverse: Mapped[str] = mapped_column(String(4), default="")  # the reverse document type
    sap: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # every column of SAP's list for this type, as SAP gives it
    source: Mapped[str] = mapped_column(String(8), default="person")  # person (Edwin's / an admin's row) | sap (added from SAP's list)
    needs_check: Mapped[bool] = mapped_column(Boolean, default=False)  # Edwin's meaning and SAP's description disagree
    check_note: Mapped[str] = mapped_column(String(300), default="")
    checked_by: Mapped[str] = mapped_column(String(160), default="")
    checked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApSapValue(Base):
    __tablename__ = "ap_sap_values"
    __table_args__ = (UniqueConstraint("kind", "key", name="uq_ap_sap_value"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # fixed | gl
    key: Mapped[str] = mapped_column(String(40))  # TransCode31 … / electricity, vat, water …
    value: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApCapture(Base):
    __tablename__ = "ap_captures"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("ap_items.id"), unique=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ap_batches.id"), index=True)
    bucket: Mapped[str] = mapped_column(String(12), default="")  # utility | manual | other | odd | ap | skipped
    stage: Mapped[str] = mapped_column(String(12), default="sort", index=True)  # see STAGES
    needs: Mapped[str] = mapped_column(String(12), default="")  # entity | odd | unread | vendor | profile — what a person must do
    worker: Mapped[str] = mapped_column(String(8), default="")  # the bot that has it (or had it last)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    profile: Mapped[str] = mapped_column(String(64), default="")  # the municipal profile that knows the bill
    read: Mapped[dict] = mapped_column(JSON, default=dict)  # what the reader got (the profile's rows, the capture bot's answer)
    fields: Mapped[dict] = mapped_column(JSON, default=dict)  # the invoice as it posts: supplier, numbers, dates, lines, totals
    confidence: Mapped[int] = mapped_column(Integer, default=0)
    read_by: Mapped[str] = mapped_column(String(8), default="")  # uma | cara | dex | eli | seth | person
    tries: Mapped[int] = mapped_column(Integer, default=0)
    match: Mapped[dict] = mapped_column(JSON, default=dict)  # {entity, account, vendor}: what Mia found and how
    company_code: Mapped[str] = mapped_column(String(12), default="")
    vendor_no: Mapped[str] = mapped_column(String(40), default="")
    bill_to_site: Mapped[str] = mapped_column(String(60), default="")  # 2.10.3: the part after " - " in the bill-to (a client or site code: RIO2X21)
    ref_key: Mapped[str] = mapped_column(String(120), default="", index=True)  # the invoice number squeezed, for duplicates
    sap_rows: Mapped[list] = mapped_column(JSON, default=list)
    sap_status: Mapped[str] = mapped_column(String(8), default="")  # green | yellow | red
    sap_issues: Mapped[list] = mapped_column(JSON, default=list)  # [{level, text}]
    sorted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    matched_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    built_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    tested_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # 1.0.103: which bot is on this document right now (cbx/busy.py): name, step, since, last sign of life (cbx/migrate.py adds them)
    busy_bot: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    busy_step: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, default="")
    busy_since: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    busy_beat: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    # 1.0.104: the portfolio the document posts under (its batch's when picked, else its account's or company code's)
    portfolio: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="", index=True)
    # 1.0.104 (Builder R, V05 cbx/ap/review.py): the independent review — selected at Tess time with the reasons, then a
    # reviewer (never someone who touched it) agrees, corrects or rejects; review_hash = the SAP rows as reviewed (a change
    # after the review selects it again); review_errors = the fields the reviewer found wrong (the coverage report)
    review_state: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default="")  # "" · selected · agreed · corrected · rejected
    review_why: Mapped[Optional[str]] = mapped_column(String(400), nullable=True, default="")
    review_selected_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    review_errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    review_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default="")
    # 1.0.104 (Builder R, V06 cbx/ap/vatcheck.py): is it a valid tax invoice (s20 of the VAT Act)? None = not checked (no VAT,
    # a credit note, not in rand); when it isn't, a person decides: claim · no_claim (posted with the no-claim tax code)
    vat_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    vat_reasons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    vat_decision: Mapped[Optional[str]] = mapped_column(String(10), nullable=True, default="")
    vat_decided_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vat_decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    vat_decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="")
    # 1.0.105 (Builder G, cbx/ap/autofill.py): {field: how} of the values CBX guessed and is not sure of (the yellow cells)
    guesses: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ---- 2.10.3: SAP knowledge ------------------------------------------------------------------------------------------
class ApGlAccount(Base):
    __tablename__ = "ap_gl_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(String(20), unique=True)  # as SAP gives it: SA110, JA600, HB000ZBEB5
    account_key: Mapped[str] = mapped_column(String(20), default="", index=True)  # upper case, no spaces, no leading zeros
    name: Mapped[str] = mapped_column(String(200), default="")  # G/L Acct Long Text
    source: Mapped[str] = mapped_column(String(8), default="seed")  # seed | upload | sap
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApVendorCompany(Base):
    """One SAP vendor opened in one company code (117 855 pairs in Edwin's export): kept lean for a fast bulk load."""
    __tablename__ = "ap_vendor_companies"
    __table_args__ = (UniqueConstraint("vendor", "company_code", name="uq_ap_vendor_company"),
                      Index("ix_ap_vendor_companies_company", "company_code", "vendor"))
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(20))  # as SAP gives it (text: leading zeros kept)
    company_code: Mapped[str] = mapped_column(String(12))


class ApCostCentre(Base):
    """One SAP cost centre in one company code (the same number exists in many company codes): ~24 400 rows."""
    __tablename__ = "ap_cost_centres"
    __table_args__ = (UniqueConstraint("cost_centre", "company_code", name="uq_ap_cost_centre"),
                      Index("ix_ap_cost_centres_company", "company_code", "cost_centre"))
    id: Mapped[int] = mapped_column(primary_key=True)
    cost_centre: Mapped[str] = mapped_column(String(20))  # as SAP gives it (text)
    company_code: Mapped[str] = mapped_column(String(12))
    currency: Mapped[str] = mapped_column(String(5), default="")
    profit_centre: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(String(120), default="")


# ---- 1.0.102: sites and SAP's vendor general data ---------------------------------------------------------------------------
class ApSite(Base):
    """A site code as bills are addressed to it ("… (Pty) Ltd - RIO2X21") → its cost centre (for one company code, or any)."""
    __tablename__ = "ap_sites"
    __table_args__ = (UniqueConstraint("site_code", "company_code", name="uq_ap_site"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site_code: Mapped[str] = mapped_column(String(60), index=True)  # upper case, as it follows " - " in the bill-to
    company_code: Mapped[str] = mapped_column(String(12), default="")  # "" = any company
    cost_centre: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(16), default="person")  # excel | sap | person
    updated_by: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApSapVendor(Base):
    """SAP's vendor general data (LFA1: LIFNR, NAME1, STCEG / STCD1, ORT01, LAND1, SPERR / LOEVM, ZTERM): Mia finds a
    supplier's vendor number by its VAT number."""
    __tablename__ = "ap_sap_vendors"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(20), unique=True)  # LIFNR, as SAP gives it (text: leading zeros kept)
    name: Mapped[str] = mapped_column(String(160), default="")  # NAME1 (+ NAME2)
    name_key: Mapped[str] = mapped_column(String(160), default="", index=True)
    vat: Mapped[str] = mapped_column(String(20), default="", index=True)  # digits only (STCEG, or a VAT number in STCD1/STCD2)
    tax1: Mapped[str] = mapped_column(String(40), default="")  # STCD1 as SAP gives it
    city: Mapped[str] = mapped_column(String(60), default="")
    country: Mapped[str] = mapped_column(String(4), default="")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)  # SPERR or LOEVM set
    payment_terms: Mapped[str] = mapped_column(String(8), default="")  # ZTERM
    source: Mapped[str] = mapped_column(String(8), default="excel")  # excel | sap | seed (1.0.103: Edwin's Supplier Master, shipped)
    account_group: Mapped[str] = mapped_column(String(8), default="")  # 1.0.103: KTOKK (ZMGN, ZVIN …)
    # 1.0.104 (Builder D, V01): created on / by and changed on (ERDAT, ERNAM, AEDAT) — None: the list had no such column
    # (the shipped seed has none, so the seeded master never opens a review)
    created_on: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # yyyy-mm-dd
    created_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # ERNAM (SAP user)
    changed_on: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # yyyy-mm-dd


# ---- 1.0.103: what SAP already posted (Edwin's FBL1N / FBL3N exports) -----------------------------------------------------------
class ApPosted(Base):
    """One vendor document SAP posted (FBL1N: invoices, credit memos, journals to the vendor; payments and clearing are
    not kept): the duplicate check reads it. `lines` are its expense lines from the G/L items (FBL3N: posting key 40/50,
    not VAT, not the vendor's reconciliation line) — [{gl, pc, amount, text}]. Document numbers repeat across company
    codes: the key is (company code, document number)."""
    __tablename__ = "ap_posted"
    __table_args__ = (UniqueConstraint("company_code", "doc_number", name="uq_ap_posted"),
                      Index("ix_ap_posted_company_vendor", "company_code", "vendor"))
    id: Mapped[int] = mapped_column(primary_key=True)
    company_code: Mapped[str] = mapped_column(String(12))
    vendor: Mapped[str] = mapped_column(String(20), default="")
    reference: Mapped[str] = mapped_column(String(40), default="")  # XBLNR as printed
    ref_key: Mapped[str] = mapped_column(String(40), default="", index=True)  # A–Z 0–9 only, leading zeros gone
    doc_type: Mapped[str] = mapped_column(String(4), default="")
    doc_number: Mapped[str] = mapped_column(String(20))
    posting_date: Mapped[str] = mapped_column(String(10), default="")  # yyyy-mm-dd
    doc_date: Mapped[str] = mapped_column(String(10), default="")
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # an invoice is positive, a credit memo negative
    currency: Mapped[str] = mapped_column(String(5), default="")
    tax_code: Mapped[str] = mapped_column(String(8), default="")
    terms: Mapped[str] = mapped_column(String(8), default="")
    text: Mapped[str] = mapped_column(String(80), default="")
    vendor_lines: Mapped[int] = mapped_column(Integer, default=1)  # the vendor line items it was made of
    lines: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    source: Mapped[str] = mapped_column(String(8), default="seed")  # seed | excel


class ApVendorHistory(Base):
    """1.0.103: how SAP coded a vendor in one company code (learned from ap_posted): the G/Ls, cost centres, profit
    centres, tax codes and payment terms with how many documents used each, the G/L + cost centre pairs (a document
    split over several pairs counts in `split`), the number of documents, the last posting date and two sample texts."""
    __tablename__ = "ap_vendor_history"
    __table_args__ = (UniqueConstraint("company_code", "vendor", name="uq_ap_vendor_history"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_code: Mapped[str] = mapped_column(String(12))
    vendor: Mapped[str] = mapped_column(String(20), index=True)
    docs: Mapped[int] = mapped_column(Integer, default=0)  # invoices and credit memos with expense lines
    split: Mapped[int] = mapped_column(Integer, default=0)
    last_posting: Mapped[str] = mapped_column(String(10), default="")
    gls: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    cost_centres: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    profit_centres: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    pairs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {"G/L|cost centre": documents}
    tax_codes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    terms: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    texts: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)


# ---- 1.0.104: the Valuence AP controls' foundations (F0a portfolios, F0b AP roles) ---------------------------------------
class ApPortfolio(Base):
    """1.0.104: a portfolio (EFM-SA, GENSEC, SHELL, TELKOM, SWIFTNET, EGYPT …): its company codes, its overrides on top of
    the global Fixed and G/L tables Pip uses, and its control settings (cbx/ap/portfolio.py: DEFAULTS)."""
    __tablename__ = "ap_portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)  # SHELL
    name: Mapped[str] = mapped_column(String(160), default="")
    company_codes: Mapped[str] = mapped_column(Text, default="")  # "T100, T101, T102" — a range as "XA00-XA60"
    fixed: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {"Company": "T100"} on top of the Fixed table
    gl: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {"electricity": "SA101"} on top of the GL Codes
    settings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {"variance_pct": 15, …}: only what differs from DEFAULTS
    notes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {"variance_pct": "confirm with the Senior FM"}
    note: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # EFM-SA: anything nothing else matches
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApRole(Base):
    """1.0.104: one person's AP role in one portfolio ("" = every portfolio): capturer · reviewer · poster · approver ·
    recon_reviewer (cbx/ap/roles.py)."""
    __tablename__ = "ap_roles"
    __table_args__ = (UniqueConstraint("user_id", "portfolio", "role", name="uq_ap_role"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    portfolio: Mapped[str] = mapped_column(String(20), default="", index=True)
    role: Mapped[str] = mapped_column(String(20))
    granted_by: Mapped[str] = mapped_column(String(160), default="")
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- 1.0.104 (Builder U): V07 consumption/tariff checks, V08 estimates — logic in cbx/ap/bill_history.py, variance.py,
# estimates.py ---------------------------------------------------------------------------------------------------------
class ApBillHistory(Base):
    """1.0.104: one utility bill per account and period, filled by every utility capture (and a one-time backfill from
    the captures already read): the variance checks compare a new bill with these, the municipal roll-forward reads
    them (`cbx/ap/bill_history.periods`). Amounts are excl. VAT, per service ({"electricity": 1234.5, "water": …})."""
    __tablename__ = "ap_bill_history"
    __table_args__ = (Index("ix_ap_bill_history_acct_period", "account_key", "period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(40), default="", index=True)  # as ApUtilityAccount.account_key
    portfolio: Mapped[str] = mapped_column(String(20), default="")
    municipality: Mapped[str] = mapped_column(String(160), default="")
    profile: Mapped[str] = mapped_column(String(64), default="")
    period: Mapped[str] = mapped_column(String(7), default="")  # yyyy-mm (the month the reading ends in, else the bill date's)
    bill_date: Mapped[str] = mapped_column(String(10), default="")
    invoice_no: Mapped[str] = mapped_column(String(60), default="")
    read_start: Mapped[str] = mapped_column(String(10), default="")  # yyyy-mm-dd
    read_end: Mapped[str] = mapped_column(String(10), default="")
    days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kwh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    kl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amounts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {service: R excl. VAT}
    total_excl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # the current charges excl. VAT
    vat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reading_type: Mapped[str] = mapped_column(String(4), default="")  # A actual · E estimated · "" unknown
    tariff: Mapped[str] = mapped_column(String(120), default="")
    opening: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # balance brought forward, when the profile reads it
    arrears: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closing: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # amount due / closing balance
    capture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, unique=True)
    source: Mapped[str] = mapped_column(String(10), default="capture")  # capture | backfill | import
    # V07: what the checks found ([{code, text, figures}]) and what a person decided about it
    findings: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)
    flags: Mapped[int] = mapped_column(Integer, default=0, index=True)  # len(findings): the Variances view's filter
    decision: Mapped[str] = mapped_column(String(10), default="")  # "" · accepted · disputed
    decision_reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(160), default="")
    decided_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    dispute_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApTariff(Base):
    """1.0.104: a tariff row (Settings → Tariffs): a profile or municipality's named tariff, effective from/to, c/kWh
    (optional blocks / time-of-use), fixed charges per month. The rate check prefers a row here over the account's own history."""
    __tablename__ = "ap_tariffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    profile: Mapped[str] = mapped_column(String(64), default="")
    municipality: Mapped[str] = mapped_column(String(160), default="")
    tariff: Mapped[str] = mapped_column(String(120), default="")
    effective_from: Mapped[str] = mapped_column(String(10), default="")  # yyyy-mm-dd
    effective_to: Mapped[str] = mapped_column(String(10), default="")  # "" = open
    c_per_kwh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    blocks: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # [{upto_kwh, c_per_kwh}] or [{tou, c_per_kwh}]
    fixed_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # fixed charges per month, R excl. VAT
    note: Mapped[str] = mapped_column(String(300), default="")
    updated_by: Mapped[str] = mapped_column(String(160), default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApDispute(Base):
    """1.0.104: a bill a person disputed with the municipality (V07): held out of the SAP upload unless "pay under dispute"."""
    __tablename__ = "ap_disputes"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(40), default="", index=True)
    municipality: Mapped[str] = mapped_column(String(160), default="")
    portfolio: Mapped[str] = mapped_column(String(20), default="")
    period: Mapped[str] = mapped_column(String(7), default="")
    capture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    history_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # the amount in dispute
    reason: Mapped[str] = mapped_column(Text, default="")
    municipal_ref: Mapped[str] = mapped_column(String(80), default="")
    owner: Mapped[str] = mapped_column(String(160), default="")  # the Council Liaison owner
    status: Mapped[str] = mapped_column(String(12), default="open")  # open · resolved · withdrawn
    pay_under_dispute: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(160), default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    closed_by: Mapped[str] = mapped_column(String(160), default="")
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApCheckMeter(Base):
    """1.0.104: a check-meter reading (Telkom's BCX readings CSV: site, period, kWh) to compare with the billed kWh."""
    __tablename__ = "ap_check_meters"
    __table_args__ = (UniqueConstraint("site", "period", name="uq_ap_check_meter"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    site: Mapped[str] = mapped_column(String(160), default="", index=True)  # as the CSV gives it (site code or name)
    account_key: Mapped[str] = mapped_column(String(40), default="", index=True)  # when the CSV names the account
    period: Mapped[str] = mapped_column(String(7), default="")
    kwh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(160), default="")
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApEstimate(Base):
    """1.0.104: an estimate for an account whose bill did not come (V08): proposed from ap_bill_history by the basis
    setting, prepared by one person and approved by another; posted through Pip as EST-<account>-<yyyymm>; trued up when
    the real bill for that account and period arrives."""
    __tablename__ = "ap_estimates"
    __table_args__ = (Index("ix_ap_estimates_acct_period", "account_key", "period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(40), default="")
    account: Mapped[str] = mapped_column(String(60), default="")
    site: Mapped[str] = mapped_column(String(160), default="")
    municipality: Mapped[str] = mapped_column(String(160), default="")
    portfolio: Mapped[str] = mapped_column(String(20), default="")
    period: Mapped[str] = mapped_column(String(7), default="")
    basis: Mapped[str] = mapped_column(String(10), default="avg6")  # last · avg3 · avg6 · daily · prepaid
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # R excl. VAT
    vat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    kwh: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    workings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # the periods and figures used
    status: Mapped[str] = mapped_column(String(12), default="prepared")  # prepared · approved · rejected · trued_up
    prepared_by: Mapped[str] = mapped_column(String(160), default="")
    prepared_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prepared_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(160), default="")
    approved_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    capture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the EST- document Pip built
    actual_capture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the real bill (true-up)
    actual_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    difference: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # actual − estimate (excl. VAT)
    true_up: Mapped[str] = mapped_column(String(10), default="")  # adjust · reverse (the portfolio setting at true-up)
    trued_up_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reversal_capture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the EST-…-R / -ADJ credit memo
    escalated_by: Mapped[str] = mapped_column(String(160), default="")  # sent to Council Liaison (N estimates running)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    history: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # [{at, who, text}]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


# ---- 1.0.104 (Builder C): V10 completeness (cbx/ap/completeness.py), SAP payments (cbx/ap/payments.py), V11 reconciliations
# (cbx/ap/recons.py) ---------------------------------------------------------------------------------------------------
class ApMasterlistImport(Base):
    """1.0.104: one masterlist refresh for a portfolio (Shell's monthly masterlist, Telkom's SAP Masterfile / Termination /
    Anomaly report, Swiftnet's Utility Master List): when, from what, by whom, and what the diff found."""
    __tablename__ = "ap_masterlist_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio: Mapped[str] = mapped_column(String(20), default="", index=True)
    kind: Mapped[str] = mapped_column(String(16), default="masterlist")  # masterlist · terminations · anomalies
    source: Mapped[str] = mapped_column(String(160), default="")  # what the person called it ("Telkom SAP Masterfile Oct 2026")
    file_name: Mapped[str] = mapped_column(String(255), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    by: Mapped[str] = mapped_column(String(160), default="")
    by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApMasterlistException(Base):
    """1.0.104: one difference a masterlist import found (on the list but not in CBX / not in SAP's vendors; in CBX but not
    on the list; terminated on the list but active in CBX; terminated but still billed or paid; CO→DO …), resolved by a
    person with a note."""
    __tablename__ = "ap_masterlist_exceptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    portfolio: Mapped[str] = mapped_column(String(20), default="", index=True)
    kind: Mapped[str] = mapped_column(String(20), default="")  # see completeness.EXCEPTIONS
    account_key: Mapped[str] = mapped_column(String(40), default="", index=True)
    account: Mapped[str] = mapped_column(String(60), default="")
    vendor: Mapped[str] = mapped_column(String(40), default="")
    site: Mapped[str] = mapped_column(String(160), default="")
    text: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(10), default="open")  # open · resolved
    note: Mapped[str] = mapped_column(Text, default="")
    resolved_by: Mapped[str] = mapped_column(String(160), default="")
    resolved_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApPayment(Base):
    """1.0.104: one payment document SAP posted to a vendor (FBL1N document types KZ / ZV / ZP), kept apart from ap_posted
    (the duplicate check never sees payments). `amount` is what was paid (positive). `allocations` says which utility
    accounts it paid ({account_key: R}), from the invoices it cleared or the account number in its reference."""
    __tablename__ = "ap_payments"
    __table_args__ = (UniqueConstraint("company_code", "doc_number", name="uq_ap_payment"),
                      Index("ix_ap_payments_vendor", "company_code", "vendor"))
    id: Mapped[int] = mapped_column(primary_key=True)
    company_code: Mapped[str] = mapped_column(String(12))
    vendor: Mapped[str] = mapped_column(String(20), default="")
    doc_number: Mapped[str] = mapped_column(String(20))
    doc_type: Mapped[str] = mapped_column(String(4), default="")
    posting_date: Mapped[str] = mapped_column(String(10), default="", index=True)
    doc_date: Mapped[str] = mapped_column(String(10), default="")
    clearing_doc: Mapped[str] = mapped_column(String(20), default="")
    clearing_date: Mapped[str] = mapped_column(String(10), default="")
    reference: Mapped[str] = mapped_column(String(40), default="")
    assignment: Mapped[str] = mapped_column(String(40), default="")
    text: Mapped[str] = mapped_column(String(80), default="")
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(5), default="")
    account_key: Mapped[str] = mapped_column(String(40), default="", index=True)  # the one account it paid, when only one
    allocations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)
    source: Mapped[str] = mapped_column(String(8), default="excel")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ApRecon(Base):
    """1.0.104: one reconciliation in the month's population (a utility account, or a vendor when account_key is ""):
    why it is in the population, the statement and ledger balances, the preparer and the reviewer (a recon reviewer who
    did not prepare it)."""
    __tablename__ = "ap_recons"
    __table_args__ = (UniqueConstraint("portfolio", "period", "account_key", "vendor", name="uq_ap_recon"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio: Mapped[str] = mapped_column(String(20), default="", index=True)
    period: Mapped[str] = mapped_column(String(7), default="", index=True)  # yyyy-mm
    account_key: Mapped[str] = mapped_column(String(40), default="")
    account: Mapped[str] = mapped_column(String(60), default="")
    vendor: Mapped[str] = mapped_column(String(40), default="")
    company_code: Mapped[str] = mapped_column(String(12), default="")
    name: Mapped[str] = mapped_column(String(200), default="")  # site · municipality, or the vendor's name
    why: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # the population rules that picked it
    roll: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # the roll-forward: prev closing, paid, opening, difference
    statement_balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ledger_balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    difference: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open · prepared · reviewed · returned
    preparer: Mapped[str] = mapped_column(String(160), default="")
    preparer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prepared_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reviewer: Mapped[str] = mapped_column(String(160), default="")
    reviewer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    editors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # user ids who changed its balances or items: never its reviewer
    history: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # [{at, who, text}]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ApReconItem(Base):
    """1.0.104: a reconciling item: credit to recover · possible duplicate · missing invoice · unallocated payment ·
    roll-forward difference · other — with its owner and target date until it is cleared."""
    __tablename__ = "ap_recon_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    recon_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(20), default="other")
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    text: Mapped[str] = mapped_column(String(400), default="")
    key: Mapped[str] = mapped_column(String(80), default="")  # an item the rules raised: raised once (e.g. "roll:2026-09")
    owner: Mapped[str] = mapped_column(String(160), default="")
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    target_date: Mapped[str] = mapped_column(String(10), default="")  # yyyy-mm-dd
    cleared: Mapped[bool] = mapped_column(Boolean, default=False)
    cleared_note: Mapped[str] = mapped_column(Text, default="")
    cleared_by: Mapped[str] = mapped_column(String(160), default="")
    cleared_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- 1.0.104 (Builder R): V12 evidence — logic in cbx/ap/evidence.py ---------------------------------------------------------
class ApEvidence(Base):
    """1.0.104: one piece of evidence on file (V12): the SAP upload and the review workbook as they were at each download
    (stored by CBX), or a file a person attached to a batch, a payment run or a utility account (a signed payment schedule,
    an F110 proposal, a funding request, a DocuSign memo, a release audit report, a POP, a Council Liaison mail …, .msg/.eml
    too). The file sits in Tools/AP evidence (the warehouse never moves or deletes a tool's files); its SHA-256 is kept
    here, and it can't be deleted before keep_until (the portfolio's retention for its type)."""
    __tablename__ = "ap_evidence"
    __table_args__ = (Index("ix_ap_evidence_portfolio_period", "portfolio", "period"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio: Mapped[str] = mapped_column(String(20), default="")
    period: Mapped[str] = mapped_column(String(7), default="")  # yyyy-mm
    stage: Mapped[str] = mapped_column(String(16), default="")  # review · upload · approval · payment · release · account · other
    type: Mapped[str] = mapped_column(String(60), default="")  # "SAP upload file", "Review workbook", "Proof of payment" …
    batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    run_item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)  # a payment run (an AP file Bo checked)
    account: Mapped[str] = mapped_column(String(60), default="", index=True)  # a utility account, as ApUtilityAccount.account_key
    file_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # stored_files.id
    name: Mapped[str] = mapped_column(String(255), default="")
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(10), default="person")  # download (stored by CBX) · person
    by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    by_name: Mapped[str] = mapped_column(String(160), default="")
    keep_until: Mapped[str] = mapped_column(String(10), default="")  # yyyy-mm-dd: no deletion before
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---- 1.0.104 (Builder D): V01 new-vendor reviews, V09 the duplicates register — logic in cbx/ap/vendor_review.py, dupcases.py ----
class ApVendorReview(Base):
    """1.0.104: an independent review of a SAP vendor created or changed from the go-live day (V01): opened by an upload or
    pull of SAP's vendor list (LFA1 with ERDAT / ERNAM / AEDAT); the SAP record beside the creation request and the bank
    letter (files on the row); a checklist; Pass, or Fail with a reason. Until it passes, Tess stops the vendor's invoices."""
    __tablename__ = "ap_vendor_reviews"
    __table_args__ = (UniqueConstraint("vendor", "kind", "stamp", name="uq_ap_vendor_review"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(20), index=True)  # LIFNR as SAP gives it
    name: Mapped[str] = mapped_column(String(160), default="")
    kind: Mapped[str] = mapped_column(String(8), default="new")  # new · changed
    stamp: Mapped[str] = mapped_column(String(10), default="")  # the day that opened it (ERDAT for new, AEDAT for changed)
    created_on: Mapped[str] = mapped_column(String(10), default="")
    created_by: Mapped[str] = mapped_column(String(40), default="")  # ERNAM
    changed_on: Mapped[str] = mapped_column(String(10), default="")
    portfolio: Mapped[str] = mapped_column(String(20), default="", index=True)
    company_codes: Mapped[str] = mapped_column(Text, default="")
    municipal: Mapped[bool] = mapped_column(Boolean, default=False)  # on the utilities list: the account → site → vendor link is checked
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # the SAP record as the list gave it
    changes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # [{field, from, to}] for a changed vendor
    state: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open · passed · failed
    source: Mapped[str] = mapped_column(String(8), default="")  # excel · sap
    opened_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # who uploaded or pulled the list
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    request_file_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # stored_files.id: the creation request
    request_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    letter_file_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the bank confirmation letter
    letter_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    checklist: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {item: ok · wrong · na}
    errors: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, default=list)  # the items found wrong (the report's error types)
    reason: Mapped[str] = mapped_column(Text, default="")
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String(160), default="")
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class ApDupCase(Base):
    """1.0.104: one possible duplicate (V09) — raised by Tess (the same invoice in the batch, an earlier upload or SAP's
    posting history; a near-duplicate utility bill), by Ivy or Bo (seen, paid, twice in a run), or a line of an imported
    MOL / Workflow listing. Outcome: genuine (never posts) or valid (released; above the review threshold a second person
    confirms). `key` keeps one case per document and twin; a case Tess no longer raises is closed as gone."""
    __tablename__ = "ap_dup_cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    source: Mapped[str] = mapped_column(String(8), default="tess")  # tess · near · ivy · bo · import
    code: Mapped[str] = mapped_column(String(24), default="")  # batch · upload · sap · near · duplicate_seen … · listing
    item_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    capture_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    batch_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # a payment run's row
    portfolio: Mapped[str] = mapped_column(String(20), default="", index=True)
    document: Mapped[str] = mapped_column(String(300), default="")  # the file, supplier and invoice number, as people read it
    vendor: Mapped[str] = mapped_column(String(40), default="")
    reference: Mapped[str] = mapped_column(String(80), default="")
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    twin_kind: Mapped[str] = mapped_column(String(8), default="")  # batch · upload · sap · seen · paid · file · listing
    twin_ref: Mapped[str] = mapped_column(String(200), default="")
    twin_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # the twin capture, or the ap_posted row
    text: Mapped[str] = mapped_column(String(400), default="")  # what raised it
    state: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open · pending (valid, waiting for a second person) · genuine · valid · gone
    proposed: Mapped[str] = mapped_column(String(8), default="")  # an import's proposal: genuine · valid · "" (a person looks)
    proposed_why: Mapped[str] = mapped_column(String(300), default="")
    owner_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(160), default="")
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    second_by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    second_by: Mapped[str] = mapped_column(String(160), default="")
    second_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    import_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    line: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # an imported listing line as read


class ApDupImport(Base):
    """1.0.104: one MOL / Workflow Basket listing uploaded into the duplicates register (V09 backlog)."""
    __tablename__ = "ap_dup_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), default="")
    portfolio: Mapped[str] = mapped_column(String(20), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    mapping: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, default=dict)  # {field: the sheet's column}
    by_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    by_name: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
