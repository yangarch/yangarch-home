"""Private report access and a local-only SSH tunnel to the analytics UI."""
import json
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from urllib.request import build_opener,ProxyHandler


class Statistics:
    def __init__(self,root):
        self.root=Path(root).resolve();self.process=None;self.lock=threading.Lock()
    def host(self):
        path=self.root/'.local/editor/publish.json'
        if not path.is_file() or path.is_symlink():raise ValueError('서버 연결 설정이 필요합니다.')
        data=json.loads(path.read_text());host=data.get('ssh_host','')
        if set(data)!={'ssh_host'} or not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}',host):raise ValueError('서버 연결 설정을 확인해 주세요.')
        return host
    def ssh_args(self):
        return ['/usr/bin/ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2']
    def report(self):
        try:
            result=subprocess.run(self.ssh_args()+['-T',self.host(),'/usr/bin/python3 -I /opt/yangarch-home-analytics/analytics_report.py'],capture_output=True,timeout=25)
            if result.returncode or len(result.stdout)>1_000_000:raise ValueError()
            data=json.loads(result.stdout)
            if not isinstance(data,dict) or not {'days','latest','browser_daily','generated_at'}<=set(data) or len(data['days'])>370 or len(data['latest'])>100 or len(data['browser_daily'])>3000:raise ValueError()
            return data
        except Exception:raise ValueError('통계 최초 설정 또는 첫 집계가 필요합니다. 서버 설정을 확인해 주세요.') from None
    def open_admin(self):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                probe=socket.socket()
                try:probe.bind(('127.0.0.1',4390))
                except OSError:raise ValueError('통계 연결용 로컬 포트가 사용 중입니다. 기존 연결을 닫고 다시 시도해 주세요.') from None
                finally:probe.close()
                args=self.ssh_args()+['-N','-T','-o','ExitOnForwardFailure=yes','-L','127.0.0.1:4390:127.0.0.1:4319',self.host()]
                self.process=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            opener=build_opener(ProxyHandler({}))
            for _ in range(50):
                if self.process.poll() is not None:break
                try:
                    with opener.open('http://127.0.0.1:4390/api/heartbeat',timeout=1) as response:
                        if response.status==200:return {'url':'http://127.0.0.1:4390/'}
                except Exception:time.sleep(.1)
            self.close()
            raise ValueError('방문 분석 화면에 연결하지 못했습니다. SSH 키와 최초 서버 설정을 확인해 주세요.')
    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.process=None
