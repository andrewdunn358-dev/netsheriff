"""Generate a static HTML report (self-contained) and optionally a PDF + email.

Examples:
  python3 report.py --db demo.db --tenant brightside-accounting --days 7 \
      --html out.html --pdf out.pdf
  python3 report.py --db nxreport.db --tenant acme --days 7 --pdf report.pdf \
      --email boss@client.co.uk --smtp smtp.office365.com:587 \
      --smtp-user reports@yourmsp.co.uk --smtp-pass '...'

Schedule weekly via cron, one line per client:
  5 7 * * MON cd /opt/nxreport && python3 report.py --db nxreport.db \
      --tenant brightside-accounting --days 7 --pdf /tmp/r.pdf --email ...
"""
import argparse, os, shutil, smtplib, subprocess, tempfile
from datetime import datetime, timedelta
from email.message import EmailMessage
import jinja2
import db as dbmod

HERE = os.path.dirname(os.path.abspath(__file__))


def _static_url_stub(endpoint, **kwargs):
    """report.py renders dashboard.html outside Flask's request context (for
    PDF export / emailed reports), so the real url_for() isn't available.
    The only url_for calls in dashboard.html are for static assets (favicon,
    logo) which are either hidden in print output or non-critical if they
    don't resolve — this just needs to not crash the render."""
    if endpoint == "static":
        return "/static/" + kwargs.get("filename", "")
    return "#"


def render_html(conn, tenant_row, start, end, brand, hide_brand=False):
    """start/end are SQL-ready: start inclusive, end EXCLUSIVE (i.e. end
    should already be one day past the last day you want included)."""
    data = dbmod.dashboard_data(conn, tenant_row["name"], start, end)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(HERE, "templates")))
    return env.get_template("dashboard.html").render(
        data=data, display_name=tenant_row["display_name"], brand=brand,
        show_logout=False, export_url="", url_for=_static_url_stub,
        hide_brand=hide_brand)


def find_chromium():
    for c in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        p = shutil.which(c)
        if p:
            return p
    for root in ("/opt/pw-browsers",):
        if os.path.isdir(root):
            for dirpath, _, files in os.walk(root):
                for f in files:
                    if f in ("chrome", "headless_shell", "chromium"):
                        return os.path.join(dirpath, f)
    raise SystemExit("No Chromium found for PDF rendering (install chromium).")


def html_to_pdf(html_path, pdf_path):
    subprocess.run([find_chromium(), "--headless", "--no-sandbox",
                    "--disable-gpu", f"--print-to-pdf={pdf_path}",
                    "--no-pdf-header-footer", "--virtual-time-budget=4000",
                    f"file://{os.path.abspath(html_path)}"],
                   check=True, capture_output=True)


def send_email(pdf_path, to_addr, tenant_row, smtp, user, pw, brand):
    host, _, port = smtp.partition(":")
    msg = EmailMessage()
    msg["Subject"] = f"Weekly internet usage report — {tenant_row['display_name']}"
    msg["From"], msg["To"] = user, to_addr
    msg.set_content(
        f"Hi,\n\nPlease find attached this week's internet usage report for "
        f"{tenant_row['display_name']}.\n\nRegards,\n{brand}")
    with open(pdf_path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf",
                           filename="internet-usage-report.pdf")
    with smtplib.SMTP(host, int(port or 587)) as s:
        s.starttls()
        s.login(user, pw)
        s.send_message(msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="nxreport.db")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--brand", default="Your IT Support Ltd")
    ap.add_argument("--html")
    ap.add_argument("--pdf")
    ap.add_argument("--email")
    ap.add_argument("--smtp"); ap.add_argument("--smtp-user"); ap.add_argument("--smtp-pass")
    args = ap.parse_args()

    conn = dbmod.connect(args.db)
    t = conn.execute("SELECT * FROM tenants WHERE name=?", (args.tenant,)).fetchone()
    if not t:
        raise SystemExit(f"Unknown tenant '{args.tenant}'")

    last = conn.execute("SELECT MAX(ts) m FROM dns_log WHERE tenant=?",
                        (args.tenant,)).fetchone()["m"]
    end_dt = (datetime.strptime(last[:10], "%Y-%m-%d") + timedelta(days=1)
              if last else datetime.now())
    start = (end_dt - timedelta(days=args.days)).strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")
    html = render_html(conn, t, start, end, args.brand)

    html_path = args.html or os.path.join(tempfile.gettempdir(), f"{args.tenant}-report.html")
    with open(html_path, "w") as f:
        f.write(html)
    print("wrote", html_path)
    if args.pdf:
        html_to_pdf(html_path, args.pdf)
        print("wrote", args.pdf)
    if args.email:
        if not (args.smtp and args.smtp_user and args.smtp_pass):
            raise SystemExit("--email needs --smtp, --smtp-user, --smtp-pass")
        send_email(args.pdf, args.email, t, args.smtp, args.smtp_user,
                   args.smtp_pass, args.brand)
        print("emailed", args.email)


if __name__ == "__main__":
    main()
