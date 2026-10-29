"""Mock banking backend shared by the voice worker and the web server. SQLite, standard library only."""
import json
import re
import secrets
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import idv

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'data/bank.db'
SEED_PATH = ROOT / 'data/bank_seed.json'
CASE_PREFIX = {'complaint': 'CMP', 'dispute': 'DSP', 'fraud': 'FRD', 'hardship': 'HDS',
               'escalation': 'ESC', 'callback': 'CBK'}
_seed_lock = threading.Lock()

SCHEMA = '''
CREATE TABLE customers (id TEXT PRIMARY KEY, first_name TEXT, middle_name TEXT, last_name TEXT, preferred_name TEXT,
  dob TEXT, email TEXT, mobile TEXT, address TEXT, profile TEXT);
CREATE TABLE accounts (id TEXT PRIMARY KEY, customer_id TEXT, name TEXT, type TEXT, bsb TEXT, number TEXT,
  balance REAL, available REAL, credit_limit REAL);
CREATE TABLE transactions (id TEXT PRIMARY KEY, account_id TEXT, customer_id TEXT, posted TEXT, description TEXT,
  amount REAL, status TEXT);
CREATE TABLE loans (id TEXT PRIMARY KEY, customer_id TEXT, product TEXT, balance REAL, rate REAL, rate_type TEXT,
  repayment REAL, frequency TEXT, next_due TEXT, term_end TEXT, offset_account TEXT);
CREATE TABLE applications (id TEXT PRIMARY KEY, customer_id TEXT, product TEXT, amount REAL, status TEXT,
  lodged TEXT, updated TEXT, next_step TEXT);
CREATE TABLE cases (ref TEXT PRIMARY KEY, type TEXT, customer_id TEXT, session_id TEXT, status TEXT, priority TEXT,
  summary TEXT, details TEXT, created_at TEXT, seeded INTEGER DEFAULT 0);
CREATE TABLE forms (id TEXT PRIMARY KEY, session_id TEXT, kind TEXT, purpose TEXT, status TEXT, created_at TEXT,
  updated_at TEXT, result TEXT);
CREATE TABLE events (seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts TEXT, kind TEXT, data TEXT);
CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at TEXT, updated_at TEXT, state TEXT);
'''


def now():
    return datetime.now(ZoneInfo('Australia/Sydney')).isoformat(timespec='seconds')


def connect(path=None):
    path = Path(path or DB_PATH)
    with _seed_lock:
        fresh = not path.exists()
        conn = sqlite3.connect(path, timeout=5, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        if fresh or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='customers'").fetchone():
            _seed(conn)
    return conn


def reset(conn):
    with _seed_lock:
        conn.execute('BEGIN IMMEDIATE')
        for table in ('customers', 'accounts', 'transactions', 'loans', 'applications', 'cases', 'forms', 'events', 'sessions'):
            conn.execute(f'DROP TABLE IF EXISTS {table}')
        conn.execute('COMMIT')
        _seed(conn)


def _seed(conn, seed_path=None):
    seed = json.loads(Path(seed_path or SEED_PATH).read_text())
    conn.executescript(SCHEMA)
    conn.execute('BEGIN')
    for c in seed['customers']:
        conn.execute('INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?)', (c['id'], c['first_name'], c['middle_name'],
                     c['last_name'], c['preferred_name'], c['dob'], c['email'], c['mobile'], json.dumps(c['address']), c['profile']))
        for a in c['accounts']:
            conn.execute('INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?)', (a['id'], c['id'], a['name'], a['type'],
                         a['bsb'], a['number'], a['balance'], a['available'], a.get('limit')))
        for t in c['transactions']:
            conn.execute('INSERT INTO transactions VALUES (?,?,?,?,?,?,?)', (t['id'], t['account_id'], c['id'],
                         t['posted'], t['description'], t['amount'], t['status']))
        for l in c['loans']:
            conn.execute('INSERT INTO loans VALUES (?,?,?,?,?,?,?,?,?,?,?)', (l['id'], c['id'], l['product'], l['balance'],
                         l['rate'], l['rate_type'], l['repayment'], l['frequency'], l['next_due'], l['term_end'], l.get('offset_account')))
        for a in c['applications']:
            conn.execute('INSERT INTO applications VALUES (?,?,?,?,?,?,?,?)', (a['id'], c['id'], a['product'],
                         a['amount'], a['status'], a['lodged'], a['updated'], a['next_step']))
        for k in c['cases']:
            conn.execute('INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,1)', (k['ref'], k['type'], c['id'], None,
                         k['status'], k['priority'], k['summary'], json.dumps(k['details']), k['created_at']))
    conn.execute('COMMIT')


def seed_data(seed_path=None):
    return json.loads(Path(seed_path or SEED_PATH).read_text())


def _rows(cursor):
    return [dict(row) for row in cursor.fetchall()]


# ---------------------------------------------------------------- customer records

def identity_records(conn):
    """The minimum the ID&V worker needs to compare answers. Never sent to a conversational model."""
    return [{'id': r['id'], 'first_name': r['first_name'], 'middle_name': r['middle_name'], 'last_name': r['last_name'],
             'preferred_name': r['preferred_name'], 'dob': r['dob'], 'address': json.loads(r['address'])}
            for r in conn.execute('SELECT * FROM customers ORDER BY id')]


def accounts(conn, customer_id):
    return _rows(conn.execute('SELECT * FROM accounts WHERE customer_id=? ORDER BY id', (customer_id,)))


def transactions(conn, customer_id, account_id=None, search=None, limit=8):
    sql, args = 'SELECT * FROM transactions WHERE customer_id=?', [customer_id]
    if account_id: sql, args = sql + ' AND account_id=?', args + [account_id]
    if search: sql, args = sql + ' AND lower(description) LIKE ?', args + ['%' + search.lower() + '%']
    return _rows(conn.execute(sql + ' ORDER BY posted DESC, id LIMIT ?', args + [limit]))


def transaction(conn, customer_id, transaction_id):
    row = conn.execute('SELECT * FROM transactions WHERE customer_id=? AND id=?', (customer_id, transaction_id)).fetchone()
    return dict(row) if row else None


def loans(conn, customer_id):
    return _rows(conn.execute('SELECT * FROM loans WHERE customer_id=? ORDER BY id', (customer_id,)))


def applications(conn, customer_id):
    return _rows(conn.execute('SELECT * FROM applications WHERE customer_id=? ORDER BY id', (customer_id,)))


def cases(conn, customer_id=None, session_id=None):
    sql, args = 'SELECT * FROM cases WHERE 1=1', []
    if customer_id: sql, args = sql + ' AND customer_id=?', args + [customer_id]
    if session_id: sql, args = sql + ' AND session_id=?', args + [session_id]
    rows = _rows(conn.execute(sql + ' ORDER BY created_at DESC, ref', args))
    for row in rows: row['details'] = json.loads(row['details'] or '{}')
    return rows


def create_case(conn, kind, summary, details, customer_id=None, session_id=None, priority='normal', status='Open'):
    while True:
        ref = f'{CASE_PREFIX[kind]}-{secrets.randbelow(9000) + 1000}'
        try:
            conn.execute('INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,0)', (ref, kind, customer_id, session_id, status,
                         priority, summary[:500], json.dumps(details), now()))
            return ref
        except sqlite3.IntegrityError:
            continue


def update_case(conn, ref, details, summary=None):
    row = conn.execute('SELECT details, summary FROM cases WHERE ref=?', (ref,)).fetchone()
    merged = {**json.loads(row['details'] or '{}'), **details}
    conn.execute('UPDATE cases SET details=?, summary=? WHERE ref=?', (json.dumps(merged), (summary or row['summary'])[:500], ref))


def overview(conn):
    """Everything the demonstrator inspector shows. Not reachable from the voice session."""
    customers = []
    for row in conn.execute('SELECT * FROM customers ORDER BY id'):
        c = dict(row)
        c['address'] = json.loads(c['address'])
        c.update(accounts=accounts(conn, c['id']), transactions=transactions(conn, c['id'], limit=50),
                 loans=loans(conn, c['id']), applications=applications(conn, c['id']), cases=cases(conn, c['id']))
        customers.append(c)
    return {'customers': customers, 'cases': cases(conn), 'scenarios': seed_data()['scenarios']}


# ---------------------------------------------------------------- frontend forms

def open_form(conn, session_id, kind, purpose):
    form_id = 'F' + secrets.token_hex(8)
    conn.execute('INSERT INTO forms VALUES (?,?,?,?,?,?,?,?)', (form_id, session_id, kind, purpose, 'open', now(), now(), None))
    return form_id


def form(conn, form_id):
    if not isinstance(form_id, str) or not re.fullmatch(r'F[0-9a-f]{16}', form_id): return None
    row = conn.execute('SELECT * FROM forms WHERE id=?', (form_id,)).fetchone()
    return dict(row) if row else None


def validate_callback(body, consent=True):
    errors = {}
    name = body.get('name') if isinstance(body.get('name'), str) else ''
    name = ' '.join(name.split())
    if not re.fullmatch(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ .'-]{1,79}", name): errors['name'] = 'Enter your name.'
    phone = re.sub(r'[\s()-]', '', body.get('phone') if isinstance(body.get('phone'), str) else '')
    if phone.startswith('+61'): phone = '0' + phone[3:]
    if not re.fullmatch(r'0[2378]\d{8}|04\d{8}', phone): errors['phone'] = 'Enter an Australian mobile or landline number.'
    if consent and body.get('consent') is not True: errors['consent'] = 'Consent is required for a callback.'
    return {'name': name, 'phone': phone}, errors


FORM_KINDS = {'loan_callback': 'Lending specialist callback', 'contact_callback': 'Call back an unverified caller'}


def submit_callback(conn, form_id, body):
    """Validate and save a callback request: a lending callback (name, phone, consent) or a call back for a caller who
    couldn't be verified (name and phone only). Returns (form, reference, errors)."""
    record = form(conn, form_id)
    if not record or record['kind'] not in FORM_KINDS: return None, None, {'form': 'This form is no longer available.'}
    if record['status'] != 'open': return record, None, {'form': 'This form was already ' + record['status'] + '.'}
    lending = record['kind'] == 'loan_callback'
    clean, errors = validate_callback(body, consent=lending)
    if errors: return record, None, errors
    state = session_state(conn, record['session_id']) or {}
    customer_id = state.get('verified_customer')
    details = {'name': clean['name'], 'phone': clean['phone'], 'purpose': record['purpose'], 'mock': True}
    summary = f"{FORM_KINDS[record['kind']]}: {record['purpose'].replace('_', ' ')}"
    if lending:
        details['consent'] = True
        needs = state.get('loan_needs') or {}  # What the caller told the agent, so the specialist doesn't ask again.
        if needs:
            details['needs'] = needs
            summary += ''.join(f", {needs[k]}" for k in ('purpose', 'amount', 'timeframe') if k in needs)
    ref = create_case(conn, 'callback', summary, details, customer_id=customer_id, session_id=record['session_id'])
    conn.execute('UPDATE forms SET status=?, updated_at=?, result=? WHERE id=?', ('submitted', now(), json.dumps({'reference': ref}), form_id))
    return record, ref, {}


def submit_customer_number(conn, form_id, body):
    """Check the format and close the box. Whether the number belongs to anyone is the Worker's call, not the backend's.
    Returns (form, number, errors)."""
    record = form(conn, form_id)
    if not record or record['kind'] != 'customer_number': return None, None, {'form': 'This form is no longer available.'}
    if record['status'] != 'open': return record, None, {'form': 'This form was already ' + record['status'] + '.'}
    number = idv.normalize_customer_number(body.get('customer_number'))
    if not number: return record, None, {'customer_number': 'Enter a customer number like CUS-12345.'}
    conn.execute('UPDATE forms SET status=?, updated_at=? WHERE id=?', ('submitted', now(), form_id))
    return record, number, {}


def close_form(conn, form_id, reason):
    record = form(conn, form_id)
    if not record or record['status'] != 'open': return None
    conn.execute('UPDATE forms SET status=?, updated_at=? WHERE id=?', (reason, now(), form_id))
    return record


# ---------------------------------------------------------------- audit and session state

def log(conn, session_id, kind, data):
    conn.execute('INSERT INTO events (session_id, ts, kind, data) VALUES (?,?,?,?)',
                 (session_id, now(), kind, json.dumps(data, default=str)))


def save_session(conn, session_id, state):
    conn.execute('INSERT INTO sessions VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, state=excluded.state',
                 (session_id, now(), now(), json.dumps(state, default=str)))


def session_state(conn, session_id):
    row = conn.execute('SELECT state FROM sessions WHERE id=?', (session_id,)).fetchone()
    return json.loads(row['state']) if row else None


def latest_session(conn, after=0):
    row = conn.execute('SELECT * FROM sessions ORDER BY started_at DESC, rowid DESC LIMIT 1').fetchone()
    if not row: return None
    events = _rows(conn.execute('SELECT * FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT 500', (row['id'], after)))
    for e in events: e['data'] = json.loads(e['data'])
    return {'id': row['id'], 'started_at': row['started_at'], 'updated_at': row['updated_at'],
            'state': json.loads(row['state']), 'events': events}
