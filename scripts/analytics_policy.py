"""Small allowlist for first-party aggregate analytics. No free-form event data."""
import ipaddress
import re
from urllib.parse import urlsplit,urlencode

ENDPOINT='/__analytics'
MAX_BODY=2048
PAGE=re.compile(r'/(?:|projects/[a-z-]{1,64}/|records/(?:|cycling/|swimming/|making/|entry/[a-z0-9]+(?:-[a-z0-9]+)*/)|privacy/)')
CAMPAIGN=re.compile(r'[A-Za-z0-9_.-]{1,80}')
SERVICE_HOSTS={'report.yangarch.net':'training-report','fingu.yangarch.net':'fingu','poogiegram.poogie.one':'poogiegram','archgrab.yangarch.net':'archgrab','njc.yangarch.net':'investment','cyberfondo.yangarch.net':'cyberfondo','cyber-topgoal.yangarch.net':'cyber-topgoal'}
EVENTS={'pageview','link','scroll','active','filter'}


def origin_only(value):
    if not isinstance(value,str) or len(value)>2048:return ''
    try:
        p=urlsplit(value);host=p.hostname or ''
        if p.scheme not in {'http','https'} or p.username or p.password or '.' not in host:return ''
        if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}',host):return ''
        try:
            if not ipaddress.ip_address(host).is_global:return ''
        except ValueError:pass
        return p.scheme+'://'+host.lower()
    except ValueError:return ''


def validate_event(data):
    keys={'event','path','screen','language','timezone','referrer','campaign','data'}
    if not isinstance(data,dict) or set(data)-keys:raise ValueError('Unexpected analytics field')
    event=data.get('event');path=data.get('path')
    if event not in EVENTS or not isinstance(path,str) or len(path)>200 or not PAGE.fullmatch(path):raise ValueError('Unknown event or page')
    screen=data.get('screen','')
    if not isinstance(screen,str) or not re.fullmatch(r'[1-9][0-9]{0,4}x[1-9][0-9]{0,4}',screen):raise ValueError('Invalid screen size')
    language=data.get('language','')
    if not isinstance(language,str) or not re.fullmatch(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,3}',language):raise ValueError('Invalid language')
    timezone=data.get('timezone','')
    if not isinstance(timezone,str) or not re.fullmatch(r'[A-Za-z0-9_+/-]{1,64}',timezone):raise ValueError('Invalid timezone')
    campaign=data.get('campaign',{})
    if not isinstance(campaign,dict) or set(campaign)-{'utm_source','utm_medium','utm_campaign'} or any(not isinstance(v,str) or not CAMPAIGN.fullmatch(v) for v in campaign.values()):raise ValueError('Unexpected campaign data')
    detail=data.get('data',{})
    if not isinstance(detail,dict):raise ValueError('Invalid event details')
    if event=='pageview':
        if detail:raise ValueError('Page views have no custom details')
    elif event=='scroll':
        if set(detail)!={'percent'} or type(detail['percent']) is not int or detail['percent'] not in {25,50,75,100}:raise ValueError('Invalid scroll bucket')
    elif event=='active':
        if set(detail)!={'seconds'} or type(detail['seconds']) is not int or detail['seconds'] not in {15,30,60,120,300}:raise ValueError('Invalid active-time bucket')
    elif event=='filter':
        if set(detail)!={'category'} or detail['category'] not in {'all','운동','가족','도구'}:raise ValueError('Invalid filter')
    elif event=='link':
        if set(detail)!={'kind','target'}:raise ValueError('Invalid link event')
        kind,target=detail['kind'],detail['target']
        known={'service':set(SERVICE_HOSTS.values()),'github':{'github'},'linkedin':{'linkedin'},'source':{'naver','dcinside','tistory'},'navigation':{'work','about','records','contact','main'}}
        if kind=='project':
            if not isinstance(target,str) or not re.fullmatch(r'[a-z-]{1,64}',target):raise ValueError('Invalid project target')
        elif kind not in known or not isinstance(target,str) or target not in known[kind]:raise ValueError('Unknown link target')
    return {'event':event,'path':path,'screen':screen,'language':language,'timezone':timezone,'referrer':origin_only(data.get('referrer','')),'campaign':campaign,'data':detail}


def umami_event(event,website,title):
    url=event['path']
    if event['campaign']:url+='?'+urlencode(event['campaign'])
    payload={'website':website,'hostname':'yangarch.net','url':url,'title':title[:200],'screen':event['screen'],'language':event['language'],'referrer':event['referrer']}
    if event['event']=='pageview':payload['data']={'timezone':event['timezone']}
    else:payload.update(name=event['event'],data=event['data'])
    return {'type':'event','payload':payload}
