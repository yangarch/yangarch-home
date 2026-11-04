#!/usr/bin/env python3
"""Read-only SSH report command. No SQL or path arguments are accepted."""
import json
from pathlib import Path
import sys

if len(sys.argv)!=1:raise SystemExit('No arguments accepted')
path=Path.home()/'.local/share/yangarch-home-analytics/overview.json'
if not path.is_file():raise SystemExit('Analytics setup or first aggregation is not ready')
raw=path.read_bytes()
if len(raw)>1_000_000:raise SystemExit('Analytics report exceeds the limit')
data=json.loads(raw)
print(json.dumps(data,ensure_ascii=False))
