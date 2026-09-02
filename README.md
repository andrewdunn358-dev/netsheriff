# NxReport — client-friendly reporting on top of NxFilter / NxCloud

Turns raw NxFilter DNS logs into reports a business owner can actually read:
a per-client web dashboard plus a scheduled, emailed PDF. Multi-tenant from
day one — each NxCloud **operator** (= client) becomes a tenant with its own
private dashboard URL and its own reports.

```
client sites ──DNS──> NxCloud (your hosted VPS)
                          │ syslog (UDP, pipe format)
                          ▼
                   collector.py ──> SQLite (nxreport.db)
                          │                │
                    app.py (Flask     report.py (weekly
                    web dashboard)    PDF via cron + email)
```

## Components

| File | Purpose |
|---|---|
| `collector.py` | UDP syslog listener; parses NxFilter pipe/JSON log lines into SQLite |
| `db.py` | Schema + all aggregation queries (category share, per-user, heatmap, trend) |
| `app.py` | Flask dashboard, one unguessable URL per tenant: `/t/<token>` |
| `report.py` | Renders the same report to static HTML / PDF (headless Chromium), optionally emails it |
| `sample_data.py` | Generates a demo week of logs (one heavy Facebook user) for testing/sales demos |
| `templates/dashboard.html` | The report itself — self-contained, light/dark, print-friendly |

## Setup

1. **Host NxCloud** (free) on a VPS — Ubuntu/Debian, install per the
   [NxCloud docs](https://tutorial.nxfilter.org/doc/en/f-install-nxcloud.php).
   Create one **operator per client**; point each client's DrayTek/DHCP DNS at
   it (or deploy NxRelay/agents per site). Install the AD login agent on each
   client's DC so logs carry usernames, not IPs.
2. **Enable syslog export** in NxFilter/NxCloud: *System > Setup > Syslog* →
   this box, port 5140, pipe format.
3. **Run the pieces** (same box is fine):
   ```bash
   pip install flask jinja2
   python3 collector.py --db nxreport.db --port 5140   # as a systemd service
   python3 app.py --db nxreport.db --port 8080 --brand "Your IT Support Ltd"
   ```
4. **Register a tenant** (name must match the NxCloud operator name):
   ```sql
   INSERT INTO tenants (name, display_name, token, email)
   VALUES ('brightside', 'Brightside Accounting Ltd', '<random-token>', 'owner@client.co.uk');
   ```
5. **Schedule the weekly PDF** — cron, Monday 7am, one line per client:
   ```
   5 7 * * MON cd /opt/nxreport && python3 report.py --db nxreport.db \
     --tenant brightside --days 7 --pdf /tmp/brightside.pdf \
     --email owner@client.co.uk --smtp smtp.office365.com:587 \
     --smtp-user reports@yourmsp.co.uk --smtp-pass '...'
   ```

## Demo

```bash
python3 sample_data.py --db demo.db
python3 report.py --db demo.db --tenant brightside-accounting --days 7 \
  --html demo_dashboard.html --pdf sample_report.pdf
python3 app.py --db demo.db          # then open /t/demo-brightside-7f3a
```

## Production notes

- Put the Flask app behind nginx + TLS (or your VPN); the tenant token is the
  only access control out of the box — add proper auth before wide rollout.
- SQLite is fine to ~10M rows; past that swap `db.connect` for Postgres.
- Add a nightly `DELETE FROM dns_log WHERE ts < date('now','-90 days')` to cap growth.
- UK clients: make sure each client's staff AUP/handbook mentions that web
  activity is monitored — ICO guidance expects transparency before reports are
  used in any HR conversation.
- "Requests" measure browsing *pattern*, not minutes on screen — the report
  footer says so, keep it there.
