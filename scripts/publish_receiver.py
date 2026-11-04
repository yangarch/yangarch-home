#!/usr/bin/python3
"""Root-owned, no-argument receiver. Only validated static data arrive on stdin."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

# -I excludes cwd/PYTHONPATH. Only explicitly add this root-owned installation.
sys.path.insert(0,str(Path(__file__).resolve().parent))
from publication import PROTOCOL, HEADER_LIMIT, canonical, decode, identifier, nginx_routes, receive_files, validate_header

ENV={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C'}
BASE=Path('/var/www/yangarch.net/home')
STATE=Path('/var/lib/yangarch-home')
CONFIG=Path('/etc/nginx/sites-available/yangarch.net')
ROUTES=Path('/etc/nginx/snippets/yangarch-home-routes.conf')
HEADERS=Path('/etc/nginx/snippets/yangarch-home-headers.conf')
PDF=Path('/var/www/yangarch.net/portfolio/giseok-chung-data-portfolio.pdf')


def atomic(path,raw,mode=0o600):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as handle:
        temp=Path(handle.name);handle.write(raw);handle.flush();os.fsync(handle.fileno())
    try:
        temp.chmod(mode);os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def swap_link(path,target):
    temp=path.with_name('.publish-current-'+uuid.uuid4().hex)
    try:temp.symlink_to(target);os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def command(args):
    subprocess.run(args,check=True,timeout=20,env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)


def request(path):
    result=subprocess.run(['/usr/bin/curl','-q','--http1.1','--silent','--show-error','--noproxy','*','--resolve','yangarch.net:443:127.0.0.1','--connect-timeout','2','--max-time','8','--include','https://yangarch.net'+path],capture_output=True,check=True,timeout=10,env=ENV)
    headers,separator,body=result.stdout.partition(b'\r\n\r\n')
    if not separator:raise ValueError('Unparseable TLS response')
    lines=headers.decode('iso-8859-1').splitlines()
    status=int(lines[0].split()[1]);values={}
    for line in lines[1:]:
        key,sep,value=line.partition(':')
        if sep:values[key.lower()]=value.strip()
    return status,values,body


def verify_live(manifest,probe=request,sleep=time.sleep,clock=time.monotonic):
    required={'content-security-policy','strict-transport-security','x-frame-options','x-content-type-options'}
    for route,name in manifest['routes'].items():
        if route=='/index.html':continue
        deadline=clock()+15
        while True:
            status,headers,body=probe(route)
            if status==200 and hashlib.sha256(body).hexdigest()==manifest['files'][name]:break
            if clock()>=deadline:raise RuntimeError('New public content did not become ready')
            sleep(.25)
        if not required<=set(headers) or headers['x-frame-options'].lower()!='deny' or headers['x-content-type-options'].lower()!='nosniff' or "connect-src 'none'" not in headers['content-security-policy']:raise RuntimeError('Missing security header')
        sleep(.12)
    for route in ('/.env','/.git/config','/editor/','/api/posts','/backup.zip','/content/records.json','/scripts/editor.py'):
        if probe(route)[0]!=404:raise RuntimeError('Private path is exposed')
        sleep(.12)
    status,headers,_=probe('/portfolio/giseok-chung-data-portfolio.pdf')
    if status!=200 or 'noindex' not in headers.get('x-robots-tag',''):raise RuntimeError('Existing PDF route changed')


class Receiver:
    def __init__(self,base=BASE,state=STATE,routes=ROUTES,config=CONFIG,headers=HEADERS,pdf=PDF,runner=command,verifier=verify_live):
        self.base=Path(base);self.state=Path(state);self.routes=Path(routes);self.config=Path(config);self.headers=Path(headers);self.pdf=Path(pdf)
        self.runner=runner;self.verifier=verifier

    def status(self):
        path=self.state/'status.json'
        latest=decode(path.read_bytes()) if path.exists() else None
        return {'ok':True,'protocol':PROTOCOL,'configured':True,'latest':latest}

    def activate(self,release,manifest):
        current=self.base/'current'
        if not current.is_symlink() or not self.routes.is_file() or not self.pdf.is_file():raise ValueError('Unexpected production layout')
        baseline=decode((self.state/'baseline.json').read_bytes())
        for path in (self.config,self.headers):
            if hashlib.sha256(path.read_bytes()).hexdigest()!=baseline[str(path)]:raise ValueError('Server configuration changed; review setup again')
        previous=os.readlink(current);old_routes=self.routes.read_bytes();pdf_hash=hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        status_file=self.state/'status.json';old_status=status_file.read_bytes() if status_file.exists() else None
        backup=self.state/'backups'/release.name
        backup.mkdir(parents=True,mode=0o700)
        atomic(backup/'routes.conf',old_routes)
        atomic(backup/'previous.json',canonical({'current':previous,'status':decode(old_status) if old_status else None}))
        try:
            atomic(self.routes,nginx_routes(manifest).encode(),0o644)
            swap_link(current,str(release))
            self.runner(['/usr/sbin/nginx','-t'])
            self.runner(['/bin/systemctl','reload','nginx'])
            self.verifier(manifest)
            if hashlib.sha256(self.pdf.read_bytes()).hexdigest()!=pdf_hash:raise RuntimeError('PDF content changed')
            receipt={'manifest_id':identifier(manifest),'release':release.name,'published_at':datetime.now(timezone.utc).isoformat()}
            atomic(status_file,canonical(receipt))
            return {'ok':True,'protocol':PROTOCOL,'receipt':receipt,'url':'https://yangarch.net/records/'}
        except BaseException as error:
            try:
                atomic(self.routes,old_routes,0o644);swap_link(current,previous)
                if old_status is None:status_file.unlink(missing_ok=True)
                else:atomic(status_file,old_status)
                self.runner(['/usr/sbin/nginx','-t']);self.runner(['/bin/systemctl','reload','nginx'])
            except BaseException:
                raise RuntimeError('Activation failed and rollback needs administrator attention') from error
            raise RuntimeError('Activation failed; restored the previous release') from error

    def publish(self,stream,header):
        validate_header(header)
        release=self.base/'releases'/('web-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12])
        release.mkdir(mode=0o755)
        manifest=receive_files(stream,header,release)
        return self.activate(release,manifest)


def interrupted(*_):raise RuntimeError('Publishing interrupted')


def main():
    if os.geteuid()!=0 or len(sys.argv)!=1:raise ValueError('Root-owned receiver takes no arguments')
    os.umask(0o077)
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGHUP,interrupted);signal.signal(signal.SIGALRM,interrupted);signal.alarm(180)
    raw=sys.stdin.buffer.readline(HEADER_LIMIT+1)
    if not raw.endswith(b'\n') or len(raw)>HEADER_LIMIT:raise ValueError('Invalid protocol header')
    header=decode(raw);validate_header(header)
    receiver=Receiver()
    if header['operation']=='status':return receiver.status()
    with (STATE/'publish.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('Another publication is running') from None
        return receiver.publish(sys.stdin.buffer,header)


if __name__=='__main__':
    try:
        result=main();print(json.dumps(result),flush=True)
    except Exception as error:
        # Fixed structural messages only; no uploaded content, tokens, or tracebacks.
        message=str(error) if isinstance(error,(ValueError,RuntimeError)) else 'Server publishing failed'
        print(json.dumps({'ok':False,'error':message}),flush=True);sys.exit(1)
