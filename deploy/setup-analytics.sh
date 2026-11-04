#!/usr/bin/env bash
# One-time, reviewed root setup. Ongoing publishing only receives static bytes.
set -Eeuo pipefail
[[ ${EUID} -eq 0 && $# -eq 0 ]] || { echo 'Run with sudo and no arguments.' >&2; exit 1; }
OPERATOR=${SUDO_USER:-}
[[ "$OPERATOR" =~ ^[a-z_][a-z0-9_-]*$ && $(id -u "$OPERATOR") -ne 0 ]]
OPERATOR_HOME=$(getent passwd "$OPERATOR" | cut -d: -f6)
[[ "$OPERATOR_HOME" =~ ^/home/[a-z_][a-z0-9_-]*$ ]]
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
BACKUP=/var/backups/yangarch-home/analytics-$STAMP
VERSION=/opt/yangarch-home-analytics-versions/$STAMP
CURRENT=/var/www/yangarch.net/home/current
APEX=/etc/nginx/sites-available/yangarch.net
HEADERS=/etc/nginx/snippets/yangarch-home-headers.conf
umask 077
[[ -L "$CURRENT" && -f "$APEX" && ! -L "$APEX" ]]
[[ -L /etc/nginx/sites-enabled/yangarch.net && $(readlink -f /etc/nginx/sites-enabled/yangarch.net) == "$APEX" ]]
/usr/bin/python3 -I "$PROJECT_ROOT/scripts/check.py"
/usr/sbin/runuser -u "$OPERATOR" -- /usr/bin/python3 -I - "$PROJECT_ROOT/scripts" <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from analytics_proxy import container_address
from urllib.request import build_opener,ProxyHandler
p=Path.home()/'.config/yangarch-home-analytics/settings.json'
assert p.stat().st_mode & 0o077 == 0
assert json.loads(p.read_text())['initialized'] is True
with build_opener(ProxyHandler({})).open('http://'+container_address()+':3000/api/heartbeat',timeout=5) as r:assert r.status==200
PY
/usr/sbin/nginx -t
for parent in /opt /etc/systemd/system /etc/logrotate.d /var/log; do
  [[ -d "$parent" && ! -L "$parent" && $(stat -c '%u' "$parent") -eq 0 ]]
  [[ $(( 8#$(stat -c '%a' "$parent") & 0022 )) -eq 0 ]]
done
for dir in /opt/yangarch-home-analytics-versions /var/log/yangarch-home; do
  if [[ -e "$dir" ]]; then [[ -d "$dir" && ! -L "$dir" && $(stat -c '%u' "$dir") -eq 0 ]]; fi
done
if [[ -e /opt/yangarch-home-analytics ]]; then [[ -L /opt/yangarch-home-analytics ]]; fi
install -d -o root -g root -m 700 "$BACKUP"
PREVIOUS=$(readlink "$CURRENT")
FILES=( "$APEX" "$HEADERS" /etc/nginx/snippets/yangarch-home-routes.conf /opt/yangarch-home-analytics /etc/logrotate.d/yangarch-home /etc/systemd/system/yangarch-home-analytics-proxy.service /etc/systemd/system/yangarch-home-analytics.service /etc/systemd/system/yangarch-home-analytics-maintenance.service /etc/systemd/system/yangarch-home-analytics-maintenance.timer /usr/local/sbin/yangarch-home-publish /etc/sudoers.d/yangarch-home-publish /var/lib/yangarch-home/baseline.json /var/lib/yangarch-home/status.json )
for index in "${!FILES[@]}"; do
  file=${FILES[$index]}
  if [[ -e "$file" || -L "$file" ]]; then cp -a -- "$file" "$BACKUP/file-$index"; fi
done
WAS_ACTIVE=0; WAS_ENABLED=0
if systemctl is-active --quiet yangarch-home-analytics.service; then WAS_ACTIVE=1; fi
if systemctl is-enabled --quiet yangarch-home-analytics-maintenance.timer; then WAS_ENABLED=1; fi
rollback() {
  trap - ERR
  set +e
  systemctl stop yangarch-home-analytics-maintenance.timer yangarch-home-analytics-maintenance.service yangarch-home-analytics.service yangarch-home-analytics-proxy.service
  systemctl disable yangarch-home-analytics-maintenance.timer yangarch-home-analytics.service yangarch-home-analytics-proxy.service >/dev/null 2>&1
  for index in "${!FILES[@]}"; do
    file=${FILES[$index]}; rm -f -- "$file"
    if [[ -e "$BACKUP/file-$index" || -L "$BACKUP/file-$index" ]]; then cp -a -- "$BACKUP/file-$index" "$file"; fi
  done
  ln -sfn -- "$PREVIOUS" /var/www/yangarch.net/home/.analytics-rollback
  mv -Tf -- /var/www/yangarch.net/home/.analytics-rollback "$CURRENT"
  systemctl daemon-reload
  if [[ $WAS_ACTIVE -eq 1 ]]; then systemctl enable --now yangarch-home-analytics-proxy.service yangarch-home-analytics.service; fi
  if [[ $WAS_ENABLED -eq 1 ]]; then systemctl enable --now yangarch-home-analytics-maintenance.timer; fi
  if /usr/sbin/nginx -t; then systemctl reload nginx; fi
  echo "Analytics activation failed; restored previous homepage configuration. Backup: $BACKUP" >&2
  exit 1
}
trap rollback ERR
install -d -o root -g root -m 755 /opt/yangarch-home-analytics-versions "$VERSION"
for name in analytics_gateway.py analytics_policy.py analytics_maintenance.py analytics_runtime.py analytics_report.py analytics_proxy.py; do
  [[ -f "$PROJECT_ROOT/scripts/$name" && ! -L "$PROJECT_ROOT/scripts/$name" ]]
  install -o root -g root -m 644 "$PROJECT_ROOT/scripts/$name" "$VERSION/$name"
done
ln -s -- "$VERSION" /opt/.yangarch-home-analytics-$STAMP
mv -Tf -- /opt/.yangarch-home-analytics-$STAMP /opt/yangarch-home-analytics
install -d -o root -g adm -m 750 /var/log/yangarch-home
if [[ ! -e /var/log/yangarch-home/access.log ]]; then install -o www-data -g adm -m 640 /dev/null /var/log/yangarch-home/access.log; fi
[[ -f /var/log/yangarch-home/access.log && ! -L /var/log/yangarch-home/access.log ]]
chown www-data:adm /var/log/yangarch-home/access.log
chmod 640 /var/log/yangarch-home/access.log
cat > /etc/logrotate.d/yangarch-home <<'EOF'
/var/log/yangarch-home/access.log {
    daily
    rotate 14
    maxage 14
    missingok
    ifempty
    compress
    delaycompress
    create 0640 www-data adm
    sharedscripts
    postrotate
        /bin/systemctl kill -s USR1 --kill-who=main nginx.service >/dev/null 2>&1 || true
    endscript
}
EOF
cat > /etc/systemd/system/yangarch-home-analytics.service <<EOF
[Unit]
Description=Homepage analytics collection gateway (loopback only)
After=network.target docker.service yangarch-home-analytics-proxy.service
Requires=docker.service yangarch-home-analytics-proxy.service
[Service]
Type=simple
User=$OPERATOR
WorkingDirectory=/
ExecStart=/usr/bin/python3 -I /opt/yangarch-home-analytics/analytics_gateway.py --settings $OPERATOR_HOME/.config/yangarch-home-analytics/settings.json
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6
CapabilityBoundingSet=
MemoryMax=128M
TasksMax=64
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/yangarch-home-analytics-maintenance.service <<EOF
[Unit]
Description=Homepage private log aggregation and analytics retention
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
User=$OPERATOR
WorkingDirectory=/
ExecStart=/usr/bin/python3 -I /opt/yangarch-home-analytics/analytics_maintenance.py
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$OPERATOR_HOME/.local/share/yangarch-home-analytics
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
MemoryMax=256M
TasksMax=64
TimeoutStartSec=180
EOF
cat > /etc/systemd/system/yangarch-home-analytics-maintenance.timer <<'EOF'
[Unit]
Description=Hourly private homepage analytics aggregation
[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
Persistent=true
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/yangarch-home-analytics-proxy.service <<EOF
[Unit]
Description=Loopback relay to unpublished homepage analytics container
After=docker.service
Requires=docker.service
[Service]
Type=simple
User=$OPERATOR
WorkingDirectory=/
ExecStart=/usr/bin/python3 -I /opt/yangarch-home-analytics/analytics_proxy.py
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
MemoryMax=128M
TasksMax=64
[Install]
WantedBy=multi-user.target
EOF
chmod 644 /etc/logrotate.d/yangarch-home /etc/systemd/system/yangarch-home-analytics*.service /etc/systemd/system/yangarch-home-analytics*.timer
systemctl daemon-reload
systemctl enable yangarch-home-analytics-proxy.service yangarch-home-analytics.service
systemctl restart yangarch-home-analytics-proxy.service yangarch-home-analytics.service
systemctl start yangarch-home-analytics-maintenance.service
systemctl enable --now yangarch-home-analytics-maintenance.timer
/usr/bin/curl -q --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 --noproxy '*' -o /dev/null http://127.0.0.1:4318/health
/usr/bin/curl -q --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 --noproxy '*' -o /dev/null http://127.0.0.1:4319/api/heartbeat
install -o root -g root -m 644 "$PROJECT_ROOT/deploy/security-headers.conf" "$HEADERS"
install -o root -g root -m 644 "$PROJECT_ROOT/deploy/yangarch.net.conf" "$APEX"
/usr/sbin/nginx -t
# Baseline the reviewed logging/CSP configuration before enabling static publishing.
bash "$PROJECT_ROOT/deploy/setup-publisher.sh"
# Publish exactly the checked static data, including the disclosure and opt-out page.
/usr/bin/python3 -I - "$PROJECT_ROOT" <<'PY' | /usr/local/sbin/yangarch-home-publish
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);manifest=json.loads((root/'artifacts/manifest.json').read_text())
files={name:(root/'dist'/name).read_bytes() for name in manifest['files']}
assert all(hashlib.sha256(raw).hexdigest()==manifest['files'][name] for name,raw in files.items())
header={'protocol':1,'operation':'publish','manifest':manifest,'sizes':{name:len(raw) for name,raw in files.items()}}
sys.stdout.buffer.write(json.dumps(header,separators=(',',':')).encode()+b'\n')
for name in sorted(files):sys.stdout.buffer.write(files[name])
PY
trap - ERR
printf 'Analytics and static publishing activation passed. Dashboard: SSH only. Backup: %s\n' "$BACKUP"
