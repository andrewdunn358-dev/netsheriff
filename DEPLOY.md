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

## 3. Install the systemd units

```bash
cp /opt/netsheriff/systemd/nxreport-collector.service /etc/systemd/system/
cp /opt/netsheriff/systemd/nxreport-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nxreport-collector nxreport-dashboard
```

## 4. Check both are running

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
