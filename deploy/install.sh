#!/usr/bin/env bash
# Run as root only after reviewing the staged site. Never copy source into a web root.
set -euo pipefail
if [[ ${EUID} -ne 0 ]]; then
  echo 'Administrator access is required: sudo bash deploy/install.sh' >&2
  exit 1
fi
PROJECT_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
SITE_ROOT=/var/www/yangarch.net
CONFIG=/etc/nginx/sites-available/yangarch.net
ENABLED=/etc/nginx/sites-enabled/yangarch.net
HEADERS=/etc/nginx/snippets/yangarch-home-headers.conf
ROUTES=/etc/nginx/snippets/yangarch-home-routes.conf
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/var/backups/yangarch-home/$STAMP
RELEASE=$SITE_ROOT/home/releases/$STAMP
CURRENT=$SITE_ROOT/home/current
umask 077
[[ -f "$CONFIG" && -L "$ENABLED" && "$(readlink -f "$ENABLED")" == "$CONFIG" ]] || { echo 'Unexpected apex nginx layout; stopping.' >&2; exit 1; }
[[ -f "$SITE_ROOT/portfolio/giseok-chung-data-portfolio.pdf" ]] || { echo 'Existing PDF is missing; stopping.' >&2; exit 1; }
if [[ ! -f /etc/systemd/system/yangarch-home-analytics.service ]]; then
  echo "Run deploy/setup-analytics.sh first to enable the reviewed collector and private logs." >&2
  exit 1
fi
python3 -I "$PROJECT_ROOT/scripts/check.py"
PDF_BEFORE=$(sha256sum "$SITE_ROOT/portfolio/giseok-chung-data-portfolio.pdf" | cut -d ' ' -f 1)
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
cp -a -- "$CONFIG" "$BACKUP/apex.conf"
for pair in "$HEADERS:headers.conf" "$ROUTES:routes.conf"; do
  target=${pair%:*}; name=${pair##*:}
  if [[ -e "$target" ]]; then cp -a -- "$target" "$BACKUP/$name"; fi
done
PREVIOUS=
if [[ -L "$CURRENT" ]]; then PREVIOUS=$(readlink "$CURRENT");
elif [[ -e "$CURRENT" ]]; then echo 'current is not a symlink; stopping.' >&2; exit 1; fi
printf '%s\n' "$PREVIOUS" > "$BACKUP/previous-release"
rollback() {
  trap - ERR
  cp -a -- "$BACKUP/apex.conf" "$CONFIG"
  for pair in "$HEADERS:headers.conf" "$ROUTES:routes.conf"; do
    target=${pair%:*}; name=${pair##*:}
    if [[ -e "$BACKUP/$name" ]]; then cp -a -- "$BACKUP/$name" "$target"; else rm -f -- "$target"; fi
  done
  if [[ -n "$PREVIOUS" ]]; then ln -sfn -- "$PREVIOUS" "$SITE_ROOT/home/.rollback-current"; mv -Tf -- "$SITE_ROOT/home/.rollback-current" "$CURRENT"; else rm -f -- "$CURRENT"; fi
  if nginx -t; then systemctl reload nginx; fi
  echo "Activation failed; restored previous configuration. Backup: $BACKUP" >&2
  exit 1
}
trap rollback ERR
install -d -m 755 "$SITE_ROOT/home" "$SITE_ROOT/home/releases" "$RELEASE"
cp -R -- "$PROJECT_ROOT/dist/." "$RELEASE/"
chown -R root:root "$RELEASE"
find "$RELEASE" -type d -exec chmod 755 {} +
find "$RELEASE" -type f -exec chmod 644 {} +
install -m 644 "$PROJECT_ROOT/deploy/security-headers.conf" "$HEADERS"
install -m 644 "$PROJECT_ROOT/deploy/routes.conf" "$ROUTES"
install -m 644 "$PROJECT_ROOT/deploy/yangarch.net.conf" "$CONFIG"
ln -s -- "$RELEASE" "$SITE_ROOT/home/.next-current-$STAMP"
mv -Tf -- "$SITE_ROOT/home/.next-current-$STAMP" "$CURRENT"
nginx -t
systemctl reload nginx
python3 -I "$PROJECT_ROOT/scripts/verify_live.py"
PDF_AFTER=$(sha256sum "$SITE_ROOT/portfolio/giseok-chung-data-portfolio.pdf" | cut -d ' ' -f 1)
[[ "$PDF_BEFORE" == "$PDF_AFTER" ]]
trap - ERR
printf 'Activation passed. Release: %s\nRollback backup: %s\n' "$RELEASE" "$BACKUP"
