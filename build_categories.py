"""Build categories.json — our own domain-category lookup, independent of
NxFilter's paid categorization services (Jahaslist/Cloudlist).

Why this exists: NxFilter's free 'Globlist' categorization only classifies
3 categories (Ads, Phishing/Malware, Porn) — everything else (social media,
shopping, streaming, etc.) comes through uncategorized unless you pay for
Jahaslist or Cloudlist. This script builds our own lookup for exactly the
categories NxReport's dashboard needs, at zero ongoing cost.

Sources:
  - The Block List Project (github.com/blocklistproject/Lists, Unlicense/MIT)
    — actively maintained, weekly updates. Used only for what NxFilter's
    free tier DOESN'T already cover well: social/streaming platforms and
    gambling. Deliberately NOT using their malware/phishing/porn lists —
    those are 100MB+ of redundant data, since free Globlist already covers
    exactly those three categories at no cost.
  - Hand-curated lists for shopping/search/news/business — no good
    actively-maintained open-source source for general commercial-site
    categorization exists, so these are maintained directly here.

Run this periodically (e.g. weekly via cron) to pick up upstream updates:
    python3 build_categories.py --out categories.json

Categories map to the exact lowercase codes db.py's FRIENDLY dict already
understands (sns, streaming, gambling, shopping, search, business, news) —
no changes needed to the dashboard/report display layer.
"""
import argparse, json, re, urllib.request

BLP_BASE = "https://raw.githubusercontent.com/blocklistproject/Lists/master"
VALID_DOMAIN_RE = re.compile(r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")


def is_valid_domain(d):
    """NxFilter (and DNS generally) rejects hostnames with underscores or
    other non-standard characters — a handful of these show up in upstream
    lists (e.g. some TikTok SDK domains use 'client_monitor.isnssdk.com').
    Filter them out here so a bad entry never breaks a bulk import."""
    return bool(VALID_DOMAIN_RE.match(d))
BLP_LISTS = {
    "facebook.txt": "sns",
    "twitter.txt": "sns",
    "tiktok.txt": "sns",
    "youtube.txt": "streaming",
    "gambling.txt": "gambling",
}

# The Block List Project's lists are designed for AD-BLOCKING within these
# sites (specific ad/tracking subdomains), not for identifying "this is
# Facebook/YouTube traffic" — the actual apex domains a user visits often
# aren't in them at all (confirmed: 'youtube.com' itself isn't in
# youtube.txt, only ~6000 ad/analytics subdomains under it). Add the real
# apex domains explicitly so actual user traffic gets categorized.
PLATFORM_APEXES = {
    "sns": ["facebook.com", "fb.com", "fbcdn.net", "messenger.com",
            "instagram.com", "threads.net", "twitter.com", "x.com", "t.co",
            "tiktok.com", "tiktokcdn.com", "reddit.com", "redd.it",
            "pinterest.com", "snapchat.com", "discord.com", "discordapp.com",
            "linkedin.com"],
    "streaming": ["youtube.com", "youtu.be", "ytimg.com", "googlevideo.com",
                  "netflix.com", "nflxvideo.net", "primevideo.com",
                  "disneyplus.com", "twitch.tv", "ttvnw.net"],
}

# Extra social platforms not covered by Block List Project's own list set.
EXTRA_SNS = ["instagram.com", "threads.net", "reddit.com", "pinterest.com",
             "snapchat.com", "discord.com", "x.com", "linkedin.com"]

SEARCH = ["google.com", "bing.com", "duckduckgo.com", "yahoo.com", "search.brave.com"]

SHOPPING = ["amazon.co.uk", "amazon.com", "ebay.co.uk", "ebay.com", "argos.co.uk",
            "next.co.uk", "asos.com", "johnlewis.com", "currys.co.uk", "very.co.uk",
            "marksandspencer.com", "tesco.com", "sainsburys.co.uk", "etsy.com",
            "wish.com", "boohoo.com", "shein.com", "wayfair.co.uk", "screwfix.com"]

NEWS = ["bbc.co.uk", "bbc.com", "skynews.com", "theguardian.com", "dailymail.co.uk",
        "thesun.co.uk", "mirror.co.uk", "telegraph.co.uk", "independent.co.uk",
        "itv.com", "reuters.com", "ft.com", "standard.co.uk"]

# More specific than SEARCH's bare 'google.com' — categorize() checks the
# most specific hostname first, so 'mail.google.com' matches here before
# ever falling back to the apex 'google.com' -> search match.
BUSINESS = ["mail.google.com", "docs.google.com", "drive.google.com",
            "calendar.google.com", "meet.google.com", "workspace.google.com",
            "office.com", "office365.com", "outlook.office365.com", "outlook.com",
            "live.com", "sharepoint.com", "onedrive.live.com", "teams.microsoft.com",
            "microsoft.com", "azure.com", "zoom.us", "slack.com", "salesforce.com",
            "xero.com", "sage.com", "quickbooks.intuit.com", "hmrc.gov.uk", "gov.uk",
            "companieshouse.gov.uk", "dropbox.com", "atlassian.com", "asana.com",
            "trello.com", "zendesk.com", "adobe.com", "docusign.com", "monday.com",
            "notion.so", "canva.com"]


def fetch_blp_list(name):
    url = f"{BLP_BASE}/{name}"
    with urllib.request.urlopen(url, timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
    domains = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("0.0.0.0 "):
            d = line.split(None, 1)[1].strip()
            if is_valid_domain(d):
                domains.append(d)
    return domains


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="categories.json")
    ap.add_argument("--offline-dir", help="use local .txt files instead of downloading (for testing)")
    args = ap.parse_args()

    lookup = {}
    counts = {}

    for fname, code in BLP_LISTS.items():
        if args.offline_dir:
            import os
            with open(os.path.join(args.offline_dir, fname)) as f:
                domains = [l.split(None, 1)[1].strip() for l in f
                          if l.strip().startswith("0.0.0.0 ")]
            domains = [d for d in domains if is_valid_domain(d)]
        else:
            domains = fetch_blp_list(fname)
        for d in domains:
            lookup[d] = code
        counts[fname] = len(domains)
        print(f"  {fname}: {len(domains)} domains -> {code}")

    for code, domains in PLATFORM_APEXES.items():
        for d in domains:
            lookup[d] = code
    for d in EXTRA_SNS:
        lookup[d] = "sns"
    for d in SEARCH:
        lookup[d] = "search"
    for d in SHOPPING:
        lookup[d] = "shopping"
    for d in NEWS:
        lookup[d] = "news"
    for d in BUSINESS:
        lookup[d] = "business"

    # Applied last so it wins over everything above, including upstream
    # blocklist entries. This is where we correct domains that get
    # miscategorised - by a blocklist we import, or (once this file is the
    # authoritative source) by NxFilter's own fallback. E.g. lenovo.com was
    # showing as social media because NxFilter labelled it that way and we
    # had no entry of our own; pinning it here fixes it at source.
    OVERRIDES = {
        # NxFilter tags anything 'lenovo' as Shopping (they have a store), but
        # these subdomains are software-update and support infrastructure, not
        # shopping, and were inflating people's 'leisure' counts. Pinned to
        # business. Deliberately specific - overriding whole vendor apexes
        # (apple.com, microsoft.com) wrongly re-tags legitimately-categorised
        # domains (Apple Music as Entertainment, etc), so only the actual
        # offenders are listed.
        "lenovo.com": "business", "lenovomm.com": "business",
        "csw.lenovo.com": "business", "pcsupport.lenovo.com": "business",
        "vantage.csw.lenovo.com": "business", "uds.lenovo.com": "business",
        "webrootcloudav.com": "business",
    }
    for d, code in OVERRIDES.items():
        lookup[d] = code

    with open(args.out, "w") as f:
        json.dump(lookup, f)

    print(f"\nWrote {len(lookup)} total domain entries to {args.out}")


if __name__ == "__main__":
    main()
