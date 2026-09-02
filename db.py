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
    token TEXT NOT NULL UNIQUE,    -- unguessable URL token for client dashboard
    email TEXT                     -- where the PDF report goes
);
"""

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


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def friendly(cat):
    return FRIENDLY.get((cat or "").strip().lower(), (cat or "Other").title())


def insert_rows(conn, rows):
    conn.executemany(
        "INSERT INTO dns_log (ts,tenant,user,domain,category,blocked,client_ip,policy,reason)"
        " VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()


def dashboard_data(conn, tenant, start, end):
    """All aggregates the dashboard/report needs, one dict, JSON-serialisable."""
    q = lambda sql, *a: conn.execute(sql, (tenant, start, end, *a)).fetchall()
    base = "FROM dns_log WHERE tenant=? AND ts>=? AND ts<?"

    total, users, blocked = conn.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT user), SUM(blocked) {base}",
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
    user_rows = q(f"SELECT user, category, COUNT(*) n {base} GROUP BY user, category")
    per_user_all, per_user_distr = defaultdict(int), defaultdict(int)
    for r in user_rows:
        per_user_all[r["user"]] += r["n"]
        if friendly(r["category"]) in DISTRACTION:
            per_user_distr[r["user"]] += r["n"]
    users_sorted = sorted(per_user_all, key=lambda u: -per_user_distr.get(u, 0))
    flagged = users_sorted[0] if users_sorted else None
    per_user = [{"user": u, "total": per_user_all[u],
                 "distraction": per_user_distr.get(u, 0)} for u in users_sorted]

    # hourly heatmap for flagged user's distraction traffic: {day: [24 counts]}
    heat = {}
    if flagged:
        rows = q(f"SELECT substr(ts,1,10) d, CAST(substr(ts,12,2) AS INT) h,"
                 f" category, COUNT(*) n {base} AND user=? GROUP BY d,h,category", flagged)
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
