# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Aria ITOps Portal is a FastAPI web application for credit management in the Aria billing system. It supports creating/cancelling recurring and service credits, account detail lookups, dunning operations, and Salesforce redflag management for Prodapt Puerto Rico Inc.

## Commands

### Run the server
```bash
# Development (with auto-reload) — Windows local
venv_win\Scripts\python -m uvicorn app:app --host 0.0.0.0 --port 8002 --reload

# Production (Linux server)
nohup venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8002 > Aria_ITOPS_Portal.log 2>&1 &
```

### Install dependencies
```bash
pip install -r requirements.txt
```

### User management
```bash
python create_user.py add <username> <password>
python create_user.py remove <username>
python create_user.py list
```

### Deploy to server
```bash
scp D:\AI_Tools\Aria_ITOPS\Aria_ITOPS_Portal-JJ\static\index.html wasifakram.i@prodapt.com@10.169.101.69:~/Aria_ITOPS_Portal-JJ/static/index.html
scp D:\AI_Tools\Aria_ITOPS\Aria_ITOPS_Portal-JJ\app.py wasifakram.i@prodapt.com@10.169.101.69:~/Aria_ITOPS_Portal-JJ/app.py
ssh wasifakram.i@prodapt.com@10.169.101.69 "pkill -f 'uvicorn app:app'; cd ~/Aria_ITOPS_Portal-JJ && nohup venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8002 > Aria_ITOPS_Portal.log 2>&1 &"
```

## Local vs Production

`static/index.html` line ~372 has a hardcoded API base URL — must be switched before running:
```js
var API = "http://localhost:8002";        // local dev
var API = "http://10.169.101.69:8002";    // production server
```

The `venv/` directory is Linux-based (copied from server) — unusable on Windows. Use `venv_win/` for local development.

## Architecture

This is a monolithic Python/vanilla JS application with no build step.

**Backend — [app.py](app.py) (single file)**
- FastAPI app with all routes, models, and business logic in one file
- Aria API integration via `httpx` (async HTTP client)
- SQLite authentication (`users.db`) with SHA256 password hashing and session tokens via cookies
- Sections in order: config → DB helpers → auth → Aria API helpers → Pydantic models → route handlers → static file serving

**Frontend — [static/index.html](static/index.html) (single file)**
- Vanilla JS SPA with no framework or bundler
- Views: Login, Credit Management (single/bulk), Account Details
- Bulk account lookup uses a concurrency pool of 3 — calls `/api/account-details` per row individually and updates each row as results arrive (progressive rendering)
- `apiFetch()` is the central fetch wrapper — it automatically calls `doLogout()` on any `401` response. **Do not use `apiFetch` for Salesforce calls** — SF token expiry returns 401 and would log the user out. Use raw `fetch` instead.

## Key Configuration (app.py lines 13–43)

```python
ARIA_URL       = "https://secure.ariasystems.net/v1/core"
ARIA_QUERY_URL = "https://secure.ariasystems.net/api/AriaQuery/objects.php"
CLIENT_NO      = "8100011"
AUTH_KEY       = "..."
DB_PATH        = "users.db"
SF_URL         = "https://cwc.my.salesforce.com"   # Salesforce instance
SF_API         = f"{SF_URL}/services/data/v57.0"
```

`DUNNING_STEPS` dict maps 12 dunning process names (Low/Medium/High Risk × ACH_15/ACH_5/Credit_Card_15/Net_Terms) to their `suspend`/`final_suspend`/`disconnect` step numbers. The frontend mirrors this same dict for UI color-coding (Susp@/Disc@ columns).

## API Surface

All routes require session cookie auth except `/api/login`.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/login` | Authenticate, set session cookie |
| POST | `/api/logout` | Clear session |
| GET | `/api/me` | Current user info |
| POST | `/api/lookup` | Lookup device by `client_plan_instance_id` |
| POST | `/api/create-credit` | Create single recurring or service credit |
| POST | `/api/bulk-create-credit` | Bulk create credits from list |
| POST | `/api/cancel-service-credit` | Cancel a service credit |
| POST | `/api/cancel-recurring-credit` | Cancel a recurring credit |
| POST | `/api/bulk-cancel-service-credit` | Bulk cancel service credits |
| POST | `/api/bulk-cancel-recurring-credit` | Bulk cancel recurring credits |
| POST | `/api/account-details` | Account balance, payment history, dunning info |
| POST | `/api/bulk-account-details` | Bulk account details (concurrency 3 via `asyncio.Semaphore`) |
| POST | `/api/update-dunning` | Suspend or disconnect — looks up step from `DUNNING_STEPS` |
| POST | `/api/resume-dunning` | Resume — sets `dunning_state=0`, no step |
| POST | `/api/sf-update-flag` | Set/clear Salesforce redflag (`vlocity_cmt__HasFraud__c`) on Account |

## Salesforce Integration

`/api/sf-update-flag` authenticates using a user-supplied `sid` session cookie from the browser (SSO login). The token is short-lived — users must refresh it from browser DevTools → Application → Cookies → `.salesforce.com` → `sid`.

Flow:
1. SOQL query: `SELECT Id FROM Account WHERE PR_Mobile_Billing_Number__c = '<BAN>'`
2. PATCH Account: `vlocity_cmt__HasFraud__c`, `vlocity_cmt__FraudReason__c`
3. CustomerInteraction creation (`vlocity_cmt__CustomerInteraction__c`) is currently disabled — fields: `vlocity_cmt__AccountId__c` (ParentId), `Name`, `Notes__c`, `Status='Completed'`, `VerifiedAgentName='BOT'`, `StartDateTime=now GMT`

## Aria API Integration Pattern

All Aria API calls follow this pattern:
1. Build a JSON payload with `client_no`, `auth_key`, and operation-specific fields
2. POST to `ARIA_URL` or `ARIA_QUERY_URL` via `httpx.AsyncClient`
3. Check `error_code` in response (0 = success)

## Logging

- `Aria_ITOPS_Portal.log` — main application log (also used as stdout for the production nohup process)
- `disco-validator.log` — disconnect/validator operations
