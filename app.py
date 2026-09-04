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
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
import db as dbmod
import mailer
import report as reportmod

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


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def resolve_range(conn, tenant_name, args):
    """Figure out the (start, end) inclusive date strings to show, from
    query args. Both are plain YYYY-MM-DD dates a human would recognise —
    callers needing a SQL-ready exclusive end should use sql_end_exclusive().

    Explicit ?start=YYYY-MM-DD&end=YYYY-MM-DD wins if both are valid dates
    and start <= end; otherwise falls back to ?days=N (capped 1-90) ending
    at the most recent log we hold for this tenant."""
    start, end = args.get("start"), args.get("end")
    if start and end:
        try:
            d0 = datetime.strptime(start, "%Y-%m-%d")
            d1 = datetime.strptime(end, "%Y-%m-%d")
            if d0 <= d1:
                return start, end
        except ValueError:
            pass
    days = max(1, min(int(args.get("days", 7) or 7), 90))
    last = conn.execute("SELECT MAX(ts) m FROM dns_log WHERE tenant=?",
                        (tenant_name,)).fetchone()["m"]
    end_dt = (datetime.strptime(last[:10], "%Y-%m-%d")
              if last else (datetime.now() - timedelta(days=1)))
    start_dt = end_dt - timedelta(days=days - 1)
    return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")


def sql_end_exclusive(end_inclusive):
    """dashboard_data's query is ts < end, so the inclusive end date a human
    picked needs pushing one day later to actually include that whole day."""
    return (datetime.strptime(end_inclusive, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def render_dashboard(conn, t, start, end, show_logout, export_url, hide_brand=False):
    data = dbmod.dashboard_data(conn, t["name"], start, sql_end_exclusive(end))
    return render_template("dashboard.html", data=data,
                           display_name=t["display_name"], brand=BRAND,
                           show_logout=show_logout, export_url=export_url,
                           hide_brand=hide_brand, range_start=start, range_end=end)


def build_pdf(conn, t, start, end, hide_brand=False):
    """Render the dashboard HTML and convert to PDF via report.py's existing
    Chromium logic. Returns (pdf_path, error) — error is a string if
    Chromium isn't available or conversion fails, in which case pdf_path
    is None."""
    import tempfile
    try:
        html = reportmod.render_html(conn, t, start, sql_end_exclusive(end), BRAND, hide_brand=hide_brand)
        tmpdir = tempfile.mkdtemp()
        html_path = os.path.join(tmpdir, "report.html")
        pdf_path = os.path.join(tmpdir, "report.pdf")
        with open(html_path, "w") as f:
            f.write(html)
        reportmod.html_to_pdf(html_path, pdf_path)
    except Exception as e:
        return None, str(e)
    return pdf_path, None


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


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    message = None
    if request.method == "POST":
        conn = get_conn()
        identifier = request.form.get("identifier", "").strip()
        t = dbmod.create_reset_token(conn, identifier)
        # Always show the same message whether or not a match was found —
        # confirming/denying a match here would let someone probe for which
        # usernames/emails exist in the system.
        if t and t.get("email"):
            link = url_for("reset_password", token=t["reset_token"], _external=True)
            try:
                mailer.send_reset_email(t["email"], link, BRAND)
            except Exception:
                pass  # still show the generic message below either way
        message = ("If that username or email matches an account, we've sent "
                   "a password reset link to the email on file.")
    return render_template("forgot_password.html", brand=BRAND, message=message)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_conn()
    t = dbmod.get_tenant_by_reset_token(conn, token)
    if not t:
        return render_template("reset_password.html", brand=BRAND, invalid=True)
    error = None
    if request.method == "POST":
        pw1, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw1) < 8:
            error = "Password must be at least 8 characters."
        elif pw1 != pw2:
            error = "Passwords don't match."
        else:
            dbmod.set_tenant_password(conn, t["name"], pw1)
            return redirect(url_for("login"))
    return render_template("reset_password.html", brand=BRAND, error=error, token=token)


@app.route("/admin/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():
    message = None
    if request.method == "POST":
        conn = get_conn()
        identifier = request.form.get("identifier", "").strip()
        a = dbmod.create_admin_reset_token(conn, identifier)
        # Same generic message either way — don't reveal whether a match
        # was found, to avoid letting someone probe for valid usernames.
        if a and a.get("email"):
            link = url_for("admin_reset_password", token=a["reset_token"], _external=True)
            try:
                mailer.send_reset_email(a["email"], link, f"{BRAND} Admin")
            except Exception:
                pass
        message = ("If that username or email matches an admin account, we've "
                   "sent a password reset link to the email on file.")
    return render_template("admin_forgot_password.html", brand=BRAND, message=message)


@app.route("/admin/reset-password/<token>", methods=["GET", "POST"])
def admin_reset_password(token):
    conn = get_conn()
    a = dbmod.get_admin_by_reset_token(conn, token)
    if not a:
        return render_template("admin_reset_password.html", brand=BRAND, invalid=True)
    error = None
    if request.method == "POST":
        pw1, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if len(pw1) < 8:
            error = "Password must be at least 8 characters."
        elif pw1 != pw2:
            error = "Passwords don't match."
        else:
            dbmod.set_admin_password(conn, a["username"], pw1)
            return redirect(url_for("admin_login"))
    return render_template("admin_reset_password.html", brand=BRAND, error=error, token=token)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        conn = get_conn()
        a = dbmod.verify_admin(conn, request.form.get("username", ""),
                                request.form.get("password", ""))
        if a:
            session.clear()
            session["admin"] = a["username"]
            return redirect(request.args.get("next") or url_for("admin_dashboard"))
        error = "Incorrect username or password."
    return render_template("admin_login.html", brand=BRAND, error=error)


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    conn = get_conn()
    tenants = dbmod.list_tenants(conn)
    return render_template("admin_dashboard.html", brand=BRAND, tenants=tenants)


@app.route("/admin/tenants/new", methods=["GET", "POST"])
@admin_required
def admin_new_tenant():
    error = None
    if request.method == "POST":
        conn = get_conn()
        name = request.form.get("name", "").strip()
        display_name = request.form.get("display_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip() or None
        if not (name and display_name and username and len(password) >= 8):
            error = "All fields are required and password must be at least 8 characters."
        elif not email:
            error = "Email is required — it's needed for password-reset links and the weekly report."
        else:
            try:
                dbmod.create_tenant(conn, name, display_name, username, password, email)
                return redirect(url_for("admin_dashboard"))
            except Exception as e:
                error = f"Couldn't create tenant — {e}"
    return render_template("admin_new_tenant.html", brand=BRAND, error=error)


@app.route("/admin/tenants/<name>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_tenant(name):
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE name=?", (name,)).fetchone()
    if not t:
        abort(404)
    error = None
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        email = request.form.get("email", "").strip() or None
        if not display_name:
            error = "Display name is required."
        elif not email:
            error = "Email is required — it's needed for password-reset links and the weekly report."
        else:
            dbmod.update_tenant(conn, name, display_name, email)
            return redirect(url_for("admin_dashboard"))
    return render_template("admin_edit_tenant.html", brand=BRAND, error=error, t=t)


@app.route("/admin/tenants/<name>/reset", methods=["POST"])
@admin_required
def admin_reset_tenant(name):
    import secrets
    conn = get_conn()
    new_password = secrets.token_urlsafe(9)
    dbmod.set_tenant_password(conn, name, new_password)
    tenants = dbmod.list_tenants(conn)
    return render_template("admin_dashboard.html", brand=BRAND, tenants=tenants,
                           reset_name=name, reset_password=new_password)


@app.route("/admin/admins")
@admin_required
def admin_admins():
    conn = get_conn()
    admins = dbmod.list_admins(conn)
    return render_template("admin_admins.html", brand=BRAND, admins=admins)


@app.route("/admin/admins/new", methods=["GET", "POST"])
@admin_required
def admin_new_admin():
    error = None
    if request.method == "POST":
        conn = get_conn()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        email = request.form.get("email", "").strip() or None
        if not (username and len(password) >= 8):
            error = "Username is required and password must be at least 8 characters."
        elif not email:
            error = "Email is required — it's needed so this admin can reset their own password if forgotten."
        else:
            dbmod.create_admin(conn, username, password, email)
            return redirect(url_for("admin_admins"))
    return render_template("admin_new_admin.html", brand=BRAND, error=error)


@app.route("/admin/admins/<username>/reset", methods=["POST"])
@admin_required
def admin_reset_admin(username):
    import secrets
    conn = get_conn()
    new_password = secrets.token_urlsafe(9)
    dbmod.set_admin_password(conn, username, new_password)
    admins = dbmod.list_admins(conn)
    return render_template("admin_admins.html", brand=BRAND, admins=admins,
                           reset_username=username, reset_password=new_password)


@app.route("/admin/change-password", methods=["GET", "POST"])
@admin_required
def admin_change_password():
    error = success = None
    if request.method == "POST":
        conn = get_conn()
        current = request.form.get("current_password", "")
        new1 = request.form.get("new_password", "")
        new2 = request.form.get("new_password2", "")
        if not dbmod.verify_admin(conn, session["admin"], current):
            error = "Current password is incorrect."
        elif len(new1) < 8:
            error = "New password must be at least 8 characters."
        elif new1 != new2:
            error = "New passwords don't match."
        else:
            dbmod.set_admin_password(conn, session["admin"], new1)
            success = "Password updated."
    return render_template("admin_change_password.html", brand=BRAND, error=error, success=success)


def _auth_agent(conn, tenant):
    """Authenticate an agent posting for a tenant against that tenant's own
    token. Falls back to the legacy shared env token so existing deployments
    keep working until they're reissued a per-tenant one. Returns the tenant
    row on success, or a (json, status) error tuple.
    """
    supplied = request.headers.get("X-Agent-Token", "")
    row = conn.execute("SELECT * FROM tenants WHERE name=?", (tenant,)).fetchone()
    if not row:
        return jsonify(error="unknown tenant"), 404
    tok = row["agent_token"] if "agent_token" in row.keys() else None
    shared = os.environ.get("NXREPORT_AGENT_TOKEN", "")
    ok = ((tok and secrets.compare_digest(supplied, tok))
          or (shared and secrets.compare_digest(supplied, shared)))
    if not ok:
        return jsonify(error="unauthorised"), 401
    return row


@app.route("/api/ip-users", methods=["POST"])
def api_ip_users():
    """Ingest IP-to-username observations from a site agent.

    Authenticated by the tenant's own token in the X-Agent-Token header (see
    _auth_agent). Machine-to-machine, so a token rather than a session.

    Expected body:
        {"tenant": "NCS", "sessions": [{"ip": "192.168.0.34",
                                        "username": "ThomasLeonard"}, ...]}
    """
    payload = request.get_json(silent=True) or {}
    tenant = (payload.get("tenant") or "").strip()
    sessions = payload.get("sessions")
    if not tenant or not isinstance(sessions, list):
        return jsonify(error="tenant and sessions[] required"), 400

    conn = get_conn()
    try:
        auth = _auth_agent(conn, tenant)
        if isinstance(auth, tuple):
            return auth
        pairs = [(s.get("ip"), s.get("username")) for s in sessions
                 if isinstance(s, dict)]
        extended, created = dbmod.record_ip_users(conn, tenant, pairs)
    finally:
        conn.close()
    return jsonify(ok=True, received=len(sessions), extended=extended, created=created)


@app.route("/api/activity", methods=["POST"])
def api_activity():
    """Foreground samples from a domain-joined workstation.

    Two gates: the tenant's agent token, and the tenant's screen_monitoring
    flag. If monitoring is off for the client, samples are refused even from
    a valid agent — so the feature is controlled entirely from the server and
    agents can sit installed-but-dormant. Only an application name and a
    matched leisure site are accepted; never a raw window title.
    """
    payload = request.get_json(silent=True) or {}
    tenant = (payload.get("tenant") or "").strip()
    samples = payload.get("samples")
    if not tenant or not isinstance(samples, list):
        return jsonify(error="tenant and samples[] required"), 400

    conn = get_conn()
    try:
        auth = _auth_agent(conn, tenant)
        if isinstance(auth, tuple):
            return auth
        enabled = ("screen_monitoring" in auth.keys()
                   and auth["screen_monitoring"])
        if not enabled:
            # Not an error the agent should retry on — it's a deliberate off.
            return jsonify(ok=True, stored=0, monitoring="off"), 200
        stored = dbmod.record_app_usage(conn, tenant, samples)
    finally:
        conn.close()
    return jsonify(ok=True, stored=stored)


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE name=?", (session["tenant"],)).fetchone()
    if not t:
        session.clear()
        return redirect(url_for("login"))
    start, end = resolve_range(conn, t["name"], request.args)
    export_url = url_for("dashboard_export", start=start, end=end)
    return render_dashboard(conn, t, start, end, show_logout=True, export_url=export_url)


@app.route("/dashboard/data.json")
@login_required
def dashboard_data_json():
    """Same data the main /dashboard route renders, as plain JSON - lets the
    page auto-refresh in place (re-fetch + redraw) rather than needing a full
    page reload every few minutes."""
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE name=?", (session["tenant"],)).fetchone()
    if not t:
        return jsonify({"error": "not logged in"}), 401
    start, end = resolve_range(conn, t["name"], request.args)
    data = dbmod.dashboard_data(conn, t["name"], start, sql_end_exclusive(end))
    return jsonify(data)


@app.route("/dashboard/export.pdf")
@login_required
def dashboard_export():
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE name=?", (session["tenant"],)).fetchone()
    if not t:
        session.clear()
        return redirect(url_for("login"))
    start, end = resolve_range(conn, t["name"], request.args)
    hide_brand = request.args.get("brand") == "0"
    pdf_path, error = build_pdf(conn, t, start, end, hide_brand=hide_brand)
    if error:
        abort(503, description=f"PDF export unavailable on this server: {error}")
    return send_file(pdf_path, as_attachment=True, mimetype="application/pdf",
                     download_name=f"{t['name']}-internet-usage-report.pdf")


@app.route("/t/<token>")
def tenant_dash(token):
    # No-password fallback link — useful for a first demo before you've
    # issued the client a username/password. Keep this out of anything you
    # hand the client long-term; the login form is the real front door.
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE token=?", (token,)).fetchone()
    if not t:
        abort(404)
    start, end = resolve_range(conn, t["name"], request.args)
    export_url = url_for("tenant_dash_export", token=token, start=start, end=end)
    return render_dashboard(conn, t, start, end, show_logout=False, export_url=export_url)


@app.route("/t/<token>/export.pdf")
def tenant_dash_export(token):
    conn = get_conn()
    t = conn.execute("SELECT * FROM tenants WHERE token=?", (token,)).fetchone()
    if not t:
        abort(404)
    start, end = resolve_range(conn, t["name"], request.args)
    hide_brand = request.args.get("brand") == "0"
    pdf_path, error = build_pdf(conn, t, start, end, hide_brand=hide_brand)
    if error:
        abort(503, description=f"PDF export unavailable on this server: {error}")
    return send_file(pdf_path, as_attachment=True, mimetype="application/pdf",
                     download_name=f"{t['name']}-internet-usage-report.pdf")


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
