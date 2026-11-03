#!/usr/bin/env python3
"""Package exact checked artifacts plus the reviewed installer; never archive cwd."""
import hashlib,json,tarfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check import check
check()
manifest=json.loads((ROOT/'artifacts/manifest.json').read_text())
files=['dist/'+name for name in manifest['files']]
files += ['artifacts/manifest.json','artifacts/content-policy.json','deploy/install.sh','deploy/security-headers.conf','deploy/routes.conf','deploy/yangarch.net.conf','scripts/check.py','scripts/content_policy.py','scripts/public_links.py','scripts/record_media.py','scripts/verify_live.py','scripts/nginx_config.py','tests/nginx_integration.py']
archive=ROOT/'artifacts/yangarch-home.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
 for rel in sorted(files):
  p=ROOT/rel
  assert p.is_file() and not p.is_symlink()
  tar.add(p,arcname=rel,recursive=False)
sha=hashlib.sha256(archive.read_bytes()).hexdigest()
(ROOT/'artifacts/release.json').write_text(json.dumps({'sha256':sha,'file_count':len(files),'archive':archive.name},indent=2)+'\n')
print(json.dumps({'archive':str(archive),'sha256':sha,'file_count':len(files)}))
