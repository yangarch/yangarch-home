#!/usr/bin/env python3
"""Exercise the actual receiver and reload/rollback with non-root isolated nginx."""
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from publication import REQUIRED,canonical,nginx_routes,route_map
from publish_receiver import Receiver


def payload(label,record):
    files={name:(f'<html><head><title>{label}</title></head><body><h1>{label}</h1></body></html>'.encode() if name.endswith('.html') else b'example') for name in REQUIRED}
    if record:files['records/entry/deployment-check/index.html']=b'<html><head><title>Example</title></head><body><h1>Example</h1></body></html>'
    manifest={'files':{n:hashlib.sha256(raw).hexdigest() for n,raw in files.items()},'routes':route_map(files),'record_sources':[]}
    return {'protocol':1,'operation':'publish','manifest':manifest,'sizes':{n:len(raw) for n,raw in files.items()}},b''.join(files[n] for n in sorted(files))


work=Path(tempfile.mkdtemp(prefix='publisher-nginx-'))
base=work/'home';(base/'releases/old').mkdir(parents=True)
old_header,old_payload=payload('Previous',False)
from publication import receive_files
receive_files(io.BytesIO(old_payload),old_header,base/'releases/old')
(base/'current').symlink_to(base/'releases/old')
state=work/'state';state.mkdir()
headers=work/'headers.conf';headers.write_text('add_header Content-Security-Policy "default-src \'none\'; connect-src \'none\'" always;\nadd_header X-Frame-Options DENY always;\nadd_header X-Content-Type-Options nosniff always;\n')
routes=work/'routes.conf'
def isolated_routes(manifest):
    return nginx_routes(manifest).replace('/etc/nginx/snippets/yangarch-home-headers.conf',str(headers))
routes.write_text(isolated_routes(old_header['manifest']))
pdf=work/'portfolio.pdf';pdf.write_bytes(b'preserved PDF')
# The generated PDF location reads the existing public PDF only; never modifies it.
conf=work/'nginx.conf'
conf.write_text(f'''worker_processes 1;
error_log {work}/error.log warn;
pid {work}/nginx.pid;
events {{ worker_connections 128; }}
http {{
 include /etc/nginx/mime.types;
 access_log off;
 server {{
  listen 127.0.0.1:18082;
  server_name localhost;
  root {base}/current;
  disable_symlinks on from=$document_root;
  include {headers};
  include {routes};
 }}
}}
''')
(state/'baseline.json').write_bytes(canonical({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (conf,headers)}))
nginx=shutil.which('nginx')
cmd=[nginx,'-p',str(work)+'/', '-c',str(conf)]
proc=None

def request(path):
    connection=http.client.HTTPConnection('127.0.0.1',18082,timeout=5)
    connection.request('GET',path)
    response=connection.getresponse();body=response.read();values=dict(response.getheaders());status=response.status;connection.close()
    return status,values,body


def runner(args):
    # Replace only the fixture's routes include; production receiver does not do this.
    text=routes.read_text().replace('/etc/nginx/snippets/yangarch-home-headers.conf',str(headers));routes.write_text(text)
    subprocess.run(cmd+(['-t'] if args[-1]=='-t' else ['-s','reload']),check=True,capture_output=True)


def verifier(manifest):
    for path,name in manifest['routes'].items():
        deadline=time.monotonic()+10
        while True:
            status,values,body=request(path)
            if status==200 and hashlib.sha256(body).hexdigest()==manifest['files'][name]:break
            if time.monotonic()>deadline:raise RuntimeError('New nginx release not ready')
            time.sleep(.1)
        assert values['X-Frame-Options']=='DENY'
    for path in ('/.env','/scripts/editor.py','/api/posts','/editor/','/backup.zip'):
        assert request(path)[0]==404,path

try:
    subprocess.run(cmd+['-t'],check=True,capture_output=True)
    proc=subprocess.Popen(cmd+['-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    for _ in range(30):
        try:
            with socket.create_connection(('127.0.0.1',18082),timeout=.2):break
        except OSError:time.sleep(.1)
    receiver=Receiver(base,state,routes,conf,headers,pdf,runner=runner,verifier=verifier)
    header,body=payload('Published',True)
    result=receiver.publish(io.BytesIO(body),header)
    assert result['ok'] and request('/records/entry/deployment-check/')[0]==200
    current=os.readlink(base/'current');route_bytes=routes.read_bytes();status_bytes=(state/'status.json').read_bytes()
    def failed(_):raise RuntimeError('Intentional failure after reload')
    receiver.verifier=failed
    next_header,next_body=payload('Must rollback',False)
    try:receiver.publish(io.BytesIO(next_body),next_header)
    except RuntimeError as error:assert 'restored the previous release' in str(error)
    else:raise AssertionError('Failure was accepted')
    assert os.readlink(base/'current')==current
    assert routes.read_bytes()==route_bytes
    assert (state/'status.json').read_bytes()==status_bytes
    verifier(header['manifest'])
    assert pdf.read_bytes()==b'preserved PDF'
    print(json.dumps({'publish':'passed','dynamic_record':'200','private_paths':'404','nginx_reload':'passed','forced_failure_rollback':'passed','pdf_preserved':True}))
finally:
    if proc:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
    shutil.rmtree(work)
