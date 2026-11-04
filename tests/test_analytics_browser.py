"""Exercise the actual browser tracker without sending any network requests."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]


class TrackerTest(unittest.TestCase):
    def test_collection_opt_out_signals_and_event_buckets(self):
        code=r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
function run(options={}) {
  const calls=[],handlers={},buttons={},store=new Map(),state={now:0};
  for (const id of ['analytics-preference','exclude-analytics','allow-analytics']) buttons[id]={textContent:'',addEventListener(type,fn){this[type]=fn;}};
  const document={cookie:'',referrer:'https://example.com/private?secret=discard',visibilityState:'visible',hasFocus:()=>true,documentElement:{scrollHeight:2000},getElementById:id=>buttons[id],addEventListener:(type,fn)=>{handlers[type]=fn;}};
  if (options.excluded) store.set('yangarch_analytics_optout','1');
  const context={document,URL,URLSearchParams,Set,Intl,screen:{width:1920,height:1080},navigator:{language:'ko-KR',doNotTrack:options.dnt?'1':'0',globalPrivacyControl:!!options.gpc},localStorage:{getItem:key=>store.get(key),setItem:(key,value)=>store.set(key,value)},location:{hostname:options.local?'localhost':'yangarch.net',protocol:'https:',origin:'https://yangarch.net',pathname:'/',search:options.off?'?analytics=off':'?token=discard&utm_source=naver&gclid=discard',hash:''},history:{replaceState:()=>{}},fetch:(url,config)=>{calls.push({url,...config,body:JSON.parse(config.body)});return Promise.resolve();},window:{addEventListener:(type,fn)=>{handlers[type]=fn;}},requestAnimationFrame:fn=>fn(),innerHeight:600,scrollY:1400,performance:{now:()=>state.now},setInterval:fn=>{state.timer=fn;}};
  vm.runInNewContext(source,context);return {calls,handlers,buttons,context,state};
}
for (const option of ['excluded','dnt','gpc','off','local']) assert.equal(run({[option]:true}).calls.length,0,option);
const result=run();assert.equal(result.calls.length,1);
assert.equal(result.calls[0].url,'/__analytics');assert.equal(result.calls[0].credentials,'omit');
assert.equal(result.calls[0].body.path,'/');assert.equal(result.calls[0].body.referrer,'https://example.com');
assert.equal(JSON.stringify(result.calls).includes('discard'),false);
result.handlers.scroll();result.handlers.scroll();assert.equal(result.calls.filter(x=>x.body.event==='scroll').length,4);
for (const now of [5000,10000,15000,20000]) {result.state.now=now;result.state.timer();}
assert.equal(result.calls.filter(x=>x.body.event==='active').length,1);
const anchor={href:'https://poogiegram.poogie.one/private?discard',classList:{contains:()=>false}};
result.handlers.click({target:{closest:selector=>selector==='a[href]'?anchor:null}});
assert.equal(result.calls.at(-1).body.data.target,'poogiegram');assert.equal(JSON.stringify(result.calls).includes('private?'),false);
result.buttons['exclude-analytics'].click();const before=result.calls.length;
result.handlers.click({target:{closest:selector=>selector==='a[href]'?anchor:null}});assert.equal(result.calls.length,before);
assert.match(result.context.document.cookie,/SameSite=Lax; Secure/);
console.log('PASS: tracker privacy controls, URL reduction, fixed destination, clicks and event deduplication.');
'''
        result=subprocess.run(['node','-e',code,str(ROOT/'assets/analytics.js')],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main()
