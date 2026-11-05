#!/usr/bin/env python3
"""Linux/non-root integration; private containers + disposable nginx and fixtures."""
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.request import Request,build_opener,ProxyHandler

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from analytics_gateway import make_server
from analytics_maintenance import sql,retention_sql,read_logs
from analytics_nginx import HTTP_CONFIG,COLLECTOR,LOG_LINE
from analytics_proxy import Server as Relay


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def retention_test():
    site,other,old_session,new_session,other_session=[str(uuid.uuid4()) for _ in range(5)]
    event_old,event_new,event_other=[str(uuid.uuid4()) for _ in range(3)]
    work=f"""BEGIN;
INSERT INTO website (website_id,name) VALUES ('{site}','Retention fixture'),('{other}','Other fixture');
INSERT INTO session (session_id,website_id,created_at) VALUES ('{old_session}','{site}',now()-interval '100 days'),('{new_session}','{site}',now()-interval '1 day'),('{other_session}','{other}',now()-interval '100 days');
INSERT INTO website_event (event_id,website_id,session_id,visit_id,created_at,url_path,event_type) VALUES
('{event_old}','{site}','{old_session}','{old_session}',now()-interval '100 days','/old/',1),
('{event_new}','{site}','{new_session}','{new_session}',now()-interval '1 day','/recent/',1),
('{event_other}','{other}','{other_session}','{other_session}',now()-interval '100 days','/untouched/',1);
"""
    body=retention_sql(site).removeprefix('BEGIN;').rsplit('COMMIT;',1)[0]
    # The same production SQL executes twice in a rollback-only database transaction.
    work+=body+f"""INSERT INTO home_analytics.daily VALUES ('{site}',current_date-400,'page','/expired/',1),('{site}',current_date-100,'page','/historical/',5);
"""+body+f"""DO $$ BEGIN
ASSERT NOT EXISTS (SELECT 1 FROM website_event WHERE event_id='{event_old}');
ASSERT EXISTS (SELECT 1 FROM website_event WHERE event_id='{event_new}');
ASSERT EXISTS (SELECT 1 FROM website_event WHERE event_id='{event_other}');
ASSERT NOT EXISTS (SELECT 1 FROM session WHERE session_id='{old_session}');
ASSERT NOT EXISTS (SELECT 1 FROM home_analytics.daily WHERE website_id='{site}' AND label='/expired/');
ASSERT EXISTS (SELECT 1 FROM home_analytics.daily WHERE website_id='{site}' AND label='/historical/' AND value=5);
ASSERT EXISTS (SELECT 1 FROM home_analytics.daily WHERE website_id='{site}' AND label='/recent/' AND value=1);
END $$;
ROLLBACK;
"""
    sql(work)


def collection_test():
    site=str(uuid.uuid4());work=Path(tempfile.mkdtemp(prefix='home-analytics-qa-'))
    relay=Relay(('127.0.0.1',0),__import__('analytics_proxy').Handler)
    relay_thread=threading.Thread(target=relay.serve_forever,daemon=True);relay_thread.start()
    sql(f"INSERT INTO website (website_id,name,domain) VALUES ('{site}','Collection fixture','example.com');")
    proc=None;gateway=None
    try:
        settings=work/'settings.json';settings.write_text(json.dumps({'website_id':site}))
        gateway=make_server(settings,0);gateway.public=ROOT/'dist'
        class TestUpstream:
            def open(self,request,timeout):
                assert request.full_url=='http://127.0.0.1:4319/api/send'
                target='http://127.0.0.1:'+str(relay.server_address[1])+'/api/send'
                return build_opener(ProxyHandler({})).open(Request(target,data=request.data,headers=dict(request.header_items()),method='POST'),timeout=timeout)
        gateway.opener=TestUpstream()
        thread=threading.Thread(target=gateway.serve_forever,daemon=True);thread.start()
        http_config=HTTP_CONFIG.replace('127.0.0.1 0; ::1 0;','127.0.0.1 1; ::1 1;')
        log_line=LOG_LINE.replace('/var/log/yangarch-home/access.log',str(work/'access.log'))
        collector=COLLECTOR.replace('127.0.0.1:4318',f'127.0.0.1:{gateway.server_port}')
        port=free_port();conf=work/'nginx.conf'
        conf.write_text(f'''worker_processes 1;
error_log {work}/error.log warn;
pid {work}/nginx.pid;
events {{ worker_connections 128; }}
http {{ include /etc/nginx/mime.types;
{http_config}
server {{ listen 127.0.0.1:{port}; server_name localhost;
root {ROOT}/dist;
{log_line}
if ($home_method_allowed = 0) {{ return 405; }}
include {ROOT}/deploy/security-headers.conf;
{collector}
include {ROOT}/deploy/routes.conf;
}} }}''')
        subprocess.run(['nginx','-t','-p',str(work)+'/', '-c',str(conf)],check=True,capture_output=True)
        proc=subprocess.Popen(['nginx','-p',str(work)+'/', '-c',str(conf),'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def request(path,method='GET',data=None,headers=None):
            c=http.client.HTTPConnection('127.0.0.1',port,timeout=15)
            c.request(method,path,json.dumps(data).encode() if data else None,headers or {})
            r=c.getresponse();result=r.status,dict(r.getheaders()),r.read();c.close();return result
        for _ in range(30):
            try:request('/');break
            except OSError:time.sleep(.1)
        private_marker='QA_QUERY_MUST_NOT_PERSIST'
        status,headers,body=request('/?token='+private_marker,headers={'Referer':'https://example.com/private?'+private_marker,'User-Agent':'Browser "quote" test'})
        assert status==200 and "connect-src 'self'" in headers['Content-Security-Policy']
        for path in ['/api/users','/api/websites','/admin/','/analytics/','/.env','/.local/editor/analytics-login.json']:
            assert request(path)[0]==404,path
        assert request('/','POST')[0]==405
        assert request('/__analytics')[0]==405
        payload=dict(event='pageview',path='/',screen='1920x1080',language='ko-KR',timezone='Asia/Seoul',referrer='https://example.com/private?'+private_marker,campaign={},data={})
        headers={'Origin':'https://yangarch.net','Content-Type':'application/json','User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36','X-Real-IP':'8.8.8.8','X-Forwarded-For':'8.8.8.8'}
        assert request('/__analytics','POST',payload,headers)[0]==204
        assert request('/__analytics','POST',payload,headers|{'Origin':'https://evil.example'})[0]==403
        assert request('/__analytics','POST',payload,headers|{'DNT':'1'})[0]==204
        time.sleep(.3)
        rows=json.loads(sql(f"SELECT COALESCE(json_agg(t),'[]'::json) FROM (SELECT url_path,url_query,referrer_domain,referrer_path,referrer_query FROM website_event WHERE website_id='{site}') t;"))
        assert len(rows)==1,rows
        assert rows[0]['referrer_domain']=='example.com'
        assert private_marker not in json.dumps(rows)
        raw=(work/'access.log').read_text();assert private_marker not in raw
        logs=[json.loads(line) for line in raw.splitlines()]
        visits=[row for row in logs if row['ua']=='Browser "quote" test']
        assert len(visits)==1 and visits[0]['path']=='/', 'try_files replaced the original requested path'
        fixture=work/'visitor.log';fixture.write_text(json.dumps(visits[0])+'\n')
        counted=read_logs([fixture])
        assert sum(row['page_requests'] for row in counted['days'].values())==1
        assert sum(row['estimated_visitors'] for row in counted['days'].values())==1
        assert all(row['ip']=='127.0.0.1' for row in logs), 'Caller-supplied IP was trusted'
        assert any(row['referrer']=='https://example.com' for row in logs)
        statuses=[request('/__analytics','POST',payload,headers|{'DNT':'1'})[0] for _ in range(30)]
        assert 429 in statuses,'Collector rate limit was not enforced'
        print('PASS: nginx routing/log redaction, fixed-IP forwarding, actual Umami collection, private-path denial, privacy signals and rate limits.')
    finally:
        if proc:
            proc.terminate();proc.wait(5)
        if gateway:gateway.shutdown();gateway.server_close()
        relay.shutdown();relay.server_close();relay_thread.join(2)
        sql(f"BEGIN; DELETE FROM event_data WHERE website_id='{site}'; DELETE FROM website_event WHERE website_id='{site}'; DELETE FROM session WHERE website_id='{site}'; DELETE FROM website WHERE website_id='{site}'; COMMIT;")
        shutil.rmtree(work)


if __name__=='__main__':
    retention_test();print('PASS: actual PostgreSQL 90-day detail expiration, 365-day aggregates, repeat execution and other-site isolation (rolled back).')
    collection_test()
