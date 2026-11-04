"""Privacy boundaries, request validation, bounded retention and private access."""
from datetime import datetime,timedelta,timezone
import copy
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from analytics_policy import origin_only,validate_event,umami_event
from analytics_gateway import make_server
from analytics_maintenance import read_logs,retention_sql
from analytics_runtime import compose
from analytics_client import Statistics

WEBSITE='00000000-0000-4000-8000-000000000001'


def event(**changes):
    return dict(event='pageview',path='/',screen='1920x1080',language='ko-KR',timezone='Asia/Seoul',referrer='https://example.com/private?token=discard',campaign={},data={})|changes


class PolicyTest(unittest.TestCase):
    def test_reduces_referrer_and_rejects_personal_or_spoofed_fields(self):
        result=umami_event(validate_event(event()),WEBSITE,'Public title')
        self.assertEqual(result['payload']['referrer'],'https://example.com')
        self.assertEqual(result['payload']['hostname'],'yangarch.net')
        self.assertNotIn('token',json.dumps(result))
        for key,value in [('ip','8.8.8.8'),('userAgent','forged'),('timestamp',1),('id','persistent-id'),('title','untrusted'),('password','untrusted')]:
            with self.subTest(key=key),self.assertRaises(ValueError):validate_event(event(**{key:value}))
        for value in ['http://127.0.0.1/secret','https://user:pass@example.com/a','http://intranet/a','javascript:alert(1)']:
            self.assertEqual(origin_only(value),'')

    def test_paths_campaigns_and_event_values_are_bounded(self):
        for path in ['/','/privacy/','/projects/training/','/records/','/records/entry/example-1/','/records/swimming/']:
            self.assertEqual(validate_event(event(path=path))['path'],path)
        for value in [event(path='/.env'),event(path='/?token=secret'),event(path='/records/../../secret/'),event(campaign={'gclid':'ad-id'}),event(campaign={'utm_source':'name@example.com'}),event(event='scroll',data={'percent':99}),event(event='active',data={'seconds':1}),event(event='filter',data={'category':'personal free text'}),event(event='link',data={'kind':'service','target':'https://unknown.example'})]:
            with self.subTest(value=value),self.assertRaises(ValueError):validate_event(value)
        good=validate_event(event(event='link',data={'kind':'service','target':'poogiegram'},campaign={'utm_source':'naver'}))
        result=umami_event(good,WEBSITE,'Title')['payload']
        self.assertEqual(result['name'],'link');self.assertEqual(result['url'],'/?utm_source=naver')

    def test_database_is_internal_and_admin_is_loopback_only(self):
        config=compose(dict(db_password='test',app_secret='test',two_factor_key='test'))
        self.assertNotIn('ports',config['services']['db'])
        self.assertTrue(config['networks']['database']['internal'])
        self.assertNotIn('ports',config['services']['umami'])
        self.assertEqual(config['services']['umami']['networks'],['frontend','database'])
        self.assertEqual(config['services']['umami']['environment']['SALT_ROTATION'],'day')


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'settings.json').write_text(json.dumps({'website_id':WEBSITE}))
        (self.root/'index.html').write_text('<h1>Actual public title</h1>')
        self.server=make_server(self.root/'settings.json',port=0);self.server.public=self.root
        self.forwarded=[]
        owner=self
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"cache":"not forwarded to browser"}'
        class Upstream:
            def open(self,request,timeout):owner.forwarded.append(request);return Response()
        self.server.opener=Upstream()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()
    def request(self,payload=None,headers=None,path='/collect'):
        base={'Origin':'https://yangarch.net','Content-Type':'application/json','X-Real-IP':'203.0.113.10','User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36'}
        base.update(headers or {})
        body=json.dumps(payload if payload is not None else event()).encode()
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        c.request('POST',path,body,base);r=c.getresponse();value=(r.status,r.read());c.close();return value
    def test_forwards_only_validated_payload_and_does_not_return_upstream_state(self):
        self.assertEqual(self.request(),(204,b''))
        req=self.forwarded[0];data=json.loads(req.data)['payload']
        self.assertEqual(data['title'],'Actual public title')
        self.assertNotIn('ip',data);self.assertEqual(req.get_header('X-real-ip'),'203.0.113.10')
        self.assertEqual(req.full_url,'http://127.0.0.1:4319/api/send')
    def test_rejects_spoofing_cross_origin_unknown_pages_and_oversized_data(self):
        for payload,headers,status in [(event(ip='8.8.8.8'),{},400),(event(),{'Origin':'https://evil.example'},403),(event(),{'X-Real-IP':'not-an-ip'},400),(event(path='/projects/unknown/'),{},400),(event(event='link',data={'kind':'project','target':'unknown'}),{},400),(event(referrer='a'*3000),{},413),(event(),{'Transfer-Encoding':'chunked'},400)]:
            with self.subTest(status=status):self.assertEqual(self.request(payload,headers)[0],status)
        self.assertFalse(self.forwarded)
        self.assertEqual(self.request(path='/api/users')[0],404)
    def test_privacy_signals_and_bots_do_not_reach_analytics_database(self):
        for headers in [{'DNT':'1'},{'Sec-GPC':'1'},{'User-Agent':'ExampleBot/1.0'}]:
            self.assertEqual(self.request(headers=headers)[0],204)
        self.assertFalse(self.forwarded)


class RetentionTest(unittest.TestCase):
    def test_raw_logs_expire_owner_and_bots_are_excluded_from_estimates(self):
        now=datetime(2026,11,5,tzinfo=timezone.utc)
        row=dict(time=now.isoformat(),ip='203.0.113.1',path='/',status=200,ua='Browser',client='visitor',excluded='0')
        rows=[row,row,row|{'excluded':'1'},row|{'client':'automated'},row|{'status':404,'path':'/.env'},row|{'time':(now-timedelta(days=15)).isoformat()}]
        with tempfile.TemporaryDirectory() as temp:
            log=Path(temp)/'access.log';log.write_text('\n'.join(json.dumps(x) for x in rows))
            result=read_logs([log],now)
            day=result['days']['2026-11-05']
            self.assertEqual(day['requests'],5);self.assertEqual(day['page_requests'],2)
            self.assertEqual(day['estimated_visitors'],1);self.assertEqual(day['automated'],1)
            self.assertEqual(day['excluded'],1);self.assertEqual(day['errors'],1)
            self.assertEqual(len(result['latest']),5)
            limited=read_logs([log],now,max_bytes=len(json.dumps(row))+2)
            self.assertTrue(limited['truncated']);self.assertEqual(limited['days']['2026-11-05']['estimated_visitors'],1)
    def test_sql_is_site_scoped_and_keeps_nonidentifying_aggregates(self):
        text=retention_sql(WEBSITE)
        self.assertIn("interval '90 days'",text);self.assertIn('::date-365',text)
        for line in text.splitlines():
            if line.startswith('DELETE FROM '):self.assertIn("website_id='"+WEBSITE+"'",line)
        with self.assertRaises(ValueError):retention_sql("x'; DROP TABLE website;--")


class PrivateAccessTest(unittest.TestCase):
    def test_report_uses_only_fixed_read_only_remote_command(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'.local/editor').mkdir(parents=True)
            config=root/'.local/editor/publish.json';config.write_text('{"ssh_host":"main-server"}')
            client=Statistics(root)
            response=dict(days={},latest=[],browser_daily=[],generated_at='2026-11-05T00:00:00Z')
            with patch('analytics_client.subprocess.run',return_value=subprocess.CompletedProcess([],0,json.dumps(response).encode(),b'')) as run:
                self.assertEqual(client.report(),response)
                args=run.call_args.args[0]
                self.assertEqual(args[-2:],['main-server','/usr/bin/python3 -I /opt/yangarch-home-analytics/analytics_report.py'])
                self.assertIn('StrictHostKeyChecking=yes',args)
            config.write_text('{"ssh_host":"main-server; arbitrary-command"}')
            with self.assertRaises(ValueError):client.host()


if __name__=='__main__':unittest.main()
