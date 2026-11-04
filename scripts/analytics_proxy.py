#!/usr/bin/env python3
"""Loopback TCP relay to an unpublished container, including on Docker < 28."""
import ipaddress
import json
import select
import socket
import socketserver
import subprocess
import threading
import time

_lock=threading.Lock()
_cached=(0,None)


def container_address():
    global _cached
    with _lock:
        if time.monotonic()-_cached[0]<10 and _cached[1]:return _cached[1]
        result=subprocess.run(['docker','inspect','--format','{{json .NetworkSettings.Networks}}','yangarch-home-umami'],capture_output=True,timeout=5,check=True)
        networks=json.loads(result.stdout)
        # Only the app bridge is reachable from the host; the database bridge is internal.
        if set(networks)!={'yangarch-home-analytics_frontend','yangarch-home-analytics_database'}:raise ValueError('Unexpected analytics network')
        address=ipaddress.ip_address(networks['yangarch-home-analytics_frontend']['IPAddress'])
        if not address.is_private or address.is_loopback:raise ValueError('Unexpected analytics address')
        _cached=(time.monotonic(),str(address));return str(address)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection((container_address(),3000),timeout=5) as upstream:
                peers=[self.request,upstream]
                for sock in peers:sock.settimeout(15)
                while True:
                    readable,_,_=select.select(peers,[],[],60)
                    if not readable:return
                    for source in readable:
                        data=source.recv(65536)
                        if not data:return
                        (upstream if source is self.request else self.request).sendall(data)
        except Exception:pass  # Never log HTTP bodies, cookies, tokens or visitor information.


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True
    request_queue_size=32
    def verify_request(self,request,address):return address[0]=='127.0.0.1'


if __name__=='__main__':
    with Server(('127.0.0.1',4319),Handler) as server:server.serve_forever()
