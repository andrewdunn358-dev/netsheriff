"""Multi-tenant reporting dashboard for NxFilter/NxCloud logs.

Each client (NxCloud operator) gets an unguessable URL:
    /t/<token>            e.g. /t/demo-brightside-7f3a
Optional ?days=7|30 selects the period. '/' lists tenants (protect or remove
in production - or put the whole app behind your RMM/VPN, or add auth).

Run:  python3 app.py --db nxreport.db --port 8080
"""
import argparse
from datetime import datetime, timedelta
from flask import Flask, abort, render_template, request
import db as dbmod

app = Flask(__name__)
DB_PATH = "nxreport.db"
BRAND = "Your IT Support Ltd"


def get_conn():
    return dbmod.connect(DB_PATH)


@app.route("/")
def index():
    conn = get_conn()
    rows = conn.execute("SELECT display_name, token FROM tenants ORDER BY display_name").fetchall()
    items = "".join(f'<li><a href="/t/{r["token"]}">{r["display_name"]}</a></li>' for r in rows)
    return f"<h1>NxReport tenants</h1><ul>{items}</ul>"


@app.route("/t/<token>")
def tenant_dash(token):
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE token=?", (token,)).fetchone()
    if not t:
        abort(404)
    days = min(int(request.args.get("days", 7)), 90)
    # default period: last N days ending at the most recent log we hold
    last = conn.execute("SELECT MAX(ts) m FROM dns_log WHERE tenant=?",
                        (t["name"],)).fetchone()["m"]
    end_dt = (datetime.strptime(last[:10], "%Y-%m-%d") + timedelta(days=1)
              if last else datetime.now())
    start = (end_dt - timedelta(days=days)).strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")
    data = dbmod.dashboard_data(conn, t["name"], start, end)
    return render_template("dashboard.html", data=data,
                           display_name=t["display_name"], brand=BRAND)


def main():
    global DB_PATH, BRAND
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="nxreport.db")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--brand", default=BRAND)
    args = ap.parse_args()
    DB_PATH, BRAND = args.db, args.brand
    app.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
