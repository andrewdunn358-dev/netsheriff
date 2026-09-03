# Deploying NxReport natively (no Docker)

Runs directly on the same CT as NxCloud, as two systemd services — the
same pattern NxCloud itself already uses there. No container runtime
needed, so this works fine on an unprivileged LXC.

## 1. Install Python + deps

```bash
apt-get update && apt-get install -y python3 python3-pip
cd /opt/netsheriff
pip3 install --break-system-packages -r requirements.txt gunicorn
```

## 2. Set the secret key (if not already done)

```bash
cd /opt/netsheriff && echo "NXREPORT_SECRET_KEY=$(openssl rand -hex 32)" > .env
```

## 3. Build the domain categorization lookup

NxFilter's free tier ('Globlist') only auto-classifies 3 categories
(Ads, Phishing/Malware, Porn) — everything else (social media, shopping,
streaming, etc.) would otherwise show up uncategorized. This builds our
own lookup from open-source lists, independent of NxFilter's paid
categorization services:

```bash
cd /opt/netsheriff && python3 build_categories.py --out categories.json
```

Takes a minute or two (downloads a few open-source blocklists). Re-run
this periodically to pick up upstream updates — the systemd timer below
does this weekly automatically:

```bash
cp /opt/netsheriff/systemd/nxreport-categories.service /etc/systemd/system/
cp /opt/netsheriff/systemd/nxreport-categories.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nxreport-categories.timer
```

## 4. Install the systemd units

```bash
cp /opt/netsheriff/systemd/nxreport-collector.service /etc/systemd/system/
cp /opt/netsheriff/systemd/nxreport-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nxreport-collector nxreport-dashboard
```

## 5. Check both are running

```bash
systemctl status nxreport-collector nxreport-dashboard --no-pager
```

Dashboard listens on `127.0.0.1:8080` only — nginx (with TLS) is what
should actually be reachable from outside the CT. Collector listens on
UDP `:5140` on all interfaces, since the slave node needs to reach it
across the network.

## Updating after a `git pull`

```bash
systemctl restart nxreport-collector nxreport-dashboard
```
