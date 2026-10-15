import {test} from 'node:test';
import assert from 'node:assert/strict';
import {inWindow,tick} from './worker.mjs';
const now=new Date('2026-10-15T18:32:00Z');
test('NYSE dates, extended hours and DST',()=>{
 assert.equal(inWindow(now),true);
 assert.equal(inWindow(new Date('2026-10-17T18:32:00Z')),false);
 assert.equal(inWindow(new Date('2026-12-25T18:32:00Z')),false);
 assert.equal(inWindow(new Date('2026-10-15T08:17:00Z')),true);
 assert.equal(inWindow(new Date('2026-10-16T00:32:00Z')),true);
 assert.equal(inWindow(new Date('2026-10-16T00:47:00Z')),false);
 assert.equal(inWindow(new Date('2026-11-03T09:17:00Z')),true);
});
for(const [name,published,runs,expected] of [
 ['fresh','2026-10-15T18:25:00Z',[],'recent_publication'],
 ['running','2026-10-15T15:00:00Z',[{status:'in_progress'}],'job_already_running'],
 ['stale','2026-10-15T15:00:00Z',[{status:'completed'}],'dispatched']]){
 test(name,async()=>{const calls=[];const request=async(url,init)=>{calls.push({url,init});return url.includes('latest.json')?Response.json({publishedAt:published}):url.includes('/runs?')?Response.json({workflow_runs:runs}):new Response(null,{status:204});};
 assert.equal((await tick({GITHUB_TOKEN:'test'},now,request)).state,expected);
 assert.equal(calls.filter(c=>c.init.method==='POST').length,expected==='dispatched'?1:0);
 });
}
test('API failure fails visibly',async()=>{await assert.rejects(tick({},now,async()=>new Response(null,{status:503})),/503/);});
