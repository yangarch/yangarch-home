"""Publication is a capability to replace static homepage bytes, never run code."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from publication import canonical,decode,identifier,nginx_routes,receive_files,route_map,validate_header,REQUIRED,TOTAL_LIMIT
from publish import Publisher,PublishError
from publish_receiver import Receiver,verify_live


def bundle(record=True):
    files={name:(b'<html><head><title>Example</title></head><body><h1>Example</h1></body></html>' if name.endswith('.html') else b'example') for name in REQUIRED}
    if record:files['records/entry/example-record/index.html']=b'<h1>Prepared record</h1>'
    manifest={'files':{n:hashlib.sha256(raw).hexdigest() for n,raw in files.items()},'routes':route_map(files),'record_sources':[]}
    header={'protocol':1,'operation':'publish','manifest':manifest,'sizes':{n:len(raw) for n,raw in files.items()}}
    payload=b''.join(files[n] for n in sorted(files))
    return header,payload


class ProtocolTest(unittest.TestCase):
    def test_rejects_code_traversal_hidden_paths_and_nginx_injection(self):
        for path in ('../outside','scripts/check.py','.env','assets/.env','projects/x;include-bad/index.html','media/records/example/file.php','records/entry/x/../../root/index.html'):
            header,_=bundle();header['manifest']['files'][path]='a'*64;header['sizes'][path]=1
            with self.subTest(path=path),self.assertRaises(ValueError):validate_header(header)
        header,_=bundle();header['manifest']['routes']['/;include /bad;']='index.html'
        with self.assertRaises(ValueError):validate_header(header)

    def test_rejects_duplicates_invalid_sizes_excessive_payload_and_protocol(self):
        with self.assertRaises(ValueError):decode(b'{"operation":"status","operation":"publish"}')
        for size in (-1,True,2_000_001,'12'):
            header,_=bundle();header['sizes']['index.html']=size
            with self.subTest(size=size),self.assertRaises(ValueError):validate_header(header)
        header,_=bundle();header['protocol']=True
        with self.assertRaises(ValueError):validate_header(header)
        header,_=bundle()
        for i in range(130):
            name=f'projects/project'+('a'*(i//26))+chr(97+i%26)+'/index.html'
            header['manifest']['files'][name]='a'*64;header['sizes'][name]=2_000_000
        header['manifest']['routes']=route_map(header['manifest']['files'])
        with self.assertRaises(ValueError):validate_header(header)

    def test_integrity_trailing_bytes_and_inline_scripts_are_rejected(self):
        for transform in (lambda p:p[:-1],lambda p:b'x'+p[1:],lambda p:p+b'extra'):
            header,payload=bundle()
            with tempfile.TemporaryDirectory() as temp,self.assertRaises(ValueError):receive_files(io.BytesIO(transform(payload)),header,Path(temp))
        header,payload=bundle();bad=b'<script>alert(1)</script>'
        header['manifest']['files']['index.html']=hashlib.sha256(bad).hexdigest();header['sizes']['index.html']=len(bad)
        parts={}
        offset=0
        original,_=bundle()
        for name in sorted(original['sizes']):
            size=original['sizes'][name];parts[name]=payload[offset:offset+size];offset+=size
        parts['index.html']=bad
        with tempfile.TemporaryDirectory() as temp,self.assertRaises(ValueError):receive_files(io.BytesIO(b''.join(parts[n] for n in sorted(parts))),header,Path(temp))

    def test_public_permissions_remain_readable_with_private_process_umask(self):
        header,payload=bundle()
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'release';target.mkdir()
            old=os.umask(0o077)
            try:receive_files(io.BytesIO(payload),header,target)
            finally:os.umask(old)
            self.assertEqual(target.stat().st_mode&0o777,0o755)
            self.assertEqual((target/'records/entry/example-record').stat().st_mode&0o777,0o755)
            self.assertEqual((target/'index.html').stat().st_mode&0o777,0o644)


class ActivationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.base=self.root/'home';(self.base/'releases/old').mkdir(parents=True)
        (self.base/'current').symlink_to(self.base/'releases/old')
        self.state=self.root/'state';self.state.mkdir()
        self.config=self.root/'apex.conf';self.config.write_bytes(b'trusted apex')
        self.headers=self.root/'headers.conf';self.headers.write_bytes(b'trusted headers')
        self.routes=self.root/'routes.conf';self.routes.write_bytes(b'old routes')
        self.pdf=self.root/'portfolio.pdf';self.pdf.write_bytes(b'old PDF')
        (self.state/'baseline.json').write_bytes(canonical({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.config,self.headers)}))
        self.commands=[]
        self.receiver=Receiver(self.base,self.state,self.routes,self.config,self.headers,self.pdf,runner=lambda args:self.commands.append(args),verifier=lambda manifest:None)
    def tearDown(self):self.temp.cleanup()

    def test_success_only_runs_fixed_nginx_operations_and_preserves_pdf(self):
        header,payload=bundle();result=self.receiver.publish(io.BytesIO(payload),header)
        self.assertEqual(result['receipt']['manifest_id'],identifier(header['manifest']))
        self.assertEqual(self.pdf.read_bytes(),b'old PDF')
        self.assertEqual(self.commands,[['/usr/sbin/nginx','-t'],['/bin/systemctl','reload','nginx']])
        self.assertEqual(self.routes.read_text(),nginx_routes(header['manifest']))
        self.assertEqual(set(p.relative_to((self.base/'current').resolve()).as_posix() for p in (self.base/'current').resolve().rglob('*') if p.is_file()),set(header['manifest']['files']))

    def test_live_failure_restores_current_routes_and_receipt(self):
        (self.state/'status.json').write_bytes(b'{"manifest_id":"previous"}')
        old=os.readlink(self.base/'current')
        def fail(_):raise RuntimeError('live check failed')
        self.receiver.verifier=fail
        header,payload=bundle()
        with self.assertRaisesRegex(RuntimeError,'restored the previous release'):self.receiver.publish(io.BytesIO(payload),header)
        self.assertEqual(os.readlink(self.base/'current'),old)
        self.assertEqual(self.routes.read_bytes(),b'old routes')
        self.assertEqual((self.state/'status.json').read_bytes(),b'{"manifest_id":"previous"}')
        self.assertEqual(len(self.commands),4)

    def test_changed_apex_configuration_is_not_overwritten(self):
        self.config.write_bytes(b'changed config')
        header,payload=bundle()
        with self.assertRaisesRegex(ValueError,'configuration changed'):self.receiver.publish(io.BytesIO(payload),header)
        self.assertEqual(self.routes.read_bytes(),b'old routes');self.assertFalse(self.commands)

    def test_incomplete_or_malicious_payload_never_activates(self):
        header,payload=bundle()
        with self.assertRaises(ValueError):self.receiver.publish(io.BytesIO(payload[:-1]),header)
        self.assertEqual(self.routes.read_bytes(),b'old routes');self.assertFalse(self.commands)


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        for folder in ('scripts','assets','deploy'):
            shutil.copytree(ROOT/folder,self.root/folder,ignore=shutil.ignore_patterns('__pycache__'))
        (self.root/'content').mkdir();(self.root/'.local/editor').mkdir(parents=True)
        for name in ('public.json','privacy.example.json'):
            shutil.copyfile(ROOT/'content'/name,self.root/'content'/name)
        (self.root/'content/records.json').write_text('{"entries":[]}')
        for script in ('build.py','nginx_config.py'):
            subprocess.run([sys.executable,str(self.root/'scripts'/script)],check=True,stdout=subprocess.DEVNULL)
    def tearDown(self):self.temp.cleanup()

    def test_status_transport_reads_the_complete_packet_from_the_start(self):
        def transport(packet):
            self.assertEqual(decode(packet.readline()),{'protocol':1,'operation':'status'})
            self.assertEqual(packet.read(),b'')
            return {'configured':True,'latest':None}
        self.assertTrue(Publisher(self.root,transport=transport).check_connection()['configured'])

    def test_preview_is_bound_to_snapshot_and_contains_no_drafts_or_scripts(self):
        publisher=Publisher(self.root)
        review=publisher.summary()['release_id']
        with tempfile.TemporaryDirectory() as temp:
            packet,manifest,_=publisher.snapshot(Path(temp),review)
            with packet.open('rb') as stream:
                header=decode(stream.readline())
                self.assertEqual(set(header['manifest']['files']),set(manifest['files']))
                self.assertFalse(any(name.startswith(('scripts/','editor/','.local/')) for name in manifest['files']))
                out=Path(temp)/'received';out.mkdir();receive_files(stream,header,out)
        (self.root/'content/records.json').write_text('{"entries":[]}\n')
        with tempfile.TemporaryDirectory() as temp,self.assertRaisesRegex(PublishError,'변경'):publisher.snapshot(Path(temp),review)

    def test_duplicate_job_is_rejected_and_verified_receipt_is_saved(self):
        gate=threading.Event()
        def transport(stream):
            header=decode(stream.readline());gate.wait(3)
            return {'receipt':{'manifest_id':identifier(header['manifest']),'published_at':'2026-01-01T00:00:00+00:00'}}
        publisher=Publisher(self.root,transport=transport,verifier=lambda _:None)
        review=publisher.summary()['release_id'];publisher.start(review)
        with self.assertRaisesRegex(PublishError,'이미'):publisher.start(review)
        gate.set()
        for _ in range(100):
            if publisher.job['status']!='running':break
            time.sleep(.02)
        self.assertEqual(publisher.job['status'],'done')
        self.assertTrue((publisher.local/'publish-receipt.json').is_file())

    def test_ssh_target_cannot_inject_arguments_and_remote_command_is_fixed(self):
        publisher=Publisher(self.root)
        config=publisher.local/'publish.json'
        config.write_text('{"ssh_host":"-oProxyCommand=bad"}')
        with self.assertRaises(PublishError):publisher.check_connection()
        config.write_text('{"ssh_host":"example-server"}')
        reply={'ok':True,'protocol':1,'configured':True,'latest':None}
        with patch('publish.subprocess.run',return_value=subprocess.CompletedProcess([],0,canonical(reply),b'')) as call:
            self.assertTrue(publisher.check_connection()['configured'])
            args=call.call_args.args[0]
            self.assertEqual(args[-1],'sudo -n -- /usr/local/sbin/yangarch-home-publish')
            self.assertIn('BatchMode=yes',args);self.assertIn('StrictHostKeyChecking=yes',args)


if __name__=='__main__':unittest.main()
