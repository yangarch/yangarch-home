#!/usr/bin/env python3
"""Explicit private one-time setup bundle, separate from ongoing static publishing."""
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check import check


def package():
    check()
    manifest=json.loads((ROOT/'artifacts/manifest.json').read_text())
    names={'dist/'+name for name in manifest['files']}
    names.update({'artifacts/manifest.json','artifacts/content-policy.json','deploy/setup-analytics.sh','deploy/setup-publisher.sh','deploy/security-headers.conf','deploy/routes.conf','deploy/yangarch.net.conf'})
    modules=['check','content_policy','public_links','record_media','nginx_config','analytics_nginx','publication','publish_receiver','analytics_gateway','analytics_policy','analytics_maintenance','analytics_runtime','analytics_proxy','analytics_report']
    names.update('scripts/'+name+'.py' for name in modules)
    archive=ROOT/'artifacts/analytics-setup.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        for name in sorted(names):
            path=ROOT/name
            if not path.is_file() or path.is_symlink():raise ValueError('Unexpected setup file')
            tar.add(path,arcname=name,recursive=False)
    archive.chmod(0o600)
    result={'archive':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'file_count':len(names)}
    (ROOT/'artifacts/analytics-setup.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':package()
