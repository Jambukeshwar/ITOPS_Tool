import os
import re
import httpx
import sqlite3
import hashlib
import secrets
import json
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from contextvars import ContextVar
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ARIA_URL       = "https://secure.ariasystems.net/v1/core"
ARIA_QUERY_URL = "https://secure.ariasystems.net/api/AriaQuery/objects.php"
CLIENT_NO      = "8100011"   # <-- replace
AUTH_KEY       = "aNsdrFww7h4EQ4XpUkxhPRyVPkvxFnru"    # <-- replace
DB_PATH        = os.environ.get("DB_PATH", "users.db")
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Aria ITOps Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dunning step map ──────────────────────────────────────────────────────────
DUNNING_STEPS = {
    "Low_Risk_ACH_15":           {"suspend": 10, "final_suspend": 14, "disconnect": 15},
    "Low_Risk_ACH_5":            {"suspend": 12, "final_suspend": 16, "disconnect": 17},
    "Low_Risk_Credit_Card_15":   {"suspend": 12, "final_suspend": 16, "disconnect": 17},
    "Low_Risk_Net_Terms":        {"suspend":  9, "final_suspend": 13, "disconnect": 14},
    "Medium_Risk_ACH_15":        {"suspend":  9, "final_suspend": 14, "disconnect": 15},
    "Medium_Risk_ACH_5":         {"suspend": 11, "final_suspend": 16, "disconnect": 17},
    "Medium_Risk_Credit_Card_15":{"suspend": 11, "final_suspend": 16, "disconnect": 17},
    "Medium_Risk_Net_Terms":     {"suspend":  8, "final_suspend": 13, "disconnect": 14},
    "High_Risk_ACH_15":          {"suspend":  8, "final_suspend": 13, "disconnect": 14},
    "High_Risk_ACH_5":           {"suspend": 10, "final_suspend": 15, "disconnect": 16},
    "High_Risk_Credit_Card_15":  {"suspend": 10, "final_suspend": 15, "disconnect": 16},
    "High_Risk_Net_Terms":       {"suspend":  6, "final_suspend": 11, "disconnect": 12},
    "Net_Terms":                 {"suspend": 10, "final_suspend": 15, "disconnect": 16},
    "Autopay_ACH":               {"suspend": 12, "final_suspend": 18, "disconnect": 19},
    "Autopay_Credit_Card":       {"suspend": 14, "final_suspend": 19, "disconnect": 20},
}

# Special dunning processes — no suspend/disconnect steps; portal never moves these
SPECIAL_DUNNING = {
    "Dunning_process_manual_cancelation", "Dunning_process_Payment_Plan",
    "Government", "Government_Case", "Large_Business_Wholesale", "Large_Business_Wholesale_Case",
}

# ── DB ────────────────────────────────────────────────────────────────────────
DEFAULT_ADMIN = ("aiops", "admin@123")   # created only when no admin exists — change the password after first login

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role     TEXT NOT NULL DEFAULT 'user'
        )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "role" not in cols:   # migrate older DBs
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    conn.commit()
    return conn

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def ensure_default_admin():
    conn = get_db()
    if not conn.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone():
        u, p = DEFAULT_ADMIN
        conn.execute("INSERT OR REPLACE INTO users (username, password, role) VALUES (?, ?, 'admin')", (u, hash_password(p)))
        conn.commit()
    conn.close()

ensure_default_admin()

def verify_user(username: str, password: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT password FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row is not None and row[0] == hash_password(password)

def get_role(username: str) -> str:
    conn = get_db()
    row = conn.execute("SELECT role FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row[0] if row else ""

def get_session_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token    TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn

def create_session(token: str, username: str):
    conn = get_session_db()
    conn.execute("INSERT OR REPLACE INTO sessions (token, username) VALUES (?, ?)", (token, username))
    conn.commit()
    conn.close()

def get_session_user(token: str):
    conn = get_session_db()
    row = conn.execute("SELECT username FROM sessions WHERE token = ?", (token,)).fetchone()
    conn.close()
    return row[0] if row else None

def delete_session(token: str):
    conn = get_session_db()
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()

# Logged-in user of the current request; post_aria uses it to build alt_caller_id = "username|comment"
current_user: ContextVar[str] = ContextVar("current_user", default="")

async def require_auth(request: Request):
    # async (not def) so the ContextVar set here is visible to the route handler
    token = request.cookies.get("session_token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    username = get_session_user(token)
    if not username or not get_role(username):   # removed users lose access immediately
        raise HTTPException(status_code=401, detail="Not authenticated")
    current_user.set(username)
    return username

async def require_admin(username: str = Depends(require_auth)):
    if get_role(username) != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return username

def format_alt_caller(comment: str) -> str:
    """'username|comment'. Strips a username| prefix the user typed so it isn't doubled."""
    user = current_user.get()
    c = (comment or "").strip()
    if user and c.lower().startswith(user.lower() + "|"):
        c = c[len(user) + 1:].strip()
    if not c:
        raise HTTPException(status_code=400, detail="Comment is required in Alt caller")
    return f"{user}|{c}" if user else c

RUNS_KEEP = 2  # runs kept per report, shared across all users

def get_runs_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_runs (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            report   TEXT NOT NULL,
            title    TEXT NOT NULL,
            username TEXT NOT NULL,
            created  TEXT NOT NULL,
            updated  TEXT NOT NULL,
            data     TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn

# ── Aria helpers ──────────────────────────────────────────────────────────────
async def post_aria(payload: dict) -> dict:
    if "alt_caller_id" in payload:
        payload = {**payload, "alt_caller_id": format_alt_caller(payload["alt_caller_id"])}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(ARIA_URL, json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectTimeout:
        raise HTTPException(status_code=504, detail="Aria API timeout")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e))

async def post_aria_query(payload: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(ARIA_QUERY_URL, json=payload)
            r.raise_for_status()
            return r.json()
    except httpx.ConnectTimeout:
        raise HTTPException(status_code=504, detail="Aria API timeout")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=str(e))

def base_payload() -> dict:
    return {"client_no": CLIENT_NO, "auth_key": AUTH_KEY, "output_format": "json"}

# ── Request models ────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class LookupRequest(BaseModel):
    client_plan_instance_id: str

class CreateCreditRequest(BaseModel):
    client_plan_instance_id: str
    client_acct_id: str
    amount: float
    frequency_no: int
    initial_credit_date: str
    inst_id: str
    svc_id: str
    alt_caller_id: str

class CancelServiceCreditRequest(BaseModel):
    client_acct_id: str
    scid: str
    alt_caller_id: str

class CancelRecurringCreditRequest(BaseModel):
    client_acct_id: str
    rcid: str
    alt_caller_id: str

class BulkCreateCreditRequest(BaseModel):
    records: list[CreateCreditRequest]

class BulkCancelServiceRequest(BaseModel):
    records: list[CancelServiceCreditRequest]

class BulkCancelRecurringRequest(BaseModel):
    records: list[CancelRecurringCreditRequest]

class AccountDetailsRequest(BaseModel):
    client_acct_id: str

class BulkAccountDetailsRequest(BaseModel):
    records: list[AccountDetailsRequest]

class UpdateDunningRequest(BaseModel):
    client_acct_id: str
    action: str
    alt_caller_id: str
    dunning_process_id: str
    step_offset: int = 0  # -1 = one step before target (used for re-trigger)

class ResumeDunningRequest(BaseModel):
    client_acct_id: str
    alt_caller_id: str

class SFUpdateFlagRequest(BaseModel):
    client_acct_id: str
    flag: bool
    sf_token: str
    fraud_reason: str = ""
    ci_name: str = ""
    ci_notes: str = ""

class PlanLookupRequest(BaseModel):
    msisdn: str

class UpdatePlanStatusRequest(BaseModel):
    client_plan_instance_id: str
    client_acct_id: str
    action: str  # "activate_align", "activate", "cancel"
    alt_caller_id: str

class UpdateDeviceStatusRequest(BaseModel):
    client_plan_instance_id: str
    client_acct_id: str
    action: str  # "activate_align", "activate", "cancel"
    alt_caller_id: str
    comments: str = ""

class SaveRunRequest(BaseModel):
    id: Optional[int] = None  # set to update an existing run (e.g. lookup → execute)
    report: str
    title: str
    data: dict             # {"columns": [...], "rows": [[...], ...]}

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.post("/api/login")
async def login(req: LoginRequest):
    if not verify_user(req.username, req.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = secrets.token_hex(32)
    create_session(token, req.username)
    resp = JSONResponse({"success": True, "username": req.username, "role": get_role(req.username)})
    resp.set_cookie("session_token", token, httponly=True, samesite="lax")
    return resp

@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token:
        delete_session(token)
    resp = JSONResponse({"success": True})
    resp.delete_cookie("session_token")
    return resp

@app.get("/api/me")
async def me(username: str = Depends(require_auth)):
    return {"username": username, "role": get_role(username)}

# ── Admin: user management ────────────────────────────────────────────────────
class AdminUserRequest(BaseModel):
    username: str
    password: str = ""
    role: str = "user"

def _clean_username(u: str) -> str:
    u = (u or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._ -]{2,40}", u):
        raise HTTPException(status_code=400, detail="Username: 2–40 letters, digits, space, . _ -")
    return u

def _check_password(p: str):
    if len(p or "") < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

@app.get("/api/admin/users")
async def admin_list_users(_=Depends(require_admin)):
    conn = get_db()
    rows = conn.execute("SELECT username, role FROM users ORDER BY role, username").fetchall()
    conn.close()
    return {"users": [{"username": r[0], "role": r[1]} for r in rows]}

@app.post("/api/admin/users")
async def admin_add_user(req: AdminUserRequest, _=Depends(require_admin)):
    u = _clean_username(req.username)
    _check_password(req.password)
    if req.role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Role must be user or admin")
    conn = get_db()
    try:
        conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", (u, hash_password(req.password), req.role))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"User '{u}' already exists")
    finally:
        conn.close()
    return {"success": True}

@app.post("/api/admin/users/password")
async def admin_reset_password(req: AdminUserRequest, _=Depends(require_admin)):
    _check_password(req.password)
    conn = get_db()
    n = conn.execute("UPDATE users SET password = ? WHERE username = ?", (hash_password(req.password), req.username)).rowcount
    conn.commit(); conn.close()
    if not n:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}

@app.post("/api/admin/users/delete")
async def admin_delete_user(req: AdminUserRequest, admin: str = Depends(require_admin)):
    if req.username == admin:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    conn = get_db()
    n = conn.execute("DELETE FROM users WHERE username = ?", (req.username,)).rowcount
    conn.commit(); conn.close()
    if not n:
        raise HTTPException(status_code=404, detail="User not found")
    sconn = get_session_db()
    sconn.execute("DELETE FROM sessions WHERE username = ?", (req.username,))
    sconn.commit(); sconn.close()
    return {"success": True}

# ── Lookup ────────────────────────────────────────────────────────────────────
@app.post("/api/lookup")
async def lookup_device(req: LookupRequest, _=Depends(require_auth)):
    data = await post_aria_query({
        **base_payload(),
        "rest_call":      "get_plan_instance_information_m",
        "releaseVersion": "52",
        "limit":          100,
        "offset":         0,
        "query_string":   f"client_plan_instance_id={req.client_plan_instance_id}",
    })
    if data.get("error_code") != 0:
        raise HTTPException(status_code=400, detail=data.get("error_msg", "Lookup failed"))
    details = data.get("plan_instance_details", [])
    if not details:
        raise HTTPException(status_code=404, detail="No plan instance found")
    d  = details[0]
    pv = (d.get("product_fields") or [{}])[0].get("product_field_value", "")
    p  = pv.split("|")
    def sf(i):
        try: return float(p[i])
        except: return 0.0
    def si(i):
        try: return int(p[i])
        except: return 0
    t13 = si(12)
    t14 = si(13)
    pos15 = p[14] if len(p) > 14 else ''
    return {
        "acct_id":          d.get("client_acct_id"),
        "plan_instance_id": d.get("client_plan_instance_id"),
        "plan_instance_no": d.get("plan_instance_no"),
        "device_name":      p[6] if len(p) > 6 else d.get("plan_name", "Unknown"),
        "inst_id":          p[5] if len(p) > 5 else "",
        "amount":           sf(11),
        "term_total":       t13,
        "term_remaining":   t14,
        "frequency":        t13 - t14,
        "svc_id":           d.get("client_plan_id", ""),
        "flag":             pos15 == "0" if len(p) > 14 else False,
        "pos15":            pos15,
    }

@app.post("/api/plan-status-lookup")
async def plan_status_lookup(req: LookupRequest, _=Depends(require_auth)):
    data = await post_aria_query({
        **base_payload(),
        "rest_call":      "get_plan_instance_information_m",
        "releaseVersion": "52",
        "limit":          100,
        "offset":         0,
        "query_string":   f"client_plan_instance_id={req.client_plan_instance_id}",
    })
    if data.get("error_code") != 0:
        raise HTTPException(status_code=400, detail=data.get("error_msg", "Lookup failed"))
    details = data.get("plan_instance_details", [])
    if not details:
        raise HTTPException(status_code=404, detail="No plan instance found")
    d = details[0]
    # Parse product_fields array by name
    pf_map = {pf.get("product_field_name"): pf.get("product_field_value")
              for pf in (d.get("product_fields") or [])}
    STATUS_LABELS = {1: "Active", 0: "Inactive", -1: "Suspended", -2: "Cancelled"}
    sc = d.get("status_cd")
    return {
        "client_plan_instance_id": d.get("client_plan_instance_id"),
        "client_acct_id":          d.get("client_acct_id"),
        "msisdn":                  pf_map.get("MSISDN", ""),
        "user_name":               pf_map.get("User Name", ""),
        "plan_name":               d.get("plan_name", ""),
        "last_bill_date":          d.get("last_bill_date", ""),
        "status_cd":               sc,
        "status_label":            STATUS_LABELS.get(sc, f"Unknown ({sc})"),
        "status_date":             d.get("status_date", ""),
    }

@app.post("/api/device-lookup")
async def device_lookup(req: LookupRequest, _=Depends(require_auth)):
    data = await post_aria_query({
        **base_payload(),
        "rest_call":      "get_plan_instance_information_m",
        "releaseVersion": "52",
        "limit":          100,
        "offset":         0,
        "query_string":   f"client_plan_instance_id={req.client_plan_instance_id}",
    })
    if data.get("error_code") != 0:
        raise HTTPException(status_code=400, detail=data.get("error_msg", "Lookup failed"))
    details = data.get("plan_instance_details", [])
    if not details:
        raise HTTPException(status_code=404, detail="No plan instance found")
    d = details[0]
    pf = (d.get("product_fields") or [{}])[0].get("product_field_value", "")
    STATUS_LABELS = {1: "Active", 0: "Inactive", -1: "Suspended", -2: "Cancelled"}
    sc = d.get("status_cd")
    return {
        "client_plan_instance_id": d.get("client_plan_instance_id"),
        "client_acct_id":          d.get("client_acct_id"),
        "status_cd":               sc,
        "status_label":            STATUS_LABELS.get(sc, f"Unknown ({sc})"),
        "client_plan_id":          d.get("client_plan_id"),
        "status_date":             d.get("status_date"),
        "product_field_value":     pf,
    }

# ── Create credit ─────────────────────────────────────────────────────────────
@app.post("/api/create-credit")
async def create_credit(req: CreateCreditRequest, _=Depends(require_auth)):
    payload = {
        **base_payload(),
        "rest_call":                      "create_advanced_service_credit_m",
        "client_acct_id":                 req.client_acct_id,
        "client_master_plan_instance_id": req.client_acct_id,
        "amount":                         req.amount,
        "reason_code":                    "1728",
        "comments":                       f"Installment|{req.inst_id}",
        "frequency_no":                   req.frequency_no,
        "initial_credit_date":            req.initial_credit_date,
        "frequency_interval_type":        "1",
        "service_code_option":            "2",
        "alt_caller_id":                  req.alt_caller_id,
        "client_eligible_plan_instances": [{
            "client_plan_instance_id":         req.client_plan_instance_id,
            "client_plan_instance_service_id": req.svc_id,
        }],
    }
    return await post_aria(payload)

@app.post("/api/bulk-create-credit")
async def bulk_create_credit(req: BulkCreateCreditRequest, _=Depends(require_auth)):
    results = []
    for record in req.records:
        try:
            result = await create_credit(record, _)
            results.append({"instance_id": record.client_plan_instance_id, "success": True, **result})
        except Exception as e:
            results.append({"instance_id": record.client_plan_instance_id, "success": False, "error": str(e)})
    return {"results": results}

# ── Cancel credit ─────────────────────────────────────────────────────────────
@app.post("/api/cancel-service-credit")
async def cancel_service_credit(req: CancelServiceCreditRequest, _=Depends(require_auth)):
    return await post_aria({
        **base_payload(),
        "rest_call":      "cancel_unapplied_service_credits_m",
        "releaseVersion": "52",
        "client_acct_id": req.client_acct_id,
        "alt_caller_id":  req.alt_caller_id,
        "credit_ids":     [{"credit_ids": req.scid}],
    })

@app.post("/api/cancel-recurring-credit")
async def cancel_recurring_credit(req: CancelRecurringCreditRequest, _=Depends(require_auth)):
    return await post_aria({
        **base_payload(),
        "rest_call":           "cancel_recurring_credits_m",
        "releaseVersion":      "52",
        "client_acct_id":      req.client_acct_id,
        "alt_caller_id":       req.alt_caller_id,
        "recurring_credit_no": [{"recurring_credit_no": req.rcid}],
    })

@app.post("/api/bulk-cancel-service-credit")
async def bulk_cancel_service_credit(req: BulkCancelServiceRequest, _=Depends(require_auth)):
    results = []
    for record in req.records:
        try:
            result = await cancel_service_credit(record, _)
            results.append({"acct_id": record.client_acct_id, "scid": record.scid, "success": True, **result})
        except Exception as e:
            results.append({"acct_id": record.client_acct_id, "scid": record.scid, "success": False, "error": str(e)})
    return {"results": results}

@app.post("/api/bulk-cancel-recurring-credit")
async def bulk_cancel_recurring_credit(req: BulkCancelRecurringRequest, _=Depends(require_auth)):
    results = []
    for record in req.records:
        try:
            result = await cancel_recurring_credit(record, _)
            results.append({"acct_id": record.client_acct_id, "rcid": record.rcid, "success": True, **result})
        except Exception as e:
            results.append({"acct_id": record.client_acct_id, "rcid": record.rcid, "success": False, "error": str(e)})
    return {"results": results}

# ── Get credits (RC + unapplied SC per device) ────────────────────────────────
class GetCreditsRequest(BaseModel):
    ids: list[str]

def _first_list(data: dict, *keys):
    for k in keys:
        if isinstance(data.get(k), list):
            return data[k]
    return next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), [])

def _pick(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d.get(k)
    return None

def _cpi_values(obj):
    """All client_plan_instance_id values in obj, at any depth
    (RC: eligible_plan_instance_details[].client_plan_instance_id)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "client_plan_instance_id" and v:
                yield str(v).strip()
            else:
                yield from _cpi_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _cpi_values(v)

def _credit_matches(c: dict, dev: dict) -> bool:
    cpi = str(dev.get("plan_instance_id") or "").strip()
    return bool(cpi) and cpi in set(_cpi_values(c))

def _rc_state(label: str) -> str:
    s = (label or "").lower()
    if s.startswith("cancel"):  return "cancelled"
    if s.startswith("entered"): return "scheduled"
    if "incomplete" in s:       return "active"
    if "complete" in s:         return "completed"
    return "other"

async def _acct_credits(acct: str) -> dict:
    out = {"rc": [], "sc": [], "rc_error": None, "sc_error": None}
    for kind, call, keys in (("rc", "get_recurring_credit_info_m", ("recurring_credits_details", "recurring_credit_info")),
                             ("sc", "get_unapplied_service_credits_m", ("unapplied_service_credits_details", "unapplied_service_credits"))):
        try:
            d = await post_aria({**base_payload(), "rest_call": call, "client_acct_id": acct})
            if d.get("error_code") not in (0, "0"):
                out[kind + "_error"] = d.get("error_msg") or f"error {d.get('error_code')}"
            else:
                out[kind] = _first_list(d, *keys)
        except HTTPException as e:
            out[kind + "_error"] = str(e.detail)
    return out

@app.post("/api/get-credits")
async def get_credits(req: GetCreditsRequest, _=Depends(require_auth)):
    ids = [i.strip() for i in req.ids if i.strip()]
    sem = asyncio.Semaphore(3)

    async def look(i):
        async with sem:
            try:
                return await lookup_device(LookupRequest(client_plan_instance_id=i), _)
            except HTTPException as e:
                return {"error": str(e.detail)}
    devs = await asyncio.gather(*[look(i) for i in ids])

    # RC/SC calls once per account, even if several devices share a BAN
    accts = sorted({d["acct_id"] for d in devs if "error" not in d and d.get("acct_id")})
    async def acct_job(a):
        async with sem:
            return a, await _acct_credits(a)
    credits = dict(await asyncio.gather(*[acct_job(a) for a in accts]))

    results = []
    for i, dev in zip(ids, devs):
        if "error" in dev:
            results.append({"id": i, "error": dev["error"]})
            continue
        ac = credits.get(dev.get("acct_id"), {"rc": [], "sc": [], "rc_error": "No account", "sc_error": "No account"})
        rcs = []
        for c in ac["rc"]:
            if not _credit_matches(c, dev):
                continue
            label = _pick(c, "credit_status_label", "status_label") or ""
            state = _rc_state(label)
            if state == "cancelled":
                continue   # cancelled RC = no credit
            rcs.append({
                "state": state, "label": label,
                "no": _pick(c, "recurring_credit_no_2", "recurring_credit_no"),
                "amount": _pick(c, "credit_amount", "amount"),
                "first_credit_date": _pick(c, "first_credit_date"),
                "next_credit_date": _pick(c, "next_credit_date"),
                "credits_remaining": _pick(c, "credits_remaining"),
                "comments": _pick(c, "comments"),
            })
        order = {"active": 0, "completed": 1, "other": 2, "scheduled": 3}
        rcs.sort(key=lambda r: order.get(r["state"], 9))
        scs = [{
            "no": _pick(c, "credit_id", "service_credit_no", "credit_no"),
            "amount": _pick(c, "amount_left_to_apply", "initial_amount", "amount"),
            "date": _pick(c, "create_date", "initial_credit_date", "created_date"),
            "comments": _pick(c, "comments", "reason_text"),
        } for c in ac["sc"] if _credit_matches(c, dev)]
        results.append({
            "id": i, "device": dev, "rc": rcs, "sc": scs,
            "rc_error": ac["rc_error"], "sc_error": ac["sc_error"],
            "acct_rc_total": len(ac["rc"]), "acct_sc_total": len(ac["sc"]),
        })
    return {"results": results}

# ── MPI status (master plan instance = BAN) ───────────────────────────────────
class MpiUpdateRequest(BaseModel):
    client_acct_id: str
    action: str          # "activate" | "suspend"
    alt_caller_id: str

def _ast_today():
    """Today in AST (Puerto Rico, UTC-4, no DST)."""
    return datetime.now(timezone(timedelta(hours=-4))).date()

async def _mpi_info(uid: str) -> dict:
    data = await post_aria({**base_payload(), "rest_call": "get_acct_details_all_m",
        "client_acct_id": uid, "include_master_plans": "1",
        "include_supp_plans": "0", "include_billing_groups": "0",
        "include_payment_methods": "0"})
    if data.get("error_code") not in (0, "0"):
        raise HTTPException(status_code=400, detail=data.get("error_msg") or "Account lookup failed")
    plans = data.get("master_plans_info") or []
    plan = next((p for p in plans if uid in (p.get("client_master_plan_instance_id"), p.get("client_plan_instance_id"))), None)
    if not plan:
        raise HTTPException(status_code=404, detail="Master plan instance not found for this BAN")
    nbd = _parse_dt(plan.get("next_bill_date"))
    past = nbd != datetime.min and nbd.date() < _ast_today()
    return {
        "client_acct_id":     uid,
        "account_status":     "Active" if data.get("status_cd") == 1 else "Not-Active",
        "mpi_status_cd":      plan.get("master_plan_instance_status"),
        "mpi_status":         plan.get("mp_instance_status_label") or str(plan.get("master_plan_instance_status")),
        "next_bill_date":     plan.get("next_bill_date"),
        "last_bill_date":     plan.get("last_bill_date"),
        "bill_day":           plan.get("bill_day"),
        "status_date":        plan.get("status_date"),
        "dunning_process_id": plan.get("client_dunning_process_id"),
        "dunning_state":      plan.get("dunning_state"),
        "dunning_step":       plan.get("dunning_step"),
        "bill_date_past":     past,
        "force_bill_date_reset": 2 if past else 0,   # used on Activate
    }

@app.post("/api/mpi-lookup")
async def mpi_lookup(req: AccountDetailsRequest, _=Depends(require_auth)):
    return await _mpi_info(req.client_acct_id.strip())

@app.post("/api/mpi-update")
async def mpi_update(req: MpiUpdateRequest, _=Depends(require_auth)):
    uid = req.client_acct_id.strip()
    payload = {**base_payload(), "rest_call": "update_acct_plan_status_m",
               "client_acct_id": uid, "client_plan_instance_id": uid,
               "alt_caller_id": req.alt_caller_id}
    if req.action == "activate":
        info = await _mpi_info(uid)   # re-check the bill date at execution time
        payload.update({"status_cd": 1, "dunning_state": 0,
                        "force_bill_date_reset": info["force_bill_date_reset"]})
    elif req.action == "suspend":
        payload.update({"status_cd": -1, "force_bill_date_reset": 0})
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    res = await post_aria(payload)
    res["force_bill_date_reset"] = payload["force_bill_date_reset"]
    return res

# ── Next bill date (default credit start date) ────────────────────────────────
@app.post("/api/next-bill-date")
async def next_bill_date(req: AccountDetailsRequest, _=Depends(require_auth)):
    uid = req.client_acct_id
    data = await post_aria({**base_payload(), "rest_call": "get_acct_details_all_m",
        "client_acct_id": uid, "include_master_plans": "1",
        "include_supp_plans": "0", "include_billing_groups": "0",
        "include_payment_methods": "0"})
    plans = data.get("master_plans_info") or []
    # Credits are created against MPI = client_acct_id, so prefer that master plan instance
    plan = next((p for p in plans if uid in (p.get("client_master_plan_instance_id"), p.get("client_plan_instance_id"))),
                plans[0] if plans else {})
    nbd = _parse_dt(plan.get("next_bill_date"))
    if nbd == datetime.min:
        return {"next_bill_date": plan.get("next_bill_date"), "start_date": None}
    return {
        "next_bill_date": nbd.strftime("%Y-%m-%d"),
        "start_date":     (nbd - timedelta(days=1)).strftime("%Y-%m-%d"),
    }

# ── Account details ───────────────────────────────────────────────────────────
@app.post("/api/account-details")
async def account_details(req: AccountDetailsRequest, _=Depends(require_auth)):
    uid = req.client_acct_id
    bal = await post_aria({**base_payload(), "rest_call": "get_acct_plan_balance_m",
        "client_acct_id": uid, "client_plan_instance_id": uid})
    pay = await post_aria({**base_payload(), "rest_call": "get_acct_payment_history_m",
        "client_acct_id": uid})
    dun = await post_aria({**base_payload(), "rest_call": "get_acct_details_all_m",
        "client_acct_id": uid, "include_master_plans": "1",
        "include_supp_plans": "0", "include_billing_groups": "0",
        "include_payment_methods": "0", "plan_limit": "1"})
    payment_plan = await payment_plan_summary(uid)

    # Payment history — most recent record is index 0
    ph = (pay.get("acct_payment_history") or [{}])[0]

    # Account status
    status_cd = dun.get("status_cd")
    account_status = "Active" if status_cd == 1 else "Not-Active"

    # Master plan — dunning info lives here
    plans = dun.get("master_plans_info") or []
    plan = plans[0] if plans else {}
    ds = plan.get("dunning_state", 0)

    return {
        "client_acct_id":       uid,
        "account_status":       account_status,
        "current_balance_due":  bal.get("current_balance_due", 0),
        "total_balance_due":    bal.get("total_balance_due", 0),
        "dunning_process_id":   plan.get("client_dunning_process_id", "N/A"),
        "dunning_status":       "None" if ds == 0 else "In Progress" if ds == 1 else "Completed",
        "dunning_step":         plan.get("dunning_step", "N/A"),
        "dunning_degrade_date": plan.get("dunning_degrade_date", "N/A"),
        "payment_status":       ph.get("proc_status_text", "N/A"),
        "payment_amount":       ph.get("payment_amount", "N/A"),
        "payment_date":         ph.get("payment_received_date", "N/A"),
        "payment_plan":         payment_plan,
    }

async def payment_plan_summary(uid: str) -> str:
    """'no|last due|status' of one plan: the Active plan if any, else the newest. 'None' if the account has none."""
    try:
        data = await post_aria({**base_payload(), "rest_call": "get_payment_plan_list_m",
            "client_acct_id": uid, "include_all_statuses": "true"})
    except HTTPException:
        return "Error"
    pps = data.get("payment_plan_list") or []
    if not pps:
        return "None"
    pps.sort(key=lambda p: (p.get("payment_plan_create_date") or "", p.get("payment_plan_no") or 0), reverse=True)
    active = [p for p in pps if (p.get("payment_plan_status") or "").lower() == "active"]
    p = (active or pps)[0]
    return f"{p.get('payment_plan_no') or 'N/A'}|{p.get('last_payment_plan_due_date') or 'N/A'}|{p.get('payment_plan_status') or 'N/A'}"

@app.post("/api/bulk-account-details")
async def bulk_account_details(req: BulkAccountDetailsRequest, _=Depends(require_auth)):
    sem = asyncio.Semaphore(3)
    async def fetch_one(record):
        async with sem:
            try:
                result = await account_details(record, _)
                return {"success": True, **result}
            except Exception as e:
                return {"success": False, "client_acct_id": record.client_acct_id, "error": str(e)}
    results = await asyncio.gather(*[fetch_one(r) for r in req.records])
    return {"results": results}

# ── Update dunning ────────────────────────────────────────────────────────────
@app.post("/api/update-dunning")
async def update_dunning(req: UpdateDunningRequest, _=Depends(require_auth)):
    if req.dunning_process_id in SPECIAL_DUNNING:
        raise HTTPException(status_code=400, detail=f"Special dunning process: {req.dunning_process_id} — no suspend/disconnect step")
    steps = DUNNING_STEPS.get(req.dunning_process_id)
    if not steps:
        raise HTTPException(status_code=400, detail=f"Unknown dunning process: {req.dunning_process_id}")
    step = steps.get(req.action)
    if step is None:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    if req.step_offset not in (0, -1):
        raise HTTPException(status_code=400, detail=f"Invalid step_offset: {req.step_offset}")
    step += req.step_offset
    return await post_aria({
        **base_payload(),
        "rest_call":               "update_acct_plan_status_m",
        "client_acct_id":          req.client_acct_id,
        "client_plan_instance_id": req.client_acct_id,
        "dunning_state":           1,
        "new_dunning_step":        step,
        "alt_caller_id":           req.alt_caller_id,
    })

@app.post("/api/resume-dunning")
async def resume_dunning(req: ResumeDunningRequest, _=Depends(require_auth)):
    return await post_aria({
        **base_payload(),
        "rest_call":               "update_acct_plan_status_m",
        "client_acct_id":          req.client_acct_id,
        "client_plan_instance_id": req.client_acct_id,
        "dunning_state":           0,
        "alt_caller_id":           req.alt_caller_id,
    })

# ── Salesforce flag ───────────────────────────────────────────────────────────
SF_URL = "https://cwc.my.salesforce.com"
SF_API = f"{SF_URL}/services/data/v57.0"

@app.post("/api/sf-update-flag")
async def sf_update_flag(req: SFUpdateFlagRequest, _=Depends(require_auth)):
    headers = {"Authorization": f"Bearer {req.sf_token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Step 1: find Account Id by BAN
            soql = f"SELECT Id FROM Account WHERE PR_Mobile_Billing_Number__c = '{req.client_acct_id}'"
            qr = await client.get(f"{SF_API}/query/", params={"q": soql}, headers=headers)
            if qr.status_code == 401:
                raise HTTPException(status_code=401, detail="Salesforce token expired or invalid")
            qr.raise_for_status()
            records = qr.json().get("records", [])
            if not records:
                raise HTTPException(status_code=404, detail=f"No Salesforce Account found for BAN {req.client_acct_id}")
            acct_id = records[0]["Id"]

            # Step 2: update fraud flag
            patch_body = {
                "vlocity_cmt__HasFraud__c": req.flag,
                "vlocity_cmt__FraudReason__c": req.fraud_reason if req.flag else "",
            }
            pr = await client.patch(f"{SF_API}/sobjects/Account/{acct_id}", json=patch_body, headers=headers)
            pr.raise_for_status()

            return {"success": True, "account_id": acct_id, "flag": req.flag}
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Salesforce API error: {str(e)}")

# ── Update device status ─────────────────────────────────────────────────────
@app.post("/api/update-device-status")
async def update_device_status(req: UpdateDeviceStatusRequest, _=Depends(require_auth)):
    if req.action == "cancel":
        return await post_aria({
            **base_payload(),
            "rest_call":                        "update_acct_plan_multi_m",
            "client_acct_id":                   req.client_acct_id,
            "plan_directive":                   4,
            "assignment_directive":             "3",
            "invoicing_option":                 "4",
            "existing_client_plan_instance_id": req.client_plan_instance_id,
            "plan_status_cd":                   1,
            "comments":                         req.comments,
            "alt_caller_id":                    req.alt_caller_id,
        })
    else:
        plan_update = {
            "plan_directive":                   3,
            "existing_client_plan_instance_id": req.client_plan_instance_id,
            "plan_status_cd":                   1,
            "proration_invoice_timing":         1,
            "invoice_unbilled_usage":           False,
        }
        if req.action == "activate_align":
            plan_update["auto_offset_months_option"] = 1
        return await post_aria({
            **base_payload(),
            "rest_call":            "update_acct_plan_multi_m",
            "client_acct_id":       req.client_acct_id,
            "assignment_directive": "3",
            "invoicing_option":     "4",
            "comments":             req.comments,
            "alt_caller_id":        req.alt_caller_id,
            "plan_updates":         [plan_update],
        })

# ── Update plan status ────────────────────────────────────────────────────────
@app.post("/api/update-plan-status")
async def update_plan_status(req: UpdatePlanStatusRequest, _=Depends(require_auth)):
    plan_status_cd = 1 if req.action in ("activate_align", "activate") else -2
    plan_update = {
        "plan_directive":                  3,
        "existing_client_plan_instance_id": req.client_plan_instance_id,
        "plan_status_cd":                  plan_status_cd,
        "proration_invoice_timing":        1,
        "invoice_unbilled_usage":          False,
    }
    if req.action == "activate_align":
        plan_update["auto_offset_months_option"] = 1
    return await post_aria({
        **base_payload(),
        "rest_call":            "update_acct_plan_multi_m",
        "client_acct_id":       req.client_acct_id,
        "assignment_directive": "3",
        "invoicing_option":     "4",
        "alt_caller_id":        req.alt_caller_id,
        "plan_updates":         [plan_update],
    })

# ── Plan lookup ────────────────────────────────────────────────────────────────
def _parse_dt(s):
    if not s:
        return datetime.min
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.min

@app.post("/api/plan-lookup")
async def plan_lookup(req: PlanLookupRequest, _=Depends(require_auth)):
    msisdn = req.msisdn.strip()

    # Step 1 — find account by MSISDN
    plan_data = await post_aria_query({
        **base_payload(),
        "rest_call":      "get_plan_instance_information_m",
        "releaseVersion": "52",
        "limit":          100,
        "offset":         0,
        "query_string":   f"product_field_value= {msisdn}",
    })
    if plan_data.get("error_msg") != "OK" or not plan_data.get("total_records", 0):
        raise HTTPException(status_code=404, detail=plan_data.get("error_msg", "No data found for this MSISDN"))

    details = plan_data.get("plan_instance_details", [])

    # Filter out legacy plans:
    #   - user_id (BAN/CAN) must contain a hyphen  (new-format account IDs)
    #   - client_plan_instance_id must NOT be pure digits (old-format instance IDs)
    def _is_valid(p):
        uid  = str(p.get("user_id") or "")
        inst = str(p.get("client_plan_instance_id") or "")
        return "-" in uid and not inst.isdigit()

    valid = [p for p in details if _is_valid(p)]
    if not valid:
        raise HTTPException(status_code=404, detail="No valid (non-legacy) plan found for this MSISDN")

    # Select final plan from valid list
    multiple_active_plans = []          # populated when >1 active plan found
    if len(valid) == 1:
        final_plan = valid[0]
    else:
        active = [p for p in valid if p.get("status_cd") == 1]
        if len(active) > 1:
            # Multiple active plans — flag all; pick the most-recently-changed as primary
            multiple_active_plans = [p.get("client_plan_instance_id") for p in active]
            final_plan = max(active, key=lambda x: _parse_dt(x.get("status_date")))
        elif len(active) == 1:
            # Exactly one active plan — straightforward
            final_plan = active[0]
        else:
            # All cancelled — pick most recent status_date
            final_plan = max(valid, key=lambda x: _parse_dt(x.get("status_date")))

    user_id                 = final_plan.get("user_id")
    client_plan_instance_id = final_plan.get("client_plan_instance_id")
    plan_name               = final_plan.get("plan_name")
    plan_status             = "Active" if final_plan.get("status_cd") == 1 else "Cancelled"
    plan_status_date        = final_plan.get("status_date")

    # Step 2 — child plans
    child_data  = await post_aria({**base_payload(), "rest_call": "get_acct_plans_m", "client_acct_id": user_id})
    child_plans = child_data.get("acct_plans_m", [])

    master_plan_status = "N/A"
    device_plans       = []
    for cp in child_plans:
        if cp.get("client_plan_id") == "Account_Master_Plan":
            master_plan_status = cp.get("plan_instance_status_label", "N/A")
        if (cp.get("client_parent_plan_instance_id") == client_plan_instance_id
                and cp.get("plan_name") in ("Generic Device", "LLA General Device")):
            device_plans.append(cp)

    no_of_inst        = len(device_plans)
    no_of_active_inst = sum(1 for cp in device_plans if cp.get("plan_instance_status_label") == "Active")

    child_plan1 = child_plan1_name = child_plan1_status = child_start_date = child_end_date = "N/A"
    if device_plans:
        active = [cp for cp in device_plans if cp.get("plan_instance_status_label") == "Active"]
        best   = (max(active,        key=lambda cp: _parse_dt(cp.get("plan_date")))
                  if active else
                  max(device_plans,  key=lambda cp: _parse_dt(cp.get("plan_instance_status_date"))))
        child_plan1        = best.get("plan_name", "N/A")
        child_plan1_name   = best.get("client_plan_instance_id", "N/A")
        child_plan1_status = best.get("plan_instance_status_label", "N/A")
        child_start_date   = best.get("plan_date", "N/A")
        child_end_date     = ("Not Applicable" if child_plan1_status == "Active"
                              else best.get("plan_instance_status_date", "N/A"))

    # Step 3 — account status
    acct_data      = await post_aria({
        **base_payload(), "rest_call": "get_acct_details_all_m",
        "client_acct_id": user_id, "include_master_plans": "1",
        "include_supp_plans": "0", "include_billing_groups": "0",
        "include_payment_methods": "0", "plan_limit": "1",
    })
    acct_cd        = acct_data.get("status_cd")
    account_status = "Active" if acct_cd == 1 else ("Archived" if acct_cd == -99 else "Deactivated")

    # Step 4 — installment string
    inst_string = inst_status = "N/A"
    install_id = install_rem_amt = ""
    if no_of_inst > 0 and child_plan1_name != "N/A":
        id_data  = await post_aria_query({
            **base_payload(), "rest_call": "get_plan_instance_information_m",
            "releaseVersion": "52", "limit": 100, "offset": 0,
            "query_string": f"client_plan_instance_id={child_plan1_name}",
        })
        id_dets  = id_data.get("plan_instance_details", [])
        if id_dets:
            pf          = id_dets[0].get("product_fields") or [{}]
            inst_string = pf[0].get("product_field_value", "N/A") if pf else "N/A"
        if inst_string and inst_string != "N/A":
            f           = inst_string.split("|")
            inst_status = (f[9].strip() if len(f) > 9 and f[9].strip() else "Blank")
            install_id  = f[5] if len(f) > 5 else ""
            install_rem_amt = f[2] if len(f) > 2 else ""

    # Step 5 — NSO check (always runs when device plan exists)
    nso_exist = "N/A"
    if no_of_inst > 0 and inst_string != "N/A":
        try:
            orders     = (await post_aria({**base_payload(), "rest_call": "get_order_m", "client_acct_id": user_id})).get("orders", [])
            nso_exist  = "No"
            for order in orders:
                if nso_exist == "Yes":
                    break
                items = (await post_aria({**base_payload(), "rest_call": "get_order_items_m", "order_no": order.get("order_no")})).get("order_items_list", [])
                for item in items:
                    if "Installment_Balance" not in item.get("client_sku", ""):
                        continue
                    lc      = item.get("line_comments", "")
                    nums    = re.findall(r'\b\d+\b', lc)
                    filtered= " ".join(n for n in nums if not (10 <= len(n) <= 11 or 1 <= len(n) <= 4))
                    m_msisdn= re.search(r'\|(\s*\d{10,11}\s*)\|', lc)
                    lc_msisdn = m_msisdn.group(1).strip() if m_msisdn else "NA"
                    if filtered == install_id:
                        nso_exist = "Yes"; break
                    try:
                        o_amt = round(float(str(order.get("amount", 0))), 2)
                        i_amt = round(float(install_rem_amt or 0), 2)
                        if filtered == "" and o_amt == i_amt:
                            nso_exist = "Same_Amt without_InstID"
                        elif msisdn == lc_msisdn and o_amt == i_amt:
                            nso_exist = "Same_Amt & MSISDN with Wrong_InstID"
                    except (ValueError, TypeError):
                        pass
        except Exception:
            nso_exist = "Error"

    return {
        "msisdn":                  msisdn,
        "ban_can":                 user_id,
        "account_status":          account_status,
        "master_plan_status":      master_plan_status,
        "client_plan_instance_id": client_plan_instance_id,
        "plan_name":               plan_name,
        "plan_status":             plan_status,
        "plan_status_date":        plan_status_date,
        "child_plan1":             child_plan1,
        "child_client_plan1_id":   child_plan1_name,
        "child_plan1_status":      child_plan1_status,
        "child_start_date":        child_start_date,
        "child_end_date":          child_end_date,
        "inst_string":             inst_string,
        "inst_status":             inst_status,
        "no_of_inst":              no_of_inst,
        "no_of_active_inst":       no_of_active_inst,
        "nso_exist":               nso_exist,
        "multiple_active_plans":   multiple_active_plans,   # non-empty = warning flag
    }

# ── Shared run history ────────────────────────────────────────────────────────
@app.post("/api/runs")
async def save_run(req: SaveRunRequest, username: str = Depends(require_auth)):
    now  = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    data = json.dumps(req.data)
    conn = get_runs_db()
    try:
        run_id = None
        if req.id is not None:
            cur = conn.execute(
                "UPDATE report_runs SET title = ?, updated = ?, data = ? WHERE id = ? AND username = ?",
                (req.title, now, data, req.id, username))
            if cur.rowcount:
                run_id = req.id
        if run_id is None:
            cur = conn.execute(
                "INSERT INTO report_runs (report, title, username, created, updated, data) VALUES (?, ?, ?, ?, ?, ?)",
                (req.report, req.title, username, now, now, data))
            run_id = cur.lastrowid
            conn.execute(
                "DELETE FROM report_runs WHERE report = ? AND id NOT IN "
                "(SELECT id FROM report_runs WHERE report = ? ORDER BY id DESC LIMIT ?)",
                (req.report, req.report, RUNS_KEEP))
        conn.commit()
    finally:
        conn.close()
    return {"id": run_id}

@app.get("/api/runs")
async def list_runs(_=Depends(require_auth)):
    conn = get_runs_db()
    try:
        rows = conn.execute(
            "SELECT id, report, title, username, created, updated, data FROM report_runs ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return {"runs": [
        {"id": r[0], "report": r[1], "title": r[2], "username": r[3],
         "created": r[4], "updated": r[5], "data": json.loads(r[6])}
        for r in rows
    ]}

# ── Serve UI ──────────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def serve_ui():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8002, reload=True)