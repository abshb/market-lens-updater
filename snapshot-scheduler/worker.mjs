import calendar from '../market-sessions.json' with {type:'json'};
const sessions=new Set(calendar.sessions.map(([date])=>date));
const api='https://api.github.com/repos/abshb/market-lens-updater/actions/workflows/snapshots.yml';
export function inWindow(now){
 const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now).map(p=>[p.type,p.value]));
 const date=`${parts.year}-${parts.month}-${parts.day}`,minute=Number(parts.hour)*60+Number(parts.minute);
 if(date>calendar.end)throw Error('Market calendar expired');
 return sessions.has(date)&&minute>=255&&minute<=1245;
}
export async function tick(env,now=new Date(),request=fetch){
 if(!inWindow(now))return {state:'outside_market_window'};
 const headers={'Authorization':`Bearer ${env.GITHUB_TOKEN}`,'Accept':'application/vnd.github+json','User-Agent':'market-lens-snapshot-scheduler','X-GitHub-Api-Version':'2022-11-28'};
 const get=async(url,init={})=>{const r=await request(url,{...init,signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error(`Scheduler request failed: ${r.status}`);return r;};
 const feed=await (await get(`https://raw.githubusercontent.com/abshb/market-lens-updater/codex/market-snapshots-v1/latest.json?check=${now.getTime()}`)).json();
 const age=now.getTime()-Date.parse(feed.publishedAt);
 if(Number.isFinite(age)&&age>=0&&age<12*60000)return {state:'recent_publication',publishedAt:feed.publishedAt};
 const runs=await (await get(`${api}/runs?per_page=10`,{headers})).json();
 if(runs.workflow_runs.some(r=>r.status!=='completed'))return {state:'job_already_running'};
 await get(`${api}/dispatches`,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify({ref:'main'})});
 return {state:'dispatched',previousPublication:feed.publishedAt};
}
export default {
 async scheduled(event,env){console.log(JSON.stringify(await tick(env,new Date(event.scheduledTime))));},
 fetch(){return new Response('Not found',{status:404});}
};
