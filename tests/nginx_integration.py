#!/usr/bin/env python3
"""Non-root, loopback-only nginx test with synthetic malicious files."""
import hashlib,http.client,json,shutil,socket,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from nginx_config import routes
manifest=json.loads((ROOT/'artifacts/manifest.json').read_text())
work=Path(tempfile.mkdtemp(prefix='yangarch-nginx-'))
public=work/'public'
shutil.copytree(ROOT/'dist',public)
for rel in ['.env','.env.production','.git/config','assets/.env','backup.zip','package.json','assets/site.js.map','projects/training/.env','records/.env','content/records.json','content/private.json']:
 p=public/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('QA_SENTINEL_NOT_A_SECRET')
headers=ROOT/'deploy/security-headers.conf'
(work/'routes.conf').write_text((ROOT/'deploy/routes.conf').read_text().replace('/etc/nginx/snippets/yangarch-home-headers.conf',str(headers)))
conf=f'''worker_processes 1;
error_log {work}/error.log warn;
pid {work}/nginx.pid;
events {{ worker_connections 128; }}
http {{
 include /etc/nginx/mime.types;
 access_log off;
 limit_req_zone $binary_remote_addr zone=qa_home:1m rate=10r/s;
 server {{
  listen 127.0.0.1:18081;
  server_name localhost;
  root {public};
  server_tokens off;
  autoindex off;
  disable_symlinks on from=$document_root;
  client_max_body_size 1k;
  limit_req zone=qa_home burst=40 nodelay;
  limit_req_status 429;
  if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
  include {headers};
  include {work}/routes.conf;
 }}
}}
'''
(work/'nginx.conf').write_text(conf)
subprocess.run(['nginx','-t','-p',str(work)+'/', '-c',str(work/'nginx.conf')],check=True,capture_output=True)
proc=subprocess.Popen(['nginx','-p',str(work)+'/', '-c',str(work/'nginx.conf'),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
results=[]
def request(path,method='GET',throttle=True):
 if throttle:time.sleep(.12)
 c=http.client.HTTPConnection('127.0.0.1',18081,timeout=5)
 c.request(method,path)
 r=c.getresponse();body=r.read();hdr={k.lower():v for k,v in r.getheaders()};status=r.status;c.close()
 return status,hdr,body
try:
 for _ in range(30):
  try:
   with socket.create_connection(('127.0.0.1',18081),timeout=.2):break
  except OSError:
   if proc.poll() is not None:raise RuntimeError(proc.stderr.read().decode())
   time.sleep(.1)
 for route,file in manifest['routes'].items():
  status,hdr,body=request(route)
  assert status==200,(route,status)
  assert hashlib.sha256(body).hexdigest()==manifest['files'][file],route
  assert hdr.get('x-content-type-options')=='nosniff' and hdr.get('x-frame-options')=='DENY',route
  assert "frame-ancestors 'none'" in hdr.get('content-security-policy',''),route
  results.append({'kind':'public','path':route,'status':status})
 blocked=['/.env','/.env.production','/.git/config','/assets/.env','/backup.zip','/package.json','/assets/site.js.map','/projects/training/.env','/content/private.json','/content/records.json','/records/.env','/records/entry/not-published/','/deploy/install.sh','/assets/','/projects/','/%2eenv','//.env','/assets/../.env','/assets/%2e%2e/.env','/..%2f..%2fetc/passwd','/%00','/does-not-exist']
 for path in blocked:
  status,hdr,body=request(path)
  assert status in {400,404},(path,status)
  assert b'QA_SENTINEL' not in body,path
  if status==404:assert hdr.get('x-frame-options')=='DENY' and hdr.get('content-security-policy'),path
  results.append({'kind':'blocked','path':path,'status':status})
 for method in ['POST','PUT','DELETE','OPTIONS','TRACE']:
  status,hdr,body=request('/',method)
  assert status==405,(method,status)
  assert hdr.get('content-security-policy'),method
  results.append({'kind':'method','method':method,'status':status})
 status,hdr,body=request('/','HEAD')
 assert status==200 and not body
 status,hdr,body=request('/portfolio/giseok-chung-data-portfolio.pdf')
 assert status==200 and 'noindex' in hdr.get('x-robots-tag','')
 assert hdr.get('content-security-policy') and hdr.get('x-frame-options')=='DENY'
 rate_statuses=[request('/',throttle=False)[0] for _ in range(80)]
 assert 429 in rate_statuses,'Rate limit not enforced'
 result={'public_routes':len(manifest['routes']),'blocked_paths':len(blocked),'rejected_methods':5,'legacy_pdf':'200 with security headers','rate_limit':'429 observed','cases':results}
 (ROOT/'artifacts/nginx-qa.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False))
finally:
 proc.terminate()
 try:proc.wait(timeout=5)
 except subprocess.TimeoutExpired:proc.kill();proc.wait()
 shutil.rmtree(work)
