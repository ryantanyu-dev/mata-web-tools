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
CFG         = ROOT / "config.json"

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
LEAVE_PATTERN = re.compile(
    r"\b(leave|sick|vacation|VL|SL|maternity|paternity|bereavement|emergency leave)\b", re.I)
LEAVE_TAG_RE  = re.compile(r"\bleave\b", re.I)
_MONTH_ABBR   = ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"]

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
    return {"week": weeks, "payperiod": pps, "month": months}


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
                proj_lc  = proj_name.lower().strip()
                by_email = {}
                for r in rows:
                    if (r.get("project_name") or "").lower().strip() != proj_lc:
                        continue
                    em   = (r.get("user_email") or "").lower().strip()
                    if not em:
                        continue
                    secs = int(r.get("duration_seconds") or 0)
                    if secs <= 0:
                        continue
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
                    "policy_note": (
                        "incentive = hourly_rate × hours × (multiplier − 1.0) "
                        "— i.e. 1.0x = no bonus, 1.5x = +50%, 2.0x = double pay "
                        "(per Q2 thread with norisa@mata.ph)"
                    ),
                }, origin=origin)

            return self._send_json({"error": "not found"}, 404, origin=origin)

        except Exception as exc:
            self._send_json({"error": str(exc)}, 500, origin=origin)


# ── Server startup ───────────────────────────────────────────────────────────
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
