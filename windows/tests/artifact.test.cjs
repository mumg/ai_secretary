const {test}=require('node:test');
const assert=require('node:assert/strict');
const find=require('../find-artifact.cjs');
const context={repo:{owner:'owner',repo:'repo'},sha:'a'.repeat(40),runId:99};
const run={id:1,head_sha:context.sha,conclusion:'success',event:'push',head_branch:'main',head_repository:{full_name:'owner/repo'}};
const artifact={name:'windows-installer-tested',expired:false,size_in_bytes:100,expires_at:'2099-01-01T00:00:00Z'};
async function select(runs,artifacts){
 const outputs={};const visited=[];
 const github={rest:{actions:{listWorkflowRuns:'runs',listWorkflowRunArtifacts:'artifacts'}},paginate:async(method,args)=>{
  if(method==='runs'){assert.equal(args.head_sha,context.sha);assert.equal(args.workflow_id,'windows.yml');return runs;}
  visited.push(args.run_id);return artifacts[args.run_id]||[];
 }};
 await find({github,context,core:{setOutput:(k,v)=>outputs[k]=v,info:()=>{}}});
 return {outputs,visited};
}
test('reuses only completed tested installers and falls back past newer runs without one',async()=>{
 const result=await select([{...run,id:2,event:'workflow_dispatch'},run],{1:[artifact]});
 assert.equal(result.outputs.run_id,'1');assert.deepEqual(result.visited,[2,1]);
});
test('rejects other commits, forks, PRs, failed runs, untrusted branches and current run',async()=>{
 for(const change of [{head_sha:'b'.repeat(40)},{head_repository:{full_name:'fork/repo'}},{event:'pull_request'},
  {conclusion:'failure'},{head_branch:'feature'},{id:99}]){
  const result=await select([{...run,...change}],{1:[artifact],99:[artifact]});
  assert.equal(result.outputs.run_id,'');assert.deepEqual(result.visited,[]);
 }
});
test('missing, expired, legacy and empty artifacts require a fresh build',async()=>{
 for(const artifacts of [[],[{...artifact,expired:true}],[{...artifact,name:'windows-installer'}],
  [{...artifact,size_in_bytes:0}],[{...artifact,expires_at:'2000-01-01T00:00:00Z'}]]){
  assert.equal((await select([run],{1:artifacts})).outputs.run_id,'');
 }
});
