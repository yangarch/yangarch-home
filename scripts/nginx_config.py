#!/usr/bin/env python3
"""Render exact-path routing; unknown paths never reach the filesystem."""
import json
from pathlib import Path
from analytics_nginx import HTTP_CONFIG, COLLECTOR, LOG_LINE
ROOT=Path(__file__).resolve().parents[1]

def routes(headers='/etc/nginx/snippets/yangarch-home-headers.conf', legacy=True):
    data=json.loads((ROOT/'artifacts/manifest.json').read_text())
    blocks=[]
    for route,file in data['routes'].items():
        cache='1h' if route.startswith('/assets/') else '-1'
        blocks.append(f'location = {route} {{\n    try_files /{file} =404;\n    expires {cache};\n}}')
    if legacy:
        blocks.append('''location = /portfolio/giseok-chung-data-portfolio.pdf {
    root /var/www/yangarch.net;
    try_files $uri =404;
    include '''+headers+''';
    add_header Content-Disposition 'inline; filename="giseok-chung-data-portfolio.pdf"';
    add_header X-Robots-Tag "noindex, nofollow, noarchive" always;
}''')
    blocks.append('location / { return 404; }')
    return '\n\n'.join(blocks)+'\n'

def production():
    return HTTP_CONFIG+'''# Affects only yangarch.net; included by the existing nginx http context.
limit_req_zone $binary_remote_addr zone=yangarch_home:10m rate=10r/s;
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name yangarch.net;
'''+LOG_LINE+'''    root /var/www/yangarch.net/home/current;
    server_tokens off;
    autoindex off;
    disable_symlinks on from=$document_root;
    client_max_body_size 1k;
    client_header_timeout 10s;
    client_body_timeout 10s;
    keepalive_timeout 15s;
    limit_req zone=yangarch_home burst=40 nodelay;
    limit_req_status 429;
    if ($home_method_allowed = 0) { return 405; }
    include /etc/nginx/snippets/yangarch-home-headers.conf;
    ssl_certificate /etc/letsencrypt/live/yangarch.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yangarch.net/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
'''+COLLECTOR+'''    include /etc/nginx/snippets/yangarch-home-routes.conf;
}
server {
    listen 80;
    listen [::]:80;
    server_name yangarch.net;
'''+LOG_LINE+'''    server_tokens off;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
        try_files $uri =404;
    }
    location / { return 301 https://yangarch.net$request_uri; }
}
'''

if __name__=='__main__':
    (ROOT/'deploy/routes.conf').write_text(routes())
    (ROOT/'deploy/yangarch.net.conf').write_text(production())
    print('Rendered exact-path nginx routes and apex-only TLS configuration.')
