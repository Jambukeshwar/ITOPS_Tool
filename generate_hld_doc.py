"""
Generate Aria ITOps Portal HLD document as a Word .docx file.
Run: venv_win\Scripts\python generate_hld_doc.py
Output: Aria_ITOps_Portal_HLD.docx
"""

from docx import Document
from docx.shared import Pt, RGBColor, Inches, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
import copy

# ── Colours ──────────────────────────────────────────────────────────────────
NAVY        = RGBColor(0x1B, 0x3A, 0x6B)
WHITE       = RGBColor(0xFF, 0xFF, 0xFF)
DARK        = RGBColor(0x1A, 0x20, 0x2C)
MID         = RGBColor(0x3D, 0x4D, 0x66)
LIGHT       = RGBColor(0x6B, 0x7F, 0xA0)
STRIPE      = RGBColor(0xEE, 0xF2, 0xF8)
BORDER_CLR  = RGBColor(0xD4, 0xDA, 0xE8)
CALLOUT_BG  = RGBColor(0xEB, 0xF3, 0xFD)
CODE_CLR    = RGBColor(0x25, 0x4D, 0x8F)

# ── Helpers ───────────────────────────────────────────────────────────────────

def set_cell_bg(cell, rgb: RGBColor):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    hex_color = f'{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}'
    shd.set(qn('w:fill'), hex_color)
    tcPr.append(shd)


def set_cell_border(cell, **kwargs):
    """Set borders on a cell. kwargs: top, bottom, left, right — each a dict with color, sz, val."""
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement('w:tcBorders')
    for side, props in kwargs.items():
        border = OxmlElement(f'w:{side}')
        border.set(qn('w:val'),   props.get('val', 'single'))
        border.set(qn('w:sz'),    str(props.get('sz', 4)))
        border.set(qn('w:space'), '0')
        border.set(qn('w:color'), props.get('color', 'D4DAE8'))
        tcBorders.append(border)
    tcPr.append(tcBorders)


def para_space(para, before=0, after=80):
    pf = para.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after  = Pt(after)


def add_heading(doc, text, level=2):
    p = doc.add_paragraph()
    para_space(p, before=14, after=4)
    run = p.add_run(text)
    run.bold = True
    run.font.color.rgb = NAVY
    run.font.size = Pt(16 if level == 1 else 14 if level == 2 else 11)
    if level == 3:
        run.font.color.rgb = LIGHT
        for char in text:
            pass  # already uppercase in text
    p.paragraph_format.keep_with_next = True
    return p


def add_body(doc, text, color=None):
    p = doc.add_paragraph(text)
    para_space(p, before=0, after=6)
    p.runs[0].font.color.rgb = color or MID
    p.runs[0].font.size = Pt(10)
    return p


def add_bullet(doc, text):
    p = doc.add_paragraph(style='List Bullet')
    para_space(p, before=0, after=2)
    run = p.add_run(text)
    run.font.color.rgb = MID
    run.font.size = Pt(10)
    return p


def add_code_inline(run):
    run.font.color.rgb = CODE_CLR
    run.font.name = 'Courier New'


def add_callout(doc, label, text):
    p = doc.add_paragraph()
    para_space(p, before=8, after=8)
    r1 = p.add_run(label + '  ')
    r1.bold = True
    r1.font.color.rgb = NAVY
    r1.font.size = Pt(10)
    r2 = p.add_run(text)
    r2.font.color.rgb = MID
    r2.font.size = Pt(10)
    # light background via paragraph shading would need XML — skip for simplicity
    return p


def make_table(doc, headers, rows, col_widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    table.style = 'Table Grid'

    # Header row
    hdr_row = table.rows[0]
    for i, h in enumerate(headers):
        cell = hdr_row.cells[i]
        set_cell_bg(cell, NAVY)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after  = Pt(3)
        run = p.add_run(h.upper())
        run.bold = True
        run.font.color.rgb = WHITE
        run.font.size = Pt(9)
        run.font.name = 'Calibri'

    # Data rows
    for ri, row_data in enumerate(rows):
        row = table.rows[ri + 1]
        bg = STRIPE if ri % 2 == 0 else WHITE
        for ci, cell_text in enumerate(row_data):
            cell = row.cells[ci]
            set_cell_bg(cell, bg)
            cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after  = Pt(2)
            is_code = cell_text.startswith('`') and cell_text.endswith('`')
            display = cell_text.strip('`')
            run = p.add_run(display)
            run.font.size = Pt(9.5)
            run.font.name = 'Courier New' if (is_code or ci == 0) else 'Calibri'
            if ci == 0:
                run.bold = True
                run.font.color.rgb = DARK
            else:
                run.font.color.rgb = MID
            if is_code:
                run.font.color.rgb = CODE_CLR

    # Column widths
    if col_widths:
        for ci, w in enumerate(col_widths):
            for row in table.rows:
                row.cells[ci].width = Inches(w)

    doc.add_paragraph()  # spacing after table
    return table


def section_label(doc, num, title):
    p = doc.add_paragraph()
    para_space(p, before=18, after=2)
    r = p.add_run(f'Section {num}')
    r.font.color.rgb = NAVY
    r.font.size = Pt(9)
    r.font.name = 'Courier New'
    p2 = doc.add_paragraph()
    para_space(p2, before=0, after=6)
    r2 = p2.add_run(title)
    r2.bold = True
    r2.font.color.rgb = NAVY
    r2.font.size = Pt(16)
    p2.paragraph_format.keep_with_next = True
    return p2


def h3(doc, text):
    p = doc.add_paragraph()
    para_space(p, before=10, after=4)
    r = p.add_run(text.upper())
    r.bold = True
    r.font.color.rgb = LIGHT
    r.font.size = Pt(9)
    r.font.name = 'Calibri'
    p.paragraph_format.keep_with_next = True
    return p


def hr(doc):
    p = doc.add_paragraph()
    para_space(p, before=8, after=8)
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '4')
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), 'D4DAE8')
    pBdr.append(bottom)
    pPr.append(pBdr)


# ── Build document ────────────────────────────────────────────────────────────

doc = Document()

# Page margins
section = doc.sections[0]
section.page_width  = Inches(8.5)
section.page_height = Inches(11)
section.left_margin = section.right_margin = Inches(1.1)
section.top_margin  = section.bottom_margin = Inches(1.0)

# Default style
style = doc.styles['Normal']
style.font.name = 'Calibri'
style.font.size = Pt(10)
style.font.color.rgb = MID

# ── COVER ────────────────────────────────────────────────────────────────────

p = doc.add_paragraph()
para_space(p, before=0, after=2)
r = p.add_run('PRODAPT PUERTO RICO INC · INFORMATION TECHNOLOGY')
r.font.size = Pt(9)
r.font.color.rgb = LIGHT
r.font.name = 'Calibri'

p = doc.add_paragraph()
para_space(p, before=8, after=2)
r = p.add_run('Aria ITOps Portal')
r.bold = True
r.font.size = Pt(28)
r.font.color.rgb = NAVY
r.font.name = 'Calibri'

p = doc.add_paragraph()
para_space(p, before=0, after=16)
r = p.add_run('Architecture Document — HLD, Data Flow & Data Management')
r.font.size = Pt(14)
r.font.color.rgb = MID
r.font.name = 'Calibri'

hr(doc)

meta = [
    ('Document ID',    'HLD-ARIA-001'),
    ('Version',        '1.0'),
    ('Date',           'August 2026'),
    ('Prepared by',    'Jambukeshwaran J'),
    ('Department',     'IT Operations'),
    ('Classification', 'Internal'),
]
for label, value in meta:
    p = doc.add_paragraph()
    para_space(p, before=1, after=1)
    r1 = p.add_run(f'{label:<18}')
    r1.font.color.rgb = LIGHT
    r1.font.size = Pt(10)
    r1.font.name = 'Courier New'
    r2 = p.add_run(value)
    r2.font.color.rgb = DARK
    r2.font.size = Pt(10)
    r2.bold = True

doc.add_page_break()

# ── TABLE OF CONTENTS ─────────────────────────────────────────────────────────

p = doc.add_paragraph()
para_space(p, before=0, after=6)
r = p.add_run('CONTENTS')
r.bold = True
r.font.color.rgb = LIGHT
r.font.size = Pt(9)

toc_items = [
    ('1.', 'Executive Summary'),
    ('2.', 'System Overview'),
    ('3.', 'High-Level Design (HLD)'),
    ('4.', 'Data Flow Diagram (DFD)'),
    ('5.', 'Data Management'),
    ('6.', 'Access Control & Authentication'),
    ('7.', 'Integration Summary'),
]
for num, title in toc_items:
    p = doc.add_paragraph()
    para_space(p, before=1, after=1)
    r1 = p.add_run(f'{num}  ')
    r1.font.color.rgb = LIGHT
    r1.font.size = Pt(10)
    r1.font.name = 'Courier New'
    r2 = p.add_run(title)
    r2.font.color.rgb = MID
    r2.font.size = Pt(10)

hr(doc)

# ── 1. EXECUTIVE SUMMARY ─────────────────────────────────────────────────────

section_label(doc, 1, 'Executive Summary')

add_body(doc, 'The Aria ITOps Portal is an internal web application that provides Prodapt Puerto Rico operations staff with a controlled, user-authenticated interface for performing billing management actions on the Aria billing platform. The application also supports Salesforce fraud-flag management.')
add_body(doc, 'The portal is deployed exclusively on the internal corporate network and is not accessible from the internet. It operates as a credentialed proxy — billing data is never stored locally; all state resides in the respective source systems (Aria Billing and Salesforce CWC).')
add_body(doc, 'This document describes the system\'s high-level architecture, data flows, integration points, and data management practices for review by the IT audit team.')

hr(doc)

# ── 2. SYSTEM OVERVIEW ────────────────────────────────────────────────────────

section_label(doc, 2, 'System Overview')

add_body(doc, 'The portal consolidates several Aria billing operations that would otherwise require direct API access or the Aria administrative console. It provides a browser-based interface accessible to authorised operations agents from within the LAN.')

h3(doc, 'Functional Scope')
make_table(doc,
    headers=['Feature Area', 'Capabilities'],
    rows=[
        ['Credit Management',   'Create and cancel recurring and service credits; bulk operations with CSV export'],
        ['Account Details',     'Lookup account balance, payment history, dunning status; bulk account query'],
        ['DC Data Fix',         'Suspend (DC-1) or disconnect (DC-3) accounts in bulk with eligibility checking'],
        ['Plan Status',         'Activate, align billing cycle, or cancel service plans by plan instance ID'],
        ['Device Status',       'Lookup and update device plan status; bulk operations with per-record comments'],
        ['Plan Lookup',         'Full plan detail lookup by MSISDN including installment and NSO information'],
        ['Salesforce Redflag',  'Set or clear fraud flag (HasFraud__c) on Salesforce Account records'],
    ],
    col_widths=[1.8, 4.6]
)

h3(doc, 'Deployment Details')
make_table(doc,
    headers=['Attribute', 'Detail'],
    rows=[
        ['Server',          '10.169.101.69 (internal LAN)'],
        ['Ports',           ':8002 (production)  ·  :8009 (V1 / staging)'],
        ['Runtime',         'Python 3.12 · FastAPI · Uvicorn (ASGI)'],
        ['Frontend',        'Vanilla JavaScript SPA — single HTML file, no build framework'],
        ['Network access',  'Internal LAN only — no internet exposure'],
        ['Users',           'Authorised Prodapt Puerto Rico operations agents'],
        ['User provisioning', 'CLI script (create_user.py) — managed by IT administrator'],
    ],
    col_widths=[2.0, 4.4]
)

hr(doc)

# ── 3. HIGH-LEVEL DESIGN ─────────────────────────────────────────────────────

section_label(doc, 3, 'High-Level Design')

add_body(doc, 'The application follows a monolithic proxy architecture. A single Python backend file (app.py) serves both the REST API and the static frontend. All routes require authentication via session cookie. The backend acts as a proxy between the agent\'s browser and the upstream billing and CRM systems.')

h3(doc, 'Architecture Overview')

# ASCII-style architecture diagram
p = doc.add_paragraph()
para_space(p, before=6, after=6)
arch = (
    "┌─────────────────────────────────────────────────────────────────────────┐\n"
    "│                          PORTAL SERVER :8002                           │\n"
    "│                                                                         │\n"
    "│  ┌──────────────────────────────────────────┐                          │\n"
    "│  │          FastAPI  (app.py)                │──── HTTPS ──► Aria API  │\n"
    "│  │  Auth · Routes · Aria proxy · SF proxy   │                          │\n"
    "│  └────────────────────┬─────────────────────┘──── HTTPS ──► SF CWC    │\n"
    "│                       │                                                 │\n"
    "│          ┌────────────┴──────────┐                                     │\n"
    "│          │  index.html (SPA)     │  users.db (SQLite)                  │\n"
    "│          └───────────────────────┘                                     │\n"
    "└─────────────────────────────────────────────────────────────────────────┘\n"
    "         ▲\n"
    "    HTTP  │  (LAN · 10.169.x.x)\n"
    "         │\n"
    "   Agent Browser"
)
r = p.add_run(arch)
r.font.name = 'Courier New'
r.font.size = Pt(8.5)
r.font.color.rgb = DARK

h3(doc, 'Component Responsibilities')
make_table(doc,
    headers=['Component', 'Technology', 'Responsibility'],
    rows=[
        ['Frontend SPA',         'Vanilla JS · HTML (single file)',  'All UI views, forms, bulk operations, CSV export, progressive table rendering'],
        ['Backend API',          'FastAPI · Python 3.12 · httpx',    'Authentication, session management, request validation (Pydantic), Aria proxy, Salesforce proxy'],
        ['Auth & Session store', '`users.db` SQLite',                'Usernames, hashed passwords, active session tokens'],
        ['Aria Billing API',     'HTTPS REST — ariasystems.net',     'System of record for all billing data: accounts, plans, devices, credits, dunning'],
        ['Salesforce CWC',       'HTTPS REST — salesforce.com',      'System of record for CRM data; fraud-flag updates on Account objects'],
    ],
    col_widths=[1.7, 1.8, 3.0]
)

h3(doc, 'Key Design Decisions')
make_table(doc,
    headers=['Decision', 'Rationale'],
    rows=[
        ['Monolithic single file',        'Minimal deployment footprint; no build pipeline; straightforward to audit and maintain'],
        ['No local billing data store',   'Aria is the system of record; portal does not duplicate or cache billing data'],
        ['User-supplied Salesforce SID',  'SF SSO tokens are short-lived and user-scoped; portal cannot safely hold a shared SF credential'],
        ['Concurrency pool of 3',         'Bulk lookups use a pool of 3 concurrent requests to avoid Aria rate-limit errors'],
    ],
    col_widths=[2.2, 4.2]
)

hr(doc)

# ── 4. DATA FLOW DIAGRAM ─────────────────────────────────────────────────────

section_label(doc, 4, 'Data Flow Diagram')

add_body(doc, 'Three primary data flows cover all user interactions. In all flows, the portal backend acts as an intermediary — it authenticates the agent, validates the request, and forwards it to the appropriate upstream system. No billing or CRM data is persisted locally at any point.')

h3(doc, 'Flow 1 — Authentication')
p = doc.add_paragraph()
para_space(p, before=4, after=8)
flow1 = (
    "Agent Browser\n"
    "    │  username + password\n"
    "    ▼\n"
    "POST /api/login  (FastAPI)\n"
    "    │  verify hash\n"
    "    ▼\n"
    "users.db  (SQLite)\n"
    "    │  on success → 32-byte hex token\n"
    "    ▼\n"
    "Set-Cookie: session_token\n"
    "    │\n"
    "    ▼\n"
    "Agent Browser  (cookie stored)"
)
r = p.add_run(flow1)
r.font.name = 'Courier New'
r.font.size = Pt(9)
r.font.color.rgb = DARK

h3(doc, 'Flow 2 — Billing Operations (Credit · Dunning · Plan · Device)')
p = doc.add_paragraph()
para_space(p, before=4, after=8)
flow2 = (
    "Agent Browser  (+ session cookie)\n"
    "    │\n"
    "    ▼\n"
    "API endpoint  (FastAPI)\n"
    "    │  validate session → users.db\n"
    "    │  build Aria payload (credentials + params)\n"
    "    │  POST via HTTPS\n"
    "    ▼\n"
    "Aria Billing API  (secure.ariasystems.net)\n"
    "    │  JSON response\n"
    "    ▼\n"
    "FastAPI  →  return to browser  [NOT stored locally]"
)
r = p.add_run(flow2)
r.font.name = 'Courier New'
r.font.size = Pt(9)
r.font.color.rgb = DARK

h3(doc, 'Flow 3 — Salesforce Fraud-Flag Update')
p = doc.add_paragraph()
para_space(p, before=4, after=8)
flow3 = (
    "Agent Browser  (+ session cookie + SF SID token)\n"
    "    │\n"
    "    ▼\n"
    "POST /api/sf-update-flag  (FastAPI)\n"
    "    │  SOQL: SELECT Id FROM Account WHERE BAN = '<value>'\n"
    "    ▼\n"
    "Salesforce CWC  (cwc.my.salesforce.com · HTTPS)\n"
    "    │  Account Id returned\n"
    "    ▼\n"
    "PATCH Account: HasFraud__c · FraudReason__c\n"
    "    │  HTTPS\n"
    "    ▼\n"
    "Salesforce CWC  →  result returned to browser"
)
r = p.add_run(flow3)
r.font.name = 'Courier New'
r.font.size = Pt(9)
r.font.color.rgb = DARK

add_callout(doc,
    'Note on Salesforce SID:',
    'The Salesforce session token is supplied by the agent from their own browser cookie store (obtained via SSO login). '
    'It is passed through the portal backend as a bearer token and is not stored at any point. The token is short-lived and user-scoped, '
    'consistent with Salesforce SSO session management.'
)

h3(doc, 'Aria API Call Pattern')
make_table(doc,
    headers=['Step', 'Action'],
    rows=[
        ['1', 'Authenticated agent submits a request from the browser'],
        ['2', 'Backend validates the session token against users.db'],
        ['3', 'Backend constructs the Aria JSON payload with credentials and operation fields'],
        ['4', 'Backend posts to secure.ariasystems.net via HTTPS using httpx async client'],
        ['5', 'Aria response is returned to the browser; no local storage occurs'],
    ],
    col_widths=[0.4, 6.0]
)

hr(doc)

# ── 5. DATA MANAGEMENT ────────────────────────────────────────────────────────

section_label(doc, 5, 'Data Management')

add_body(doc, 'The portal is designed as a stateless proxy with a minimal local data footprint. The only data stored locally is the user authentication and session store. No billing account data, payment information, or CRM records are persisted by the application.')

h3(doc, 'Data Inventory')
make_table(doc,
    headers=['Data Element', 'Stored Locally', 'Location', 'In Transit (Browser↔Server)', 'Server↔Upstream', 'Retention'],
    rows=[
        ['Agent usernames',       'Yes',          'users.db',              'Submitted at login only',              'Not forwarded',               'Until admin deletes account'],
        ['Password hashes',       'Yes',          'users.db',              'Plaintext at login only; never returned', 'Not forwarded',             'Until admin deletes account'],
        ['Session tokens',        'Yes',          'users.db',              'Cookie on every authenticated request', 'Not forwarded',              'Until logout'],
        ['Aria API credential',   'Yes — config', 'Server config (app.py)','Not exposed to browser',               'Included in every Aria call (HTTPS)', 'Permanent (config)'],
        ['Billing account data',  'No',           'Browser memory only',   'Returned per request',                 'Fetched from Aria (HTTPS)',   'Browser session only'],
        ['Payment history',       'No',           'Browser memory only',   'Returned per request',                 'Fetched from Aria (HTTPS)',   'Browser session only'],
        ['Salesforce SID token',  'No',           'Request body (transient)', 'Submitted per operation',           'Used as bearer to SF (HTTPS)', 'Not retained'],
        ['Alt caller / ticket ID','No',           'Browser memory only',   'Submitted per operation',              'Passed to Aria as audit field', 'Not retained locally'],
    ],
    col_widths=[1.4, 0.8, 1.2, 1.4, 1.4, 1.2]
)

h3(doc, 'Data Not Stored by This Application')
for item in [
    'Account balances, payment amounts, or credit amounts',
    'Dunning state or dunning history',
    'Plan or device instance details',
    'Installment or NSO information',
    'Any Salesforce record data (Account IDs, fraud flag values)',
    'Credit IDs or reference numbers returned by Aria operations',
]:
    add_bullet(doc, item)

doc.add_paragraph()
add_callout(doc,
    'PCI / PII Scope Note:',
    'This application does not store, process, or transmit cardholder data. It does not store any personally identifiable information '
    'beyond agent usernames (internal staff accounts). Billing account identifiers (BANCANs) and plan IDs are operational reference '
    'numbers, not personal data, and are held only in browser memory for the duration of a session.'
)

hr(doc)

# ── 6. ACCESS CONTROL ────────────────────────────────────────────────────────

section_label(doc, 6, 'Access Control & Authentication')

add_body(doc, 'All API routes are protected by session authentication. The /api/login endpoint is the only unauthenticated route. User accounts are managed exclusively by IT administrators using a command-line tool.')

make_table(doc,
    headers=['Control', 'Implementation'],
    rows=[
        ['Authentication',      'Username and password verified against local credential store'],
        ['Session management',  '32-byte cryptographically random session token issued on login; stored as HTTP cookie'],
        ['Authorisation',       'All API endpoints require a valid session token via require_auth dependency; no role-based access (all authenticated users have equal access)'],
        ['User provisioning',   'Accounts created/deleted via create_user.py CLI by IT administrator; no self-registration'],
        ['Password storage',    'Passwords are hashed before storage; plaintext is not retained'],
        ['Network boundary',    'Application server is bound to the internal LAN; not routable from the internet'],
    ],
    col_widths=[2.0, 4.4]
)

hr(doc)

# ── 7. INTEGRATION SUMMARY ────────────────────────────────────────────────────

section_label(doc, 7, 'Integration Summary')

add_body(doc, 'The portal integrates with two external systems. All outbound connections from the portal server to external systems use HTTPS with TLS.')

make_table(doc,
    headers=['System', 'Endpoint', 'Protocol', 'Authentication', 'Operations'],
    rows=[
        ['Aria Billing API',
         'secure.ariasystems.net/v1/core\nsecure.ariasystems.net/api/AriaQuery/...',
         'HTTPS (TLS)',
         'Client number + API key in payload',
         'Account lookup, credit ops, dunning, plan/device status changes'],
        ['Salesforce CWC',
         'cwc.my.salesforce.com/services/data/v57.0',
         'HTTPS (TLS)',
         'User-supplied SSO session token (SID)',
         'Account lookup (SOQL); fraud flag PATCH'],
    ],
    col_widths=[1.2, 1.8, 0.9, 1.4, 1.8]
)

h3(doc, 'Aria API Operations Reference')
make_table(doc,
    headers=['Aria REST Call', 'Purpose'],
    rows=[
        ['`get_plan_instance_information_m`', 'Look up plan or device details by instance ID or MSISDN'],
        ['`get_acct_plan_balance_m`',         'Retrieve account balance information'],
        ['`get_acct_payment_history_m`',      'Retrieve recent payment records'],
        ['`get_acct_details_all_m`',          'Retrieve account status and dunning information'],
        ['`create_advanced_service_credit_m`','Issue a recurring or one-time service credit'],
        ['`cancel_unapplied_service_credits_m`','Cancel an outstanding service credit'],
        ['`cancel_recurring_credit_m`',       'Cancel an active recurring credit'],
        ['`update_acct_plan_status_m`',       'Update dunning state (suspend / disconnect / resume)'],
        ['`update_acct_plan_multi_m`',        'Update plan or device status (activate / cancel)'],
    ],
    col_widths=[3.0, 3.4]
)

hr(doc)

# Footer paragraph
p = doc.add_paragraph()
para_space(p, before=10, after=0)
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('HLD-ARIA-001  ·  v1.0  ·  August 2026  ·  Prodapt Puerto Rico Inc  ·  Internal')
r.font.color.rgb = LIGHT
r.font.size = Pt(8.5)

# ── Save ─────────────────────────────────────────────────────────────────────

out = 'Aria_ITOps_Portal_HLD.docx'
doc.save(out)
print(f'Saved: {out}')
