# Net Sheriff — NCS deployment handover (full current state)

Read this first, in full, before doing anything. This supersedes the
earlier addendum files (#1-4) — those are still in the repo for history,
but everything current is consolidated here.

## Where things actually stand

**NCS is live.** Real DNS filtering and monitoring is working for the
whole office, right now. This is proven, not assumed — confirmed via
climbing real query counts in NxRelay's own log, correct category
attribution across every domain tested, and our own dashboard correctly
displaying it.

**Client's explicit instruction: monitor only, nothing blocked.** Do not
enable blocking on any category for NCS without the client asking for it
first. Confirmed directly with them.

**The one unresolved piece: real staff names aren't showing up yet** —
browsing currently attributes to the generic "NCS" identity rather than
individual people. Three different approaches tried, all still open (see
"Outstanding: username attribution" below). This is the one thing to pick
up next, but everything else works regardless of this gap.

---

## Architecture, as actually built (not as originally planned)

**NCS does NOT use NxMapper** (the original plan) — it uses **NxRelay**
instead, installed on NCS's own domain controller (`NCSServer`,
`192.168.0.2`, real AD domain `headoffice.ncsssl.co.uk`).

Why: NxMapper's config only exposes a bare `Server IP` field with no way
to identify which NxCloud operator it belongs to — structurally
incompatible with NxCloud's multi-tenant design (confirmed via NxFilter's
own docs: *"On NxCloud, we don't support user importation from Active
Directory... it's not full scale AD integration yet"*). NxRelay's config
has an actual `token` field tying it to a specific NxCloud user, which is
what actually let it work.

**How NCS's network is wired right now:**
- `NCSServer` has a second IP added: `192.168.0.3`, specifically for
  NxRelay (avoids a port-53 collision with Windows' own DNS Server role,
  which still runs on `.2` for AD/DHCP)
- Windows DNS Server is bound to `192.168.0.2` only (via DNS Manager →
  Interfaces → "Only the following IP addresses", `.3` unticked)
- NxRelay runs as a Windows service on the DC, config at
  `C:\Program Files (x86)\nxrelay\conf\cfg.properties`:
  ```
  server = 188.165.112.175
  token = IE1BU8BE
  local_dns = 192.168.0.2
  local_domain = headoffice.ncsssl.co.uk
  listen_ip = 192.168.0.3
  run_mapper = 1
  ```
- **NCS's DHCP scope** (Scope Options, option 006) now hands out:
  `192.168.0.3` (primary, NxRelay) → `192.168.0.2` (secondary fallback)
  This is the live setting for the whole office right now.

**NxRelay's real GUI**: the desktop shortcut is broken (points at a
nonexistent `bin\setup.bat`) — use
`C:\Program Files (x86)\nxrelay\setup.exe` directly, or fix the shortcut's
Target field to point there.

**NxRelay's log**: `C:\Program Files (x86)\nxrelay\log\nxrelay.log` — very
useful for diagnosing anything wrong with it specifically.

---

## Real bugs found and fixed tonight (all pushed to the repo)

### 1. Nginx wasn't routing bare-IP requests anywhere (the big one)

This was the actual root cause of NxRelay failing to connect for hours.
NxRelay's registration call (`hxlistener`) hits NxCloud by bare IP, not a
domain name. Nginx's certbot-managed config only knows how to route the
two real domain names — a bare-IP request had nowhere to go and got
silently dropped.

**Fixed by**: cleaning up `/etc/nginx/sites-enabled/` — there were **five
different site configs** simultaneously enabled (several redundant
attempts at a bare-IP catch-all, plus Debian's stock "default" welcome
page, plus a stray `.bak` file), all fighting each other. Reduced to just
the one correct config (already built into `netsheriff.conf` itself,
lines ~62-70 — a `listen 80 default_server` block routing to NxCloud's
real port `8880`).

**If this ever breaks again**: check `ls -la /etc/nginx/sites-enabled/`
first — if there's more than the expected two files (`default` symlink
removed, just `netsheriff.conf`), that's very likely why.

### 2. collector.py — two real data-loss bugs

- Only flushed to the database once **200** messages had buffered, with
  no time-based fallback. For a low-traffic office like NCS, this could
  mean data sitting unwritten for hours.
- The only cleanup path only caught `KeyboardInterrupt` (Ctrl+C), not
  `SIGTERM` — meaning every `systemctl restart` silently discarded
  whatever hadn't hit 200 yet.

**Fixed**: added a periodic flush (default every 5s) on a separate
thread, plus proper `SIGTERM` handling. Required `check_same_thread=False`
on the DB connection specifically for collector.py (added as an optional
parameter to `db.connect()`, default behavior unchanged for every other
caller). Verified all three paths (periodic flush, restart-safety,
original batch behavior) in isolated tests before shipping.

### 3. Dashboard's "Needs a look" tile — false positive

Was flagging the top user by distraction-category count even when that
count was literally 0 — a single quiet test request could show up as a
"flagged" concern. Fixed: only flags if `distraction count > 0`. The
template already had a clean `—` fallback for "nobody flagged"; the bug
was that `db.py` never actually returned `None` to trigger it.

### 4. Dashboard now auto-refreshes

Re-fetches data every 3 minutes and redraws in place (no full page
reload, keeps whatever date range is selected, pauses while the tab isn't
visible). New `/dashboard/data.json` endpoint, properly login-protected,
reuses the exact same data logic as the main page.

### 5. Server timezone

`filter` (the OVH box) was displaying UTC. Changed to `Europe/London` via
`timedatectl set-timezone Europe/London` — this only changes what's
*displayed*; the underlying NTP-synced clock was never touched, so no
data-consistency risk.

---

## Outstanding: username attribution (the one real gap)

Three approaches tried, all currently unresolved:

**NxMapper (standalone)** — fails with a generic "Connection error!" on
Test, even after the nginx fix. Doesn't expose a port setting anywhere
(GUI or config file), so it's using some hardcoded internal port that's
never been identified. Tried twice (fresh reinstall the second time),
same result both times.

**NxRelay's own built-in mapper** (`run_mapper = 1` in its config) —
enabled, and NxRelay has been running for hours with confirmed real
traffic and at least one genuine logout/login cycle during that time, but
its own log contains **zero** mentions of "mapper," "AD," or "logon"
anywhere — not an error, just total silence on that specific feature.
Strong sign it's not even attempting to run, not just failing quietly.

**VxLogon** — couldn't locate where this is configured in NxCloud
specifically (tried both admin and operator menus, found nothing). Also,
separately, found real evidence on NxFilter's own forum that this
mechanism may require client PCs to query NxFilter/NxCloud *directly* as
their DNS server — our actual setup relays through NxRelay first, which
uses a completely different mechanism internally. Genuine, unconfirmed
compatibility risk, not just a menu-hunting problem.

**Recommended next step, if picking this up again**: given three attempts
have hit three different, poorly-documented walls, this is a strong
candidate for actually emailing NxFilter support directly (`support@
nxfilter.org`) with the specifics above, rather than more trial and
error against undocumented internals.

---

## The 22 real NCS users already created on NxFilter

(Matches actual AD login names, cross-referenced against a real ADUC
export — service/system accounts explicitly excluded, see below.)

Adelle, A.Waggott, BarryGill, Dila, energy (=Paul, display name never
updated in AD — not a mistake, just how it's been), Glen, J.Winskill,
Julie, KHudson, L.Cassidy, L.Wake, Lorraine, Nassar, Nikola, Rosie, sam,
Scott, SUsher, S.Senturk, ThomasLeonard, Tim, W.Fernando

Also created: `Synthesisit` (Frankie's own remote-support login on their
domain — was mid-test for the username-attribution work above when this
handover was written, not yet confirmed working).

**Deliberately excluded** (shared/system/service accounts, not
individual staff): `Administrator`, `NCSaccounts`, `NCSAdmin`,
`NCSAdministrators`, `NCSAdministratotr`, `PayrollConstruction`, `Retail`,
`scanner`, `MWService`, both `MSOL_...` accounts, all 11
`HealthMailbox...` accounts (Exchange internals). Two accounts (Cheryl
Barwick, Ella Found) were confirmed disabled — correctly not created.

---

## Category-blocking infrastructure (built, all currently OFF)

Platform-wide custom categories exist on NxCloud (not NCS-specific — any
current or future client's policy can enable these):

- **Gambling** — 80,031 domains
- **Social Media** — 27,285 domains (full Block List Project data +
  explicit platform apex entries)
- **Video & Streaming** — 24,290 domains
- **Shopping**, **Search**, **News**, **Business & Work** — smaller
  hand-curated lists (no comprehensive open-source source exists for
  these), 5-35 domains each

All generated by `build_categories.py` in the repo — same curated data
used for our own reporting categorization (`categorizer.py`), kept
consistent between "what we report on" and "what we can block". Source
files also saved under `blocklists/` in the repo for reference.

**None of these block anything** until explicitly enabled in a specific
client's policy — confirmed still all unticked for NCS, matching their
explicit instruction.

---

## Client-facing deliverables already sent/prepared

- **Email to NCS**: sent, explaining go-live, confirming monitor-only,
  asking staff to do a normal restart/logout-login (needed for the
  username piece to eventually pick them up, whenever that gets resolved)
- **Sam's dashboard user guide**: built as a branded PDF (not the earlier
  markdown draft — client-facing, needed to look professional), covers
  every field on the dashboard, walked through and verified page-by-page
  as rendered images before delivery

---

## Things to double-check are still in a sane state

- **The DC's own DNS settings** were changed mid-session for a test
  (primary `192.168.0.3`, secondary `127.0.0.1`, to test NxRelay from the
  server itself) — confirm this was reverted back to normal, since the DC
  itself shouldn't depend on NxRelay long-term (risk: if NxRelay ever
  goes down, don't want the domain controller's own name resolution
  depending on it)
- **NxMapper** is currently freshly reinstalled and failing — fine to
  leave as-is (not doing any harm), or uninstall if it's not going to be
  pursued further

---

## Key reference info

- NxCloud master: `188.165.112.175` (`filter.synthesis-it.co.uk`)
- Slave: `37.59.66.48`
- Portal: `portal.netsheriff.co.uk`
- NCS's public IP (static): `138.248.163.26`
- NCS's AD domain: `headoffice.ncsssl.co.uk`
- NCS default user login token: `IE1BU8BE`
- Repo: `github.com/andrewdunn358-dev/netsheriff`
- NxCloud "Magic Password" login: operator name `NCS` + the magic
  password (set on `System > Admin` on the master) — this is how you get
  into NCS's own operator session, which is required for anything
  client-specific (creating users, VxLogon settings if ever located,
  etc.) — the master admin's own menus are read-only for this kind of
  thing, a lesson learned the hard way earlier tonight
