#!/usr/bin/env python3
"""Non-root operator setup: pinned containers, private settings, no public ports."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request,build_opener,ProxyHandler
import uuid
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from analytics_proxy import container_address

UMAMI='ghcr.io/umami-software/umami:3.4.1@sha256:e69d1a7932e9dab0fc523edc7be1f3a53f3c261f57856fc2167ae74a8fc022e8'
POSTGRES='postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b'
CONFIG=Path.home()/'.config/yangarch-home-analytics'
STATE=Path.home()/'.local/share/yangarch-home-analytics'
DB='yangarch-home-umami-db'
APP='yangarch-home-umami'
OPENER=build_opener(ProxyHandler({}))


def private_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.parent.chmod(0o700)
    temp=path.with_suffix('.new')
    with temp.open('w') as handle:json.dump(data,handle)
    temp.chmod(0o600);os.replace(temp,path)


def compose(settings):
    return {'services':{
        'db':{'image':POSTGRES,'container_name':DB,'environment':{'POSTGRES_USER':'umami','POSTGRES_DB':'umami','POSTGRES_PASSWORD':settings['db_password']},'volumes':['database:/var/lib/postgresql/data'],'networks':['database'],'restart':'unless-stopped','mem_limit':'512m','pids_limit':200,'security_opt':['no-new-privileges:true'],'healthcheck':{'test':['CMD-SHELL','pg_isready -U umami -d umami'],'interval':'5s','timeout':'5s','retries':20}},
        'umami':{'image':UMAMI,'container_name':APP,'networks':['frontend','database'],'environment':{'DATABASE_URL':'postgresql://umami:'+settings['db_password']+'@db:5432/umami','APP_SECRET':settings['app_secret'],'TWO_FACTOR_ENCRYPTION_KEY':settings['two_factor_key'],'DISABLE_TELEMETRY':'1','CLIENT_IP_HEADER':'x-real-ip','SKIP_LOCATION_HEADERS':'1','SALT_ROTATION':'day'},'depends_on':{'db':{'condition':'service_healthy'}},'init':True,'restart':'unless-stopped','mem_limit':'768m','pids_limit':200,'cap_drop':['ALL'],'security_opt':['no-new-privileges:true']}},
        'networks':{'frontend':{},'database':{'internal':True}},'volumes':{'database':{}}}


def api(path,data=None,token=None,method=None):
    headers={'Content-Type':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    req=Request('http://'+container_address()+':3000'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
    with OPENER.open(req,timeout=20) as response:
        raw=response.read(1_000_000)
        return json.loads(raw) if raw else None


def login(username,password):
    return api('/api/auth/login',{'username':username,'password':password})


def prepare():
    os.umask(0o077)
    settings_file=CONFIG/'settings.json'
    if settings_file.exists():settings=json.loads(settings_file.read_text())
    else:
        for name in (APP,DB):
            if subprocess.run(['docker','container','inspect',name],capture_output=True).returncode==0:raise RuntimeError('Analytics container name already exists; review before setup')
        settings={'website_id':str(uuid.uuid4()),'db_password':secrets.token_hex(24),'app_secret':secrets.token_hex(32),'two_factor_key':secrets.token_hex(32),'admin_username':'owner','admin_password':secrets.token_urlsafe(30),'initialized':False}
        private_json(settings_file,settings)
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700);STATE.chmod(0o700)
    compose_file=CONFIG/'compose.json';private_json(compose_file,compose(settings))
    result=subprocess.run(['docker','compose','-p','yangarch-home-analytics','-f',str(compose_file),'up','-d'],capture_output=True,timeout=240)
    if result.returncode:raise RuntimeError('Analytics containers did not start; inspect private container logs')
    ready=False;deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        try:api('/api/heartbeat');ready=True;break
        except Exception:time.sleep(2)
    if not ready:raise RuntimeError('Analytics application readiness timed out')
    try:auth=login(settings['admin_username'],settings['admin_password'])
    except HTTPError:
        if settings['initialized']:raise RuntimeError('Stored admin credentials changed; review private setup') from None
        initial=login('admin','umami')
        api('/api/users/'+initial['user']['id'],{'username':settings['admin_username'],'password':settings['admin_password']},initial['token'])
        auth=login(settings['admin_username'],settings['admin_password'])
    websites=api('/api/websites',token=auth['token'])
    items=websites.get('data',[]) if isinstance(websites,dict) else websites
    if not any(item['id']==settings['website_id'] for item in items):
        api('/api/websites',{'id':settings['website_id'],'name':'yangarch.net','domain':'yangarch.net'},auth['token'])
    settings['initialized']=True;private_json(settings_file,settings)
    private_json(CONFIG/'login.json',{'username':settings['admin_username'],'password':settings['admin_password']})
    print(json.dumps({'ok':True,'umami_version':'3.4.1','postgres_version':'17.11','admin_bind':'127.0.0.1:4319','database_public_ports':False,'application_public_ports':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.parse_args()
    try:prepare()
    except Exception:raise SystemExit('Analytics setup failed. Credentials and diagnostic content were not printed; inspect the private runtime configuration.')
