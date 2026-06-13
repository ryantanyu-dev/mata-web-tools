"""
access_layer.py — Server-side token verification and scope-based access gating.

This module is imported by panso_local.py to enforce the Panso access model.
Every request through the Python app must pass through verify_token() first.
Nothing reaches Firestore or the CSV layer without a verified token.

Access model (June 2026 — access-matrix migration):
  admin (ryan@mata.ph) → sees everything; bypasses all scope checks
  all others           → default-deny; see only explicitly granted scopes

Scope key format (stored in Firestore access_overrides.<email>.grants):
  "dept::<canonical_dept_name>"   e.g. "dept::MG - Marketing"
  "proj::<sub_code>"              e.g. "proj::MT"
  "view::<view_id>"               e.g. "view::overview", "view::control-panel"

Enforcement strategy:
  1. verify_token(id_token) → verifies Firebase ID token, returns tier dict
  2. gate_request(tier, require_edit=True) → admin-only gate (edit/config/sync)
     gate_request(tier, require_comp=True) → admin-only gate (comp-heavy aggregates)
  3. can_access_scope(tier_info, scope_key) → True if admin OR scope is in user_grants
  4. filter_comp_fields(data, tier) → no-op (comp stripping retired; see function docstring)
"""

import logging
import os
import time
from functools import lru_cache
from typing import Any

import firebase_admin
# LAZY_IMPORTS_MOVED_INTO_FUNCTIONS: from firebase_admin import auth, credentials

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-user access override cache  (Firestore access_overrides collection)
# ---------------------------------------------------------------------------
# Grants map: {scope_key: bool, ...}  e.g. {"dept::MG - Marketing": True, "proj::MT": True}
# Scope key format:
#   "dept::<canonical_dept_name>"  — matches load_departments()[n]["name"]
#   "proj::<sub_code>"             — one of MT | E | MCS | TSF
#   "view::<view_id>"              — e.g. "view::overview", "view::control-panel"
# Absent key = denied (default-deny).
_OVERRIDE_CACHE: dict = {}       # {email: (grants_dict, expiry_timestamp)}
_OVERRIDE_CACHE_TTL = 300        # 5 minutes — same TTL as the token cache


def _fs_client_ac():
    """Lazy Firestore client for access-control lookups (separate from panso_local._fs_client)."""
    _init_firebase()
    from firebase_admin import firestore as _fb_fs  # lazy — avoids gRPC init at module load
    return _fb_fs.client()


def _has_override(email: str, grant: str) -> bool:
    """Return True if the user has a specific access grant in access_overrides.

    Results are cached for _OVERRIDE_CACHE_TTL seconds. Cache is invalidated
    immediately whenever /api/admin/access-control is written via the UI.
    Fails closed on Firestore error (returns False, not True).
    """
    now = time.time()
    cached = _OVERRIDE_CACHE.get(email)
    if cached is not None and now < cached[1]:
        return bool(cached[0].get(grant, False))
    # Fetch from Firestore; fail closed on any exception
    try:
        doc = _fs_client_ac().collection("access_overrides").document(email).get()
        grants: dict = doc.to_dict().get("grants", {}) if doc.exists else {}
    except Exception:
        return False
    _OVERRIDE_CACHE[email] = (grants, now + _OVERRIDE_CACHE_TTL)
    return bool(grants.get(grant, False))


def invalidate_override_cache(email: str | None = None) -> None:
    """Flush the override cache for one email, or all entries if email is None."""
    if email:
        _OVERRIDE_CACHE.pop(email, None)
    else:
        _OVERRIDE_CACHE.clear()


def user_grants(email: str) -> set:
    """Return the set of scope keys granted to this email.

    Reads access_overrides.<email>.grants, returns keys where value is True.
    Uses the same _OVERRIDE_CACHE as _has_override.  Fails closed on error
    (returns empty set — no access granted).

    Scope key format:
      "dept::<canonical_dept_name>"   e.g. "dept::MG - Marketing"
      "proj::<sub_code>"              e.g. "proj::MT"
      "view::<view_id>"               e.g. "view::overview", "view::control-panel"
    """
    email = (email or "").lower().strip()
    if not email:
        return set()
    now = time.time()
    cached = _OVERRIDE_CACHE.get(email)
    if cached is not None and now < cached[1]:
        return {k for k, v in cached[0].items() if v}
    try:
        doc = _fs_client_ac().collection("access_overrides").document(email).get()
        grants: dict = doc.to_dict().get("grants", {}) if doc.exists else {}
    except Exception:
        return set()
    _OVERRIDE_CACHE[email] = (grants, now + _OVERRIDE_CACHE_TTL)
    return {k for k, v in grants.items() if v}


def can_access_scope(tier_info: dict, scope_key: str) -> bool:
    """Return True if the caller is admin OR scope_key is in their grants.

    Admin (ADMIN_EMAIL) bypasses the matrix entirely.
    All other users need an explicit grant — default-deny.
    """
    if tier_info.get("tier") == "admin":
        return True
    return scope_key in user_grants(tier_info["email"])


# ---------------------------------------------------------------------------
# Firebase Admin SDK initialisation (idempotent)
# ---------------------------------------------------------------------------
_SA_KEY = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "secrets/panso-roster-key.json")

# Cloud Run sets FIREBASE_PROJECT_ID via cloudbuild.yaml; local dev may rely on ADC discovery.
# Passing projectId explicitly avoids the SDK having to hit the GCP metadata server to
# discover it — without this, verify_id_token() fails to validate the 'aud' claim.
_FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "panso-ph")


def _init_firebase():
    """Lazy-init Firebase Admin SDK. Safe to call multiple times.

    Priority:
      1. Key file at _SA_KEY (local dev, CI)
      2. Application Default Credentials (Cloud Run — service account via IAM)
    """
    from firebase_admin import credentials  # lazy
    if not firebase_admin._apps:
        if os.path.exists(_SA_KEY):
            cred = credentials.Certificate(_SA_KEY)
            log.debug("Firebase Admin SDK: using service account key at %s", _SA_KEY)
        else:
            # Cloud Run / GCE — ADC picks up the attached service account automatically.
            cred = credentials.ApplicationDefault()
            log.debug("Firebase Admin SDK: using Application Default Credentials")
        firebase_admin.initialize_app(cred, {"projectId": _FIREBASE_PROJECT_ID})
        log.debug("Firebase Admin SDK: initialized for project %s", _FIREBASE_PROJECT_ID)

# ---------------------------------------------------------------------------
# Compensation fields — keep in sync with Firestore `comp` collection schema
# and with the Python data layer wherever people_extras is surfaced.
# ---------------------------------------------------------------------------
COMP_FIELDS = {
    "hourly",
    "hourly_rate",
    "base_salary",
    "salary",
    "rate",
    "payout",
    "incentive",
    "compensation",
    "comp",
    # people_extras keys that are comp-only
    "people_extras.hourly",
}

# ---------------------------------------------------------------------------
# Access error
# ---------------------------------------------------------------------------

class AccessDenied(Exception):
    """Raised when a tier tries to access a resource it is not permitted to."""
    def __init__(self, message: str = "Access denied.", status: int = 403):
        super().__init__(message)
        self.status  = status
        self.message = message


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

def verify_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token and return the decoded claims dict.

    Returns a dict with at minimum:
      {
        "uid":        str,
        "email":      str,
        "tier":       str,       # "admin" | "comp" | "mancom" | "lead" | "staff"
        "subsidiary": str|None,
        "comp":       bool,
        "is_fte":     bool,
      }

    Raises AccessDenied (401) if the token is missing, expired, or invalid.
    """
    if not id_token:
        raise AccessDenied("No authentication token provided.", status=401)

    _init_firebase()  # lazy — no-op if already initialised

    from firebase_admin import auth  # lazy
    try:
        decoded = auth.verify_id_token(id_token, check_revoked=True)
    except auth.RevokedIdTokenError:
        raise AccessDenied("Token has been revoked. Please sign in again.", status=401)
    except auth.ExpiredIdTokenError:
        raise AccessDenied("Token has expired. Please sign in again.", status=401)
    except auth.InvalidIdTokenError as exc:
        raise AccessDenied(f"Invalid token: {exc}", status=401)
    except Exception as exc:
        log.error("Token verification failed: %s", exc)
        raise AccessDenied("Authentication failed.", status=401)

    tier       = decoded.get("tier",       "staff")
    subsidiary = decoded.get("subsidiary", None)
    comp_flag  = decoded.get("comp",       False)
    is_fte     = decoded.get("is_fte",     True)
    email      = decoded.get("email",      "").lower()

    return {
        "uid":        decoded["uid"],
        "email":      email,
        "tier":       tier,
        "subsidiary": subsidiary,
        "comp":       comp_flag,
        "is_fte":     is_fte,
    }


def extract_bearer_token(authorization_header: str | None) -> str:
    """Extract the raw JWT from an 'Authorization: Bearer <token>' header."""
    if not authorization_header:
        return ""
    parts = authorization_header.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return ""


# ---------------------------------------------------------------------------
# Access gating
# ---------------------------------------------------------------------------

def gate_request(
    tier_info:       dict,
    *,
    target_email:    str | None = None,
    target_sub:      str | None = None,
    require_comp:    bool = False,
    require_edit:    bool = False,
) -> None:
    """
    Raise AccessDenied if tier_info does not permit the requested access.

    Args:
        tier_info:     dict from verify_token()
        target_email:  if set, check that this tier can see this person's data
        target_sub:    if set, check that this tier can see this subsidiary's data
        require_comp:  if True, only comp and admin tiers are allowed through
        require_edit:  if True, only admin is allowed through
    """
    tier       = tier_info["tier"]
    own_email  = tier_info["email"]
    own_sub    = tier_info["subsidiary"]

    # Edit/config/sync: admin only
    if require_edit and tier != "admin":
        raise AccessDenied("Only admin can perform edit/config/sync operations.")

    # Comp-heavy aggregate endpoints are now admin-only (comp tier is retired).
    # require_comp=True is treated identically to require_edit=True.
    if require_comp and tier != "admin":
        raise AccessDenied("Restricted to admin.")

    # Admin: unrestricted
    if tier == "admin":
        return

    # Comp: company-wide read (non-pay + comp), but no editing
    if tier == "comp":
        if require_edit:
            raise AccessDenied("Comp group members cannot edit data.")
        return  # comp can see everything (read)

    # ManCom: own subsidiary only
    if tier == "mancom":
        if target_sub and target_sub != own_sub:
            raise AccessDenied(f"ManCom access is restricted to subsidiary {own_sub}.")
        if target_email:
            # allowed as long as the target is in the same subsidiary
            # (subsidiary membership is checked at the data layer via Firestore)
            pass
        return

    # Lead: own team only (same subsidiary, subset of people)
    # For Phase 0, treat Lead the same as ManCom (subsidiary scope).
    # Phase 2 will add per-team filtering via the `team` claim.
    if tier == "lead":
        if target_sub and target_sub != own_sub:
            raise AccessDenied(f"Lead access is restricted to subsidiary {own_sub}.")
        return

    # Staff / intern / ptf: self only
    if target_email and target_email.lower() != own_email:
        raise AccessDenied("You can only access your own data.")
    if target_sub and target_sub != own_sub:
        raise AccessDenied("You can only access your own data.")


# ---------------------------------------------------------------------------
# Comp-field stripping
# ---------------------------------------------------------------------------

def filter_comp_fields(data: Any, tier_info: dict) -> Any:
    """
    Comp-field stripping is retired (access-matrix migration, June 2026).

    Data visibility is now controlled entirely by scope grants: granted scopes
    return full data; admin-only aggregate endpoints are blocked at the gate
    before this function is ever reached.  This function is kept as a no-op so
    existing call sites in panso_local.py compile without changes.
    """
    return data


# ---------------------------------------------------------------------------
# Convenience decorator for panso_local.py request handlers
# ---------------------------------------------------------------------------

def require_auth(handler_fn):
    """
    Decorator for do_GET / do_POST sub-handlers in panso_local.py.

    Usage:
        @require_auth
        def handle_api_people(self, tier_info):
            ...

    The wrapped function receives `tier_info` as its second argument.
    If auth fails, it sends a 401/403 JSON error response automatically.
    """
    import functools
    import json

    @functools.wraps(handler_fn)
    def wrapper(self, *args, **kwargs):
        auth_header = self.headers.get("Authorization", "")
        token       = extract_bearer_token(auth_header)
        try:
            tier_info = verify_token(token)
        except AccessDenied as exc:
            body = json.dumps({"error": exc.message}).encode()
            self.send_response(exc.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return handler_fn(self, tier_info, *args, **kwargs)

    return wrapper
