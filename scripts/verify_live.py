#!/usr/bin/env python3
"""Wait for the new TLS response, then check every public page and boundary."""
from pathlib import Path
import json
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
REQUIRED=['content-security-policy:','x-content-type-options: nosniff','x-frame-options: deny','strict-transport-security:']
CURL=['curl','--silent','--show-error','--noproxy','*','--resolve','yangarch.net:443:127.0.0.1']


def home_response():
    result=subprocess.run(CURL+['--max-time','2','--write-out','\n%{http_code}','https://yangarch.net/'],capture_output=True,check=True)
    body,separator,status=result.stdout.rpartition(b'\n')
    if not separator or not status.isdigit():raise RuntimeError('Could not parse homepage response')
    return int(status),body


def wait_for_home(expected, probe=home_response, timeout=15, clock=time.monotonic, sleep=time.sleep):
    deadline=clock()+timeout
    last='no response'
    while True:
        status,body=probe()
        if status==200 and body==expected:
            return
        last=f'HTTP {status}'+(' with different homepage content' if status==200 else '')
        if clock()>=deadline:
            raise RuntimeError('New release did not become ready: '+last)
        sleep(.25)


def verify(root=ROOT):
    manifest=json.loads((root/'artifacts/manifest.json').read_text())
    expected=(root/'dist/index.html').read_bytes()
    # Reload can return before new workers start serving requests.
    wait_for_home(expected)
    print('PASS: new release is serving the checked homepage.',flush=True)
    checks=[(path,200) for path,file in manifest['routes'].items() if file.endswith('.html') and path!='/index.html']
    checks += [('/assets/site.css',200),('/.env',404),('/.git/config',404),('/backup.zip',404),('/content/public.json',404),('/content/records.json',404),('/records/.env',404),('/records/entry/not-published/',404),('/assets/site.js.map',404),('/portfolio/giseok-chung-data-portfolio.pdf',200)]
    for path,status in checks:
        result=subprocess.run(CURL+['--max-time','6','--head','https://yangarch.net'+path],capture_output=True,text=True,check=True)
        headers=result.stdout.lower()
        actual=int(headers.splitlines()[0].split()[1])
        if actual!=status:raise RuntimeError(f'{path}: expected HTTP {status}, got HTTP {actual}')
        if not all(key in headers for key in REQUIRED):raise RuntimeError(path+': missing security header')
        print(f'PASS {path} -> {actual}',flush=True)
        time.sleep(.12)
    status,body=home_response()
    if status!=200 or body!=expected:raise RuntimeError('Live homepage changed during verification')
    print('PASS: live home matches the checked artifact; existing PDF route responds.',flush=True)


if __name__=='__main__':verify()
