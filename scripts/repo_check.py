#!/usr/bin/env python3
"""Inspect Git's index, including forced additions, before committing or pushing."""
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from content_policy import EXAMPLE_TERMS, blocked_text, source_policy

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'.gitignore','README.md','CONTRIBUTING.md','SECURITY.md','package.json','requirements-editor.txt','start-editor.command'}
EXACT_FILES={'content/public.json','content/records.example.json','content/privacy.example.json','deploy/install.sh','deploy/setup-publisher.sh','deploy/security-headers.conf','.github/workflows/ci.yml'}
SOURCE_TYPES={'assets':{'.css','.js','.svg'},'editor':{'.css','.js','.html'},'scripts':{'.py'},'tests':{'.py'},'docs':{'.md'}}
SECRET=re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|(?:AKIA|ASIA)[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,}|/Users/[A-Za-z0-9._-]+/')


def allowed_path(name):
    if not re.fullmatch(r'[A-Za-z0-9_./-]+',name) or '..' in PurePosixPath(name).parts:
        return False
    if name in ROOT_FILES or name in EXACT_FILES:return True
    parts=PurePosixPath(name).parts
    return len(parts)>1 and not any(part.startswith('.') for part in parts) and PurePosixPath(name).suffix in SOURCE_TYPES.get(parts[0],set()) and '__pycache__' not in parts


def check_content(name, raw, policy):
    if len(raw)>1_000_000:raise ValueError('Source file exceeds 1 MB: '+name)
    try:text=raw.decode('utf-8')
    except UnicodeDecodeError:raise ValueError('Unexpected binary file: '+name) from None
    if SECRET.search(text) or blocked_text(text,policy):
        raise ValueError('Private or credential pattern in staged file: '+name)


def check_index(root=ROOT):
    entries=subprocess.check_output(['git','ls-files','--stage','-z'],cwd=root).split(b'\0')
    policy=source_policy(root)
    policy['blocked_terms']=[term for term in policy['blocked_terms'] if term not in EXAMPLE_TERMS]
    count=0
    for entry in entries:
        if not entry:continue
        metadata,raw_name=entry.split(b'\t',1)
        mode,_,stage=metadata.decode().split()
        name=raw_name.decode('utf-8')
        if stage!='0' or mode not in {'100644','100755'} or not allowed_path(name):
            raise ValueError('Unreviewed path, symlink or conflict in Git index: '+name)
        raw=subprocess.check_output(['git','show',':'+name],cwd=root)
        check_content(name,raw,policy)
        count+=1
    if not count:raise ValueError('Git index is empty')
    print(f'PASS: {count} tracked source files; private data, generated files, binaries and credential patterns excluded.')


if __name__=='__main__':
    try:check_index()
    except (ValueError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr)
        sys.exit(1)
