#!/usr/bin/env python3
"""
Mata Web Tools API — department-specific tools ported from Panso.
ThreadingHTTPServer pattern mirrored from panso_local.py.

Tools served (this session: Incentive Calculator only):
  GET  /                         → public/index.html (launcher)
  GET  /tools/incentive.html     → public/tools/incentive.html
  GET  /api/firebase-config      → Firebase web config (public)
  GET  /api/me                   → signed-in user identity + grants
  GET  /api/periods              → available periods list
  GET  /api/projects             → project list with subsidiary (for typeahead)
  GET  /api/finance/incentive    → Incentive Calculator results

Run locally:
  PANSO_SKIP_AUTH=1 PANSO_ROOT=C:\\dev\\Panso-Local python tools_api.py
"""

import calendar
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── Auth layer ──────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from access_layer import (
    verify_token, extract_bearer_token,
    gate_request, AccessDenied,
    _init_firebase, user_grants, can_access_scope,
)

# ── Paths ───────────────────────────────────────────────────────────────────
_root_env = os.environ.get("PANSO_ROOT", "")
if _root_env:
    ROOT = Path(_root_env)
else:
    ROOT = Path(r"C:\dev\Panso-Local")

DATA_DIR    = ROOT / "data"
ENTRIES_CSV = DATA_DIR / "time-entries.csv"
MANUAL_CSV  = DATA_DIR / "manual.csv"
CFG           = ROOT / "config.json"
ALPHALISTS_DIR = ROOT / "alphalists"

# Static files served from public/ (sibling of app/)
PUBLIC_DIR = Path(_HERE).parent / "public"

# ── Auth / SKIP flags ────────────────────────────────────────────────────────
_skip_auth_requested = os.environ.get("PANSO_SKIP_AUTH", "").lower() in ("1", "true", "yes")
_in_cloud            = os.environ.get("PANSO_ROOT", "").startswith("/data")
_SKIP_AUTH           = _skip_auth_requested and not _in_cloud

_DEV_TIER: dict = {
    "uid": "local", "email": "ryan@mata.ph",
    "tier": "admin", "subsidiary": None,
    "comp": True, "is_fte": True,
}

_ADMIN_EMAIL = "ryan@mata.ph"
PORT         = int(os.environ.get("PORT", 5056))

# ── CORS ────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = {
    "https://mata-tools.web.app", "https://tools.mata.ph",
    "http://localhost:5056", "null",
}

# ── Constants ───────────────────────────────────────────────────────────────
LEAVE_PATTERN   = re.compile(
    r"\b(leave|sick|vacation|VL|SL|maternity|paternity|bereavement|emergency leave)\b", re.I)
LEAVE_TAG_RE    = re.compile(r"\bleave\b", re.I)
TRAINING_TAG_RE = re.compile(r"\btraining\b", re.I)
_MONTH_ABBR     = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]

# Pay Matrix helpers — verbatim from panso_local.py 219–565
_NUMBERED_PROJECT_RE = re.compile(r"^\s*\d")
_cat_re              = re.compile(r"^\s*(\d+[a-z]?)[.\s]")
CAT_ORDER            = ["0", "1a", "1b", "1c", "2", "2a", "2b", "3", "4", "5", "?"]


def is_training_row(r):
    """True if the row carries the Training tag. Training hours are unpaid."""
    return bool(TRAINING_TAG_RE.search(r.get("tags") or ""))


def is_numbered_project(name):
    """Numbered project = name starts with a digit (e.g. '81 Expedify Mobile App')."""
    return bool(_NUMBERED_PROJECT_RE.match(name or ""))


def is_paid_category(code):
    """Cat 0/2/3/4 are paid; Cat 1a/1b/1c/5 and '?' are ₱0."""
    if not code:
        return False
    return code[0] in ("0", "2", "3", "4")


def classify_pay_row(row, subsidiary_resolver):
    if is_training_row(row):
        return "training"
    proj   = (row.get("project_name") or "").strip()
    client = (row.get("client_name")  or "").strip()
    if is_numbered_project(proj):
        if subsidiary_resolver(client, proj) == "TSF":
            return "experimental"
        return "billable"
    code, _label = parse_category(row.get("task_name") or "")
    return "productive" if is_paid_category(code) else "idle"


def parse_category(task_name):
    if not task_name:
        return ("?", "Uncategorized")
    s = task_name.strip()
    m = _cat_re.match(s)
    if not m:
        return ("?", s)
    return (m.group(1), (s[m.end():].strip() or s))


def entry_matches_dept(row, dept_filter):
    if not dept_filter:
        return False
    clients = dept_filter.get("clients") or []
    projs   = dept_filter.get("projects_contains") or []
    client  = _norm(row.get("client_name"))
    project = _norm(row.get("project_name"))
    client_ok = False
    if clients:
        for c in clients:
            cn = _norm(c)
            if cn and (cn == client or cn in client):
                client_ok = True
                break
    if not clients:
        client_ok = True
    project_ok = False
    if projs:
        for p in projs:
            pn = _norm(p)
            if pn and pn in project:
                project_ok = True
                break
    if not projs:
        project_ok = True
    return client_ok and project_ok

# ── File cache ──────────────────────────────────────────────────────────────
_FILE_CACHE      = {}
_FILE_CACHE_LOCK = threading.Lock()
_DISCOVER_CACHE  = {"sig": None, "value": None}
_DISCOVER_LOCK   = threading.Lock()


def _cached_load(path, parser):
    try:
        st = path.stat()
    except OSError:
        return parser(path)
    sig = (st.st_mtime_ns, st.st_size)
    key = str(path)
    with _FILE_CACHE_LOCK:
        cached = _FILE_CACHE.get(key)
        if cached and cached[0] == sig[0] and cached[1] == sig[1]:
            return cached[2]
    value = parser(path)
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[key] = (sig[0], sig[1], value)
    return value


def _parse_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_config():
    if not CFG.exists():
        return {}
    return _cached_load(CFG, _parse_config)


def discover_weeks():
    if not DATA_DIR.exists():
        return []
    try:
        st  = DATA_DIR.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    with _DISCOVER_LOCK:
        if sig is not None and _DISCOVER_CACHE["sig"] == sig:
            return _DISCOVER_CACHE["value"]
    if ENTRIES_CSV.exists():
        value = [("entries", ENTRIES_CSV)]
    else:
        _wk_re = re.compile(r"^(\d{4})-wk(\d+)\.csv$")
        pairs  = []
        for p in DATA_DIR.iterdir():
            m = _wk_re.match(p.name)
            if m:
                pairs.append((p.stem, int(m.group(1)), int(m.group(2)), p))
        pairs.sort(key=lambda x: (x[1], x[2]))
        value = [(stem, path) for (stem, _, _, path) in pairs]
    with _DISCOVER_LOCK:
        _DISCOVER_CACHE["sig"] = sig
        _DISCOVER_CACHE["value"] = value
    return value


def _parse_week_csv(path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_week(path):
    return _cached_load(path, _parse_week_csv)


def dedupe_by_entry_id(rows, keep="last"):
    seen, blanks = {}, []
    for r in rows:
        eid = (r.get("entry_id") or "").strip()
        if not eid:
            blanks.append(r)
            continue
        if keep == "first" and eid in seen:
            continue
        seen[eid] = r
    return list(seen.values()) + blanks


def is_leave_row(r):
    tags = r.get("tags") or ""
    if LEAVE_TAG_RE.search(tags):
        return True
    blob = " ".join(str(r.get(k) or "") for k in ("project_name", "task_name", "description"))
    return bool(LEAVE_PATTERN.search(blob))


def exclude_leaves(rows):
    return [r for r in rows if not is_leave_row(r)]


_ALL_TIME_ACTIVE_CACHE = None  # tuple (last_scan_ts, set_of_emails)

def all_time_active_emails():
    """Emails that have ever logged non-leave hours across all CSVs.
    Two layers of truth:
    - If config.active_users is populated, it is authoritative.
    - Otherwise: anyone who has ever logged non-leave hours across CSVs.
    Cached 30s.
    """
    global _ALL_TIME_ACTIVE_CACHE
    now = dt.datetime.now().timestamp()
    if _ALL_TIME_ACTIVE_CACHE and now - _ALL_TIME_ACTIVE_CACHE[0] < 30:
        return _ALL_TIME_ACTIVE_CACHE[1]
    cfg = load_config()
    explicit = cfg.get("active_users") or []
    if explicit:
        s = set((e or "").strip().lower() for e in explicit if e)
    else:
        s = set()
        for _label, path in discover_weeks():
            for r in load_week(path):
                if is_leave_row(r):
                    continue
                sec = int(r.get("duration_seconds") or 0)
                if sec <= 0:
                    continue
                e = (r.get("user_email") or "").lower().strip()
                if e:
                    s.add(e)
    _ALL_TIME_ACTIVE_CACHE = (now, s)
    return s


def rows_in_range(start_date, end_date):
    out = []
    for _label, path in discover_weeks():
        for r in load_week(path):
            d = r.get("date", "")
            if not d:
                continue
            try:
                ed = dt.date.fromisoformat(d)
            except ValueError:
                continue
            if start_date <= ed <= end_date:
                out.append(r)
    if MANUAL_CSV.exists():
        for r in load_week(MANUAL_CSV):
            d = r.get("date", "")
            if not d:
                continue
            try:
                ed = dt.date.fromisoformat(d)
            except ValueError:
                continue
            if start_date <= ed <= end_date:
                out.append(r)
    return dedupe_by_entry_id(out)


def parse_period(period_type, period_value):
    if period_type == "week":
        m = re.match(r"^(\d{4})-wk(\d+)$", period_value)
        if not m:
            return None
        year, wk = int(m.group(1)), int(m.group(2))
        jan4 = dt.date(year, 1, 4)
        monday_of_w1 = jan4 - dt.timedelta(days=jan4.isoweekday() - 1)
        monday = monday_of_w1 + dt.timedelta(weeks=wk - 1)
        start  = monday - dt.timedelta(days=1)
        return start, start + dt.timedelta(days=6)
    if period_type == "payperiod":
        m = re.match(r"^([a-z]{3})(\d{4})([ab])$", period_value)
        if not m:
            return None
        mabbr, year, half = m.group(1), int(m.group(2)), m.group(3)
        if mabbr not in _MONTH_ABBR:
            return None
        mo = _MONTH_ABBR.index(mabbr) + 1
        if half == "a":
            return dt.date(year, mo, 1), dt.date(year, mo, 15)
        last = calendar.monthrange(year, mo)[1]
        return dt.date(year, mo, 16), dt.date(year, mo, last)
    if period_type == "month":
        m = re.match(r"^(\d{4})-(\d{2})$", period_value)
        if not m:
            return None
        y, mo = int(m.group(1)), int(m.group(2))
        last = calendar.monthrange(y, mo)[1]
        return dt.date(y, mo, 1), dt.date(y, mo, last)
    return None


def format_period_label(ptype, pvalue, start, end):
    mons = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    if ptype == "week":
        m  = re.match(r"^(\d{4})-wk(\d+)$", pvalue)
        wk = int(m.group(2)) if m else 0
        return f"wk{wk} ({mons[start.month-1]} {start.day} - {mons[end.month-1]} {end.day})"
    if ptype == "payperiod":
        half = pvalue[-1]
        return f"{mons[start.month-1]} {start.year}{half} ({mons[start.month-1]} {start.day}-{end.day})"
    if ptype == "month":
        return f"{mons[start.month-1]} {start.year}"
    return pvalue


def load_rows_for_period(params):
    ptype  = params.get("type",  [None])[0]
    pvalue = params.get("value", [None])[0]
    if ptype and pvalue:
        rng = parse_period(ptype, pvalue)
        if not rng:
            return None, None
        start, end = rng
        rows  = rows_in_range(start, end)
        label = format_period_label(ptype, pvalue, start, end)
        return rows, {"type": ptype, "value": pvalue, "label": label,
                      "start": start.isoformat(), "end": end.isoformat()}
    label = params.get("week", [None])[0]
    if label:
        target = DATA_DIR / f"{label}.csv"
        if not target.exists():
            return None, None
        rows = dedupe_by_entry_id(load_week(target))
        rng  = parse_period("week", label)
        if rng:
            start, end = rng
            return rows, {"type": "week", "value": label,
                          "label": format_period_label("week", label, start, end),
                          "start": start.isoformat(), "end": end.isoformat()}
        return rows, {"type": "week", "value": label, "label": label}
    return None, None


def load_departments():
    cfg   = load_config()
    depts = cfg.get("departments") or []
    out   = []
    for d in depts:
        name = (d.get("name") or "").strip()
        if not name:
            continue
        member_emails = [e.lower().strip() for e in (d.get("member_emails") or []) if e]
        raw_roles     = d.get("member_roles") or {}
        member_roles  = {(k or "").lower().strip(): (v or "").strip()
                         for k, v in raw_roles.items() if k}
        out.append({
            "name":          name,
            "manager_email": (d.get("manager_email") or "").lower().strip(),
            "member_emails": member_emails,
            "member_roles":  member_roles,
            # Preserve entry_filters for subsidiary attribution (verbatim from Panso)
            "entry_filters": d.get("entry_filters") or {},
        })
    return out


# ── Dept Documents helpers (verbatim from Panso panso_local.py L2148–2231) ──

def slugify_dept(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "unknown"


def docs_dir():
    """Return the dept-documents directory (read-only view of Panso's single source).

    D11 decision (2026-06-27): read-only single shared bucket — no drift possible.
      Panso writes  → gs://panso-ph-data/data/dept-documents/  (read/write FUSE mount)
      Mata reads    → same path                                 (readonly=true FUSE mount,
                      see cloudbuild.yaml --add-volume readonly=true)
    The mkdir is wrapped in try/except because the GCS FUSE read-only mount will raise
    OSError(EROFS) if the directory does not yet exist locally; if it already exists in
    the bucket the call is a no-op and succeeds.
    """
    d = DATA_DIR / "dept-documents"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # read-only FUSE mount — directory must already exist in bucket
    return d


def load_dept_documents(dept_name):
    path = docs_dir() / f"{slugify_dept(dept_name)}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("documents") or []
    except Exception:
        return []


def _norm(s):
    return (s or "").strip().lower()


def default_dept_entry_filter(dept_name):
    """Build default (client, project) filter from dept name (verbatim from Panso)."""
    if " - " not in (dept_name or ""):
        return None
    company, function_raw = dept_name.split(" - ", 1)
    code_to_clients = {
        "MG":  ["MG", "Mata Group"],
        "MT":  ["MT", "Mata Technologies"],
        "E":   ["E", "Ehrlich"],
        "MCS": ["MCS", "Mata Creative Services"],
        "TSF": ["TSF", "The Sandbox Foundation", "Sandbox Foundation", "Sandbox"],
    }
    clients = code_to_clients.get(company.strip(), [])
    fn = re.split(r"[\(\-\/]", function_raw, maxsplit=1)[0].strip()
    return {"clients": clients, "projects_contains": [fn] if fn else []}


def _effective_entry_filter(dept):
    """Return effective entry_filter for a dept dict (verbatim from Panso)."""
    raw = (dept.get("entry_filters") if isinstance(dept, dict) else None)
    if raw and (raw.get("clients") or raw.get("projects_contains")):
        return raw
    return default_dept_entry_filter((dept.get("name") or "") if isinstance(dept, dict) else "") or {}


def _prefix_of_dept(name):
    m = re.match(r"^([A-Z]+)\s*-\s*", name or "")
    return m.group(1) if m else ""


PROJECTS_ROSTER = DATA_DIR / "projects-roster.json"


def load_projects_roster():
    """Return list of {id, name, client_name, is_archived, ...} or [] if not synced."""
    if not PROJECTS_ROSTER.exists():
        return []
    try:
        data = json.loads(PROJECTS_ROSTER.read_text(encoding="utf-8"))
        return data.get("projects") or []
    except Exception:
        return []


def _build_dept_resolvers():
    global_roles    = {}
    global_managers = set()
    for _d in load_departments():
        global_managers.add((_d.get("manager_email") or "").lower())
        for _em, _rl in (_d.get("member_roles") or {}).items():
            _em_lc = (_em or "").lower()
            if _em_lc and _em_lc not in global_roles and _rl:
                global_roles[_em_lc] = _rl
    people_extras = (load_config().get("people_extras") or {})
    return global_roles, global_managers, people_extras


def list_available_periods():
    today = dt.date.today()
    years = {today.year}
    for label, _path in discover_weeks():
        m = re.match(r"^(\d{4})-wk\d+$", label)
        if m:
            years.add(int(m.group(1)))
    weeks, pps, months = [], [], []
    for y in sorted(years):
        last_wk = 52
        if y == today.year:
            last_wk = min(53, today.isocalendar()[1] + 1)
        elif y > today.year:
            last_wk = 1
        for w in range(1, last_wk + 1):
            value = f"{y}-wk{w}"
            rng   = parse_period("week", value)
            if not rng:
                continue
            s, e = rng
            if s.isocalendar()[0] != y and e.isocalendar()[0] != y:
                continue
            weeks.append({"type": "week", "value": value,
                          "label": format_period_label("week", value, s, e),
                          "start": s.isoformat(), "end": e.isoformat()})
        for mo in range(1, 13):
            for half in ("a", "b"):
                value = f"{_MONTH_ABBR[mo-1]}{y}{half}"
                rng   = parse_period("payperiod", value)
                if not rng:
                    continue
                s, e = rng
                if y == today.year and s > today and not (s.month == today.month):
                    continue
                pps.append({"type": "payperiod", "value": value,
                             "label": format_period_label("payperiod", value, s, e),
                             "start": s.isoformat(), "end": e.isoformat()})
            value = f"{y}-{mo:02d}"
            rng   = parse_period("month", value)
            if not rng:
                continue
            s, e = rng
            if y == today.year and s > today:
                continue
            months.append({"type": "month", "value": value,
                            "label": format_period_label("month", value, s, e),
                            "start": s.isoformat(), "end": e.isoformat()})
    # Freshness signal: DATA_DIR mtime as ISO UTC datetime, or None if unavailable.
    # Used by the frontend persistent "Data as of —" header chip (task 14).
    data_synced_at = None
    try:
        if DATA_DIR.exists():
            mtime = DATA_DIR.stat().st_mtime
            data_synced_at = dt.datetime.utcfromtimestamp(mtime).strftime("%Y-%m-%d %H:%M UTC")
    except OSError:
        pass
    return {"week": weeks, "payperiod": pps, "month": months, "data_synced_at": data_synced_at}


# ── Static file serving ─────────────────────────────────────────────────────
_STATIC_CACHE: dict = {}
_STATIC_LOCK        = threading.Lock()


def _serve_static(path: Path):
    """Return (bytes_plain, bytes_gz, etag, content_type) for a public file,
    reloading when mtime changes."""
    try:
        st = path.stat()
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    key = str(path)
    with _STATIC_LOCK:
        cached = _STATIC_CACHE.get(key)
        if cached and cached[0] == sig:
            return cached[1]
    body  = path.read_bytes()
    gz    = gzip.compress(body, compresslevel=6)
    etag  = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
    ext   = path.suffix.lower()
    ctype = {"html": "text/html; charset=utf-8",
             ".html": "text/html; charset=utf-8",
             ".js":   "application/javascript; charset=utf-8",
             ".css":  "text/css; charset=utf-8",
             ".json": "application/json; charset=utf-8",
             }.get(ext, "application/octet-stream")
    result = (body, gz, etag, ctype)
    with _STATIC_LOCK:
        _STATIC_CACHE[key] = (sig, result)
    return result


# ── HTTP Handler ─────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    _GZIP_MIN_BYTES = 1024

    def _client_accepts_gzip(self):
        ae = self.headers.get("Accept-Encoding", "") or ""
        return "gzip" in ae.lower()

    def _cors_headers(self, origin):
        if origin in CORS_ALLOWED_ORIGINS or origin is None:
            self.send_header("Access-Control-Allow-Origin", origin or "*")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Max-Age", "86400")

    def _send(self, status, content_type, body, origin=None):
        encoding = None
        if (status == 200
                and len(body) >= self._GZIP_MIN_BYTES
                and self._client_accepts_gzip()
                and any(t in content_type for t in (
                    "text/", "application/json", "application/javascript"))):
            body     = gzip.compress(body, compresslevel=6)
            encoding = "gzip"
        self.send_response(status)
        self.send_header("Content-Type",   content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "no-store")
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary",             "Accept-Encoding")
        self._cors_headers(origin)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload, status=200, origin=None):
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(payload).encode("utf-8"), origin)

    def _send_page(self, path: Path, origin=None):
        """Serve a static HTML/JS/CSS file with gzip + ETag revalidation."""
        result = _serve_static(path)
        if result is None:
            self._send_json({"error": "not found"}, 404, origin=origin)
            return
        body_plain, body_gz, etag, ctype = result
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self._cors_headers(origin)
            self.end_headers()
            return
        body = body_gz if self._client_accepts_gzip() else body_plain
        enc  = "gzip"  if self._client_accepts_gzip() else None
        self.send_response(200)
        self.send_header("Content-Type",   ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control",  "no-cache")
        self.send_header("ETag",           etag)
        if enc:
            self.send_header("Content-Encoding", enc)
            self.send_header("Vary",             "Accept-Encoding")
        self._cors_headers(origin)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        self.send_response(204)
        self._cors_headers(origin)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        u      = urlparse(self.path)
        origin = self.headers.get("Origin")
        try:
            # ── Public pages (no auth) ───────────────────────────────────
            if u.path in ("/", "/index.html"):
                return self._send_page(PUBLIC_DIR / "index.html", origin)

            # Map /tools/<name>.html → public/tools/<name>.html
            if u.path.startswith("/tools/") and u.path.endswith(".html"):
                rel  = u.path.lstrip("/")  # "tools/incentive.html"
                page = PUBLIC_DIR / rel
                return self._send_page(page, origin)

            # Firebase config (public — fetched before sign-in)
            if u.path == "/api/firebase-config":
                cfg_path = Path(_HERE).parent / "secrets" / "firebase-web-config.json"
                if cfg_path.exists():
                    with open(cfg_path) as _f:
                        _web_cfg = json.load(_f)
                else:
                    _web_cfg = {
                        "apiKey":            os.environ.get("FIREBASE_API_KEY", ""),
                        "authDomain":        os.environ.get("FIREBASE_AUTH_DOMAIN",
                                                            "panso-ph.firebaseapp.com"),
                        "projectId":         os.environ.get("FIREBASE_PROJECT_ID", "panso-ph"),
                        "storageBucket":     os.environ.get("FIREBASE_STORAGE_BUCKET",
                                                            "panso-ph.appspot.com"),
                        "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID",
                                                            "965834933814"),
                        "appId":             os.environ.get("FIREBASE_APP_ID", ""),
                    }
                return self._send_json(_web_cfg, origin=origin)

            # Favicon
            if u.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return

            # ── Auth gate ────────────────────────────────────────────────
            if _SKIP_AUTH:
                tier_info = _DEV_TIER
            else:
                try:
                    tier_info = verify_token(
                        extract_bearer_token(self.headers.get("Authorization", ""))
                    )
                except AccessDenied as exc:
                    return self._send_json({"error": exc.message}, exc.status, origin=origin)

            # ── Authenticated endpoints ──────────────────────────────────
            if u.path == "/api/me":
                _me_email  = tier_info["email"]
                _me_admin  = (_me_email == _ADMIN_EMAIL)
                _me_grants = [] if _me_admin else sorted(user_grants(_me_email))
                return self._send_json({
                    "user_email": _me_email,
                    "is_admin":   _me_admin,
                    "grants":     _me_grants,
                    "tier":       tier_info["tier"],
                    "subsidiary": tier_info["subsidiary"],
                    "is_fte":     tier_info["is_fte"],
                }, origin=origin)

            if u.path == "/api/periods":
                return self._send_json(list_available_periods(), origin=origin)

            if u.path == "/api/projects":
                params      = parse_qs(u.query)
                yr          = (params.get("year", [str(dt.date.today().year)])[0] or "").strip()
                year_prefix = (yr + "-") if yr else ""

                # ── Dept-based subsidiary resolver (verbatim from Panso) ──────
                depts = load_departments()
                _sub_clients = {}
                _sub_projs   = {}
                _email_to_dept = {}
                for _d in depts:
                    _pref = _prefix_of_dept(_d.get("name") or "")
                    if not _pref:
                        continue
                    _f = _effective_entry_filter(_d)
                    for _c in (_f.get("clients") or []):
                        _cn = _norm(_c)
                        if _cn:
                            _sub_clients.setdefault(_pref, set()).add(_cn)
                    for _p in (_f.get("projects_contains") or []):
                        _pn = _norm(_p)
                        if _pn:
                            _sub_projs.setdefault(_pref, set()).add(_pn)
                    for _em in (_d.get("member_emails") or []):
                        _email_to_dept.setdefault((_em or "").lower(), _d.get("name") or "")

                _PARENTHETICALS = {"MT": "(mt)", "MCS": "(mcs)", "E": "(e)", "TSF": "(tsf)"}
                _sub_clients_rx = {pref: [(_kn, re.compile(r"\b" + re.escape(_kn) + r"\b", re.IGNORECASE))
                                           for _kn in clients]
                                   for pref, clients in _sub_clients.items()}
                _sub_projs_rx   = {pref: [(_kn, re.compile(r"\b" + re.escape(_kn) + r"\b", re.IGNORECASE))
                                           for _kn in projs_]
                                   for pref, projs_ in _sub_projs.items()}

                def _subsidiary_of(client, project_name):
                    cn = client or ""
                    pn = project_name or ""
                    if cn.strip():
                        for pref, rxs in _sub_clients_rx.items():
                            for _kn, rx in rxs:
                                if rx.search(cn):
                                    return pref
                    pn_lc = pn.lower()
                    if pn_lc:
                        for pref, tag in _PARENTHETICALS.items():
                            if tag in pn_lc:
                                return pref
                    if pn.strip():
                        for pref, rxs in _sub_projs_rx.items():
                            for _kn, rx in rxs:
                                if rx.search(pn):
                                    return pref
                    return ""

                def _sub_from_members(members_list):
                    by_prefix = {}
                    for m in members_list or []:
                        em = (m.get("email") or "").lower()
                        dept_name = _email_to_dept.get(em)
                        if not dept_name:
                            continue
                        pref = _prefix_of_dept(dept_name)
                        if pref:
                            by_prefix[pref] = by_prefix.get(pref, 0.0) + (m.get("hours") or 0)
                    return max(by_prefix.items(), key=lambda kv: kv[1])[0] if by_prefix else ""

                # ── Iterate all CSV rows ──────────────────────────────────────
                projs = {}
                for _lbl, path in discover_weeks():
                    for r in load_week(path):
                        d_date = (r.get("date") or "").strip()
                        if year_prefix and not d_date.startswith(year_prefix):
                            continue
                        key = (r.get("project_name") or "").strip()
                        if not key:
                            continue
                        p = projs.setdefault(key, {
                            "project":       key,
                            "client":        (r.get("client_name") or "").strip(),
                            "total_seconds": 0,
                            "_member_secs":  {},
                            "_member_name":  {},
                            "_people":       set(),
                        })
                        secs = int(r.get("duration_seconds") or 0)
                        p["total_seconds"] += secs
                        e = (r.get("user_email") or "").strip()
                        if e:
                            p["_people"].add(e)
                            ek = e.lower()
                            p["_member_secs"][ek] = p["_member_secs"].get(ek, 0) + secs
                            nm = (r.get("user_name") or "").strip()
                            if nm and len(nm) > len(p["_member_name"].get(ek, "")):
                                p["_member_name"][ek] = nm

                out = []
                for p in projs.values():
                    members = sorted(
                        [{"email": ek, "name": p["_member_name"].get(ek, ek),
                          "hours": round(secs / 3600, 1)}
                         for ek, secs in p["_member_secs"].items() if secs > 0],
                        key=lambda m: -m["hours"]
                    )
                    _sub = _subsidiary_of(p["client"], p["project"])
                    if not _sub:
                        _sub = _sub_from_members(members)
                    out.append({
                        "project":      p["project"],
                        "client":       p["client"],
                        "subsidiary":   _sub,
                        "total_hours":  round(p["total_seconds"] / 3600, 1),
                        "people_count": len(p["_people"]),
                    })

                # ── Merge roster so zero-hour projects appear too ─────────────
                seen_names = {x["project"].lower(): True for x in out}
                for rp in load_projects_roster():
                    name = (rp.get("name") or "").strip()
                    if not name or name.lower() in seen_names:
                        continue
                    client = (rp.get("client_name") or "").strip()
                    _sub   = _subsidiary_of(client, name)
                    out.append({
                        "project":      name,
                        "client":       client,
                        "subsidiary":   _sub,
                        "total_hours":  0.0,
                        "people_count": 0,
                    })

                out.sort(key=lambda x: x["project"].lower())
                return self._send_json({"items": out}, origin=origin)

            if u.path == "/api/finance/incentive":
                try:
                    gate_request(tier_info, require_comp=True)
                except AccessDenied as exc:
                    return self._send_json({"error": exc.message}, exc.status, origin=origin)
                params    = parse_qs(u.query)
                proj_name = (params.get("project", [""])[0] or "").strip()
                if not proj_name:
                    return self._send_json({"error": "missing project"}, 400, origin=origin)
                try:
                    multiplier = float(params.get("multiplier", ["2.0"])[0])
                except (TypeError, ValueError):
                    multiplier = 2.0
                rows, meta = load_rows_for_period(params)
                if rows is None:
                    return self._send_json({"error": "missing period"}, 400, origin=origin)
                rows     = exclude_leaves(rows)
                proj_lc        = proj_name.lower().strip()
                by_email       = {}
                max_entry_date: str | None = None
                for r in rows:
                    if (r.get("project_name") or "").lower().strip() != proj_lc:
                        continue
                    em   = (r.get("user_email") or "").lower().strip()
                    if not em:
                        continue
                    secs = int(r.get("duration_seconds") or 0)
                    if secs <= 0:
                        continue
                    entry_date = (r.get("date") or "").strip()
                    if entry_date:
                        if max_entry_date is None or entry_date > max_entry_date:
                            max_entry_date = entry_date
                    bucket = by_email.setdefault(em, {
                        "user_email": em,
                        "user_name":  r.get("user_name") or em,
                        "seconds":    0,
                    })
                    if r.get("user_name") and len(r["user_name"]) > len(bucket["user_name"]):
                        bucket["user_name"] = r["user_name"]
                    bucket["seconds"] += secs
                cfg     = load_config()
                _extras = cfg.get("people_extras") or {}
                global_roles, _gm, _pe = _build_dept_resolvers()
                items       = []
                grand_hours = grand_base = grand_inc = 0.0
                for em, b in by_email.items():
                    extras = _extras.get(em) or _extras.get(em.lower()) or {}
                    rate   = extras.get("hourly")
                    try:
                        rate_f = float(rate) if rate not in (None, "") else 0.0
                    except (TypeError, ValueError):
                        rate_f = 0.0
                    hours     = round(b["seconds"] / 3600.0, 2)
                    base      = round(hours * rate_f, 2)
                    incentive = round(base * (max(0.0, multiplier - 1.0)), 2)
                    grand_hours += hours
                    grand_base  += base
                    grand_inc   += incentive
                    is_sandbox = em.endswith("@sandbox.org.ph")
                    role_raw   = (global_roles.get(em) or global_roles.get(em.lower()) or "").strip().lower()
                    if not role_raw and is_sandbox:
                        role_raw = "intern"
                    is_fte = (not is_sandbox) and role_raw not in ("intern", "ptf")
                    items.append({
                        "user_email":  em,
                        "user_name":   b["user_name"],
                        "hours":       hours,
                        "hourly_rate": rate_f,
                        "base":        base,
                        "multiplier":  multiplier,
                        "incentive":   incentive,
                        "has_rate":    rate_f > 0,
                        "role":        role_raw or "staff",
                        "is_fte":      is_fte,
                    })
                items.sort(key=lambda x: (-x["incentive"], x["user_name"].lower()))
                return self._send_json({
                    "project":    proj_name,
                    "period":     meta,
                    "multiplier": multiplier,
                    "items":      items,
                    "totals": {
                        "hours":     round(grand_hours, 2),
                        "base":      round(grand_base, 2),
                        "incentive": round(grand_inc, 2),
                    },
                    "data_as_of": max_entry_date,
                    "policy_note": (
                        "incentive = hourly_rate × hours × (multiplier − 1.0) "
                        "— i.e. 1.0x = no bonus, 1.5x = +50%, 2.0x = double pay "
                        "(per Q2 thread with norisa@mata.ph)"
                    ),
                }, origin=origin)

            if u.path == "/api/hr/interns/pay":
                if not can_access_scope(tier_info, "dept::MG - HR"):
                    return self._send_json({"error": "Restricted to MG - HR."}, 403, origin=origin)
                params = parse_qs(u.query)
                rows, meta = load_rows_for_period(params)
                if rows is None:
                    return self._send_json({"error": "missing period"}, 400, origin=origin)
                interns_csv = ALPHALISTS_DIR / "export_interns.csv"
                if not interns_csv.exists():
                    return self._send_json(
                        {"error": "alphalists/export_interns.csv missing"},
                        404, origin=origin)
                import csv as _csv
                interns = []
                with interns_csv.open(encoding="utf-8") as fh:
                    rdr = _csv.reader(fh)
                    for row in rdr:
                        if len(row) < 7:
                            continue
                        em = (row[0] or "").strip().lower()
                        if not em or "@" not in em:
                            continue
                        interns.append({
                            "email":   em,
                            "manager": (row[1] or "").strip(),
                            "name":    (row[2] or "").strip(),
                            "company": (row[4] or "").strip() if len(row) > 4 else "",
                            "dept":    (row[5] or "").strip() if len(row) > 5 else "",
                        })
                if not interns:
                    return self._send_json({"items": [], "period": meta}, origin=origin)
                cfg       = load_config()
                pay_basis = cfg.get("intern_pay_basis") or {}
                depts_for_attribution = load_departments()

                def _subsidiary_for(client, project_name):
                    fake_row = {"client_name": client, "project_name": project_name}
                    for d in depts_for_attribution:
                        f = _effective_entry_filter(d)
                        if not (f.get("clients") or f.get("projects_contains")):
                            continue
                        if entry_matches_dept(fake_row, f):
                            name = d.get("name") or ""
                            m = re.match(r"^([A-Z]+)\s*-\s*", name)
                            return m.group(1) if m else ""
                    return ""

                wanted_emails = {it["email"] for it in interns}
                from collections import defaultdict as _dd
                by_email_buckets = _dd(lambda: {"productive": 0, "billable": 0,
                                                "training": 0, "experimental": 0, "idle": 0})
                by_email_cats   = _dd(lambda: _dd(int))
                latest_by_email = {}
                first_by_email  = {}
                for r in rows:
                    em = (r.get("user_email") or "").lower().strip()
                    if em not in wanted_emails:
                        continue
                    if is_leave_row(r):
                        continue
                    secs = int(r.get("duration_seconds") or 0)
                    if secs <= 0:
                        continue
                    d     = (r.get("date") or "").strip()
                    basis = pay_basis.get(em) or {}
                    start = (basis.get("start_date") or "").strip()
                    if start and d and d < start:
                        continue
                    bucket = classify_pay_row(r, _subsidiary_for)
                    by_email_buckets[em][bucket] += secs
                    if bucket == "productive":
                        code, _lbl = parse_category(r.get("task_name") or "")
                        by_email_cats[em][code] += secs
                    if d:
                        if em not in latest_by_email or d > latest_by_email[em]:
                            latest_by_email[em] = d
                        if em not in first_by_email or d < first_by_email[em]:
                            first_by_email[em] = d

                PRODUCTIVE_RATE = 15.0
                BILLABLE_RATE   = 30.0
                CAT_DEFS = [
                    {"code": "0",  "label": "Strategy / Reports",         "rate": PRODUCTIVE_RATE},
                    {"code": "1a", "label": "Internal meetings",          "rate": 0.0},
                    {"code": "1b", "label": "External meetings",          "rate": 0.0},
                    {"code": "1c", "label": "Personal admin / task mgmt", "rate": 0.0},
                    {"code": "2",  "label": "Design",                     "rate": PRODUCTIVE_RATE},
                    {"code": "3",  "label": "Development / Build",        "rate": PRODUCTIVE_RATE},
                    {"code": "4",  "label": "QA / Project mgmt",          "rate": PRODUCTIVE_RATE},
                    {"code": "5",  "label": "Other / Idle",               "rate": 0.0},
                ]
                items = []
                for it in interns:
                    em    = it["email"]
                    basis = pay_basis.get(em) or {}
                    start = (basis.get("start_date") or "").strip() or None
                    buckets = by_email_buckets.get(em, {"productive": 0, "billable": 0,
                                                        "training": 0, "experimental": 0, "idle": 0})
                    pay = {
                        "productive":   round(buckets["productive"] / 3600.0 * PRODUCTIVE_RATE, 2),
                        "billable":     round(buckets["billable"]   / 3600.0 * BILLABLE_RATE,   2),
                        "training":     0.0,
                        "experimental": 0.0,
                        "idle":         0.0,
                    }
                    total_pay = round(pay["productive"] + pay["billable"], 2)
                    cats_secs = by_email_cats.get(em, {})
                    cat_breakdown = []
                    for cd in CAT_DEFS:
                        secs = sum(s for k, s in cats_secs.items() if k.startswith(cd["code"]))
                        hrs  = round(secs / 3600.0, 2)
                        cat_breakdown.append({
                            "code":  cd["code"],
                            "label": cd["label"],
                            "rate":  cd["rate"],
                            "hours": hrs,
                            "pay":   round(hrs * cd["rate"], 2),
                        })
                    items.append({
                        "email":   em,
                        "name":    it["name"],
                        "manager": it["manager"],
                        "dept":    it["dept"],
                        "pay_basis": {
                            "start_date":      start,
                            "productive_rate": PRODUCTIVE_RATE,
                            "billable_rate":   BILLABLE_RATE,
                        },
                        "hours":         {k: round(v / 3600.0, 2) for k, v in buckets.items()},
                        "pay":           pay,
                        "total_pay":     total_pay,
                        "cat_breakdown": cat_breakdown,
                        "categories":    [{"code": c, "hours": round(s / 3600.0, 2)}
                                          for c, s in sorted(cats_secs.items())],
                        "first_logged":  first_by_email.get(em),
                        "latest_logged": latest_by_email.get(em),
                        "has_basis":     True,
                    })
                items.sort(key=lambda x: (
                    (x["manager"] or "(unassigned)").lower(),
                    -x["total_pay"],
                    (x["name"] or "").lower(),
                ))
                return self._send_json({
                    "items":  items,
                    "period": meta,
                    "policy": {
                        "name":            "Flexible Internship Allowance Policy",
                        "policy_no":       "002502",
                        "version":         "1.1",
                        "effective":       "2025-08-16",
                        "productive_rate": 15.0,
                        "billable_rate":   30.0,
                        "rate_formula":    "flat ₱15/hr productive, ₱30/hr billable (numbered non-TSF)",
                        "url":             "https://docs.google.com/document/d/1jdcCLyxdjUb53iyWkpbVxeYku7Pi8qO3/edit",
                    },
                }, origin=origin)

            if u.path == "/api/hr/interns":
                if not can_access_scope(tier_info, "dept::MG - HR"):
                    return self._send_json({"error": "Restricted to MG - HR."}, 403, origin=origin)
                interns_csv = ALPHALISTS_DIR / "export_interns.csv"
                if not interns_csv.exists():
                    return self._send_json({"error": "alphalists/export_interns.csv missing — refresh from the Payroll 2026 sheet"}, 404, origin=origin)
                import csv as _csv
                interns = []
                with interns_csv.open(encoding="utf-8") as fh:
                    rdr = _csv.reader(fh)
                    for row in rdr:
                        if len(row) < 7:
                            continue
                        email = (row[0] or "").strip().lower()
                        if not email or "@" not in email:
                            continue
                        interns.append({
                            "email":   email,
                            "manager": (row[1] or "").strip(),
                            "name":    (row[2] or "").strip(),
                            "status":  (row[3] or "").strip() or "Active",
                            "company": (row[4] or "").strip(),
                            "dept":    (row[5] or "").strip(),
                            "role":    (row[6] or "").strip(),
                        })
                if not interns:
                    return self._send_json({"items": [], "by_manager": {}, "stats": {}}, origin=origin)
                from collections import defaultdict
                hours_by_email      = defaultdict(int)
                first_date_by_email = {}
                last_date_by_email  = {}
                days_active_by_email= defaultdict(set)
                wanted_emails       = {i["email"] for i in interns}
                for _label, path in discover_weeks():
                    for r in load_week(path):
                        em   = (r.get("user_email") or "").lower().strip()
                        if em not in wanted_emails:
                            continue
                        secs = int(r.get("duration_seconds") or 0)
                        if not secs:
                            continue
                        d = (r.get("date") or "").strip()
                        hours_by_email[em] += secs
                        if d:
                            days_active_by_email[em].add(d)
                            if em not in first_date_by_email or d < first_date_by_email[em]:
                                first_date_by_email[em] = d
                            if em not in last_date_by_email or d > last_date_by_email[em]:
                                last_date_by_email[em] = d
                _csv201 = ALPHALISTS_DIR / "201.csv"
                _af_targets = {}
                if _csv201.exists():
                    import csv as _csv201_mod
                    with _csv201.open(encoding="utf-8") as _fh201:
                        _rows201 = list(_csv201_mod.reader(_fh201))
                    for _r201 in _rows201:
                        if len(_r201) <= 31:
                            continue
                        _af_val = (_r201[31] or "").strip()
                        _af_float = None
                        if _af_val:
                            try:
                                _af_float = float(_af_val.replace(",", ""))
                            except (TypeError, ValueError):
                                pass
                        for _cv in _r201:
                            _cv_s = (_cv or "").strip().lower()
                            if "@" in _cv_s and "." in _cv_s.split("@")[-1]:
                                _af_targets[_cv_s] = _af_float
                                _local = _cv_s.split("@")[0]
                                if _local and _local not in _af_targets:
                                    _af_targets[_local] = _af_float
                cfg   = load_config()
                today = dt.date.today()
                out_items = []
                for it in interns:
                    em   = it["email"]
                    secs = hours_by_email.get(em, 0)
                    hrs  = round(secs / 360) / 10
                    target_raw = _af_targets.get(em)
                    if target_raw is None:
                        _local = em.split("@")[0] if "@" in em else em
                        target_raw = _af_targets.get(_local)
                    target_missing = (target_raw is None)
                    target = target_raw if target_raw is not None else 600.0
                    pct = (hrs / target * 100) if target > 0 else 0
                    pct_capped = min(100, pct)
                    first_d = first_date_by_email.get(em)
                    last_d  = last_date_by_email.get(em)
                    days_active   = len(days_active_by_email.get(em, set()))
                    days_since_last = None
                    if last_d:
                        try:
                            days_since_last = (today - dt.date.fromisoformat(last_d)).days
                        except ValueError:
                            pass
                    days_since_first = None
                    if first_d:
                        try:
                            days_since_first = (today - dt.date.fromisoformat(first_d)).days + 1
                        except ValueError:
                            pass
                    if not first_d:
                        status_bucket = "not_started"
                    elif pct >= 100:
                        status_bucket = "completed"
                    elif (days_since_last is not None) and days_since_last > 14:
                        status_bucket = "stalled"
                    elif pct >= 75:
                        status_bucket = "final"
                    elif pct >= 25:
                        status_bucket = "mid"
                    else:
                        status_bucket = "early"
                    out_items.append({
                        **it,
                        "hours_logged":   hrs,
                        "target_hours":   None if target_missing else target,
                        "target_missing": target_missing,
                        "pct":            round(pct, 1) if not target_missing else None,
                        "pct_capped":     round(pct_capped, 1) if not target_missing else None,
                        "first_date":     first_d,
                        "last_date":      last_d,
                        "days_since_first": days_since_first,
                        "days_since_last":  days_since_last,
                        "days_active":    days_active,
                        "status_bucket":  status_bucket if not target_missing else "no_target",
                    })
                by_manager = defaultdict(list)
                for it in out_items:
                    by_manager[it["manager"] or "(unassigned)"].append(it)
                for k in by_manager:
                    by_manager[k].sort(key=lambda x: (-(x["pct"] or 0), (x["name"] or "").lower()))
                manager_order = sorted(by_manager.keys(),
                                       key=lambda m: (-len(by_manager[m]), m.lower()))
                _pct_valid = [x["pct_capped"] for x in out_items if x.get("pct_capped") is not None]
                stats = {
                    "total":       len(out_items),
                    "no_target":   sum(1 for x in out_items if x.get("target_missing")),
                    "completed":   sum(1 for x in out_items if x["status_bucket"] == "completed"),
                    "final":       sum(1 for x in out_items if x["status_bucket"] == "final"),
                    "mid":         sum(1 for x in out_items if x["status_bucket"] == "mid"),
                    "early":       sum(1 for x in out_items if x["status_bucket"] == "early"),
                    "not_started": sum(1 for x in out_items if x["status_bucket"] == "not_started"),
                    "stalled":     sum(1 for x in out_items if x["status_bucket"] == "stalled"),
                    "avg_pct":     round(sum(_pct_valid) / len(_pct_valid), 1) if _pct_valid else 0,
                }
                return self._send_json({
                    "items":         out_items,
                    "by_manager":    dict(by_manager),
                    "manager_order": manager_order,
                    "stats":         stats,
                    "source":        "alphalists/export_interns.csv",
                }, origin=origin)
            if u.path == "/api/leaves":
                if not can_access_scope(tier_info, "dept::MG - HR"):
                    return self._send_json({"error": "Restricted to MG - HR."}, 403, origin=origin)
                params = parse_qs(u.query)
                date_from = params.get("date_from", [None])[0]
                date_to   = params.get("date_to",   [None])[0]
                if date_from and date_to:
                    all_rows = []
                    for _lbl, _path in discover_weeks():
                        all_rows.extend(load_week(_path))
                    rows = [r for r in all_rows if date_from <= (r.get("date") or "") <= date_to]
                    meta = {"start": date_from, "end": date_to}
                else:
                    rows, meta = load_rows_for_period(params)
                    if rows is None:
                        return self._send_json({"error": "missing period"}, 400, origin=origin)
                _cfg_leaves = load_config()
                # D05: build status map; exclude Resigned/AWOL/non-{Active,Floating}
                _status_map_leaves = {
                    (em or "").lower(): (extras.get("status") or "").strip().lower()
                    for em, extras in (_cfg_leaves.get("people_extras") or {}).items()
                }
                _ACTIVE_ST_L = {"", "active", "floating"}
                _emp_type_map = {}
                for _d in (_cfg_leaves.get("departments") or []):
                    _dn = (_d.get("name") or "")
                    _et = "Central" if _dn.startswith("MG") else ("Assigned" if _dn else "")
                    for _em in (_d.get("member_emails") or []):
                        _emp_type_map[(_em or "").lower()] = _et
                items = []
                leave_pat = re.compile(r"\b(leave|sick|vacation|VL|SL|maternity|paternity|bereavement|emergency leave)\b", re.I)
                for r in rows:
                    _em_st_chk = (r.get("user_email") or "").lower().strip()
                    _lst = _status_map_leaves.get(_em_st_chk, "")
                    if _lst and _lst not in _ACTIVE_ST_L:
                        continue  # D05: exclude Resigned/AWOL/non-{Active,Floating}
                    blob = " ".join(str(r.get(k) or "") for k in ("project_name", "task_name", "description", "tags"))
                    if not leave_pat.search(blob):
                        continue
                    kind = ""
                    low = blob.lower()
                    if "maternity" in low:   kind = "Maternity"
                    elif "paternity" in low: kind = "Paternity"
                    elif "bereavement" in low: kind = "Bereavement"
                    elif "sick" in low or re.search(r"\bSL\b", blob): kind = "Sick"
                    elif "vacation" in low or re.search(r"\bVL\b", blob): kind = "Vacation"
                    elif "emergency" in low: kind = "Emergency"
                    else: kind = "Leave"
                    _em_lc = (r.get("user_email") or "").lower().strip()
                    items.append({
                        "date":        r.get("date", ""),
                        "user_email":  _em_lc,
                        "user_name":   r.get("user_name", ""),
                        "kind":        kind,
                        "description": r.get("description") or r.get("task_name") or "",
                        "hours":       round(int(r.get("duration_seconds") or 0) / 3600, 1),
                        "emp_type":    _emp_type_map.get(_em_lc, ""),
                    })
                items.sort(key=lambda x: (x["date"], x["user_name"]))
                return self._send_json({"items": items, "period": meta}, origin=origin)

            if u.path == "/api/hr/dates":
                if not can_access_scope(tier_info, "dept::MG - HR"):
                    return self._send_json({"error": "Restricted to MG - HR."}, 403, origin=origin)
                params = parse_qs(u.query)
                try:
                    year  = int(params.get("year",  [""])[0])
                    month = int(params.get("month", [""])[0])
                except (ValueError, TypeError):
                    return self._send_json({"error": "year and month required (integers)"}, 400, origin=origin)
                if month < 1 or month > 12:
                    return self._send_json({"error": "month must be 1-12"}, 400, origin=origin)
                cfg   = load_config()
                # D05: status map — exclude Resigned/AWOL/non-{Active,Floating}
                _status_map_dates = {
                    (em or "").lower(): (extras.get("status") or "").strip().lower()
                    for em, extras in (cfg.get("people_extras") or {}).items()
                }
                _ACTIVE_ST_D = {"", "active", "floating"}
                dates = cfg.get("person_dates") or {}
                email_to_dept = {}
                email_to_name = {}
                for d in (cfg.get("departments") or []):
                    for em in (d.get("member_emails") or []):
                        email_to_dept[em.lower()] = d.get("name") or ""
                for _label, path in reversed(discover_weeks()):
                    for r in load_week(path):
                        e = (r.get("user_email") or "").lower().strip()
                        if e and e not in email_to_name:
                            email_to_name[e] = r.get("user_name") or e
                # Also scan export_interns.csv for names not in time-tracking data.
                _interns_csv_dates = ALPHALISTS_DIR / "export_interns.csv"
                if _interns_csv_dates.exists():
                    import csv as _csv_di
                    with _interns_csv_dates.open(encoding="utf-8") as _fh_di:
                        for _ri_di in _csv_di.reader(_fh_di):
                            if len(_ri_di) < 3:
                                continue
                            _em_di = (_ri_di[0] or "").strip().lower()
                            _nm_di = (_ri_di[2] or "").strip()
                            if _em_di and "@" in _em_di and _nm_di and _em_di not in email_to_name:
                                email_to_name[_em_di] = _nm_di
                # Last-resort: humanize email local-part (e.g. carlo@mata.ph -> "Carlo").
                def _humanize_email_dates(em):
                    local = (em or "").split("@")[0]
                    parts = re.split(r"[._\-]+", local)
                    parts = [p.title() for p in parts if p and not p.isdigit()]
                    return " ".join(parts) if parts else em
                def _resolve_name_dates(em):
                    return email_to_name.get(em) or _humanize_email_dates(em)
                items = []
                for email, rec in dates.items():
                    email_lc = (email or "").lower()
                    _pst_d = _status_map_dates.get(email_lc, "")
                    if _pst_d and _pst_d not in _ACTIVE_ST_D:
                        continue  # D05: exclude Resigned/AWOL
                    _is_float_d = (_pst_d == "floating")
                    for kind, iso in (("birthday", rec.get("birthday")), ("milestone", rec.get("hire_date"))):
                        if not iso:
                            continue
                        try:
                            d0 = dt.date.fromisoformat(iso)
                        except ValueError:
                            continue
                        if d0.month != month:
                            continue
                        try:
                            event_date = dt.date(year, d0.month, d0.day)
                        except ValueError:
                            event_date = dt.date(year, d0.month, 28)
                        item = {
                            "date":       event_date.isoformat(),
                            "user_email": email_lc,
                            "user_name":  _resolve_name_dates(email_lc),
                            "dept":       email_to_dept.get(email_lc, ""),
                            "kind":       kind,
                            "status":     "Floating" if _is_float_d else "Active",
                        }
                        if _is_float_d:
                            item["flag"] = "floating"
                        if kind == "birthday":
                            item["age"] = year - d0.year if d0.year else None
                        else:
                            item["years"]     = year - d0.year if d0.year else None
                            item["hire_date"] = iso
                        items.append(item)
                seen_milestone = {item["user_email"] for item in items if item.get("kind") == "milestone"}
                _people_extras = (cfg.get("people_extras") or {})
                _reg_days      = cfg.get("regularization_days") or 180
                for email, extras in _people_extras.items():
                    email_lc = (email or "").lower()
                    _pst_e = _status_map_dates.get(email_lc, "")
                    if _pst_e and _pst_e not in _ACTIVE_ST_D:
                        continue  # D05: exclude Resigned/AWOL/non-{Active,Floating}
                    _is_float_e = (_pst_e == "floating")
                    _sfte  = (extras.get("sdate_fte")   or "").strip()
                    _sptf  = (extras.get("sdate_ptf")   or "").strip()
                    _sintn = (extras.get("sdate_intern") or "").strip()
                    iso = _sfte or _sptf or _sintn
                    emp_level = "fte" if _sfte else ("ptf" if _sptf else ("intern" if _sintn else ""))
                    if email_lc not in seen_milestone and iso:
                        try:
                            d0 = dt.date.fromisoformat(iso)
                        except ValueError:
                            d0 = None
                        if d0 and d0.month == month:
                            try:
                                event_date = dt.date(year, d0.month, d0.day)
                            except ValueError:
                                event_date = dt.date(year, d0.month, 28)
                            _ms_item = {
                                "date":       event_date.isoformat(),
                                "user_email": email_lc,
                                "user_name":  _resolve_name_dates(email_lc),
                                "dept":       email_to_dept.get(email_lc, ""),
                                "kind":       "milestone",
                                "emp_level":  emp_level,
                                "years":      year - d0.year if d0.year else None,
                                "hire_date":  iso,
                                "status":     "Floating" if _is_float_e else "Active",
                            }
                            if _is_float_e:
                                _ms_item["flag"] = "floating"
                            items.append(_ms_item)
                    if _sfte:
                        try:
                            fte_d = dt.date.fromisoformat(_sfte)
                            reg_d = fte_d + dt.timedelta(days=_reg_days)
                        except (ValueError, OverflowError):
                            reg_d = None
                        if reg_d and reg_d.year == year and reg_d.month == month:
                            _reg_item = {
                                "date":       reg_d.isoformat(),
                                "user_email": email_lc,
                                "user_name":  _resolve_name_dates(email_lc),
                                "dept":       email_to_dept.get(email_lc, ""),
                                "kind":       "regularization",
                                "sdate_fte":  _sfte,
                                "emp_level":  "fte",
                                "status":     "Floating" if _is_float_e else "Active",
                            }
                            if _is_float_e:
                                _reg_item["flag"] = "floating"
                            items.append(_reg_item)
                    # D05: floating_review — one per Floating person per queried month
                    if _is_float_e:
                        _flt_day = 1
                        _sdate_for_day = _sfte or _sptf or _sintn
                        if _sdate_for_day:
                            try:
                                _flt_day = dt.date.fromisoformat(_sdate_for_day).day
                            except ValueError:
                                _flt_day = 1
                        try:
                            _flt_date = dt.date(year, month, _flt_day)
                        except ValueError:
                            _flt_date = dt.date(year, month, 1)
                        items.append({
                            "date":       _flt_date.isoformat(),
                            "user_email": email_lc,
                            "user_name":  _resolve_name_dates(email_lc),
                            "dept":       email_to_dept.get(email_lc, ""),
                            "kind":       "floating_review",
                            "status":     "Floating",
                            "flag":       "floating",
                            "message":    "Floating status · 6-month cap. Please review: regularize or update 201 Status.",
                        })
                _et_map_dates = {}
                for _d2 in (cfg.get("departments") or []):
                    _dn2 = (_d2.get("name") or "")
                    _et2 = "Central" if _dn2.startswith("MG") else ("Assigned" if _dn2 else "")
                    for _em2 in (_d2.get("member_emails") or []):
                        _et_map_dates[(_em2 or "").lower()] = _et2
                for _it2 in items:
                    _it2["emp_type"] = _et_map_dates.get((_it2.get("user_email") or "").lower(), "")
                # Intra-identity de-dupe: collapse same resolved-name + kind + date.
                _seen_nkd = set()
                _deduped_items = []
                for _it3 in items:
                    _nkd = (_it3["date"], _it3["kind"], (_it3["user_name"] or "").lower())
                    if _nkd not in _seen_nkd:
                        _seen_nkd.add(_nkd)
                        _deduped_items.append(_it3)
                items = _deduped_items
                items.sort(key=lambda x: (x["date"], x["user_name"]))
                return self._send_json({"items": items, "year": year, "month": month}, origin=origin)

            # ── Ops: Workload Matrix ────────────────────────────────────────────
            if u.path == "/api/ops/workload":
                if not can_access_scope(tier_info, "dept::MT - Operations (Domestic)"):
                    return self._send_json({"error": "Restricted to MT - Operations (Domestic)."}, 403, origin=origin)
                params = parse_qs(u.query)
                names_raw = (params.get("names", [None])[0] or "").strip()
                if not names_raw:
                    names_raw = (params.get("name", [None])[0] or "").strip()
                start_raw = (params.get("start", [None])[0] or "").strip()
                end_raw   = (params.get("end",   [None])[0] or "").strip()
                clients_only = (params.get("clients_only", [None])[0] or "").strip().lower() in ("1", "true", "yes")
                if not names_raw:
                    return self._send_json({"error": "missing names"}, 400, origin=origin)
                try:
                    start_date = dt.date.fromisoformat(start_raw)
                    end_date   = dt.date.fromisoformat(end_raw)
                except ValueError:
                    return self._send_json({"error": "start + end must be ISO YYYY-MM-DD"}, 400, origin=origin)
                if end_date < start_date:
                    return self._send_json({"error": "end must be on or after start"}, 400, origin=origin)
                wanted_names = [n.strip() for n in names_raw.split(",") if n.strip()]
                depts = load_departments()
                depts_by_name = {d["name"]: d for d in depts}
                selected_depts = [(n, depts_by_name.get(n)) for n in wanted_names]
                missing = [n for (n, d) in selected_depts if d is None]
                if missing:
                    return self._send_json({"error": f"dept(s) not found: {', '.join(missing)}"}, 404, origin=origin)
                wl_rows = rows_in_range(start_date, end_date)
                wl_rows = exclude_leaves(wl_rows)
                all_active = all_time_active_emails()
                dept_of_email    = {}
                dept_role_lookup = {}
                dept_team_lookup = {}
                dept_manager     = {}
                for wl_n, wl_dept in selected_depts:
                    roles_dict = wl_dept.get("member_roles") or {}
                    teams_dict = wl_dept.get("member_teams") or {}
                    dept_manager[wl_n] = (wl_dept.get("manager_email") or "").lower()
                    for wl_em in (wl_dept.get("member_emails") or []):
                        wl_el = (wl_em or "").lower().strip()
                        if not wl_el or wl_el not in all_active:
                            continue
                        dept_role_lookup[(wl_n, wl_el)] = roles_dict.get(wl_em, "") or roles_dict.get(wl_el, "")
                        dept_team_lookup[(wl_n, wl_el)] = teams_dict.get(wl_em, "") or teams_dict.get(wl_el, "")
                        if wl_el not in dept_of_email:
                            dept_of_email[wl_el] = wl_n
                wl_cells         = {}
                wl_proj_totals   = {}
                wl_user_totals   = {}
                wl_users_meta    = {}
                wl_projects_meta = {}
                for wl_r in wl_rows:
                    wl_em = (wl_r.get("user_email") or "").lower().strip()
                    if wl_em not in dept_of_email:
                        continue
                    wl_proj = (wl_r.get("project_name") or "").strip() or "(no project)"
                    wl_secs = int(wl_r.get("duration_seconds") or 0)
                    if not wl_secs:
                        continue
                    wl_num = int(re.match(r"^\s*(\d+)", wl_proj).group(1)) if re.match(r"^\s*(\d+)", wl_proj) else -1
                    if clients_only and wl_num < 0:
                        continue
                    wl_cells[(wl_proj, wl_em)] = wl_cells.get((wl_proj, wl_em), 0) + wl_secs
                    wl_proj_totals[wl_proj] = wl_proj_totals.get(wl_proj, 0) + wl_secs
                    wl_user_totals[wl_em]   = wl_user_totals.get(wl_em, 0) + wl_secs
                    if wl_em not in wl_users_meta:
                        wl_home = dept_of_email[wl_em]
                        wl_role = dept_role_lookup.get((wl_home, wl_em), "")
                        if not wl_role:
                            wl_role = "lead" if wl_em == dept_manager.get(wl_home) else "staff"
                        wl_users_meta[wl_em] = {
                            "email": wl_em,
                            "name":  wl_r.get("user_name") or wl_em,
                            "role":  wl_role,
                            "team":  dept_team_lookup.get((wl_home, wl_em), ""),
                            "dept":  wl_home,
                        }
                    if wl_proj not in wl_projects_meta:
                        wl_projects_meta[wl_proj] = {
                            "project_name": wl_proj,
                            "client_name":  wl_r.get("client_name") or "",
                        }
                wl_proj_list = []
                for wl_p, wl_pm in wl_projects_meta.items():
                    wl_pm["total_seconds"] = wl_proj_totals.get(wl_p, 0)
                    wl_pm["total_hours"]   = round(wl_pm["total_seconds"] / 360) / 10
                    wl_proj_list.append(wl_pm)
                def wl_proj_sort(wl_pp):
                    wl_nm = int(re.match(r"^\s*(\d+)", wl_pp["project_name"]).group(1)) if re.match(r"^\s*(\d+)", wl_pp["project_name"]) else -1
                    return (1 if wl_nm < 0 else 0, wl_nm, (wl_pp["project_name"] or "").lower())
                wl_proj_list.sort(key=wl_proj_sort)
                wl_role_rank = {"lead": 0, "staff": 1, "intern": 2, "ptf": 3}
                wl_users_by_dept = {wl_n: [] for wl_n, _ in selected_depts}
                for wl_em, wl_um in wl_users_meta.items():
                    wl_um["total_seconds"] = wl_user_totals.get(wl_em, 0)
                    wl_um["total_hours"]   = round(wl_um["total_seconds"] / 360) / 10
                    wl_users_by_dept[wl_um["dept"]].append(wl_um)
                for wl_n in wl_users_by_dept:
                    wl_users_by_dept[wl_n].sort(key=lambda wl_u: (
                        wl_role_rank.get((wl_u.get("role") or "").lower(), 9),
                        -(wl_u.get("total_seconds") or 0),
                        (wl_u.get("name") or "").lower(),
                    ))
                wl_user_list = []
                for wl_n, _ in selected_depts:
                    wl_user_list.extend(wl_users_by_dept[wl_n])
                wl_matrix = {}
                for (wl_p, wl_em), wl_secs in wl_cells.items():
                    wl_matrix.setdefault(wl_p, {})[wl_em] = round(wl_secs / 360) / 10
                wl_grand = sum(wl_user_totals.values())
                return self._send_json({
                    "dept_names":    wanted_names,
                    "start":         start_date.isoformat(),
                    "end":           end_date.isoformat(),
                    "clients_only":  clients_only,
                    "projects":      wl_proj_list,
                    "users":         wl_user_list,
                    "users_by_dept": wl_users_by_dept,
                    "matrix":        wl_matrix,
                    "total_seconds": wl_grand,
                    "total_hours":   round(wl_grand / 360) / 10,
                }, origin=origin)

            # ── Dept list (admin: all dept names for pickers) ───────────────────
            if u.path == "/api/departments":
                if tier_info.get("tier") != "admin":
                    return self._send_json({"error": "Admin only."}, 403, origin=origin)
                depts = load_departments()
                return self._send_json({"items": [d["name"] for d in depts]}, origin=origin)

            # ── Dept Documents (read-only) ──────────────────────────────────────
            if u.path == "/api/dept/documents":
                params = parse_qs(u.query)
                name = (params.get("name", [None])[0] or "").strip()
                if not name:
                    return self._send_json({"error": "missing name"}, 400, origin=origin)
                if not (tier_info.get("tier") == "admin" or can_access_scope(tier_info, f"dept::{name}")):
                    return self._send_json({"error": "Restricted to your department."}, 403, origin=origin)
                return self._send_json({"items": load_dept_documents(name)}, origin=origin)

            return self._send_json({"error": "not found"}, 404, origin=origin)

        except Exception as exc:
            self._send_json({"error": str(exc)}, 500, origin=origin)



# ── Server startup ───────────────────────────────────────────────────────────────────────────────────────
def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True

    def _startup():
        try:
            weeks = discover_weeks()
            print(f"[info] Found {len(weeks)} data source(s) in {DATA_DIR}")
            load_config()
        except Exception as _e:
            print(f"[warn] Startup init (non-fatal): {_e}")
        url = f"http://localhost:{PORT}"
        print(f"[info] Mata Web Tools serving on {url}")
        print("[info] Press Ctrl+C to stop.")
        try:
            import threading as _t
            _t.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass

    import threading as _t2
    _t2.Thread(target=_startup, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[info] Stopping.")
        server.server_close()


if __name__ == "__main__":
    main()
