# Net Sheriff — handover (2026-09-03, evening)

Supersedes nothing; adds to `handover-full-current-state.md`. Focus of this
session was the outstanding username-attribution gap. Ends in a much better
place than it started: **per-machine attribution now works**, and the
remaining username piece has a clear build path that doesn't depend on
NxFilter fixing anything.

---

## THE BIG WIN — private IPs were being discarded by our own code

**Symptom**: every row in `dns_log` carried NCS's public IP
(`138.248.163.26`), so no device could be told apart. NxCloud's own log view
meanwhile showed correct private IPs (`192.168.0.34`, `.42`, `.47` …).

**Cause**: NxCloud's syslog export is set to **JSON format** (System > Setup >
Syslog). Each record carries *both*:

```json
{"...","ClientIp":"138.248.163.26","Operator":"NCS","LocalIp":"192.168.0.61"}
```

`ClientIp` is the site's public IP — identical for every device behind the
relay. `LocalIp` is the real private IP that NxRelay preserves. `collector.py`
read `ClientIp` and ignored `LocalIp` entirely.

**Fix** (applied and pushed): the JSON branch of `parse_line()` now prefers
`LocalIp`, falling back to `ClientIp` for any future site with no relay.

**Verified live**: after `systemctl restart nxreport-collector`, fresh rows
show `192.168.0.34`, `.42`, `.46`, `.47`, `.61`, `.69`, `.72`.

This alone gets NCS most of what they asked for — one desk is effectively one
person. Say that honestly to the client rather than implying authenticated
identity.

**Note**: pipe format has the same field as a trailing `localIp`. The pipe
branch of the parser still doesn't read it. Not urgent (we export JSON), but
it's an inconsistency worth closing if the format ever changes.

---

## Username attribution — mapper is a genuine NxFilter-side failure

### How the mechanism actually works (previously misunderstood)

NxMapper — and NxRelay's integrated mapper — do **not** read the Windows
Security event log. Per NxFilter's own developer, they use **SMB session
information**: `net session` on the DC. That's why asking staff to log out and
back in never helped.

`net session` on NCSServer returns real data:

```
\\192.168.0.34   ThomasLeonard
\\192.168.0.46   Scott
\\192.168.0.47   Lorraine
\\192.168.0.61   energy
\\192.168.0.69   Glen
```

Matches the 22 created NxCloud users exactly. **The source data is available.**

### Everything ruled out

- NxRelay **2.9.2** is the current release (2026-04-16). Not a version issue.
- `run_mapper = 1` confirmed present in the file the service reads, with a
  restart afterwards (13:01 startup).
- Startup logs `NP, LoL, NT, LcL, HM, RH, US` — **no mapper module** appears.
- `nxrelay.log` has zero occurrences of "mapper", "AD" or "logon". No error;
  silence.
- `C:\Windows\Temp\nxmapper.log` exists but is 0 bytes (leftover from the
  standalone NxMapper install).
- Server side: recursive grep across `/nxcloud/log/` **including all 20
  rotations** finds nothing mapper-related.
- **nginx is not implicated.** All relay traffic returns 200. The only 404s
  were historical (12:41–12:43, before the earlier nginx fix). CxLogon's
  traffic never touches port 80 at all.
- Ports: 19003/19004 listen and are reachable from NCS. 19002 does not listen
  — that's NxFilter's port, not NxCloud's, and is **not** a fault.
- Clustering **confirmed working** (slave → master 19003 open), not just
  assumed.

### CxLogon was trialled and works — but nothing arrives

Installed `cxlogon-1.0.6-p2-win.msi` on NCSServer. Runs as a service. Its log
shows, every 60 seconds:

```
Program._main, Found username from registry, uname = Synthesisit.
Program.SendUname, uname = Synthesisit, azUname = .
Program.SendUname, /CXB <hash> c3ludGhlc2lzaXQ= win
```

(`c3ludGhlc2lzaXQ=` is base64 for `synthesisit`.) So the agent detects and
transmits correctly. NxCloud never receives it — no trace in any log, plain or
base64. **Uninstall was requested**; confirm it's gone.

Docs say CxLogon with NxRelay+NxCloud yields `tokenname_username` form and
doesn't create login requests. Requires the relay to be the only DNS server —
NCS's DHCP hands out `.2` as secondary, which may matter, untested.

### Conclusion

Two independent, documented username mechanisms both produce nothing at the
server, through a relay whose ordinary filtering works flawlessly all day.
That's an NxRelay 2.9.2 issue. A support email is drafted (see below).

---

## NEXT BUILD — attribution service (recommended, no NxFilter dependency)

We now have `client_ip` per row. Add a timestamped IP→user mapping and join at
report time. NxFilter never needs to know usernames exist.

- **Collector**: Tactical RMM scheduled script on the DC, every 60s, parsing
  `net session` and POSTing `{ip, username, seen_at}` to a new authenticated
  NxReport endpoint.
- **Storage**: new `ip_user_map` table (tenant, ip, username, first_seen,
  last_seen).
- **Reporting**: join `dns_log.client_ip` to the mapping active at
  `dns_log.ts`.

Why this beats the alternatives: timestamped mappings handle DHCP churn
naturally (no reservations needed — Frankie explicitly didn't want to pin
NCS's DHCP), no agent on workstations, Tactical is already deployed
everywhere, and it becomes a standard onboarding step for future clients.
Cost: zero.

**Caveat to design around**: `net session` only shows currently-connected
sessions, so the collector must run continuously and accumulate. Machines that
are off simply don't appear.

---

## Other fixes and findings tonight

### Block Redirection IP — was pointing at the UniFi controller (FIXED)

`System > Setup > Block and Authentication > Block Redirection IP` was
`188.165.112.174` — Frankie's UniFi controller, not NxCloud. Auto-populated at
install from the old OVH failover IP.

Harmless only because NCS blocks nothing. The first time *any* client enabled
blocking, users would have hit the UniFi login page instead of the Net Sheriff
block page — and it advertises the management box to every client network.

Corrected to `188.165.112.175`. Verified end-to-end: relay log flipped from
`.174` to `.175` at 16:46:55.

**Optional follow-up**: docs allow comma-separated redirection IPs for
clustering redundancy (`188.165.112.175,37.59.66.48`) — do once the slave is
confirmed serving the block page.

### Timezone mismatch — OUTSTANDING

`filter` is now `Europe/London` (BST) but the syslog stream carries **UTC**.
Database rows are consistently **one hour behind** the OS clock (17:00 row at
18:00 BST). Dashboards and the date-range picker are affected. Fix in
NxCloud's own timezone setting, not the OS.

### NCS DNS oddities — worth raising with the client

- **Wildcard on `ncsssl.co.uk`**: any unresolvable internal name devolves and
  resolves to `185.151.30.183` (their web host). Makes odd faults very hard to
  diagnose. Caused several false leads tonight.
- **IPv6 responds** (`2a07:7800::183`). If filtering is v4-only, v6 lookups
  could bypass. Worth confirming.
- **DC's own DNS is still `192.168.0.3, 127.0.0.1`** — the leftover test
  config flagged in the previous handover as needing reverting. A DC shouldn't
  depend on the relay for its own resolution.

### Diagnostic gotchas (cost real time tonight)

- Syslog Host is `127.0.0.1`, so `tcpdump -i any` sees nothing. Use `-i lo`.
- `nxrelay.log` is extremely chatty (~8 min per 40 lines). Filter out
  `hxlistener` to find anything useful; use `-TotalCount` for the startup
  banner.
- Windows appends the DNS suffix — always use a trailing dot
  (`nslookup facebook.com. 192.168.0.3`) or you test the wrong name.
- `query_cache_ttl = 300` means repeat lookups don't reach the server. Use a
  unique hostname when testing.
- Domains matching `local_domain` are bypassed and never logged.
- NxCloud's GUI log view and the syslog export can disagree briefly.

### Config tidy-up (low priority)

`cfg.properties` has `use_radius = 1` with `radius_shared_secret = testing123`
— a RADIUS listener running on a client DC that isn't used (no 802.1X).
Also `radius_acct_port` is not a documented key (it's
`radius_accounting_port`), so that line is ignored. Turn RADIUS off next time
the file is edited.

---

## Support email

Drafted and anonymised (usernames masked, domain/IP/token as placeholders).
Send to **support@nxfilter.org**, or post to `forum.nxfilter.org` where the
developer answers directly and usually faster.

Subject: *NxRelay 2.9.2 — no usernames reaching NxCloud from either
run_mapper or CxLogon*

Key question to lead with: **how does CxLogon transmit the username when used
with NxRelay and NxCloud — DNS, HTTP, or direct, and to which host/port?**
Knowing that lets us trace the exact path ourselves.

---

## State of play

NCS is live, filtering correctly, monitor-only per client instruction, with
per-machine reporting now working. The only gap is putting names to machines,
and that's a build we own rather than a vendor dependency.

---

## UPDATE — attribution built and working end to end

Built and deployed the same evening. Working per-person reporting confirmed
against live NCS data.

**What was added** (commits `bd80460`, `8dd956e`):

- `ip_user_map` table — timestamped intervals, not a single current value.
- `db.record_ip_users()` / `db.user_for_ip_at()`.
- `POST /api/ip-users`, authenticated by `X-Agent-Token` against
  `NXREPORT_AGENT_TOKEN` (503 if unset, so it can't run open).
- `agent/report-sessions.ps1` — Tactical RMM script for the client DC.
  Regex validated against real `net session` output; machine accounts (`$`),
  `MSOL_`, `HealthMailbox` and named service accounts filtered out.
- **Timezone fix in `collector.py`**: NxCloud exports UTC, host runs
  Europe/London. `to_local()` converts via the OS (not a fixed offset, so DST
  is handled). This was also silently breaking the attribution join.

**Verified live**: 6 users mapped (ThomasLeonard, Julie, Scott, Lorraine,
energy, Glen) and joined against real DNS activity with correct local
timestamps. Historic rows (id <= 2188) shifted +1 hour to match.

### OUTSTANDING — must fix before a second client

**The agent token is global.** One `NXREPORT_AGENT_TOKEN` for all tenants
means any site's agent could post mappings for any other tenant. Needs to be
a per-tenant token stored on the tenant record and checked against the posted
tenant name. Not urgent with one client; blocking for two.

### Also still outstanding

- Dashboard/PDF don't yet use the mapping — reports still show IPs. The join
  is proven (see above), it just needs wiring into `db.py` aggregations and
  the templates.
- Tactical scheduled task needs creating on NCSServer (every 1-2 min) with
  `NS_PORTAL_URL`, `NS_TENANT`, `NS_AGENT_TOKEN` set in the task.
- Rotate the GitHub PAT and the agent token — both were pasted in chat.
- DC's own DNS still `192.168.0.3, 127.0.0.1` (leftover test config).
- `use_radius = 1` / `testing123` still live on the client DC, unused.

### Honest limitation to state to the client

This attributes activity to a **machine's logged-in user**, not an
authenticated identity. One desk is effectively one person, which answers
NCS's question, but shared machines and `Switch User` would misattribute.
Say so plainly in any report that feeds an HR conversation.
