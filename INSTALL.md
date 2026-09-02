# INSTALL — NxCloud two-node cluster on Proxmox + NxReport

Target setup:

```
                     ┌─ Proxmox node 1 ─────────────┐   ┌─ Proxmox node 2 ─────────────┐
                     │  LXC 101: NxCloud MASTER      │   │  LXC 201: NxCloud SLAVE      │
                     │  (admin GUI, all config)      │◄──┤  (auto-syncs config, always  │
                     │                               │   │   live — no manual failover) │
                     └──────────────┬────────────────┘   └──────────────┬───────────────┘
                                    │  syslog (UDP 5140)                │  syslog (UDP 5140)
                                    ▼                                   ▼
                                  ┌─ Proxmox node 2, LXC 202: NxReport ──┐
                                  │  collector.py + app.py + weekly PDFs │
                                  └──────────────────────────────────────┘

Client sites:  DNS 1 = master IP,  DNS 2 = slave IP   (never a public resolver!)
```

Fill this in before you start:

| Thing | Value |
|---|---|
| Master LXC IP | `10.x.x.A` |
| Slave LXC IP | `10.x.x.B` |
| NxReport LXC IP | `10.x.x.C` |
| Client WAN IPs (for the port-53 allowlist) | ... |

---

## 1. Create the containers (both Proxmox nodes)

Debian 12 template. Per NxCloud container: **2 cores, 2–4 GB RAM, 10 GB disk,
static IP, unprivileged, Features → nesting=1**. NxReport container can be
1 core / 1 GB / 8 GB.

**Older Proxmox whose catalogue doesn't list Debian 12** — download the
template manually on the host; it then shows up in the create-CT dialog:

```bash
cd /var/lib/vz/template/cache
wget http://download.proxmox.com/images/system/debian-12-standard_12.7-1_amd64.tar.zst
# browse download.proxmox.com/images/system/ for the current filename
```

Boot a throwaway CT from it first and check `systemctl status` inside comes up
clean. If the old host kernel won't run it (systemd/cgroup errors), fall back to:

**Fallback: Ubuntu 20.04 LXC** (EOL for free updates since May 2025 — mitigate!)

- Attach free Ubuntu Pro ESM (personal tier covers 5 machines, updates to
  2030): get a token at ubuntu.com/pro, then `pro attach <token>` in each CT.
- Use `openjdk-11-jre-headless` instead of 17 (matches the NxFilter docs).
- The systemd-resolved port-53 fix in step 2 definitely applies.
- For the NxReport container, 20.04's Chromium is snap-only (broken in LXC):
  install Google Chrome instead and pass it to report.py —
  ```bash
  curl -O https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
  apt install ./google-chrome-stable_current_amd64.deb
  ```
  (`report.py` auto-detects `google-chrome`.)

## 2. Free up port 53 (inside each NxCloud container)

Debian/Ubuntu may have a stub resolver on 53. Check and clear it — **do this
on every fresh container even if you think it's clean**; a residual
systemd-resolved cost us the best part of an hour on the first build:

```bash
ss -ulnp | grep :53          # anything listening?
systemctl disable --now systemd-resolved 2>/dev/null
rm -f /etc/resolv.conf
echo "nameserver 1.1.1.1" > /etc/resolv.conf
ss -ulnp | grep :53          # must now be EMPTY before installing nxcloud
```

## 3. Install NxCloud (both NxCloud containers)

```bash
apt update && apt install -y openjdk-17-jre-headless curl
# Get the current version number from https://nxfilter.org/p4/download/
curl -4 -O http://pub.nxfilter.org/nxcloud-<VERSION>.deb
apt install ./nxcloud-<VERSION>.deb
systemctl enable --now nxcloud     # unit name may be 'nxcloud' or 'nxfilter' — check: systemctl list-units | grep nx
```

First start populates the DB — give it a minute, don't restart it mid-init.
If the download crawls to a halt (rare but happens), run it inside `tmux`
first (`apt install -y tmux && tmux new -s setup`) so a dropped console
doesn't kill it mid-transfer.
(Docs reference OpenJDK 11; Debian 12 ships 17, which works. If the service
fails with a Java version error, install Temurin 11 instead.)

Log in to the GUI at `http://<container-ip>/admin` (default **admin / admin** —
change it immediately). Also change the **magic password** (System > Admin,
default `magic1023`) — it grants admin access to every operator GUI.

## 4. Master node config (LXC 101)

1. System > Clustering → set this node as **master**, and enter the **slave's
   real IP** in the Slave IP field (not this node's own IP — double-check
   `ip -4 a` on both boxes rather than assuming, especially with OVH failover
   IPs where it's easy to mix up which address belongs to which container).
   Restart the service — the clustering ports (19003/19004, see below) only
   start listening after this restart.
2. Operator menu → create one **operator per client** (e.g. `brightside`).
   Each operator automatically gets a default user + policy. The operator's
   default password is the operator name — change it.
3. System > Setup > Syslog → host = NxReport IP, port = **5140**, pipe format.

## 5. Slave node config (LXC 201)

1. Install as above, then System > Clustering → point Master IP at the
   master's **real, current** IP (verify with `ip -4 a` on the master, don't
   assume from memory).
2. Ports between master and slave: **TCP 19002, 19003, 19004 + UDP 19004**.
   Verify with `nc -zv <other-node-ip> <port>` in both directions before
   assuming a firewall is the problem — the master's TCP 19002/19003/19004
   and UDP 19004 listeners only come up once it has a valid Slave IP saved
   and has been restarted.
3. Restart; check the master's Clustering page shows the slave connected
   with a real Last Contact timestamp (not "No contact within the last 60
   seconds" repeating). Start order after any outage: **master first, then
   slave**.
4. Set the same syslog export on the slave (logs do NOT replicate between
   nodes — both must send to the collector, or reports will have gaps).

Optionally on the slave: `cluster_double_check=1` in
`/nxcloud/conf/cfg.properties` for stricter master-connection monitoring.

**If the slave's own GUI won't load after a restart**, don't assume it's
broken — a slave's web server may not finish starting until it has
successfully synced its initial config from the master (large classification
ruleset/blocklist imports can take several minutes on first sync). Check
`journalctl -u nxcloud -n 100` for "rows inserted" / "Copying ruleset" lines
before concluding something's wrong, and give it 5–10 minutes untouched
rather than restarting repeatedly — repeated restarts mid-import is what
corrupts the local H2 database (symptom: "Couldn't connect to config DB!" in
the log). If that happens, it's safe to wipe and let it rebuird from the
master: `systemctl stop nxcloud && rm -f /nxcloud/db/* && systemctl start
nxcloud`.

If the systemd unit gets killed mid-sync with `TimeoutStartSec` exceeded,
raise it rather than fighting the import:
```bash
mkdir -p /etc/systemd/system/nxcloud.service.d
cat > /etc/systemd/system/nxcloud.service.d/override.conf <<'EOF'
[Service]
TimeoutStartSec=900
EOF
systemctl daemon-reload
```

## 6. NxReport (LXC 202)

```bash
apt update && apt install -y python3-pip python3-flask python3-jinja2 chromium git
git clone <your-repo-url> /opt/nxreport
```

`/etc/systemd/system/nxreport-collector.service`:

```ini
[Unit]
Description=NxReport syslog collector
After=network.target
[Service]
WorkingDirectory=/opt/nxreport
ExecStart=/usr/bin/python3 collector.py --db /opt/nxreport/nxreport.db --port 5140
Restart=always
[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/nxreport-web.service`:

```ini
[Unit]
Description=NxReport dashboard
After=network.target
[Service]
WorkingDirectory=/opt/nxreport
ExecStart=/usr/bin/python3 app.py --db /opt/nxreport/nxreport.db --port 8080 --brand "Your IT Support Ltd"
Restart=always
[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now nxreport-collector nxreport-web
```

Register each tenant (name must equal the NxCloud **operator** name):

```bash
sqlite3 /opt/nxreport/nxreport.db "INSERT INTO tenants (name,display_name,token,email)
  VALUES ('brightside','Brightside Accounting Ltd','$(openssl rand -hex 12)','owner@client.co.uk');"
```

Weekly PDF, crontab (one line per client):

```
5 7 * * MON cd /opt/nxreport && python3 report.py --db nxreport.db --tenant brightside \
  --days 7 --pdf /tmp/brightside.pdf --email owner@client.co.uk \
  --smtp smtp.office365.com:587 --smtp-user reports@yourmsp.co.uk --smtp-pass '...'
```

## 7. Client site rollout

1. **DrayTek/DHCP**: DNS 1 = master IP, DNS 2 = slave IP. Never set a public
   resolver as secondary — Windows uses the secondary whenever it likes, which
   silently bypasses filtering and reporting.
2. **AD agent** on the client's DC (download page) so logs carry usernames.
   Without it, set a default user per site IP range in the operator GUI —
   do this anyway: it's also the fallback that keeps filtering sane if the
   master (which handles login) is down.
3. If sites reach the cluster over the internet (e.g. a node on an OVH box):
   allowlist client WAN IPs on port 53 (Proxmox firewall or nftables) and do
   NOT expose the admin GUI (80/443) or NxReport (8080) publicly — LAN/VPN
   only. An open resolver gets abused for DDoS amplification within hours.

## 8. Failure & recovery

- **Master down**: slave keeps filtering; clients fail over to DNS 2
  automatically. Login redirection pauses (see default-user note above).
  Fix the master, start it, then restart the slave.
- **Master dead long-term**: on the slave, System > Clustering → make it
  master; point client DNS 1 at it (or re-IP the container to the old
  master's address). Config is already synced.
- **PBS**: back up all three containers nightly. NxCloud state lives in
  `/nxcloud/conf`, `/nxcloud/db`, `/nxcloud/log`; NxReport state is
  `/opt/nxreport/nxreport.db`.
- Prune old logs monthly (cron):
  `sqlite3 /opt/nxreport/nxreport.db "DELETE FROM dns_log WHERE ts < date('now','-90 days'); VACUUM;"`

## 9. Branding the admin login page (optional)

NxCloud/NxFilter is fully rebrandable free of charge. The login page lives at
`/nxcloud/guipack/cloudwatch/admin.jsp` (guipack name may differ if you switch
themes). To add a background image:

1. Copy the image into `/nxcloud/guipack/cloudwatch/img/`.
2. Edit `admin.jsp`, adding a `<style>` block (background + a near-opaque
   panel behind the login card so it stays readable) just before the
   `<!-- Action info -->` comment near the top of the file.
3. Clear the pre-rendered cache and restart:
   ```bash
   rm -f /nxcloud/tmp/cache/login-page.html
   systemctl restart nxcloud
   ```
4. Hard-refresh the browser.

Note this edits the stock install file directly — a future NxCloud package
upgrade will overwrite it back to default. For a permanent setup, copy the
whole `guipack` directory to your own name and point NxCloud at it instead
(see the NxFilter forum's rebranding guidance) so upgrades don't wipe your
branding.

## 10. Checklist before first client goes live

- [ ] admin password + magic password changed on BOTH nodes
- [ ] slave shows connected on master's Clustering page, with a recent Last
      Contact time (not "No contact")
- [ ] `nslookup facebook.com <master-ip>` and `<slave-ip>` both answer
- [ ] stop master container → resolution still works via slave → start it again
- [ ] syslog rows appearing: `sqlite3 nxreport.db "SELECT COUNT(*) FROM dns_log"`
- [ ] dashboard loads at `http://<nxreport-ip>:8080/t/<token>`
- [ ] test PDF generates and emails
- [ ] port 53 not reachable from arbitrary internet IPs
- [ ] SSH root login reverted from `PermitRootLogin yes` back to
      `prohibit-password` (or key-only) on any box it was temporarily opened on
- [ ] client AUP/handbook mentions web monitoring (ICO transparency)
