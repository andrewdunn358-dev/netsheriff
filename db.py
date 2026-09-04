"""SQLite storage + aggregation queries for NxReport."""
import re
import sqlite3
from datetime import datetime
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
CREATE TABLE IF NOT EXISTS app_usage (
    id INTEGER PRIMARY KEY,
    tenant TEXT NOT NULL,
    username TEXT NOT NULL,        -- AD login name from the agent
    hostname TEXT,                 -- machine the sample came from
    app TEXT NOT NULL,             -- friendly application name, e.g. 'Chrome'
    site TEXT,                     -- matched leisure site only, never a raw title
    sampled_at TEXT NOT NULL,      -- 'YYYY-MM-DD HH:MM:SS'
    seconds INTEGER NOT NULL       -- seconds this sample represents
);
CREATE INDEX IF NOT EXISTS idx_usage ON app_usage(tenant, username, sampled_at);
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

# A DNS lookup for facebook.com can mean someone opened Facebook, or it can
# mean a like-button loaded inside an unrelated news article. These subdomain
# prefixes are the ones that are unambiguously machine-to-machine — tracking
# pixels, beacons and telemetry — so counting them as leisure browsing
# overstates what a person actually did. Excluded from per-person counts,
# which matters because a name gets attached to those numbers.
TRACKING_PREFIXES = (
    "tr.", "pixel.", "pixels.", "analytics.", "beacon.", "telemetry.",
    "metrics.", "stats.", "collect.", "log.", "logs.", "events.", "track.",
    "tracking.", "ads.", "ad.", "adservice.", "sb.", "graph.", "connect.",
    "mqtt.", "edge-mqtt.", "gateway.", "api.",
)


def is_tracking(domain):
    """True for domains that are almost certainly background chatter rather
    than someone visiting a site."""
    d = (domain or "").lower()
    return d.startswith(TRACKING_PREFIXES)


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


def record_app_usage(conn, tenant, samples):
    """Store foreground samples from a workstation agent.

    Each sample says "at this moment, this app was in front, for this many
    seconds". Time is counted by adding up samples, so it reflects what was
    actually observed rather than an estimate. If a machine sleeps or the
    agent misses a run, the time simply isn't counted — an undercount, which
    is the safer direction when a person's name is attached.
    """
    n = 0
    for s in samples:
        if not isinstance(s, dict):
            continue
        user = (s.get("username") or "").strip()
        app = (s.get("app") or "").strip()
        if not user or not app:
            continue
        conn.execute(
            "INSERT INTO app_usage (tenant, username, hostname, app, site,"
            " sampled_at, seconds) VALUES (?,?,?,?,?,?,?)",
            (tenant, user, (s.get("hostname") or "").strip() or None, app,
             (s.get("site") or "").strip() or None,
             s.get("sampled_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             int(s.get("seconds") or 60)))
        n += 1
    conn.commit()
    return n


def screen_time(conn, tenant, start, end):
    """Minutes per person, split into leisure sites and everything else."""
    rows = conn.execute(
        "SELECT username, app, site, SUM(seconds) secs FROM app_usage"
        " WHERE tenant=? AND sampled_at>=? AND sampled_at<?"
        " GROUP BY username, app, site", (tenant, start, end)).fetchall()
    people = defaultdict(lambda: {"total": 0, "leisure": 0, "sites": defaultdict(int)})
    for r in rows:
        p = people[r["username"]]
        p["total"] += r["secs"]
        if r["site"]:
            p["leisure"] += r["secs"]
            p["sites"][r["site"]] += r["secs"]
    out = []
    for user, d in people.items():
        top = sorted(d["sites"].items(), key=lambda kv: -kv[1])[:4]
        out.append({
            "user": user,
            "minutes": round(d["total"] / 60),
            "leisure_minutes": round(d["leisure"] / 60),
            "sites": [{"site": s, "minutes": round(v / 60)} for s, v in top],
        })
    return sorted(out, key=lambda x: -x["leisure_minutes"])


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

# Identities that are really just an IP: a device we saw but nobody was
# logged into. Matches IPv4 and the bare IPv6 forms NxRelay can emit.
IP_LIKE = re.compile(r"^(\d{1,3}(\.\d{1,3}){3}|[0-9a-fA-F:]{6,})$")

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
    # Grouped by domain as well as category so tracking chatter can be
    # excluded from the per-person counts — see is_tracking().
    user_rows = q(f"SELECT {IDENTITY} AS person, domain, category, COUNT(*) n {base}"
                  f" GROUP BY person, domain, category")
    per_user_all, per_user_distr = defaultdict(int), defaultdict(int)
    for r in user_rows:
        per_user_all[r["person"]] += r["n"]
        if friendly(r["category"]) in DISTRACTION and not is_tracking(r["domain"]):
            per_user_distr[r["person"]] += r["n"]

    # Anything that resolved to a bare IP is a device nobody was logged into —
    # in practice phones and handsets on the wifi. They can't be identified
    # (iOS randomises its MAC per network) and the client can't act on them,
    # so showing them by IP invites false conclusions: an unnamed phone can
    # top the leisure chart while being someone's own device on their own
    # time. Report them as one honest total instead, so the numbers still
    # reconcile without implying anything about a person.
    named = [u for u in per_user_all if not IP_LIKE.match(str(u))]
    unattributed_total = sum(per_user_all[u] for u in per_user_all
                             if IP_LIKE.match(str(u)))
    unattributed_distr = sum(per_user_distr.get(u, 0) for u in per_user_all
                             if IP_LIKE.match(str(u)))

    users_sorted = sorted(named, key=lambda u: -per_user_distr.get(u, 0))
    # Only flag someone if they've genuinely got distraction activity - without
    # this check, the top user by distraction count still "wins" even with
    # zero such requests, making the dashboard look like it's flagging normal
    # (or in this case, test) traffic as a concern when nothing's actually
    # happened. The template already has a clean '-' fallback for
    # flagged_user=None; this just makes sure that path actually gets used.
    flagged = (users_sorted[0] if users_sorted and per_user_distr.get(users_sorted[0], 0) > 0
               else None)

    # Top leisure domains per person, so the chart tooltip can say what someone
    # was actually on rather than just a count. Capped at 4 — enough to be
    # meaningful, not a full browsing history in a summary chart.
    sites = defaultdict(list)
    for r in q(f"SELECT {IDENTITY} AS person, domain, category, COUNT(*) n {base}"
               f" GROUP BY person, domain ORDER BY n DESC"):
        if (friendly(r["category"]) in DISTRACTION and not is_tracking(r["domain"])
                and len(sites[r["person"]]) < 4):
            sites[r["person"]].append({"domain": r["domain"], "requests": r["n"]})

    # How spread out someone's leisure activity was. Deliberately NOT a
    # duration: DNS gives no session start or end, and caching means a long
    # visit can produce few lookups. What we can say honestly is how many
    # separate 15-minute windows contained leisure activity, and when the
    # first and last were. "Activity in 14 separate periods between 09:10 and
    # 16:45" is checkable; "spent 3.5 hours on Instagram" would be invented.
    spread = defaultdict(lambda: {"windows": set(), "first": None, "last": None})
    for r in q(f"SELECT {IDENTITY} AS person, ts, domain, category {base}"
               f" ORDER BY ts"):
        if friendly(r["category"]) not in DISTRACTION or is_tracking(r["domain"]):
            continue
        p = r["person"]
        ts = r["ts"]
        # bucket = date + quarter-hour index, so windows don't merge across days
        spread[p]["windows"].add(ts[:13] + ":" + str(int(ts[14:16]) // 15))
        if spread[p]["first"] is None:
            spread[p]["first"] = ts
        spread[p]["last"] = ts

    per_user = [{"user": u, "total": per_user_all[u],
                 "distraction": per_user_distr.get(u, 0),
                 "top_sites": sites.get(u, []),
                 "active_windows": len(spread[u]["windows"]) if u in spread else 0,
                 "first_seen": spread[u]["first"] if u in spread else None,
                 "last_seen": spread[u]["last"] if u in spread else None}
                for u in users_sorted]

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

    # Flagged requests split by what they actually are. NxCloud sets the same
    # 'blocked' flag for a phishing domain and for a phone looking up
    # cloudflare-dns.com, but they mean completely different things to a
    # client: one is a threat, the other is a device resolving DNS outside
    # the filter so its browsing never reaches this report. Lumping them
    # together would either cry wolf or bury a real detection.
    # Matched by pattern, not a fixed list: mask-h2.icloud.com slipped past an
    # earlier exact-match version and raised a red 'harmful sites' banner on a
    # client dashboard for what is just an iPhone. Apple and the DoH providers
    # add hostnames whenever they like, so anything mask*.icloud.com counts,
    # and resolver hostnames are matched on their recognisable parts.
    bypass = threat = 0
    bypass_re = re.compile(
        r"(^mask[\w-]*\.icloud\.com$)"          # iCloud Private Relay, any variant
        r"|(^|\.)(dns|doh)\."                    # dns.quad9.net, doh.opendns.com
        r"|(cloudflare-dns|nextdns|adguard-dns|dns\.google|one\.one\.one\.one)",
        re.I)
    for r in q(f"SELECT domain, category, COUNT(*) n {base} AND blocked=1"
               f" GROUP BY domain, category"):
        dom = (r["domain"] or "").lower()
        if "proxy" in (r["category"] or "").lower() or bypass_re.search(dom):
            bypass += r["n"]
        else:
            threat += r["n"]

    # The detail behind the flagged count. A number the client can't check is
    # worse than no number: it either gets ignored or believed on faith.
    flagged_detail = []
    for r in q(f"SELECT domain, category, {IDENTITY} AS person, COUNT(*) n,"
               f" MAX(ts) last_seen {base} AND blocked=1"
               f" GROUP BY domain, person ORDER BY n DESC LIMIT 20"):
        dom = (r["domain"] or "").lower()
        kind = ("bypass" if "proxy" in (r["category"] or "").lower()
                or bypass_re.search(dom) else "threat")
        flagged_detail.append({
            "domain": r["domain"], "person": r["person"], "requests": r["n"],
            "last_seen": r["last_seen"], "kind": kind})

    distr_total = sum(per_user_distr.values())
    return {
        "tenant": tenant, "start": start, "end": end,
        "kpis": {"total": total, "users": len(named), "blocked": blocked,
                 "distinct_domains": conn.execute(
                     f"SELECT COUNT(DISTINCT domain) {base}",
                     (tenant, start, end)).fetchone()[0] or 0,
                 "bypass": bypass, "threat": threat,
                 "distraction_pct": round(100 * distr_total / total, 1) if total else 0},
        "category_share": [{"category": c, "requests": n} for c, n in cat_share],
        "per_user": per_user, "flagged_user": flagged,
        "unattributed": {"total": unattributed_total,
                         "distraction": unattributed_distr},
        "heatmap": heat, "daily": daily, "top_domains": top_domains,
        "flagged_detail": flagged_detail,
    }
