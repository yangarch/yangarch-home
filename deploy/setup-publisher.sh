#!/usr/bin/env bash
# One-time installation of a root-owned, static-data-only publishing receiver.
set -euo pipefail
[[ ${EUID} -eq 0 && $# -eq 0 ]] || { echo 'Run this setup with sudo and no arguments.' >&2; exit 1; }
PUBLISH_USER=${SUDO_USER:-}
[[ "$PUBLISH_USER" =~ ^[a-z_][a-z0-9_-]*$ && $(id -u "$PUBLISH_USER") -ne 0 ]] || { echo 'A non-root sudo user is required.' >&2; exit 1; }
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
LIB=/usr/local/lib/yangarch-home
STATE=/var/lib/yangarch-home
WRAPPER=/usr/local/sbin/yangarch-home-publish
SUDO_RULE=/etc/sudoers.d/yangarch-home-publish
STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
umask 077
for parent in /usr/local/lib /usr/local/sbin /etc/sudoers.d; do
  [[ -d "$parent" && ! -L "$parent" && $(stat -c '%u' "$parent") -eq 0 ]] || { echo 'Unexpected system directory ownership.' >&2; exit 1; }
  [[ $(( 8#$(stat -c '%a' "$parent") & 0022 )) -eq 0 ]] || { echo 'System directory must not be writable by other users.' >&2; exit 1; }
done
for dir in "$LIB" "$STATE" "$LIB/versions" "$STATE/backups"; do
  if [[ -e "$dir" ]]; then
    [[ -d "$dir" && ! -L "$dir" && $(stat -c '%u' "$dir") -eq 0 ]] || { echo 'Unexpected publisher directory ownership.' >&2; exit 1; }
    [[ $(( 8#$(stat -c '%a' "$dir") & 0022 )) -eq 0 ]] || { echo 'Publisher directory must not be writable by other users.' >&2; exit 1; }
  fi
done
[[ -L /var/www/yangarch.net/home/current && -f /etc/nginx/snippets/yangarch-home-routes.conf ]] || { echo 'Deploy the homepage first.' >&2; exit 1; }
/usr/sbin/nginx -t
install -d -m 755 "$LIB" "$LIB/versions"
install -d -m 700 "$STATE" "$STATE/backups"
VERSION=$LIB/versions/$STAMP
BACKUP=$STATE/backups/setup-$STAMP
install -d -m 755 "$VERSION"
install -d -m 700 "$BACKUP"
for name in publication.py publish_receiver.py record_media.py; do
  [[ -f "$PROJECT_ROOT/scripts/$name" && ! -L "$PROJECT_ROOT/scripts/$name" ]]
  install -o root -g root -m 644 "$PROJECT_ROOT/scripts/$name" "$VERSION/$name"
done
for pair in "$WRAPPER:wrapper" "$SUDO_RULE:sudoers" "$STATE/baseline.json:baseline.json"; do
  file=${pair%:*}; label=${pair##*:}
  if [[ -e "$file" ]]; then [[ ! -L "$file" ]]; cp -a -- "$file" "$BACKUP/$label"; fi
done
rollback() {
  trap - ERR
  for pair in "$WRAPPER:wrapper" "$SUDO_RULE:sudoers" "$STATE/baseline.json:baseline.json"; do
    file=${pair%:*}; label=${pair##*:}
    if [[ -e "$BACKUP/$label" ]]; then cp -a -- "$BACKUP/$label" "$file"; else rm -f -- "$file"; fi
  done
  echo 'Publisher setup failed; restored previous publisher configuration.' >&2
  exit 1
}
trap rollback ERR
cat > "$BACKUP/wrapper.new" <<EOF
#!/bin/sh
[ "\$#" -eq 0 ] || exit 64
exec /usr/bin/python3 -I "$VERSION/publish_receiver.py"
EOF
printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/yangarch-home-publish ""\n' "$PUBLISH_USER" > "$BACKUP/sudoers.new"
/usr/sbin/visudo -cf "$BACKUP/sudoers.new"
/usr/bin/python3 -I - "$BACKUP/baseline.new" <<'PY'
import hashlib,json,sys
from pathlib import Path
paths=['/etc/nginx/sites-available/yangarch.net','/etc/nginx/snippets/yangarch-home-headers.conf']
Path(sys.argv[1]).write_text(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}))
PY
install -o root -g root -m 600 "$BACKUP/baseline.new" "$STATE/baseline.json"
install -o root -g root -m 755 "$BACKUP/wrapper.new" "$WRAPPER"
install -o root -g root -m 440 "$BACKUP/sudoers.new" "$SUDO_RULE"
/usr/sbin/visudo -c
printf '{"protocol":1,"operation":"status"}\n' | "$WRAPPER"
printf '{"protocol":1,"operation":"status"}\n' | /usr/sbin/runuser -u "$PUBLISH_USER" -- /usr/bin/sudo -n -- "$WRAPPER"
trap - ERR
printf 'Publisher setup passed for %s. Only the no-argument homepage receiver is allowed without a sudo password.\n' "$PUBLISH_USER"
