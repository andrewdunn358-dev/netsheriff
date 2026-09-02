"""Multi-tenant reporting dashboard for NxFilter/NxCloud logs.

Clients log in at /login (username + password, set via db.create_tenant)
and land on their own /dashboard — they never see NxCloud or other tenants.
The old unguessable /t/<token> URL still works as a no-password fallback
link (e.g. for a first demo before credentials are issued) but the intended
path for a real client is the login form.

Set NXREPORT_SECRET_KEY in the environment in production — the random
fallback below means sessions won't survive an app restart otherwise.

Run:  python3 app.py --db nxreport.db --port 8080
"""
import argparse, os, secrets
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, abort, redirect, render_template, request, session, url_for
import db as dbmod

app = Flask(__name__)
app.secret_key = os.environ.get("NXREPORT_SECRET_KEY", secrets.token_hex(32))
# Env vars, not just argparse defaults: gunicorn imports this module and
# runs the `app` object directly, so main()'s argparse never executes.
DB_PATH = os.environ.get("NXREPORT_DB", "nxreport.db")
BRAND = os.environ.get("NXREPORT_BRAND", "Your IT Support Ltd")


def get_conn():
    return dbmod.connect(DB_PATH)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("tenant"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def render_dashboard(conn, t, days):
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


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        conn = get_conn()
        t = dbmod.verify_login(conn, request.form.get("username", ""),
                                request.form.get("password", ""))
        if t:
            session.clear()
            session["tenant"] = t["name"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        error = "Incorrect username or password."
    return render_template("login.html", brand=BRAND, error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE name=?", (session["tenant"],)).fetchone()
    if not t:
        session.clear()
        return redirect(url_for("login"))
    days = min(int(request.args.get("days", 7)), 90)
    return render_dashboard(conn, t, days)


@app.route("/t/<token>")
def tenant_dash(token):
    # No-password fallback link — useful for a first demo before you've
    # issued the client a username/password. Keep this out of anything you
    # hand the client long-term; the login form is the real front door.
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE token=?", (token,)).fetchone()
    if not t:
        abort(404)
    days = min(int(request.args.get("days", 7)), 90)
    return render_dashboard(conn, t, days)


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
