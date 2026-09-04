"""SQLite storage + aggregation queries for NxReport."""
import sqlite3
from collections import defaultdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS dns_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,              -- 'YYYY-MM-DD HH:MM:SS'
    tenant TEXT NOT NULL,          -- NxCloud operator name
    user TEXT NOT NULL,
    domain TEXT NOT NULL,
    category TEXT NOT NULL,
    blocked INTEGER NOT NULL DEFAULT 0,
    client_ip TEXT,
    policy TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_tenant_ts ON dns_log(tenant, ts);
CREATE TABLE IF NOT EXISTS tenants (
    name TEXT PRIMARY KEY,         -- matches NxCloud operator name
    display_name TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,    -- unguessable URL token, kept as a fallback link
    email TEXT,                    -- where the PDF report goes + password reset emails
    username TEXT UNIQUE,          -- client's login username
    password_hash TEXT,            -- werkzeug hash; NULL = login disabled for this tenant
    reset_token TEXT UNIQUE,       -- set when a password-reset email is requested
    reset_expiry TEXT              -- ISO timestamp; token invalid after this
);
CREATE TABLE IF NOT EXISTS ip_user_map (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,          -- NxCloud operator name, matches dns_log.tenant
    ip TEXT NOT NULL,              -- private IP as seen in dns_log.client_ip
    username TEXT NOT NULL,        -- AD login name reported by the site agent
    first_seen TEXT NOT NULL,      -- 'YYYY-MM-DD HH:MM:SS'
    last_seen TEXT NOT NULL        -- extended while the same user holds the IP
);
CREATE INDEX IF NOT EXISTS idx_map_lookup ON ip_user_map(tenant, ip, first_seen, last_seen);
CREATE TABLE IF NOT EXISTS admin_users (
    username TEXT PRIMARY KEY,     -- staff login, separate from client tenants entirely
    password_hash TEXT NOT NULL,
    email TEXT,
    reset_token TEXT UNIQUE,       -- set when a password-reset email is requested
    reset_expiry TEXT              -- ISO timestamp; token invalid after this
);
"""


def create_tenant(conn, name, display_name, username, password, email=None, token=None):
    """Register (or update) a tenant with real login credentials.

    `name` must exactly match the NxCloud operator name — it's the join key
    against dns_log.tenant. Safe to call again for an existing tenant (e.g.
    fixing a typo, or re-issuing credentials) — updates in place rather
    than failing on the name/token already existing. Preserves the current
    token if one isn't explicitly passed, so existing fallback links keep
    working. Returns the token in use (existing or newly generated).
    """
    import secrets
    from werkzeug.security import generate_password_hash
    existing = conn.execute("SELECT token FROM tenants WHERE name=?", (name,)).fetchone()
    if token is None:
        token = existing["token"] if existing else secrets.token_hex(12)
    conn.execute("""
        INSERT INTO tenants (name, display_name, token, email, username, password_hash)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            display_name=excluded.display_name,
            token=excluded.token,
            email=excluded.email,
            username=excluded.username,
            password_hash=excluded.password_hash
        """,
        (name, display_name, token, email, username, generate_password_hash(password)))
    conn.commit()
    return token


def verify_login(conn, username, password):
    """Return the tenant row if username/password match, else None."""
    from werkzeug.security import check_password_hash
    row = conn.execute("SELECT * FROM tenants WHERE username=?", (username,)).fetchone()
    if row and row["password_hash"] and check_password_hash(row["password_hash"], password):
        return row
    return None


def list_tenants(conn):
    return conn.execute("SELECT name, display_name, username, email, token FROM tenants"
                         " ORDER BY display_name").fetchall()


def create_admin(conn, username, password, email=None):
    """Register (or update) a staff admin login — entirely separate table
    from client tenants. Safe to call again for an existing username."""
    from werkzeug.security import generate_password_hash
    conn.execute("""
        INSERT INTO admin_users (username, password_hash, email) VALUES (?,?,?)
        ON CONFLICT(username) DO UPDATE SET
            password_hash=excluded.password_hash,
            email=excluded.email
        """, (username, generate_password_hash(password), email))
    conn.commit()


def verify_admin(conn, username, password):
    from werkzeug.security import check_password_hash
    row = conn.execute("SELECT * FROM admin_users WHERE username=?", (username,)).fetchone()
    if row and check_password_hash(row["password_hash"], password):
        return row
    return None


def list_admins(conn):
    return conn.execute("SELECT username, email FROM admin_users ORDER BY username").fetchall()


def set_admin_password(conn, username, new_password):
    from werkzeug.security import generate_password_hash
    conn.execute("UPDATE admin_users SET password_hash=?, reset_token=NULL, reset_expiry=NULL"
                 " WHERE username=?", (generate_password_hash(new_password), username))
    conn.commit()


def create_admin_reset_token(conn, identifier):
    """identifier = admin's username OR email. Returns the admin row (with a
    fresh reset_token, valid 1 hour) or None if no match."""
    import secrets
    from datetime import datetime, timedelta
    row = conn.execute("SELECT * FROM admin_users WHERE username=? OR email=?",
                        (identifier, identifier)).fetchone()
    if not row:
        return None
    token = secrets.token_urlsafe(32)
    expiry = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    conn.execute("UPDATE admin_users SET reset_token=?, reset_expiry=? WHERE username=?",
                 (token, expiry, row["username"]))
    conn.commit()
    return dict(row, reset_token=token)


def get_admin_by_reset_token(conn, token):
    from datetime import datetime
    row = conn.execute("SELECT * FROM admin_users WHERE reset_token=?", (token,)).fetchone()
    if not row or not row["reset_expiry"]:
        return None
    if datetime.utcnow().isoformat() > row["reset_expiry"]:
        return None
    return row


def update_tenant(conn, name, display_name, email):
    """Update a tenant's display name/email only — deliberately never touches
    username or password_hash, so editing details can't accidentally reset
    a client's login."""
    conn.execute("UPDATE tenants SET display_name=?, email=? WHERE name=?",
                 (display_name, email, name))
    conn.commit()


def set_tenant_password(conn, name, new_password):
    """Set a tenant's password directly (admin-triggered reset) and clear any
    outstanding self-service reset token."""
    from werkzeug.security import generate_password_hash
    conn.execute("UPDATE tenants SET password_hash=?, reset_token=NULL, reset_expiry=NULL"
                 " WHERE name=?", (generate_password_hash(new_password), name))
    conn.commit()


def create_reset_token(conn, identifier):
    """identifier = client's username OR email. Returns the tenant row (with a
    fresh reset_token set, valid 1 hour) or None if no match — caller decides
    whether to reveal that distinction (usually: don't, to avoid username
    enumeration)."""
    import secrets
    from datetime import datetime, timedelta
    row = conn.execute("SELECT * FROM tenants WHERE username=? OR email=?",
                        (identifier, identifier)).fetchone()
    if not row:
        return None
    token = secrets.token_urlsafe(32)
    expiry = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    conn.execute("UPDATE tenants SET reset_token=?, reset_expiry=? WHERE name=?",
                 (token, expiry, row["name"]))
    conn.commit()
    return dict(row, reset_token=token)


def get_tenant_by_reset_token(conn, token):
    """Return the tenant row if the token exists and hasn't expired, else None."""
    from datetime import datetime
    row = conn.execute("SELECT * FROM tenants WHERE reset_token=?", (token,)).fetchone()
    if not row or not row["reset_expiry"]:
        return None
    if datetime.utcnow().isoformat() > row["reset_expiry"]:
        return None
    return row

# Map raw NxFilter/Jahaslist category names -> client-friendly labels.
FRIENDLY = {
    "sns": "Social Media", "social-networking": "Social Media", "social": "Social Media",
    "streaming": "Video & Streaming", "video": "Video & Streaming",
    "news": "News", "shopping": "Shopping", "webmail": "Personal Email",
    "search": "Search", "business": "Business & Work", "finance": "Business & Work",
    "ads": "Ads & Trackers", "adult": "Adult (blocked)", "gambling": "Gambling (blocked)",
    "malware": "Malicious (blocked)", "phishing": "Malicious (blocked)",
    "games": "Games", "sports": "Sports", "travel": "Travel",
}
# Categories counted as "distraction" for the flagged-user metrics.
DISTRACTION = {"Social Media", "Video & Streaming", "Games", "Shopping", "Sports"}


def connect(path, check_same_thread=True):
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migrate DBs created before login existed (CREATE TABLE IF NOT EXISTS
    # won't add columns to an already-existing tenants table).
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tenants)")}
    if "username" not in cols:
        conn.execute("ALTER TABLE tenants ADD COLUMN username TEXT")
    if "password_hash" not in cols:
        conn.execute("ALTER TABLE tenants ADD COLUMN password_hash TEXT")
    if "reset_token" not in cols:
        conn.execute("ALTER TABLE tenants ADD COLUMN reset_token TEXT")
    if "reset_expiry" not in cols:
        conn.execute("ALTER TABLE tenants ADD COLUMN reset_expiry TEXT")
    admin_cols = {r["name"] for r in conn.execute("PRAGMA table_info(admin_users)")}
    if "reset_token" not in admin_cols:
        conn.execute("ALTER TABLE admin_users ADD COLUMN reset_token TEXT")
    if "reset_expiry" not in admin_cols:
        conn.execute("ALTER TABLE admin_users ADD COLUMN reset_expiry TEXT")
    conn.commit()
    return conn


def record_ip_users(conn, tenant, pairs, seen_at=None, gap_seconds=600):
    """Record observed (ip, username) pairs from a site agent.

    Stored as time intervals rather than a single current value, so a report
    for last Tuesday attributes to whoever held that IP last Tuesday. This is
    what makes the whole thing safe under DHCP churn — no reservations needed.

    If the same user still holds the same IP and we saw them within
    gap_seconds, the open interval is extended. Otherwise a new interval
    starts, which is what happens when a lease moves to someone else.

    Returns (extended, created) counts.
    """
    from datetime import datetime, timedelta
    now = seen_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cutoff = (datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
              - timedelta(seconds=gap_seconds)).strftime("%Y-%m-%d %H:%M:%S")
    extended = created = 0
    for ip, username in pairs:
        ip = (ip or "").strip()
        username = (username or "").strip()
        if not ip or not username:
            continue
        row = conn.execute(
            "SELECT id FROM ip_user_map WHERE tenant=? AND ip=? AND username=?"
            " AND last_seen >= ? ORDER BY last_seen DESC LIMIT 1",
            (tenant, ip, username, cutoff)).fetchone()
        if row:
            conn.execute("UPDATE ip_user_map SET last_seen=? WHERE id=?", (now, row["id"]))
            extended += 1
        else:
            conn.execute(
                "INSERT INTO ip_user_map (tenant, ip, username, first_seen, last_seen)"
                " VALUES (?,?,?,?,?)", (tenant, ip, username, now, now))
            created += 1
    conn.commit()
    return extended, created


def user_for_ip_at(conn, tenant, ip, ts):
    """Who held this IP at this moment? None if unknown."""
    row = conn.execute(
        "SELECT username FROM ip_user_map WHERE tenant=? AND ip=?"
        " AND first_seen <= ? AND last_seen >= ?"
        " ORDER BY first_seen DESC LIMIT 1", (tenant, ip, ts, ts)).fetchone()
    return row["username"] if row else None


def friendly(cat):
    return FRIENDLY.get((cat or "").strip().lower(), (cat or "Other").title())


def insert_rows(conn, rows):
    conn.executemany(
        "INSERT INTO dns_log (ts,tenant,user,domain,category,blocked,client_ip,policy,reason)"
        " VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


# How long after a mapping was last confirmed we still trust it. The site
# agent polls every few minutes, so a request can legitimately land just after
# the last observation but before the next one. Too small and traffic falls
# through to a bare IP; too large and a machine handed to another user could
# briefly misattribute. 15 minutes suits a 5-minute poll.
MAP_GRACE_MINUTES = 15

# Resolves each dns_log row to a person where we can. Order matters:
#   1. the username mapped to that private IP at that moment (real name)
#   2. the private IP itself (a device we know about but nobody was logged in,
#      e.g. a handset or a machine that was off when the agent last polled)
#   3. NxCloud's own user field, which is the bare operator name
# Written as a correlated subquery so it can be dropped into any existing
# aggregate without restructuring the queries around it.
IDENTITY = f"""COALESCE(
    NULLIF(CASE
        WHEN dns_log.user IS NULL THEN NULL
        -- NxCloud puts the operator name here when it has no real username,
        -- so 'NCS' means "unattributed", not a person.
        WHEN dns_log.user = dns_log.tenant THEN NULL
        WHEN dns_log.user = 'unknown' THEN NULL
        -- Where no exactly-matching NxCloud user exists, names arrive
        -- prefixed as 'tenant_username'. Strip it for display.
        WHEN dns_log.user LIKE dns_log.tenant || '\\_%' ESCAPE '\\'
            THEN substr(dns_log.user, length(dns_log.tenant) + 2)
        ELSE dns_log.user
    END, ''),
    (SELECT m.username FROM ip_user_map m
      WHERE m.tenant = dns_log.tenant
        AND m.ip = dns_log.client_ip
        AND m.first_seen <= dns_log.ts
        AND datetime(m.last_seen, '+{MAP_GRACE_MINUTES} minutes') >= dns_log.ts
      ORDER BY m.first_seen DESC LIMIT 1),
    dns_log.client_ip,
    dns_log.user)"""


def dashboard_data(conn, tenant, start, end):
    """All aggregates the dashboard/report needs, one dict, JSON-serialisable."""
    q = lambda sql, *a: conn.execute(sql, (tenant, start, end, *a)).fetchall()
    base = "FROM dns_log WHERE tenant=? AND ts>=? AND ts<?"

    total, users, blocked = conn.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT {IDENTITY}), SUM(blocked) {base}",
        (tenant, start, end)).fetchone()
    total, blocked = total or 0, blocked or 0

    # per-category totals (friendly names, exclude ad/tracker noise from share)
    cat_rows = q(f"SELECT category, COUNT(*) n {base} GROUP BY category")
    cats = defaultdict(int)
    for r in cat_rows:
        cats[friendly(r["category"])] += r["n"]
    cats.pop("Ads & Trackers", None)
    cat_share = sorted(cats.items(), key=lambda kv: -kv[1])
    if len(cat_share) > 6:
        head, tail = cat_share[:6], cat_share[6:]
        cat_share = head + [("Other", sum(n for _, n in tail))]

    # distraction requests per user
    user_rows = q(f"SELECT {IDENTITY} AS person, category, COUNT(*) n {base}"
                  f" GROUP BY person, category")
    per_user_all, per_user_distr = defaultdict(int), defaultdict(int)
    for r in user_rows:
        per_user_all[r["person"]] += r["n"]
        if friendly(r["category"]) in DISTRACTION:
            per_user_distr[r["person"]] += r["n"]
    users_sorted = sorted(per_user_all, key=lambda u: -per_user_distr.get(u, 0))
    # Only flag someone if they've genuinely got distraction activity - without
    # this check, the top user by distraction count still "wins" even with
    # zero such requests, making the dashboard look like it's flagging normal
    # (or in this case, test) traffic as a concern when nothing's actually
    # happened. The template already has a clean '-' fallback for
    # flagged_user=None; this just makes sure that path actually gets used.
    flagged = (users_sorted[0] if users_sorted and per_user_distr.get(users_sorted[0], 0) > 0
               else None)
    per_user = [{"user": u, "total": per_user_all[u],
                 "distraction": per_user_distr.get(u, 0)} for u in users_sorted]

    # hourly heatmap for flagged user's distraction traffic: {day: [24 counts]}
    heat = {}
    if flagged:
        rows = q(f"SELECT substr(ts,1,10) d, CAST(substr(ts,12,2) AS INT) h,"
                 f" category, COUNT(*) n {base} AND {IDENTITY}=? GROUP BY d,h,category", flagged)
        hm = defaultdict(lambda: [0] * 24)
        for r in rows:
            if friendly(r["category"]) in DISTRACTION:
                hm[r["d"]][r["h"]] += r["n"]
        heat = dict(sorted(hm.items()))

    # daily trend: total vs distraction, whole company
    rows = q(f"SELECT substr(ts,1,10) d, category, COUNT(*) n {base} GROUP BY d, category")
    trend = defaultdict(lambda: {"total": 0, "distraction": 0})
    for r in rows:
        trend[r["d"]]["total"] += r["n"]
        if friendly(r["category"]) in DISTRACTION:
            trend[r["d"]]["distraction"] += r["n"]
    daily = [{"date": d, **v} for d, v in sorted(trend.items())]

    # top domains (skip ads/trackers)
    rows = q(f"SELECT domain, category, COUNT(*) n {base}"
             f" GROUP BY domain ORDER BY n DESC LIMIT 60")
    top_domains = [{"domain": r["domain"], "category": friendly(r["category"]),
                    "requests": r["n"]} for r in rows
                   if friendly(r["category"]) != "Ads & Trackers"][:12]

    distr_total = sum(per_user_distr.values())
    return {
        "tenant": tenant, "start": start, "end": end,
        "kpis": {"total": total, "users": users or 0, "blocked": blocked,
                 "distraction_pct": round(100 * distr_total / total, 1) if total else 0},
        "category_share": [{"category": c, "requests": n} for c, n in cat_share],
        "per_user": per_user, "flagged_user": flagged,
        "heatmap": heat, "daily": daily, "top_domains": top_domains,
    }
