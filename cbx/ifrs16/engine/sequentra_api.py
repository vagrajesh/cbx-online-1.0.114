"""
Sequentra API client for IFRS 16 Workflow.

Makes the same request Power BI's Web connector makes: the report's web
address plus one HTTP request header,

    URL:     https://api.excellerate.tech/sequentra/<report>
    Header:  Clientkey = <your key>

and hands the answer to the engine in exactly the shape the file loaders
produce, so the rules, CIW, IBR and validation behave the same whether
the data came from a file or from the API.

REPORTS
    Charge reports take fromdate / todate, YYYY-MM-DD in single quotes
    sent as %27 - the form the API team confirmed on 23/09/2026:
        ?fromdate=%272026-07-01%27&todate=%272026-09-22%27
    (the published list said datefrom / dateto; the server fails on
    those). The app asks for the whole workflow month:
        saactivecharge, saterminatedcharge, aroactivecharge,
        aroterminatedcharge                     -> step payments
    Tab reports (no parameters):
        buildingrecordtabs, keydateoptionstab   -> lease facts
        leaseclausetab, vendortab, customertab, chargeschedule

WHY urllib AND NOT requests
    urllib uses the Windows certificate store and the Windows proxy
    settings - the same ones Power BI uses - so a corporate network that
    inspects HTTPS traffic works here too. It also sends the single
    quotes in the dates as literal characters, exactly as typed in
    Power BI ('requests' encodes them as %27).

THE KEY
    Held in memory for the session only. It is never written to disk,
    never logged, and never appears in a message (it is masked even if a
    server echoes it back).
"""

from __future__ import annotations

VERSION = "2.6.0"

import calendar
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO, StringIO

BASE_URL = "https://api.excellerate.tech/sequentra/"
HEADER = "Clientkey"
DEFAULT_TIMEOUT = 180            # seconds per page (Power BI's default)
MAX_PAGES = 500

# key -> (report name in the URL, what it holds, takes datefrom/dateto)
ENDPOINTS = {
    "sa_active":        ("saactivecharge", "SA active charges", True),
    "sa_terminated":    ("saterminatedcharge", "SA terminated charges", True),
    "aro_active":       ("aroactivecharge", "ARO active charges", True),
    "aro_terminated":   ("aroterminatedcharge", "ARO terminated charges",
                         True),
    "charge_schedule":  ("chargeschedule", "ARO charge schedule", False),
    "building_record":  ("buildingrecordtabs", "Building & Record tabs",
                         False),
    "key_date_options": ("keydateoptionstab", "Key Dates & Options", False),
    "lease_clause":     ("leaseclausetab", "Lease clauses", False),
    "vendor":           ("vendortab", "Vendors (landlords)", False),
    "customer":         ("customertab", "Customers (payees)", False),
}
DATED_ENDPOINTS = {k for k, v in ENDPOINTS.items() if v[2]}
_BY_NAME = {v[0]: k for k, v in ENDPOINTS.items()}


class SequentraAPIError(Exception):
    """A problem worth showing the user, already in plain words.
    .status is the HTTP status when the server answered (else None)."""

    def __init__(self, message, status=None, short=None):
        super().__init__(message)
        self.status = status
        self.short = short or message      # one line, for attempt lists


# How the dates are written in the charge-report address. 'literal' is
# what Power BI sends when the address is typed with quotes; the others
# are tried automatically when the server fails on it.
DATE_STYLES = ("api", "literal", "encoded", "plain")
# tried only when all of the above fail: other parameter names and date
# formats, in case the published description is out of date, and last of
# all no parameters at all
PROBE_STYLES = ("fromdate", "DateFrom", "startdate", "slash", "compact",
                "none")
_LEARNED = {}           # base URL -> (date style, days per part), per session


# ---------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------
def normalise_base_url(url):
    """Accept the host alone, the /sequentra folder, or a full report
    address copied out of Power BI - always return .../sequentra/."""
    u = (url or "").strip() or BASE_URL
    u = u.split("?", 1)[0].rstrip("/")
    tail = u.rsplit("/", 1)[-1].lower()
    if tail in _BY_NAME:                       # a pasted report address
        u = u.rsplit("/", 1)[0]
    if not u.lower().endswith("/sequentra"):
        u += "/sequentra"
    return u + "/"


@dataclass
class APIConfig:
    client_key: str = ""
    base_url: str = BASE_URL
    header_name: str = HEADER
    timeout: int = DEFAULT_TIMEOUT
    # the charge reports list what falls due in each period, so a
    # payment schedule needs the months ahead as well as the run month
    months_back: int = 12
    months_ahead: int = 120
    # off until the charge reports' format is confirmed (see
    # fetch_for_run); the stored step payments file is used meanwhile
    use_charge_reports: bool = False

    @classmethod
    def from_settings(cls, settings):
        api = (settings or {}).get("sequentra_api", {}) or {}
        try:
            timeout = int(api.get("timeout") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        return cls(client_key=str(api.get("client_key") or "").strip(),
                   base_url=normalise_base_url(api.get("base_url")),
                   header_name=(str(api.get("header_name") or "").strip()
                                or HEADER),
                   timeout=timeout,
                   months_back=_int(api.get("charge_months_back"), 12),
                   months_ahead=_int(api.get("charge_months_ahead"), 120),
                   use_charge_reports=bool(api.get("use_charge_reports")))


def _int(v, default):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------
# months
# ---------------------------------------------------------------------
def _month_bounds(d):
    """First and last day of d's month - single-month runs must cover
    the entire month."""
    first = date(d.year, d.month, 1)
    return first, date(d.year, d.month,
                       calendar.monthrange(d.year, d.month)[1])


def default_month():
    """The month just ended - the one normally being processed."""
    t = date.today()
    return date(t.year - (t.month == 1), (t.month - 2) % 12 + 1, 1)


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v).strip().strip("'")[:10],
                             "%Y-%m-%d").date()


def _date_query(a, b, style="api"):
    if style == "none":
        return ""
    if style == "api":
        # exactly the API team's working link
        return "fromdate=%%27%s%%27&todate=%%27%s%%27" % (a.isoformat(),
                                                        b.isoformat())
    if style in ("fromdate", "DateFrom", "startdate"):
        n1, n2 = {"fromdate": ("fromdate", "todate"),
                  "DateFrom": ("DateFrom", "DateTo"),
                  "startdate": ("startdate", "enddate")}[style]
        return urllib.parse.quote("%s='%s'&%s='%s'" % (
            n1, a.isoformat(), n2, b.isoformat()), safe="='&-")
    if style == "slash":
        return urllib.parse.quote("datefrom='%s'&dateto='%s'" % (
            a.strftime("%Y/%m/%d"), b.strftime("%Y/%m/%d")),
            safe="='&-/")
    if style == "compact":
        return "datefrom=%s&dateto=%s" % (a.strftime("%Y%m%d"),
                                          b.strftime("%Y%m%d"))
    if style == "plain":
        return "datefrom=%s&dateto=%s" % (a.isoformat(), b.isoformat())
    q = "datefrom='%s'&dateto='%s'" % (a.isoformat(), b.isoformat())
    if style == "encoded":
        return urllib.parse.quote(q, safe="=&-")        # ' -> %27
    return urllib.parse.quote(q, safe="='&-")           # ' sent as typed


def _bounds(datefrom=None, dateto=None, month=None):
    if month is not None:
        return _month_bounds(_as_date(month))
    if datefrom is not None and dateto is not None:
        return _as_date(datefrom), _as_date(dateto)
    return None


def build_url(cfg, key, datefrom=None, dateto=None, month=None,
              style="api"):
    """The report's address. The charge reports carry
    ?fromdate=%27YYYY-MM-DD%27&todate=%27YYYY-MM-DD%27 - single quotes
    around the dates, sent as %27."""
    name, about, dated = ENDPOINTS[key]
    url = normalise_base_url(cfg.base_url) + name
    if dated:
        ab = _bounds(datefrom, dateto, month)
        if ab is None:
            raise SequentraAPIError(
                "%s needs a month (datefrom / dateto)." % about)
        q = _date_query(ab[0], ab[1], style)
        if q:
            url += "?" + q
    return url


def _parts(a, b, days):
    """a..b split into consecutive ranges of at most `days` days."""
    from datetime import timedelta
    out, cur = [], a
    while cur <= b:
        end = min(b, cur + timedelta(days=days - 1))
        out.append((cur, end))
        cur = end + timedelta(days=1)
    return out


# ---------------------------------------------------------------------
# the request - GET plus the key header, nothing else
# ---------------------------------------------------------------------
def _mask(text, key):
    text = str(text)
    if key and len(key) >= 4:
        text = text.replace(key, "********")
        text = text.replace(urllib.parse.quote(key, safe=""), "********")
    return text


def _http_get(url, header, key, timeout):
    """(status, content type, body bytes). Separate so tests can
    replace it without touching the network."""
    req = urllib.request.Request(url, method="GET", headers={
        header: key,
        "Accept": "application/json, text/csv, */*",
        "User-Agent": "IFRS16-Workflow/%s" % VERSION})
    ctx = ssl.create_default_context()      # Windows certificate store
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(),      # Windows / environment proxy
        urllib.request.HTTPSHandler(context=ctx))
    with opener.open(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def _get(url, cfg):
    import time
    key = cfg.client_key
    t0 = time.time()
    try:
        return _http_get(url, cfg.header_name, key, cfg.timeout)
    except urllib.error.HTTPError as e:
        took = time.time() - t0
        body = ""
        try:
            body = e.read().decode("utf-8", "replace").strip()[:300]
        except Exception:
            pass
        hint = {
            400: "the server rejected the request - check the address "
                 "and the dates",
            401: "the key was refused - check the key, and that the "
                 "header name is '%s'" % cfg.header_name,
            403: "the key was not accepted for this report (a wrong key "
                 "gives this too)",
            404: "that report address does not exist - check the URL",
            407: "the office proxy wants a login - ask IT to allow "
                 "Python to reach api.excellerate.tech",
            429: "too many requests - wait a minute and try again",
        }.get(e.code, "the API failed while building this report (an "
                      "error on the server, not the key)"
              if e.code >= 500 else "the server returned an error")
        raise SequentraAPIError(_mask("HTTP %s after %.1fs: %s.%s" % (
            e.code, took, hint, (" Server said: " + body) if body else ""),
            key), status=e.code, short=_mask("HTTP %s after %.1fs%s" % (
                e.code, took, (" - server said: " + body[:120])
                if body else " (no message from the server)"), key)) \
            from None
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        if "CERTIFICATE" in reason.upper() or "SSL" in reason.upper():
            raise SequentraAPIError(
                "Secure connection failed (%s). The network may be "
                "inspecting HTTPS traffic - ask IT to let Python reach "
                "api.excellerate.tech the way Power BI does." % reason
            ) from None
        raise SequentraAPIError(
            "Could not reach the server (%s). Check you are on the "
            "network or VPN where Power BI works." % reason) from None
    except (TimeoutError, OSError) as e:
        if "timed out" in str(e).lower() or isinstance(e, TimeoutError):
            raise SequentraAPIError(
                "No answer within %s seconds. Try again - large reports "
                "can be slow." % cfg.timeout) from None
        raise SequentraAPIError(_mask("Connection problem: %s" % e,
                                      key)) from None


_NEXT_KEYS = ("@odata.nextLink", "odata.nextLink", "nextLink",
              "next_page_url", "nextPage", "next")
_ROW_KEYS = ("value", "data", "items", "results", "records", "rows",
             "result", "content")


def _next_link(payload, url):
    if not isinstance(payload, dict):
        return None
    for k in _NEXT_KEYS:
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return urllib.parse.urljoin(url, v.strip())
    links = payload.get("links") or payload.get("_links")
    if isinstance(links, dict):
        v = links.get("next")
        if isinstance(v, dict):
            v = v.get("href")
        if isinstance(v, str) and v.strip():
            return urllib.parse.urljoin(url, v.strip())
    return None


def _tables_from_json(payload):
    """{name: [row dicts]} from a plain list, a list wrapped in 'data' /
    'value' / ..., or several named lists (one per tab)."""
    if isinstance(payload, list):
        return {"Data": payload}
    if not isinstance(payload, dict):
        return {}
    for k in _ROW_KEYS:
        v = payload.get(k)
        if isinstance(v, list):
            return {"Data": v}
        if isinstance(v, dict):
            inner = _tables_from_json(v)
            if inner:
                return inner
    tabs = {k: v for k, v in payload.items()
            if isinstance(v, list) and v and isinstance(v[0], dict)}
    if tabs:
        return tabs
    if payload and all(not isinstance(v, (list, dict))
                       for v in payload.values()):
        return {"Data": [payload]}
    return {}


def fetch_tables(cfg, url):
    """Every page of one report as ({table: DataFrame}, info)."""
    import pandas as pd
    if not cfg.client_key:
        raise SequentraAPIError(
            "No client key entered. Open Settings > Configure Sequentra "
            "API and paste the key, as you would in Power BI.")
    tables, pages, next_url, ctype, seen = {}, 0, url, "", set()
    while next_url and pages < MAX_PAGES and next_url not in seen:
        seen.add(next_url)
        _status, ctype, body = _get(next_url, cfg)
        pages += 1
        low = (ctype or "").lower()
        if body[:2] == b"PK":                        # an Excel file
            for n, df in pd.read_excel(BytesIO(body),
                                       sheet_name=None).items():
                tables.setdefault(str(n), []).append(df)
            break
        if "csv" in low or "text/plain" in low:
            tables.setdefault("Data", []).append(pd.read_csv(
                StringIO(body.decode("utf-8-sig", "replace"))))
            break
        if not body.strip():
            break
        try:
            payload = json.loads(body.decode("utf-8-sig"))
        except Exception:
            raise SequentraAPIError(_mask(
                "The server answered (%s) but not with data the app can "
                "read. First characters: %r"
                % (ctype or "no content type", body[:160]),
                cfg.client_key)) from None
        for name, rows in _tables_from_json(payload).items():
            if rows:
                tables.setdefault(name, []).append(
                    pd.json_normalize(rows, sep=" "))
        next_url = _next_link(payload, next_url)
    out = {n: (pd.concat(f, ignore_index=True) if len(f) > 1 else f[0])
           for n, f in tables.items()}
    return out, {"pages": pages, "content_type": ctype,
                 "rows": sum(len(d) for d in out.values())}


def _one_table(tables):
    import pandas as pd
    frames = [d for d in tables.values() if len(d)]
    if not frames:
        return pd.DataFrame()
    return frames[0] if len(frames) == 1 else pd.concat(
        frames, ignore_index=True)


# ---------------------------------------------------------------------
# field names: the API's spelling -> the spelling the engine reads
# ('Record ID', 'recordId', 'record_id' and 'RecordID' all match)
# ---------------------------------------------------------------------
def _norm(name):
    return re.sub(r"[^a-z0-9]", "", str(name).strip().lower())


FACT_MAP = {
    "recordid": "Record ID", "recordname": "Record Name",
    "currenttermstartdate": "Current Term Start Date",
    "currentleaseenddate": "Current Lease End Date",
    "moveindate": "Move-In Date", "signaturedate": "Signature Date",
    "country": "Country", "currency": "Currency",
    "newdiscountrate": "New Discount Rate", "discountrate": "Discount Rate",
    "externalreference": "External Reference",
    "address1": "Address 1", "address2": "Address 2", "city": "City",
    "postalcode": "Postal Code", "titledescription": "Title Description",
    "contractcostcentre": "Contract Cost Centre",
    "contractcostcenter": "Contract Cost Centre",
    "absaleasetype": "ABSA Lease Type", "leasetype": "Lease Type",
    "lessee": "Lessee",
    "bodate": "BO Date", "beneficialoccupationdate": "BO Date",
    "occupancyend": "OccupancyEnd", "termdate": "TermDate",
    "agreementdate": "AgreementDate", "mtmdate": "MTM Date",
    "originaltermenddate": "Original Term End Date",
    "calculationdate": "Calculation Date",
    "leaseexpirydate": "Lease Expiry Date",
    # field names as the live API sends them (first download, 23/09/2026)
    "leasecurrentcommencement": "Current Term Start Date",
    "leasecurrentexpiration": "Current Lease End Date",
    "leaseoriginalcommencement": "Original Commencement Date",
    "name": "Record Name", "buildingname": "Building Name",
    "leasestatus": "Lease Status", "leasemarker": "Lease Marker",
    "businessunit": "Business Unit",
}
# Building & Record tabs carries the lease key as Property_ID (9713P001);
# Building_Id (9713) is the building, not the lease
FACT_KEY_FALLBACKS = ("propertyid", "leasereference", "schedulenumber",
                      "recordnumber")
FACT_FIELDS_USED = ("Record ID", "Record Name", "Country", "Currency",
                    "Current Term Start Date", "Current Lease End Date",
                    "Move-In Date", "Signature Date", "ABSA Lease Type",
                    "Discount Rate", "New Discount Rate",
                    "External Reference", "Contract Cost Centre")

CHARGE_MAP = {
    "recordid": "Schedule Number", "schedulenumber": "Schedule Number",
    "leasereference": "Schedule Number", "schedule": "Schedule Number",
    "scheduleno": "Schedule Number",
    "expensetypename": "Expense Type Name",
    "expensetype": "Expense Type Name", "chargetype": "Expense Type Name",
    "chargename": "Expense Type Name", "expensename": "Expense Type Name",
    "startdate": "StartDate", "chargestartdate": "StartDate",
    "fromdate": "StartDate", "effectivefrom": "StartDate",
    "enddate": "EndDate", "chargeenddate": "EndDate", "todate": "EndDate",
    "effectiveto": "EndDate",
    "numberofpayments": "Number of Payments",
    "noofpayments": "Number of Payments",
    "paymentcount": "Number of Payments",
    "startingpaymentnumber": "Starting Payment Number",
    "paymentamount": "Payment Amount", "chargeamount": "Payment Amount",
    "periodicamount": "Payment Amount", "amount": "Payment Amount",
    "paymentfrequency": "Payment Frequency",
    "chargefrequency": "Payment Frequency", "frequency": "Payment Frequency",
    # names as the live chargeschedule report sends them
    "majorcode": "Expense Type Name", "recurrence": "Payment Frequency",
    "calculatedamountperrecurrence": "Payment Amount",
}
def _freq_months(freq):
    f = re.sub(r"[^a-z]", "", str(freq).lower())
    if f in ("onetime", "once", "single"):
        return None
    for key, n in (("semi", 6), ("biann", 6), ("half", 6), ("quarter", 3),
                   ("ann", 12), ("year", 12), ("month", 1)):
        if key in f:
            return n
    return 1


STEP_COLUMNS = ["Schedule Number", "Expense Type Name", "StartDate",
                "EndDate", "Number of Payments", "Payment Amount",
                "Payment Frequency"]


def _rename(df, mapping):
    """Rename recognised fields. Exact names ('Record ID', 'recordId',
    'record_id') are matched first; then nested names ('Lease Record
    ID') match on their last part. A column already carrying a target
    name is never overwritten, and each target is used once."""
    ren, used = {}, set(df.columns)
    targets = set(mapping.values())
    for exact in (True, False):
        for c in df.columns:
            if c in targets or c in ren:
                continue
            n = _norm(c)
            target = mapping.get(n)
            if target is None and not exact:
                for k, val in mapping.items():
                    if len(k) >= 8 and n.endswith(k) and val not in used:
                        target = val
                        break
            if target and target not in used:
                ren[c] = target
                used.add(target)
    return df.rename(columns=ren)


def facts_table(df):
    df = _rename(df, FACT_MAP)
    if "Record ID" not in df.columns:
        for c in df.columns:
            if _norm(c) in FACT_KEY_FALLBACKS:
                df = df.rename(columns={c: "Record ID"})
                break
    if "Record ID" in df.columns:
        df = df[df["Record ID"].notna()].copy()
        df["Record ID"] = df["Record ID"].astype(str).str.strip()
        df = df[df["Record ID"].ne("") & df["Record ID"].ne("nan")]
        # the engine looks leases up by Record ID - one row each
        df = df.drop_duplicates(subset="Record ID", keep="first")
    return df.reset_index(drop=True)


LEDGER_AMOUNT_KEYS = ("rentalchargeexclusiveamt", "exclusiveamt",
                      "exclusiveamount", "amountexclusive")


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def is_ledger(df):
    """The live charge reports list one row per charge per billing
    period (Period, RentalChargeExclusiveAmt, ...), not dated blocks."""
    return df is not None and "period" in {_norm(c) for c in df.columns}


def ledger_to_blocks(df):
    """Billing-period rows -> the engine's dated step blocks.

    Every charge falling due in a month is added up (base rent and
    operating costs together, as the step payments do), VAT excluded.
    Between a lease's first and last period, a month with nothing due is
    a nil month - that is how an annual or quarterly payer shows up. Runs
    of months with the same total become one monthly block."""
    import pandas as pd
    cols = {_norm(c): c for c in df.columns}
    rid = cols.get("recordid") or cols.get("schedulenumber")
    per = cols.get("period")
    amt = next((cols[k] for k in LEDGER_AMOUNT_KEYS if k in cols), None)
    if amt is None:
        amt = next((c for n, c in cols.items() if "exclusive" in n), None)
    major = cols.get("majorcode")
    if not (rid and per and amt):
        return pd.DataFrame(columns=STEP_COLUMNS)
    work = pd.DataFrame({
        "ref": df[rid].astype(str).str.strip(),
        "month": pd.to_datetime(df[per].astype(str).str[:10],
                                errors="coerce").dt.to_period("M"),
        "amount": pd.to_numeric(df[amt], errors="coerce").fillna(0.0),
        "type": df[major].astype(str) if major else ""})
    if "_source" in df.columns:
        work["src"] = df["_source"].values
    work = work[work["month"].notna() & work["ref"].ne("")]
    # the same charge can come back from two overlapping requests
    if cols.get("propertyexpenseid"):
        work["pid"] = df.loc[work.index, cols["propertyexpenseid"]] \
            .astype(str).values
        work = work.drop_duplicates(subset=["ref", "pid", "month"])
    rows = []
    for ref, g in work.groupby("ref", sort=True):
        by_month = g.groupby("month")["amount"].sum()
        types = "; ".join(sorted(set(t for t in g["type"] if t and
                                     t != "nan"))) or "Charges"
        src = ", ".join(sorted(set(g["src"]))) if "src" in g else ""
        first, last = by_month.index.min(), by_month.index.max()
        months = pd.period_range(first, last, freq="M")
        vals = [round(float(by_month.get(m, 0.0)), 2) for m in months]
        i = 0
        while i < len(vals):
            j = i
            while j + 1 < len(vals) and vals[j + 1] == vals[i]:
                j += 1
            a, b = months[i], months[j]
            rows.append({
                "Schedule Number": ref, "Expense Type Name": types,
                "StartDate": a.start_time.date().isoformat(),
                "EndDate": b.end_time.date().isoformat(),
                "Number of Payments": j - i + 1,
                "Payment Amount": vals[i], "Payment Frequency": "Monthly",
                "Source Report": src})
            i = j + 1
    return pd.DataFrame(rows, columns=STEP_COLUMNS + ["Source Report"])


def charges_table(df):
    """A charge report reshaped into the step-payment columns. Where the
    report carries dates but no payment count, the count is the months
    between the dates. A missing frequency is NOT defaulted - a blank
    there would silently turn quarterly leases into monthly ones."""
    import pandas as pd
    df = _rename(df, CHARGE_MAP)
    if "Schedule Number" in df.columns:
        df = df[df["Schedule Number"].notna()].copy()
        df["Schedule Number"] = df["Schedule Number"].astype(str) \
            .str.strip()
    if "Number of Payments" not in df.columns and \
            {"StartDate", "EndDate"} <= set(df.columns):
        # months covered, divided by the months between payments - a
        # quarterly block over 12 months is 4 payments, not 12
        s = pd.to_datetime(df["StartDate"].astype(str).str[:10],
                           errors="coerce")
        e = pd.to_datetime(df["EndDate"].astype(str).str[:10],
                           errors="coerce")
        months = (e.dt.year - s.dt.year) * 12 + \
            (e.dt.month - s.dt.month) + 1
        if "Payment Frequency" in df.columns:
            step = df["Payment Frequency"].map(_freq_months)
            n = (months / step).apply(
                lambda x: int(-(-x // 1)) if x == x else x)
            df["Number of Payments"] = n.where(step.notna(), 1)
        else:
            df["Number of Payments"] = months
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------
# the client the app uses
# ---------------------------------------------------------------------
def _plan_label(style, days, n):
    how = {"api": "fromdate / todate, quotes as %27 (the API team's link)",
           "literal": "datefrom / dateto, quotes as typed",
           "encoded": "datefrom / dateto, quotes as %27",
           "plain": "datefrom / dateto without quotes",
           "fromdate": "fromdate / todate, quotes as typed",
           "DateFrom": "named DateFrom / DateTo",
           "startdate": "named startdate / enddate",
           "slash": "dates as YYYY/MM/DD",
           "compact": "dates as YYYYMMDD",
           "none": "no dates at all (everything the report holds)"}[style]
    if days:
        return "%s, in %d %s part(s)" % (how, n,
                                         "weekly" if days == 7 else "daily")
    return how if style == "none" else how + ", whole month"


class SequentraClient:
    def __init__(self, config):
        self.cfg = config
        self.notes = []            # plain-English notes from the last call
        self.attempts = []         # how the last charge report was asked

    def url(self, key, datefrom=None, dateto=None, month=None):
        return _mask(build_url(self.cfg, key, datefrom, dateto, month),
                     self.cfg.client_key)

    def fetch(self, key, datefrom=None, dateto=None, month=None):
        """One report as a single DataFrame (every page, flattened)."""
        if key not in ENDPOINTS:
            key = _BY_NAME.get(str(key).strip().lower(), key)
        if key not in ENDPOINTS:
            raise SequentraAPIError("Unknown report '%s'." % key)
        if key in DATED_ENDPOINTS:
            return self._fetch_dated(key, *(_bounds(datefrom, dateto, month)
                                            or (None, None)))
        tables, _info = fetch_tables(self.cfg, build_url(self.cfg, key))
        return _one_table(tables)

    def _fetch_dated(self, key, a, b):
        """A charge report for a..b. Asked for the way Power BI sends it
        first. If the server fails (HTTP 5xx), the other ways of writing
        the dates are tried, then the month in weekly and daily parts
        (joined, duplicates removed) - a server that times out on a whole
        month often answers smaller ranges. Whatever works is remembered
        for the rest of the session. Every attempt is kept in
        self.attempts so a failure can be explained."""
        import time
        import pandas as pd
        if a is None:
            raise SequentraAPIError("%s needs a month (datefrom / dateto)."
                                    % ENDPOINTS[key][1])
        base = normalise_base_url(self.cfg.base_url)
        learned = _LEARNED.get(base)
        plans = []
        if learned:
            plans.append(learned)
        plans += [(st, 0) for st in DATE_STYLES]
        plans += [("api", 7), ("api", 1)]
        plans += [(st, 0) for st in PROBE_STYLES]
        seen, first_err = set(), None
        self.attempts = []

        for style, days in plans:
            if (style, days) in seen:
                continue
            seen.add((style, days))
            ranges = _parts(a, b, days) if days else [(a, b)]
            frames, t0, pa = [], time.time(), a
            try:
                for pa, pb in ranges:
                    url = build_url(self.cfg, key, pa, pb, style=style)
                    tables, _i = fetch_tables(self.cfg, url)
                    frames.append(_one_table(tables))
            except SequentraAPIError as exc:
                self.attempts.append("%s: %s%s" % (
                    _plan_label(style, days, len(ranges)), exc.short,
                    (" (first part, %s)" % pa.isoformat())
                    if len(ranges) > 1 else ""))
                if first_err is None:
                    first_err = exc
                # only a server-side failure is worth another way of
                # asking; a refused key or a network problem is not
                if exc.status is None or exc.status < 500:
                    raise
                continue
            frames = [f for f in frames if len(f)]
            df = (pd.concat(frames, ignore_index=True) if frames
                  else pd.DataFrame())
            if len(ranges) > 1 and len(df):
                try:
                    df = df.drop_duplicates().reset_index(drop=True)
                except TypeError:              # unhashable cells
                    df = df.loc[~df.astype(str).duplicated()] \
                        .reset_index(drop=True)
            self.attempts.append("%s: OK, %d rows in %.1fs" % (
                _plan_label(style, days, len(ranges)), len(df),
                time.time() - t0))
            if (style, days) != ("api", 0):
                self.notes.append(
                    "%s: the server failed on the standard request, so it "
                    "was fetched %s" % (ENDPOINTS[key][1],
                                        _plan_label(style, days,
                                                    len(ranges))))
            _LEARNED[base] = (style, days)
            return df
        raise SequentraAPIError(
            "%s: the API failed on every way of asking for %s to %s "
            "(%s) - an error on the server, not the key."
            % (ENDPOINTS[key][1], a.isoformat(), b.isoformat(),
               first_err.short),
            status=getattr(first_err, "status", None),
            short=first_err.short)

    def step_payments(self, run_type, workflow_month,
                      include_terminated=True):
        """The charge reports as the engine's step payments.

        SA runs read saactivecharge (+ saterminatedcharge), ARO runs the
        aro reports. The live reports list what falls due in each
        billing period, so they are read from months_back before the run
        month to months_ahead after it, a year at a time (stopping once
        a year comes back empty), and turned into dated blocks. The
        notes say which periods actually came back - if the API only
        holds past periods, the schedule will be short and the engine
        flags it. A terminated report that fails is noted; the active
        report must succeed."""
        import pandas as pd
        pre = "aro" if str(run_type).strip().upper() == "ARO" else "sa"
        keys = [pre + "_active"] + ([pre + "_terminated"]
                                    if include_terminated else [])
        wm = date(workflow_month.year, workflow_month.month, 1)
        start = _add_months(wm, -self.cfg.months_back)
        horizon = _add_months(wm, self.cfg.months_ahead + 1)
        frames, dated, self.notes = [], [], []
        for i, k in enumerate(keys):
            got, lo = [], start
            try:
                while lo < horizon:
                    hi = min(_add_months(lo, 12), horizon)
                    last = date.fromordinal(hi.toordinal() - 1)
                    df = self.fetch(k, datefrom=lo, dateto=last)
                    got.append(df)
                    # nothing due in a whole year after the run month:
                    # the report holds no further periods
                    if not len(df) and lo > wm:
                        break
                    lo = hi
            except SequentraAPIError as exc:
                if i == 0 and not got:
                    raise
                self.notes.append("%s skipped - %s" % (
                    ENDPOINTS[k][1], getattr(exc, "short", exc)))
                if not got:
                    continue
            got = [g for g in got if len(g)]
            raw = pd.concat(got, ignore_index=True) if got \
                else pd.DataFrame()
            if len(got) > 1:            # overlapping answers
                try:
                    raw = raw.drop_duplicates().reset_index(drop=True)
                except TypeError:
                    raw = raw.loc[~raw.astype(str).duplicated()] \
                        .reset_index(drop=True)
            if not len(raw):
                self.notes.append("%s: no charges %s to %s" % (
                    ENDPOINTS[k][1], start.strftime("%m/%Y"),
                    _add_months(horizon, -1).strftime("%m/%Y")))
                continue
            if is_ledger(raw):
                raw = raw.copy()
                raw["_source"] = ENDPOINTS[k][0]
                frames.append(raw)
                per = pd.to_datetime(
                    raw[{_norm(c): c for c in raw.columns}["period"]]
                    .astype(str).str[:10], errors="coerce")
                self.notes.append(
                    "%s: %d charge line(s), periods %s to %s%s" % (
                        ENDPOINTS[k][1], len(raw),
                        per.min().strftime("%m/%Y"),
                        per.max().strftime("%m/%Y"),
                        "" if per.max() > pd.Timestamp(wm) else
                        " - no future periods, so schedules only run to "
                        "the run month"))
            else:
                df = charges_table(raw)
                df["Source Report"] = ENDPOINTS[k][0]
                dated.append(df)
                self.notes.append("%s: %d row(s)" % (ENDPOINTS[k][1],
                                                     len(df)))
        out = []
        if frames:
            out.append(ledger_to_blocks(pd.concat(frames,
                                                  ignore_index=True)))
        out += dated
        out = [o for o in out if len(o)]
        if not out:
            return pd.DataFrame(columns=STEP_COLUMNS)
        return pd.concat(out, ignore_index=True)

    def lease_facts(self):
        """Building & Record tabs joined to Key Dates & Options on
        Record ID - stands in for the Sequentra validation export."""
        base = facts_table(self.fetch("building_record"))
        if "Record ID" not in base.columns:
            raise SequentraAPIError(
                "Building & Record tabs came back without a Record ID "
                "field. Fields: %s" % ", ".join(
                    map(str, list(base.columns)[:15])))
        try:
            kd = facts_table(self.fetch("key_date_options"))
            if "Record ID" in kd.columns:
                add = [c for c in kd.columns if c not in base.columns]
                if add:
                    # outer: a lease with key dates but no building row
                    # still gets its dates
                    base = base.merge(kd[["Record ID"] + add],
                                      on="Record ID", how="outer")
        except SequentraAPIError as exc:
            self.notes.append("Key Dates & Options skipped - %s" % exc)
        return base

    # -- checks shown in the settings window -------------------------
    def test_connection(self):
        """One call to Building & Record tabs: proves the key works and
        shows whether the fields the engine needs came back."""
        df = facts_table(self.fetch("building_record"))
        lines = ["Connected. Building & Record tabs: %d row(s), %d "
                 "field(s)." % (len(df), len(df.columns))]
        if "Record ID" in df.columns:
            lines.append("Lease key found: Record ID (%d leases)."
                         % df["Record ID"].nunique())
        else:
            lines.append("WARNING: no Record ID field came back. Fields: "
                         "%s" % ", ".join(map(str, list(df.columns)[:15])))
        missing = [c for c in FACT_FIELDS_USED if c not in df.columns]
        if missing:
            lines.append("Not in this tab (may come from Key Dates & "
                         "Options): " + ", ".join(missing))
        return lines

    def check_all(self, month, progress=None):
        """Call all ten reports once and say what each returned."""
        lines = []
        for k, (name, about, dated) in ENDPOINTS.items():
            if progress:
                progress("Checking %s…" % about)
            try:
                df = self.fetch(k, month=month if dated else None)
                extra = ""
                if dated:
                    cdf = charges_table(df)
                    miss = [c for c in STEP_COLUMNS if c not in cdf.columns]
                    extra = ("  all step columns found" if not miss else
                             "  missing: " + ", ".join(miss)) if len(df) \
                        else ""
                lines.append("OK    %-20s %6d rows%s"
                             % (name, len(df), extra))
            except SequentraAPIError as exc:
                lines.append("FAIL  %-20s %s" % (
                    name, exc.short if dated else str(exc)[:200]))
                if dated and len(self.attempts) > 1:
                    lines += ["        tried: " + t for t in self.attempts]
            else:
                if dated and len(self.attempts) > 1:
                    lines += ["        tried: " + t for t in self.attempts]
        lines += ["", "The charge-report address used (no key in it) - "
                  "paste it into Power BI, Web > Advanced, with the same "
                  "Clientkey header, to compare:",
                  build_url(self.cfg, "aro_active", month=month)]
        return lines


def fetch_for_run(cfg, run_type, month, need_steps=True):
    """(lease facts, step payments or None, notes) for one run.

    The lease facts must come back. The live charge reports list single
    charges per billing period (Period, RentalChargeExclusiveAmt) - no
    start/end dates, frequency or payment count - and one month held
    only 11 ARO lines, so what they cover is not yet confirmed. Until it
    is (Settings > Configure Sequentra API > 'Use the charge reports as
    step payments'), steps is None and the stored step payments file is
    used; the charge lines still feed the API cross-check."""
    cli = SequentraClient(cfg)
    facts = cli.lease_facts()
    notes = list(cli.notes)
    steps = None
    if need_steps and cfg.use_charge_reports:
        try:
            steps = cli.step_payments(run_type, month)
            notes += [n for n in cli.notes if n not in notes]
        except SequentraAPIError as exc:
            notes.append("Charge reports not available from the API - %s"
                         % getattr(exc, "short", exc))
    elif need_steps:
        notes.append("API charge reports used for the cross-check only; "
                     "step payments from the stored file")
    return facts, steps, notes


def save_to_excel(path, facts, steps, month, run_type, notes=()):
    """What came from the API, saved for checking against Power BI. The
    key is never written."""
    import pandas as pd
    if steps is None:
        steps = pd.DataFrame(columns=STEP_COLUMNS)
    about = [("Fetched", datetime.now().strftime("%Y-%m-%d %H:%M")),
             ("Month", "%s to %s" % tuple(d.isoformat() for d in
                                          _month_bounds(_as_date(month)))),
             ("Run type", run_type), ("Lease facts rows", len(facts)),
             ("Step payment rows", len(steps))] + \
        [("Note", n) for n in notes] + \
        [("Client", "sequentra_api.py " + VERSION)]
    try:
        with pd.ExcelWriter(path, engine="openpyxl") as xl:
            facts.to_excel(xl, sheet_name="Record Info", index=False)
            steps.to_excel(xl, sheet_name="Step Payments", index=False)
            pd.DataFrame(about, columns=["Item", "Value"]).to_excel(
                xl, sheet_name="Fetched", index=False)
    except PermissionError:
        raise SequentraAPIError("Could not write %s - it is probably open "
                                "in Excel." % path) from None
    return path


def diagnose(client_key, base_url=BASE_URL, endpoint_key="vendor",
             verify_ssl=True, timeout=60):
    """Kept for older callers: tries the Clientkey header (the way Power
    BI sends it) and returns (winner, log)."""
    cli = SequentraClient(APIConfig(client_key=client_key,
                                    base_url=base_url, timeout=timeout))
    try:
        df = cli.fetch(endpoint_key)
        return ({"key_mode": "header", "key_name": HEADER, "scheme": ""},
                [("header %s" % HEADER, "200", "OK - %d rows" % len(df))])
    except SequentraAPIError as exc:
        return None, [("header %s" % HEADER, "ERR", str(exc)[:160])]


# ---------------------------------------------------------------------
# download everything, exactly as the API sends it - to see what is
# there before deciding what the engine should read
# ---------------------------------------------------------------------
def _example(series):
    for v in series:
        if v is None:
            continue
        t = str(v).strip()
        if t and t.lower() not in ("nan", "none", "nat", "[]", "{}"):
            return t[:80]
    return ""


def download_all(cfg, month, path, progress=None, to_month=None):
    """Every report into a folder of CSV files (and a zip of it): a Summary (what each report
    returned, and every attempt for the charge reports), a Fields sheet
    (each field as the API spells it, what the app reads it as, how
    often it is filled, an example) and one CSV per report with the
    data untouched. Returns the summary lines. The key is never
    written."""
    import pandas as pd
    say = progress or (lambda m: None)
    cli = SequentraClient(cfg)
    a, b = _month_bounds(_as_date(month))
    if to_month is not None:
        b = _month_bounds(_as_date(to_month))[1]
    summary, fields, sheets = [], [], []
    for k, (name, about, dated) in ENDPOINTS.items():
        say("Downloading %s…" % about)
        cli.attempts = []
        try:
            df = cli.fetch(k, datefrom=a, dateto=b) if dated \
                else cli.fetch(k)
            status = "OK"
            detail = ("%s to %s" % (a.isoformat(), b.isoformat())
                      if dated else "no parameters")
        except SequentraAPIError as exc:
            df, status, detail = pd.DataFrame(), "FAILED", exc.short
        summary.append({"Report": name, "What it holds": about,
                        "Status": status, "Rows": len(df),
                        "Fields": len(df.columns), "Detail": detail,
                        "Attempts": " | ".join(cli.attempts)})
        mapping = CHARGE_MAP if dated or k == "charge_schedule" \
            else FACT_MAP
        mapped = _rename(pd.DataFrame(columns=list(df.columns)), mapping)
        for orig, new in zip(df.columns, mapped.columns):
            col = df[orig]
            filled = int(col.map(
                lambda v: v is not None and not (isinstance(v, float)
                                                 and v != v)
                and str(v).strip().lower() not in ("", "nan", "none",
                                                   "nat")).sum())
            fields.append({"Report": name, "Field (as the API sends it)":
                           str(orig),
                           "App reads it as": new if new != orig else "",
                           "Filled rows": filled, "Of rows": len(df),
                           "Example": _example(col.head(200))})
        if len(df):
            sheets.append((name, df))
    # one CSV per report (UTF-8 with BOM so Excel opens it cleanly),
    # plus the summary and the field list, in a folder and a zip of it
    import zipfile
    from pathlib import Path
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    about = pd.DataFrame([
        ("Fetched", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Month", "%s to %s" % (a.isoformat(), b.isoformat())),
        ("URL", normalise_base_url(cfg.base_url)),
        ("Client", "sequentra_api.py " + VERSION)],
        columns=["Item", "Value"])
    files = [("00_Summary.csv", pd.DataFrame(summary)),
             ("00_Fields.csv", pd.DataFrame(fields)),
             ("00_Fetched.csv", about)]
    files += [("%s.csv" % name, df) for name, df in sheets]
    try:
        for fname, df in files:
            out = df.copy()
            for c in out.columns:              # lists / dicts -> text
                if out[c].map(lambda v: isinstance(v, (list, dict))).any():
                    out[c] = out[c].map(
                        lambda v: json.dumps(v, default=str)
                        if isinstance(v, (list, dict)) else v)
            out.to_csv(folder / fname, index=False, encoding="utf-8-sig")
        zpath = folder.with_suffix(".zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for fname, _df in files:
                z.write(folder / fname, arcname="%s/%s" % (folder.name,
                                                           fname))
    except PermissionError:
        raise SequentraAPIError("Could not write into %s - a file there "
                                "is probably open in Excel." % folder) \
            from None
    return ["%-7s %-20s %7d rows %4d fields  %s" % (
        r["Status"], r["Report"], r["Rows"], r["Fields"],
        r["Detail"] if r["Status"] != "OK" else "") for r in summary]


# ---------------------------------------------------------------------
# the other reports: checks on the month's leases
# ---------------------------------------------------------------------
def fetch_reference(cfg, run_type=None, month=None):
    """Charge schedule, vendors, customers and (with a month) the run
    type's charge lines for that month - used to check the run, never to
    change it. A report that fails is noted and skipped."""
    import pandas as pd
    cli = SequentraClient(cfg)
    out, notes = {}, []
    if month is not None:
        pre = "aro" if str(run_type).strip().upper() == "ARO" else "sa"
        frames = []
        for k in (pre + "_active", pre + "_terminated"):
            try:
                df = cli.fetch(k, month=month)
                if len(df):
                    frames.append(df)
            except SequentraAPIError as exc:
                notes.append("%s skipped - %s" % (
                    ENDPOINTS[k][1], getattr(exc, "short", exc)))
        if frames:
            out["charges_month"] = pd.concat(frames, ignore_index=True)
    for k in ("charge_schedule", "vendor", "customer"):
        try:
            out[k] = cli.fetch(k)
        except SequentraAPIError as exc:
            notes.append("%s skipped - %s" % (ENDPOINTS[k][1],
                                              getattr(exc, "short", exc)))
    return out, notes


def _t(v):
    if v is None or (isinstance(v, float) and v != v):
        return ""
    t = str(v).strip()
    return "" if t.lower() in ("nan", "none", "nat") else t


def _d(v):
    t = _t(v)[:10]
    try:
        return datetime.strptime(t, "%Y-%m-%d").date() if t else None
    except ValueError:
        return None


def _f(v):
    try:
        x = float(str(v).replace(",", ""))
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _parties(df, code_col):
    """{Record ID: 'code name (role); ...'} from the vendor/customer tab."""
    out = {}
    if df is None or not len(df):
        return out
    cols = {_norm(c): c for c in df.columns}
    rid = cols.get("recordid")
    if not rid:
        return out
    code = cols.get(code_col)
    name = cols.get("tradingname") or cols.get("tradename") or \
        cols.get("name1")
    role = cols.get("role")
    for _, r in df.iterrows():
        bits = [_t(r[c]) for c in (code, name) if c]
        txt = " ".join(b for b in bits if b)
        if role and _t(r[role]):
            txt += " (%s)" % _t(r[role])
        if txt:
            out.setdefault(_t(r[rid]), []).append(txt)
    return {k: "; ".join(dict.fromkeys(v)) for k, v in out.items()}


CROSS_CHECK_COLS = [
    "Lease Reference", "Move Type", "Routed To", "Flags",
    "API Lease Status", "API Lease Marker", "API ABSA Lease Type",
    "API Country", "API Currency", "API Term Start", "API Term End",
    "API Discount Rate", "Drafted IBR", "Drafted Start", "Drafted Payment",
    "API Charge Type", "API Charge Amount", "API Charge Frequency",
    "API Charge From", "API Charge To", "API Charges This Month",
    "API Amount This Month", "Funder (movement file)",
    "API Landlord / Vendor", "API Customer"]


def cross_check(results, facts, reference, workflow_month):
    """One row per lease in the month's run, setting what was drafted
    beside what the API holds, with plain flags. Returns (frame,
    exception rows). Nothing drafted is changed."""
    import pandas as pd
    routing = results.get("Routing")
    if routing is None or not len(routing):
        return pd.DataFrame(columns=CROSS_CHECK_COLS), []
    fx = facts.set_index("Record ID") if facts is not None and \
        "Record ID" in facts.columns else pd.DataFrame()
    charges = {}
    cs = reference.get("charge_schedule")
    if cs is not None and len(cs):
        c = charges_table(cs)
        if "Schedule Number" in c.columns:
            for _, r in c.iterrows():
                charges.setdefault(_t(r["Schedule Number"]), r)
    month_lines = {}
    cm = reference.get("charges_month")
    if cm is not None and len(cm):
        cols = {_norm(c): c for c in cm.columns}
        rid = cols.get("recordid")
        amt = next((cols[k] for k in LEDGER_AMOUNT_KEYS if k in cols),
                   None)
        typ = cols.get("majorcode")
        if rid:
            for _, r in cm.iterrows():
                e = month_lines.setdefault(_t(r[rid]), [0.0, []])
                e[0] += (_f(r[amt]) or 0.0) if amt else 0.0
                if typ and _t(r[typ]):
                    e[1].append(_t(r[typ]))
    vendors = _parties(reference.get("vendor"), "vendor")
    customers = _parties(reference.get("customer"), "customer")

    drafted = {}
    for sheet, key, start_col in (("PIW Schedule", "Schedule Number",
                                   "Lease Start Date"),
                                  ("MDIW Schedule", "Schedule", None)):
        df = results.get(sheet)
        if df is None or not len(df) or key not in df.columns:
            continue
        for _, r in df.iterrows():
            drafted[_t(r[key])] = {
                "start": _t(r.get(start_col)) if start_col else "",
                "payment": r.get("Payment"), "ibr": r.get("IBR", ""),
                "funder": _t(r.get("Funder"))}
    mods = results.get("MDIW Modification Summary")
    if mods is not None and len(mods) and "Schedule Number" in mods:
        for _, r in mods.iterrows():
            d = drafted.setdefault(_t(r["Schedule Number"]), {})
            d["start"] = _t(r.get("Modification Date"))
            d["ibr"] = r.get("IBR", d.get("ibr", ""))

    def fget(ref, col):
        if len(fx) and ref in fx.index and col in fx.columns:
            v = fx.at[ref, col]
            if isinstance(v, pd.Series):
                v = v.iloc[0]
            return _t(v)
        return ""

    rows, exc = [], []
    for _, m in routing.iterrows():
        ref = _t(m["Lease Reference"])
        mtype = _t(m.get("Move Type"))
        dest = _t(m.get("Routed To"))
        dr = drafted.get(ref, {})
        ch = charges.get(ref)
        flags = []
        in_api = len(fx) and ref in fx.index
        status = fget(ref, "Lease Status")
        marker = fget(ref, "Lease Marker")
        if not in_api:
            flags.append("not in the API lease data")
        drafted_to_la = dest in ("PIW", "MDIW")
        if drafted_to_la and status.lower() == "terminated" and \
                mtype.lower() in ("addition", "renewal"):
            flags.append("API says Terminated but the movement is %s"
                         % mtype)
        if drafted_to_la and marker.lower() == "receivable":
            flags.append("API Lease Marker is Receivable")
        pay = _f(dr.get("payment"))
        amt = _f(ch.get("Payment Amount")) if ch is not None else None
        if drafted_to_la and not pay and amt:
            flags.append("no payment drafted but the API charge schedule "
                         "shows %s" % format(amt, ",.2f"))
        api_rate, ibr = _f(fget(ref, "Discount Rate")), _f(dr.get("ibr"))
        if drafted_to_la and api_rate and ibr and \
                abs(api_rate - ibr) > 0.005:
            flags.append("IBR %s differs from the API discount rate %s"
                         % (ibr, api_rate))
        row = {
            "Lease Reference": ref, "Move Type": mtype, "Routed To": dest,
            "Flags": "; ".join(flags),
            "API Lease Status": status, "API Lease Marker": marker,
            "API ABSA Lease Type": fget(ref, "ABSA Lease Type"),
            "API Country": fget(ref, "Country"),
            "API Currency": fget(ref, "Currency"),
            "API Term Start": fget(ref, "Current Term Start Date")[:10],
            "API Term End": fget(ref, "Current Lease End Date")[:10],
            "API Discount Rate": api_rate if api_rate else "",
            "Drafted IBR": ibr if ibr else "",
            "Drafted Start": dr.get("start", ""),
            "Drafted Payment": pay if pay is not None else "",
            "API Charge Type": _t(ch.get("Expense Type Name"))
            if ch is not None else "",
            "API Charge Amount": amt if amt is not None else "",
            "API Charge Frequency": _t(ch.get("Payment Frequency"))
            if ch is not None else "",
            "API Charge From": _t(ch.get("StartDate"))[:10]
            if ch is not None else "",
            "API Charge To": _t(ch.get("EndDate"))[:10]
            if ch is not None else "",
            "API Charges This Month": "; ".join(dict.fromkeys(
                month_lines[ref][1])) if ref in month_lines else "",
            "API Amount This Month": round(month_lines[ref][0], 2)
            if ref in month_lines else "",
            "Funder (movement file)": dr.get("funder", ""),
            "API Landlord / Vendor": vendors.get(ref, ""),
            "API Customer": customers.get(ref, ""),
        }
        rows.append(row)
        for fl in flags:
            if fl == "not in the API lease data" and not drafted_to_la:
                continue
            exc.append({"Lease Reference": ref, "Move Type": mtype,
                        "Issue": "API check: " + fl,
                        "Severity": "Info" if fl.startswith("IBR ")
                        else "Review"})
    return pd.DataFrame(rows, columns=CROSS_CHECK_COLS), exc
