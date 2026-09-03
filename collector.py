"""Syslog collector for NxFilter / NxCloud DNS logs.

NxFilter (System > Setup > Syslog) exports each DNS request either as a
pipe-separated line:

    NXFILTER|2026-07-27 10:53:23|Y|www.bbc.co.uk|john|192.168.0.101|Default|news|Blocked by admin|33|operator[|localIp]

or as JSON with the same fields. Under NxCloud the group field carries the
OPERATOR (= tenant/client) name, which is what makes multi-client reporting work.

Run:  python3 collector.py --db nxreport.db --port 5140
Then point NxFilter/NxCloud syslog at this host:5140 (UDP).
"""
import argparse, json, re, socketserver, sys
import db as dbmod
import categorizer

PIPE_RE = re.compile(r"NXFILTER\|")
CATEGORY_LOOKUP = categorizer.load("categories.json")
if not CATEGORY_LOOKUP:
    print("WARNING: categories.json not found or empty — falling back entirely "
          "to NxFilter's own categorization (limited to Ads/Phishing/Porn on "
          "the free Globlist tier). Run build_categories.py to fix this.",
          file=sys.stderr)


def resolve_category(domain, nxfilter_category):
    """Our own lookup takes priority (it's what gives us Social Media,
    Shopping, Search, News, Business & Work — categories the free NxFilter
    tier doesn't classify at all). Falls back to whatever NxFilter itself
    provided (Ads/Phishing/Malware/Porn come from there), then 'unknown'."""
    return (categorizer.categorize(domain, CATEGORY_LOOKUP)
            or nxfilter_category or "unknown")


def parse_line(line):
    line = line.strip()
    # strip syslog priority/header if present, keep from NXFILTER onward
    m = PIPE_RE.search(line)
    if m:
        parts = line[m.start():].split("|")
        if len(parts) >= 9:
            _, ts, blocked, domain, user, cip, policy, cat, reason = parts[:9]
            tenant = parts[10] if len(parts) > 10 else "default"
            return (ts, tenant.strip() or "default", user or "unknown", domain,
                    resolve_category(domain, cat), 1 if blocked.strip().upper() == "Y" else 0,
                    cip, policy, reason)
    # JSON format
    try:
        j = json.loads(line[line.index("{"):]) if "{" in line else None
    except (ValueError, json.JSONDecodeError):
        j = None
    if j and "Domain" in j:
        domain = j.get("Domain", "")
        return (j.get("Time", ""), j.get("Group") or j.get("Operator") or "default",
                j.get("User", "unknown"), domain,
                resolve_category(domain, j.get("Category")),
                1 if str(j.get("Blocked", "N")).upper() in ("Y", "TRUE", "1") else 0,
                j.get("ClientIp", ""), j.get("Policy", ""), j.get("Reason", ""))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="nxreport.db")
    ap.add_argument("--port", type=int, default=5140)
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()
    conn = dbmod.connect(args.db)
    buf = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            data = self.request[0].decode("utf-8", "replace")
            for line in data.splitlines():
                row = parse_line(line)
                if row:
                    buf.append(row)
            if len(buf) >= args.batch:
                dbmod.insert_rows(conn, buf)
                buf.clear()

    print(f"nxreport collector listening on UDP :{args.port} -> {args.db}", file=sys.stderr)
    with socketserver.UDPServer(("0.0.0.0", args.port), Handler) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            if buf:
                dbmod.insert_rows(conn, buf)


if __name__ == "__main__":
    main()
