#!/usr/bin/env python3
"""Re-categorise existing dns_log rows using the current categories.json.

Categorisation happens at collection time, so when the lookup or overrides
change, historic rows keep their old (possibly wrong) category. This re-runs
the same resolve_category logic over existing rows so the fix applies to data
already collected - important when a client is looking at a report built from
weeks of history.

Only touches rows whose category would change, and prints a summary. Run after
build_categories.py. Safe to run repeatedly.

  python3 recategorize.py --db /opt/netsheriff/nxreport.db
"""
import argparse
import sqlite3
import categorizer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="nxreport.db")
    ap.add_argument("--categories", default="categories.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change without writing")
    args = ap.parse_args()

    lookup = categorizer.load(args.categories)
    if not lookup:
        print("No categories.json found - run build_categories.py first.")
        return

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT DISTINCT domain, category FROM dns_log").fetchall()

    changes = {}   # (old, new) -> count of distinct domains
    updates = []   # (new_category, domain)
    for r in rows:
        domain, old = r["domain"], r["category"]
        new = categorizer.categorize(domain, lookup)
        # Only override with our own lookup; if we don't categorise it, leave
        # NxFilter's category as-is (same precedence as collection time).
        if new and new != old:
            changes[(old, new)] = changes.get((old, new), 0) + 1
            updates.append((new, domain))

    if not updates:
        print("Nothing to re-categorise - all rows already match the lookup.")
        return

    print(f"{len(updates)} domains would change category:")
    for (old, new), n in sorted(changes.items(), key=lambda kv: -kv[1]):
        print(f"  {old or '(none)':20} -> {new:12} {n} domains")

    if args.dry_run:
        print("\nDry run - no changes written.")
        return

    affected = 0
    for new, domain in updates:
        cur = conn.execute(
            "UPDATE dns_log SET category=? WHERE domain=? AND category IS NOT ?",
            (new, domain, new))
        affected += cur.rowcount
    conn.commit()
    print(f"\nUpdated {affected} rows across {len(updates)} domains.")


if __name__ == "__main__":
    main()
