#!/usr/bin/env python3
"""Check apex response and blockers through local nginx with TLS validation."""
from pathlib import Path
import json,subprocess,time
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'artifacts/manifest.json').read_text())
required=['content-security-policy:','x-content-type-options: nosniff','x-frame-options: deny','strict-transport-security:']
checks=[(path,200) for path,file in manifest['routes'].items() if file.endswith('.html') and path!='/index.html']
checks += [('/assets/site.css',200),('/.env',404),('/.git/config',404),('/backup.zip',404),('/content/public.json',404),('/content/records.json',404),('/records/.env',404),('/records/entry/not-published/',404),('/assets/site.js.map',404),('/portfolio/giseok-chung-data-portfolio.pdf',200)]
for path,expected in checks:
 r=subprocess.run(['curl','--silent','--show-error','--noproxy','*','--resolve','yangarch.net:443:127.0.0.1','--max-time','6','--head','https://yangarch.net'+path],capture_output=True,text=True,check=True)
 headers=r.stdout.lower()
 assert int(headers.splitlines()[0].split()[1])==expected,(path,'unexpected status')
 assert all(k in headers for k in required),(path,'missing security header')
 print(f'PASS {path} -> {expected}')
 time.sleep(.12)
r=subprocess.run(['curl','--silent','--show-error','--noproxy','*','--resolve','yangarch.net:443:127.0.0.1','--max-time','6','https://yangarch.net/'],capture_output=True,check=True)
assert r.stdout==(ROOT/'dist/index.html').read_bytes(),'Live page differs from prepared artifact'
print('PASS: live home matches the checked artifact; existing PDF route responds.')
