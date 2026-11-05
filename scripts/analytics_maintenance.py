#!/usr/bin/env python3
"""Aggregate first-party nginx logs and retain only bounded analytics history."""
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta,timezone
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
from analytics_policy import PAGE
from analytics_runtime import CONFIG,STATE,DB,private_json

LOG=Path('/var/log/yangarch-home/access.log')


def page_path(value):
    # Older nginx logs contain try_files' final URI rather than the requested URL.
    if value.endswith('/index.html'):value=value[:-len('index.html')]
    return value if PAGE.fullmatch(value) else None


def sql(text):
    result=subprocess.run(['docker','exec','-i',DB,'psql','-X','-q','-t','-A','-v','ON_ERROR_STOP=1','-U','umami','-d','umami'],input=text.encode(),capture_output=True,timeout=120)
    if result.returncode:raise RuntimeError('Analytics maintenance database operation failed')
    return result.stdout.decode().strip()


def retention_sql(website):
    website=str(uuid.UUID(website))
    return f"""BEGIN;
CREATE SCHEMA IF NOT EXISTS home_analytics;
CREATE TABLE IF NOT EXISTS home_analytics.daily (
 website_id uuid NOT NULL, day date NOT NULL, dimension text NOT NULL, label text NOT NULL,
 value bigint NOT NULL, PRIMARY KEY (website_id,day,dimension,label));
INSERT INTO home_analytics.daily
SELECT website_id, timezone('Asia/Seoul',created_at)::date, 'page', url_path, count(*)
FROM website_event WHERE website_id='{website}' AND event_type=1 AND created_at >= now()-interval '90 days'
GROUP BY website_id, timezone('Asia/Seoul',created_at)::date, url_path
ON CONFLICT (website_id,day,dimension,label) DO UPDATE SET value=GREATEST(home_analytics.daily.value,EXCLUDED.value);
INSERT INTO home_analytics.daily
SELECT website_id, timezone('Asia/Seoul',created_at)::date, 'event', event_name, count(*)
FROM website_event WHERE website_id='{website}' AND event_type=2 AND created_at >= now()-interval '90 days' AND event_name IS NOT NULL
GROUP BY website_id, timezone('Asia/Seoul',created_at)::date, event_name
ON CONFLICT (website_id,day,dimension,label) DO UPDATE SET value=GREATEST(home_analytics.daily.value,EXCLUDED.value);
DELETE FROM event_data WHERE website_id='{website}' AND (created_at < now()-interval '90 days' OR website_event_id IN (SELECT event_id FROM website_event WHERE website_id='{website}' AND created_at < now()-interval '90 days'));
DELETE FROM revenue WHERE website_id='{website}' AND created_at < now()-interval '90 days';
DELETE FROM heatmap_event WHERE website_id='{website}' AND created_at < now()-interval '90 days';
DELETE FROM session_replay WHERE website_id='{website}' AND created_at < now()-interval '90 days';
DELETE FROM website_event WHERE website_id='{website}' AND created_at < now()-interval '90 days';
DELETE FROM session_data WHERE website_id='{website}' AND session_id IN (SELECT session_id FROM session WHERE website_id='{website}' AND created_at < now()-interval '90 days' AND NOT EXISTS (SELECT 1 FROM website_event e WHERE e.session_id=session.session_id));
DELETE FROM session_link WHERE website_id='{website}' AND created_at < now()-interval '90 days';
DELETE FROM session WHERE website_id='{website}' AND created_at < now()-interval '90 days' AND NOT EXISTS (SELECT 1 FROM website_event e WHERE e.session_id=session.session_id);
DELETE FROM home_analytics.daily WHERE website_id='{website}' AND day < timezone('Asia/Seoul',now())::date-365;
COMMIT;
"""


def read_logs(paths,now=None,max_bytes=64_000_000):
    now=now or datetime.now(timezone.utc)
    by_day=defaultdict(Counter);unique=defaultdict(set);latest=[];used=0;truncated=False
    for path in paths:
        if truncated:break
        try:
            opener=gzip.open if str(path).endswith('.gz') else open
            with opener(path,'rt',encoding='utf-8',errors='replace') as handle:
                for line in handle:
                    used+=len(line)
                    if used>max_bytes:truncated=True;break
                    if len(line)>10000:continue
                    try:
                        row=json.loads(line);stamp=datetime.fromisoformat(row['time'])
                        if stamp.tzinfo is None or stamp<now-timedelta(days=14) or stamp>now+timedelta(minutes=5):continue
                        day=stamp.astimezone(timezone(timedelta(hours=9))).date().isoformat()
                        status=int(row['status']);path_value=str(row['path'])[:200];kind=str(row.get('client','visitor'))
                        by_day[day]['requests']+=1
                        if status>=400:by_day[day]['errors']+=1
                        if status==429:by_day[day]['limited']+=1
                        if kind=='automated':by_day[day]['automated']+=1
                        if row.get('excluded')=='1':by_day[day]['excluded']+=1
                        visitor=kind=='visitor' and row.get('excluded')!='1' and row.get('method','GET')=='GET' and status in {200,304} and page_path(path_value)
                        if visitor:
                            by_day[day]['page_requests']+=1
                            unique[day].add(hashlib.sha256((str(row['ip'])+'\0'+str(row.get('ua',''))).encode()).digest())
                        item={'time':stamp.isoformat(),'ip':str(row.get('ip',''))[:64],'path':path_value,'status':status,'client':kind[:20],'excluded':row.get('excluded')=='1','ua':str(row.get('ua',''))[:300]}
                        latest.append(item)
                        if len(latest)>500:latest=sorted(latest,key=lambda x:x['time'],reverse=True)[:100]
                    except (ValueError,KeyError,TypeError):continue
        except FileNotFoundError:continue
    for day,values in by_day.items():values['estimated_visitors']=len(unique[day])
    return {'days':dict(by_day),'latest':sorted(latest,key=lambda x:x['time'],reverse=True)[:100],'truncated':truncated}


def maintain(settings_file=CONFIG/'settings.json'):
    settings=json.loads(Path(settings_file).read_text());website=str(uuid.UUID(settings['website_id']))
    sql(retention_sql(website))
    rows=sql(f"SELECT COALESCE(json_agg(t),'[]'::json) FROM (SELECT day,dimension,label,value FROM home_analytics.daily WHERE website_id='{website}' ORDER BY day DESC,dimension,label LIMIT 3000) t;")
    data=read_logs(sorted(LOG.parent.glob('access.log*'),reverse=True))
    previous=STATE/'overview.json'
    if previous.exists():
        old=json.loads(previous.read_text())
        days=old.get('days',{})
        for day,values in data['days'].items():
            existing=days.get(day,{})
            days[day]={key:max(int(existing.get(key,0)),int(value)) for key,value in values.items()}
        data['days']=days
    cutoff=(datetime.now(timezone(timedelta(hours=9))).date()-timedelta(days=365)).isoformat()
    data['days']={day:values for day,values in data['days'].items() if day>=cutoff}
    data.update(generated_at=datetime.now(timezone.utc).isoformat(),browser_daily=json.loads(rows),retention={'raw_logs_days':14,'events_days':90,'daily_days':365})
    private_json(STATE/'overview.json',data)
    print('Analytics aggregation and retention completed.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--settings',type=Path,default=CONFIG/'settings.json');args=parser.parse_args()
    try:maintain(args.settings)
    except Exception:raise SystemExit('Analytics maintenance failed. No visitor data or credentials were printed.')
