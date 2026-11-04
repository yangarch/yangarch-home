"""Mac-side immutable public snapshot, SSH transport, and background publication."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.request import ProxyHandler,build_opener
import uuid

from publication import PROTOCOL, canonical, decode, identifier, validate_header
from records import validate_records
from record_media import media_paths

SSH_COMMAND='sudo -n -- /usr/local/sbin/yangarch-home-publish'


class PublishError(ValueError):pass


def atomic_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as handle:
        temp=Path(handle.name);handle.write(canonical(data));handle.flush()
    try:temp.chmod(0o600);temp.replace(path)
    finally:temp.unlink(missing_ok=True)


class Publisher:
    def __init__(self,root,transport=None,verifier=None):
        self.root=Path(root).resolve();self.local=self.root/'.local/editor'
        self.transport=transport or self.ssh
        self.verifier=verifier or self.verify_external
        self.lock=threading.Lock();self.job=None

    def selection(self):
        manifest=decode((self.root/'artifacts/manifest.json').read_bytes())
        raw=(self.root/'content/records.json').read_bytes()
        entries=validate_records(decode(raw))
        expected={'/records/entry/'+e['id']+'/': 'records/entry/'+e['id']+'/index.html' for e in entries}
        actual={p:f for p,f in manifest['routes'].items() if p.startswith('/records/entry/')}
        if expected!=actual:raise PublishError('공개본 준비 상태가 일치하지 않습니다. 공개본을 다시 준비해 주세요.')
        sizes={name:(self.root/'dist'/name).stat().st_size for name in manifest['files']}
        header={'protocol':PROTOCOL,'operation':'publish','manifest':manifest,'sizes':sizes}
        validate_header(header)
        review_id=hashlib.sha256(canonical(manifest)+raw).hexdigest()
        return header,entries,review_id

    def summary(self):
        header,entries,review_id=self.selection()
        last=self.local/'publish-receipt.json'
        return {'release_id':review_id,'records':[{'id':e['id'],'title':e['title'],'date':e['date'],'images':len(media_paths([e]))} for e in entries],
                'images':sum(len(media_paths([e])) for e in entries),'last_published':decode(last.read_bytes()) if last.exists() else None,'job':self.job}

    def ssh(self,packet):
        config=self.local/'publish.json'
        if not config.is_file() or config.is_symlink():raise PublishError('서버 연결 설정이 필요합니다. 최초 배포 설정을 먼저 완료해 주세요.')
        data=decode(config.read_bytes());host=data.get('ssh_host','')
        if set(data)!={'ssh_host'} or not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}',host):raise PublishError('서버 연결 설정을 확인해 주세요.')
        args=['/usr/bin/ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2',host,SSH_COMMAND]
        packet.seek(0)
        try:
            result=subprocess.run(args,stdin=packet,capture_output=True,timeout=210)
        except subprocess.TimeoutExpired:raise PublishError('서버 응답 시간이 초과됐습니다. 연결 확인으로 반영 결과를 확인해 주세요.') from None
        try:reply=decode(result.stdout)
        except (ValueError,UnicodeDecodeError):
            raise PublishError('서버 배포 연결을 확인하지 못했습니다. SSH 키와 최초 서버 설정을 확인해 주세요.') from None
        if result.returncode or not isinstance(reply,dict) or not reply.get('ok') or reply.get('protocol')!=PROTOCOL:
            error=reply.get('error','') if isinstance(reply,dict) else ''
            if 'rollback needs administrator' in error:raise PublishError('서버 반영과 자동 복구를 완료하지 못했습니다. 관리자 확인이 필요합니다.')
            if 'restored the previous release' in error:raise PublishError('서버 검증에 실패하여 이전 버전으로 복구했습니다.')
            raise PublishError('서버가 배포 요청을 거부했습니다. 최초 설정과 서버 상태를 확인해 주세요.')
        return reply

    def check_connection(self):
        with tempfile.TemporaryFile() as packet:
            packet.write(canonical({'protocol':PROTOCOL,'operation':'status'})+b'\n')
            packet.seek(0)
            reply=self.transport(packet)
        if not reply.get('configured'):raise PublishError('서버 최초 설정이 필요합니다.')
        return {'configured':True,'latest':reply.get('latest')}

    def snapshot(self,folder,expected):
        result=subprocess.run([sys.executable,'-I',str(self.root/'scripts/check.py')],cwd=self.root,capture_output=True,timeout=60)
        if result.returncode:raise PublishError('공개 파일 검사에 실패했습니다. 공개본을 다시 준비해 주세요.')
        header,entries,review_id=self.selection()
        if review_id!=expected:raise PublishError('공개본이 변경됐습니다. 배포할 목록을 다시 확인해 주세요.')
        packet=folder/'publication.bin'
        with packet.open('xb') as handle:
            handle.write(canonical(header)+b'\n')
            for name in sorted(header['manifest']['files']):
                path=self.root/'dist'/name
                if path.resolve()!=path.absolute() or not path.is_file():raise PublishError('공개 파일 경로를 확인해 주세요.')
                raw=path.read_bytes()
                if len(raw)!=header['sizes'][name] or hashlib.sha256(raw).hexdigest()!=header['manifest']['files'][name]:raise PublishError('공개본이 변경됐습니다. 배포할 목록을 다시 확인해 주세요.')
                handle.write(raw)
        packet.chmod(0o600)
        if self.selection()[2]!=review_id:raise PublishError('공개본이 변경됐습니다. 배포할 목록을 다시 확인해 주세요.')
        return packet,header['manifest'],entries

    def start(self,expected):
        if not isinstance(expected,str):raise PublishError('배포할 공개본을 다시 확인해 주세요.')
        with self.lock:
            if self.job and self.job['status']=='running':raise PublishError('이미 서버에 반영 중입니다. 완료될 때까지 기다려 주세요.')
            folder=Path(tempfile.mkdtemp(prefix='publish-',dir=self.local))
            try:packet,manifest,entries=self.snapshot(folder,expected)
            except BaseException:shutil.rmtree(folder);raise
            self.job={'id':uuid.uuid4().hex,'status':'running','message':'서버로 전송하고 확인하고 있습니다…'}
            job=dict(self.job)
            threading.Thread(target=self.perform,args=(folder,packet,manifest,entries),daemon=True).start()
            return job

    def perform(self,folder,packet,manifest,entries):
        try:
            with packet.open('rb') as handle:reply=self.transport(handle)
            receipt=reply.get('receipt',{})
            if receipt.get('manifest_id')!=identifier(manifest):raise PublishError('서버 반영 결과가 준비된 공개본과 다릅니다. 연결 확인이 필요합니다.')
            try:self.verifier(manifest)
            except Exception:raise PublishError('서버 내부 검증은 통과했지만 외부 접속을 확인하지 못했습니다. 실제 사이트를 확인해 주세요.') from None
            result={'published_at':receipt['published_at'],'manifest_id':receipt['manifest_id'],'records':[{'id':e['id'],'title':e['title']} for e in entries]}
            atomic_json(self.local/'publish-receipt.json',result)
            self.job.update(status='done',message='서버에 반영했습니다.',url='https://yangarch.net/records/')
        except Exception as error:
            message=str(error) if isinstance(error,PublishError) else '서버 반영 결과를 확인하지 못했습니다. 연결 확인 후 실제 사이트를 확인해 주세요.'
            self.job.update(status='error',message=message)
        finally:shutil.rmtree(folder)

    @staticmethod
    def verify_external(manifest):
        # Check one index and every selected record; receiver already checks all bytes.
        opener=build_opener(ProxyHandler({}))
        routes=['/records/']+[p for p in manifest['routes'] if p.startswith('/records/entry/')]
        for path in routes:
            with opener.open('https://yangarch.net'+path,timeout=12) as response:
                if response.status!=200 or hashlib.sha256(response.read()).hexdigest()!=manifest['files'][manifest['routes'][path]]:raise ValueError('External content mismatch')
