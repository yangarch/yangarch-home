#!/usr/bin/env python3
"""Loopback-only validation gateway. Public nginx can proxy only POST /collect."""
import argparse
from functools import lru_cache
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import re
import sys
from urllib.request import Request,build_opener,ProxyHandler
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
from analytics_policy import MAX_BODY,validate_event,umami_event

BOT=re.compile(r'bot|crawler|spider|curl|wget|python|headless|uptime|monitor',re.I)


class Title(HTMLParser):
    def __init__(self):super().__init__();self.active=False;self.value=''
    def handle_starttag(self,tag,attrs):
        if tag=='h1':self.active=True
    def handle_endtag(self,tag):
        if tag=='h1':self.active=False
    def handle_data(self,data):
        if self.active:self.value+=data


@lru_cache(maxsize=256)
def page_title(path,mtime):
    parser=Title();parser.feed(Path(path).read_text()[:2_000_000]);return parser.value.strip()[:200]


class Handler(BaseHTTPRequestHandler):
    server_version='AnalyticsGateway'
    def log_message(self,*_):pass
    def reply(self,status):
        self.send_response(status);self.send_header('Content-Length','0');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
    def do_GET(self):self.reply(204 if self.path=='/health' else 404)
    def do_POST(self):
        if self.path!='/collect':return self.reply(404)
        if self.headers.get('Origin')!='https://yangarch.net':return self.reply(403)
        if self.headers.get('Content-Type')!='application/json' or self.headers.get('Transfer-Encoding'):return self.reply(400)
        try:length=int(self.headers.get('Content-Length','-1'))
        except ValueError:return self.reply(400)
        if not 0<length<=MAX_BODY:self.close_connection=True;return self.reply(413)
        if self.headers.get('DNT')=='1' or self.headers.get('Sec-GPC')=='1' or BOT.search(self.headers.get('User-Agent','')):return self.reply(204)
        try:
            ip=str(ipaddress.ip_address(self.headers.get('X-Real-IP','')))
            event=validate_event(json.loads(self.rfile.read(length)))
            root=self.server.public.resolve()
            target=(root/event['path'].lstrip('/')/'index.html').resolve()
            if not target.is_relative_to(root) or not target.is_file():return self.reply(400)
            if event['event']=='link' and event['data']['kind']=='project' and not (root/'projects'/event['data']['target']/'index.html').is_file():return self.reply(400)
            title=page_title(str(target),target.stat().st_mtime_ns)
            payload=umami_event(event,self.server.website,title)
            req=Request('http://127.0.0.1:4319/api/send',data=json.dumps(payload).encode(),method='POST',headers={'Content-Type':'application/json','X-Real-IP':ip,'User-Agent':self.headers.get('User-Agent','')[:512]})
            with self.server.opener.open(req,timeout=8) as response:
                if response.status!=200:return self.reply(502)
                response.read(4096)
        except (ValueError,TypeError,KeyError,UnicodeDecodeError):return self.reply(400)
        except Exception:return self.reply(503)
        self.reply(204)
    def do_OPTIONS(self):self.reply(405)


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def get_request(self):
        connection,address=super().get_request();connection.settimeout(10);return connection,address


def make_server(settings,port=4318):
    data=json.loads(Path(settings).read_text());website=str(uuid.UUID(data['website_id']))
    server=Server(('127.0.0.1',port),Handler)
    server.public=Path('/var/www/yangarch.net/home/current');server.website=website
    server.opener=build_opener(ProxyHandler({}))
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--settings',required=True);args=parser.parse_args()
    server=make_server(args.settings)
    try:server.serve_forever()
    finally:server.server_close()
