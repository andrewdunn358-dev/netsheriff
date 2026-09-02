"""Generate a realistic week of demo DNS logs for a fictional client tenant.

One user ("dave") hammers Facebook all day; everyone else has normal patterns
with a lunchtime social bump. Deterministic (seeded) so runs are reproducible.

Run: python3 sample_data.py --db demo.db
"""
import argparse, random
from datetime import datetime, timedelta
import db as dbmod

TENANT = "brightside-accounting"

USERS = {  # user: (client_ip, style)
    "dave":      ("192.168.10.34", "heavy_social"),
    "sarah":     ("192.168.10.21", "normal"),
    "mark":      ("192.168.10.22", "normal"),
    "lisa":      ("192.168.10.23", "light"),
    "priya":     ("192.168.10.24", "normal"),
    "tom":       ("192.168.10.25", "light"),
    "reception": ("192.168.10.30", "light"),
}

WORK = [("outlook.office365.com", "business"), ("teams.microsoft.com", "business"),
        ("sage.com", "business"), ("hmrc.gov.uk", "business"),
        ("xero.com", "business"), ("sharepoint.com", "business"),
        ("google.co.uk", "search"), ("bing.com", "search"),
        ("companieshouse.gov.uk", "business"), ("quickbooks.intuit.com", "business")]
SOCIAL = [("facebook.com", "sns"), ("www.facebook.com", "sns"),
          ("static.xx.fbcdn.net", "sns"), ("scontent.fbcdn.net", "sns"),
          ("instagram.com", "sns"), ("edge-chat.facebook.com", "sns")]
CASUAL = [("bbc.co.uk", "news"), ("dailymail.co.uk", "news"),
          ("amazon.co.uk", "shopping"), ("ebay.co.uk", "shopping"),
          ("youtube.com", "streaming"), ("netflix.com", "streaming"),
          ("skysports.com", "sports"), ("rightmove.co.uk", "shopping")]
ADS = [("doubleclick.net", "ads"), ("googlesyndication.com", "ads"),
       ("graph.facebook.com", "ads")]
BLOCKED = [("bet365.com", "gambling"), ("pornhub.com", "adult"),
           ("malware-test.example", "malware")]


def emit(rows, ts, user, ip, pool, blocked=0):
    d, c = random.choice(pool)
    rows.append((ts.strftime("%Y-%m-%d %H:%M:%S"), TENANT, user, d, c,
                 blocked, ip, "Default", "Blocked by category" if blocked else ""))


def gen_day(rows, day, user, ip, style):
    if day.weekday() >= 5:  # weekend: office closed
        return
    for hour in range(8, 18):
        t0 = day.replace(hour=hour)
        work_rate = 40 if 9 <= hour < 17 else 8
        lunch = hour in (12, 13)
        if style == "heavy_social":
            social_rate = 55 if 9 <= hour < 17 else 5   # all day, every day
            work_rate = 12
        elif style == "normal":
            social_rate = 14 if lunch else 2
        else:
            social_rate = 6 if lunch else 1
        casual_rate = 10 if lunch else 3
        for _ in range(random.randint(int(work_rate * .7), work_rate)):
            emit(rows, t0 + timedelta(seconds=random.randint(0, 3599)), user, ip, WORK)
        for _ in range(random.randint(int(social_rate * .7), social_rate)):
            emit(rows, t0 + timedelta(seconds=random.randint(0, 3599)), user, ip, SOCIAL)
        for _ in range(random.randint(0, casual_rate)):
            emit(rows, t0 + timedelta(seconds=random.randint(0, 3599)), user, ip, CASUAL)
        for _ in range(random.randint(2, 8)):
            emit(rows, t0 + timedelta(seconds=random.randint(0, 3599)), user, ip, ADS)
    if random.random() < 0.15:
        emit(rows, day.replace(hour=random.randint(9, 16),
             minute=random.randint(0, 59)), user, ip, BLOCKED, blocked=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="demo.db")
    ap.add_argument("--start", default="2026-07-20")  # a Monday
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)

    conn = dbmod.connect(args.db)
    conn.execute("DELETE FROM dns_log WHERE tenant=?", (TENANT,))
    conn.execute("INSERT OR REPLACE INTO tenants (name, display_name, token, email)"
                 " VALUES (?,?,?,?)",
                 (TENANT, "Brightside Accounting Ltd", "demo-brightside-7f3a", None))
    rows = []
    start = datetime.strptime(args.start, "%Y-%m-%d")
    for i in range(args.days):
        for user, (ip, style) in USERS.items():
            gen_day(rows, start + timedelta(days=i), user, ip, style)
    dbmod.insert_rows(conn, rows)
    print(f"inserted {len(rows)} rows for tenant '{TENANT}' into {args.db}")


if __name__ == "__main__":
    main()
