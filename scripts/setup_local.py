#!/usr/bin/env python3
"""Initialize ignored content files on a fresh clone without replacing local data."""
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]


def setup(root=ROOT):
    for example,local in [('records.example.json','records.json'),('privacy.example.json','privacy.local.json')]:
        source,target=root/'content'/example,root/'content'/local
        if target.exists():
            print('Kept local file: content/'+local)
            continue
        with target.open('xb') as output:
            with source.open('rb') as handle:shutil.copyfileobj(handle,output)
        target.chmod(0o600)
        print('Created local file: content/'+local)


if __name__=='__main__':setup()
