'use strict';
(() => {
  const key = 'yangarch_analytics_optout';
  const cookie = 'home_analytics_optout';
  const allowedHosts = {'report.yangarch.net':'training-report','fingu.yangarch.net':'fingu','poogiegram.poogie.one':'poogiegram','archgrab.yangarch.net':'archgrab','njc.yangarch.net':'investment','cyberfondo.yangarch.net':'cyberfondo','cyber-topgoal.yangarch.net':'cyber-topgoal'};
  const setExcluded = excluded => {
    try { localStorage.setItem(key,excluded ? '1' : '0'); } catch (_) { /* Cookie remains available. */ }
    document.cookie = `${cookie}=${excluded ? '1' : '0'}; Max-Age=31536000; Path=/; SameSite=Lax; Secure`;
  };
  const isExcluded = () => {
    let stored = false;
    try { stored = localStorage.getItem(key) === '1'; } catch (_) { /* Storage may be disabled. */ }
    return stored || document.cookie.split(';').some(value=>value.trim()===cookie+'=1') || navigator.doNotTrack === '1' || navigator.globalPrivacyControl === true;
  };
  const status = document.getElementById('analytics-preference');
  const updateStatus = () => { if (status) status.textContent = isExcluded() ? '이 브라우저의 방문 분석을 제외하고 있습니다.' : '이 브라우저의 방문 분석을 허용하고 있습니다.'; };
  document.getElementById('exclude-analytics')?.addEventListener('click',()=>{setExcluded(true);updateStatus();});
  document.getElementById('allow-analytics')?.addEventListener('click',()=>{setExcluded(false);updateStatus();});
  const params = new URLSearchParams(location.search);
  if (params.get('analytics') === 'off') {
    setExcluded(true);params.delete('analytics');
    history.replaceState(null,'',location.pathname+(params.size ? '?'+params.toString() : '')+location.hash);
  }
  updateStatus();
  if (location.hostname !== 'yangarch.net' || location.protocol !== 'https:' || isExcluded()) return;
  let referrer = '';
  try { const url = new URL(document.referrer); if (['http:','https:'].includes(url.protocol)) referrer = url.origin; } catch (_) { /* No referrer. */ }
  const campaign = {};
  for (const name of ['utm_source','utm_medium','utm_campaign']) { const value=params.get(name); if (value && /^[A-Za-z0-9_.-]{1,80}$/.test(value)) campaign[name]=value; }
  const common = {path:location.pathname,screen:`${Math.max(1,Math.min(99999,screen.width))}x${Math.max(1,Math.min(99999,screen.height))}`,language:navigator.language || 'en',timezone:Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',referrer,campaign};
  let sent = 0;
  const send = (event,data={}) => {
    if (isExcluded() || sent >= 40) return;
    sent += 1;
    fetch('/__analytics',{method:'POST',credentials:'omit',cache:'no-store',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify({...common,event,data})}).catch(()=>{});
  };
  send('pageview');
  document.addEventListener('click',event=>{
    const button = event.target.closest('[data-filter]');
    if (button) { send('filter',{category:button.dataset.filter}); return; }
    const anchor = event.target.closest('a[href]');
    if (!anchor) return;
    let url;try { url=new URL(anchor.href); } catch (_) {return;}
    let detail;
    const project=url.origin===location.origin && /^\/projects\/([a-z-]+)\/$/.exec(url.pathname);
    if (project) detail={kind:'project',target:project[1]};
    else if (allowedHosts[url.hostname]) detail={kind:'service',target:allowedHosts[url.hostname]};
    else if (url.hostname==='github.com') detail={kind:'github',target:'github'};
    else if (url.hostname==='www.linkedin.com') detail={kind:'linkedin',target:'linkedin'};
    else if (anchor.classList.contains('record-source')) {
      const target=url.hostname.endsWith('naver.com') ? 'naver' : url.hostname.endsWith('dcinside.com') ? 'dcinside' : url.hostname.endsWith('.tistory.com') ? 'tistory' : null;
      if (target) detail={kind:'source',target};
    } else if (url.origin===location.origin && ['#work','#about','#records','#contact','#main'].includes(url.hash)) detail={kind:'navigation',target:url.hash.slice(1)};
    if (detail) send('link',detail);
  });
  const scrollBuckets = new Set();let queued=false;
  window.addEventListener('scroll',()=>{
    if (queued) return;queued=true;
    requestAnimationFrame(()=>{
      queued=false;
      const height=document.documentElement.scrollHeight;
      if (height<=innerHeight+100) return;
      const percent=(scrollY+innerHeight)/height*100;
      for (const bucket of [25,50,75,100]) if (percent>=bucket-0.5 && !scrollBuckets.has(bucket)) {scrollBuckets.add(bucket);send('scroll',{percent:bucket});}
    });
  },{passive:true});
  let active=0,last=performance.now();const activeBuckets=new Set();
  setInterval(()=>{
    const now=performance.now();
    if (document.visibilityState==='visible' && document.hasFocus()) active+=Math.min(now-last,10000)/1000;
    last=now;
    for (const seconds of [15,30,60,120,300]) if (active>=seconds && !activeBuckets.has(seconds)) {activeBuckets.add(seconds);send('active',{seconds});}
  },5000);
})();
