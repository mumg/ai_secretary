'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { OllamaManager, registerIPC, modelName, download } = require('../ollama.cjs');
const { assess, GiB } = require('../ollama-system.cjs');
const selected = { model: 'qwen3:4b', contextLength: 8192 };
const hardware = overrides => ({ platform: 'win32', arch: 'x64', osVersion: '10.0.22631', avx2: true,
  threads: 8, ram: 32 * GiB, volumes: Object.fromEntries(['install','temporary','models'].map(k=>[k,{id:'disk1',free:100*GiB}])), ...overrides });
const version = ready => new Response(JSON.stringify(ready ? { version: '0.34.2' } : {}), { status: ready ? 200 : 503 });

test('hardware policy blocks unsupported OS, CPU, architecture, RAM, disk and unknown data', () => {
  assert.equal(assess(hardware(), 2*GiB,8192).eligible,true);
  assert.equal(assess(hardware({platform:'darwin',arch:'arm64',osVersion:'14.7',avx2:null}),2*GiB,8192).eligible,true);
  for (const bad of [{platform:'linux'},{arch:'ia32'},{osVersion:'10.0.17763'},{avx2:false},{avx2:null},{threads:2},{ram:4*GiB},{volumes:{}},
    {platform:'darwin',arch:'arm64',osVersion:'13.0'}]) assert.equal(assess(hardware(bad),2*GiB,8192).eligible,false,JSON.stringify(bad));
  assert.equal(assess(hardware(),null,8192).eligible,false);
  assert.equal(assess(hardware({ram:8*GiB}),16*GiB,16384).eligible,false);
  const disks=Object.fromEntries(['install','temporary','models'].map(k=>[k,{id:'same',free:12*GiB}]));
  assert.equal(assess(hardware({volumes:disks}),3*GiB,8192).eligible,false,'space must be summed on same volume');
  assert.equal(assess(hardware({volumes:disks}),3*GiB,8192,{installing:false}).eligible,true);
});

test('calling install directly cannot bypass fresh hardware inspection', async () => {
  let downloads=0, installs=0;
  for (const snapshot of [hardware({ram:4*GiB}),hardware({avx2:false}),hardware({volumes:{}})]) {
    const manager=new OllamaManager({platform:'win32',inspector:async()=>snapshot,size:async()=>2*GiB,find:async()=>null,
      fetcher:async()=>version(false),downloader:async()=>downloads++,installer:async()=>installs++});
    await manager.run('install',{...selected,eligible:true,report:{eligible:true},command:'ignored'});
    assert.equal(manager.state.phase,'blocked');assert.equal(manager.busy,false);
  }
  assert.equal(downloads,0);assert.equal(installs,0);
});

test('system inspection and model metadata failures fail closed', async () => {
  let installed=0;
  for (const inspector of [async()=>{throw Error('private-path');},async()=>hardware()]) {
    const manager=new OllamaManager({platform:'win32',inspector,size:async()=>{throw Error('offline');},find:async()=>null,fetcher:async()=>version(false),installer:async()=>installed++});
    await manager.run('install',selected);assert.equal(manager.state.report?.eligible||false,false);
    assert.ok(!JSON.stringify(manager.state).includes('private-path'));
  }
  assert.equal(installed,0);
});

test('installation rechecks resources after downloading and does not execute if disk changed', async () => {
  let inspections=0,downloads=0,installs=0;
  const manager=new OllamaManager({platform:'win32',inspector:async()=>++inspections===1?hardware():hardware({volumes:{}}),size:async()=>2*GiB,
    find:async()=>null,fetcher:async()=>version(false),downloader:async()=>downloads++,installer:async()=>installs++});
  await manager.run('install',selected);
  assert.equal(downloads,1);assert.equal(installs,0);assert.equal(manager.state.phase,'blocked');
});

test('eligible installation and existing installation reuse have no settings writes', async () => {
  for (const existing of [null,'user/Ollama']) {
    let downloads=0,installs=0,started=false,fetches=[];
    const manager=new OllamaManager({platform:'win32',inspector:async()=>hardware(),size:async()=>2*GiB,find:async()=>existing,
      fetcher:async url=>{fetches.push(url);return version(started);},downloader:async()=>downloads++,
      installer:async()=>{installs++;return 'user/Ollama';},starter:async()=>{started=true;}});
    await manager.run('install',selected);
    assert.equal(manager.state.phase,'ready');assert.equal(installs,existing?0:1);assert.equal(downloads,existing?0:1);
    assert.ok(fetches.every(url=>url==='http://127.0.0.1:11434/api/version'));
  }
});

test('a model becomes usable only after successful download AND inference', async () => {
  for (const inference of [true,false]) {
    const manager=new OllamaManager({platform:'win32',inspector:async()=>hardware(),size:async()=>2*GiB,find:async()=>null,
      fetcher:async(url,options)=>{
        if(url.endsWith('/api/version'))return version(true);
        if(url.endsWith('/api/pull'))return new Response('{"total":100,"completed":90}\n{"status":"success"}\n');
        assert.ok(url.endsWith('/api/generate'));
        assert.equal(JSON.parse(options.body).options.num_ctx,8192);
        assert.equal(JSON.parse(options.body).options.num_gpu,-1);
        return new Response(JSON.stringify(inference?{done:true}:{error:'private diagnostic'}),{status:inference?200:500});
      }});
    await manager.run('pull',selected);
    assert.equal(manager.state.modelReady,inference);
    assert.equal(manager.state.phase,inference?'model_ready':'error');
    assert.ok(!JSON.stringify(manager.state).includes('private diagnostic'));
  }
});

test('IPC rejects remote pages, wrong windows and subframes', () => {
  let listener,calls=0;
  const frame={url:'http://127.0.0.1:18000/admin'},contents={mainFrame:frame};
  const manager={on(){},run(){calls++;},cancel(){calls++;}};
  registerIPC({on(c,fn){listener=fn;}},manager,()=>contents,()=> 'http://127.0.0.1:18000');
  for(const [sender,senderFrame] of [[{},frame],[contents,{...frame}],[contents,{url:'https://evil.test/admin'}]])listener({sender,senderFrame},{action:'install',...selected});
  assert.equal(calls,0);
  listener({sender:contents,senderFrame:frame},{action:'install',...selected});assert.equal(calls,1);
  frame.url='http://127.0.0.1:18000/app/';listener({sender:contents,senderFrame:frame},{action:'install',...selected});assert.equal(calls,1);
});

test('downloads require the pinned digest and size; redirects cannot escape HTTPS release hosts',async t=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'secretary-download-test-'));t.after(()=>fs.rm(dir,{recursive:true,force:true}));
  const body=Buffer.from('verified test package');
  const artifact={url:'https://github.com/ollama/ollama/releases/download/v0.0.0/test',bytes:body.length,sha256:crypto.createHash('sha256').update(body).digest('hex')};
  await download(artifact,path.join(dir,'valid'),async()=>new Response(body),new AbortController().signal,()=>{});
  assert.deepEqual(await fs.readFile(path.join(dir,'valid')),body);
  await assert.rejects(download({...artifact,sha256:'0'.repeat(64)},path.join(dir,'wrong'),async()=>new Response(body),new AbortController().signal,()=>{}),/integrity/);
  await assert.rejects(download(artifact,path.join(dir,'redirect'),async()=>new Response(null,{status:302,headers:{location:'http://evil.test/file'}}),new AbortController().signal,()=>{}),/download/);
});

test('model identifiers cannot inject paths or alternative registries',()=>{
  for(const value of ['../../file','https://evil.test/model','model;command','-x','model:tag/else'])assert.throws(()=>modelName(value));
  assert.equal(modelName('qwen3'),'qwen3:latest');
});

test('cancelled download cannot execute installer and concurrent clicks do not duplicate work', async () => {
  let entered, releaseDownload, downloads=0, installs=0;
  const waiting=new Promise(resolve=>{entered=resolve;});
  const manager=new OllamaManager({platform:'win32',inspector:async()=>hardware(),size:async()=>2*GiB,find:async()=>null,fetcher:async()=>version(false),
    downloader:async()=>{downloads++;entered();await new Promise(resolve=>{releaseDownload=resolve;});},installer:async()=>installs++});
  const run=manager.run('install',selected);await waiting;
  await manager.run('install',selected);manager.cancel();releaseDownload();await run;
  assert.equal(downloads,1);assert.equal(installs,0);assert.equal(manager.state.phase,'cancelled');
});

test('desktop package definitions include installer code and pinned release metadata', () => {
  const fsSync=require('node:fs');
  for(const config of [require('../electron-builder.cjs'),require('../../windows/electron-builder.cjs')]) {
    for(const file of ['ollama.cjs','ollama-system.cjs','ollama-release.json']) {
      assert.ok(config.files.includes(file));assert.ok(fsSync.existsSync(path.join(__dirname,'..',file)));
    }
  }
  const manifest=require('../ollama-release.json');
  for(const platform of ['win32','darwin']) {
    assert.match(manifest[platform].sha256,/^[a-f0-9]{64}$/);
    assert.ok(manifest[platform].url.startsWith(`https://github.com/ollama/ollama/releases/download/v${manifest.version}/`));
  }
});

test('cancelling during the second inspection still prevents execution',async()=>{
  let count=0,installs=0,entered,release;
  const ready=new Promise(resolve=>{entered=resolve;});
  const manager=new OllamaManager({platform:'win32',inspector:async()=>{
    if(++count===2){entered();await new Promise(resolve=>{release=resolve;});}
    return hardware();
  },size:async()=>2*GiB,find:async()=>null,fetcher:async()=>version(false),downloader:async()=>{},installer:async()=>installs++});
  const run=manager.run('install',selected);await ready;manager.cancel();release();await run;
  assert.equal(installs,0);assert.equal(manager.state.phase,'cancelled');
});


test('setup installs, starts, downloads and verifies in a single operation, reusing an existing server', async () => {
  for (const alreadyRunning of [false, true]) {
    let ready=alreadyRunning, installs=0, downloads=0;const phases=[];
    const manager=new OllamaManager({platform:'win32',inspector:async()=>hardware(),size:async()=>2*GiB,find:async()=>null,
      downloader:async()=>{downloads++;},installer:async()=>{installs++;return 'user/Ollama';},starter:async()=>{ready=true;},
      fetcher:async url=>{
        if(url.endsWith('/api/version'))return version(ready);
        if(url.endsWith('/api/pull'))return new Response('{"total":100,"completed":50}\n{"status":"success"}\n');
        assert.ok(url.endsWith('/api/generate'));return new Response('{"done":true}');
      }});
    manager.on('state', state=>phases.push(state.phase));
    await manager.run('setup',selected);
    assert.equal(manager.state.phase,'model_ready');assert.equal(manager.state.modelReady,true);
    assert.equal(installs,alreadyRunning?0:1);assert.equal(downloads,alreadyRunning?0:1);
    assert.ok(phases.indexOf('pulling')<phases.indexOf('verifying'));
  }
});

test('setup rechecks resources and blocks before any installation on insufficient hardware', async () => {
  let downloads=0;
  const manager=new OllamaManager({platform:'win32',inspector:async()=>hardware({ram:4*GiB}),size:async()=>2*GiB,
    find:async()=>null,fetcher:async()=>version(false),downloader:async()=>downloads++});
  await manager.run('setup',selected);
  assert.equal(manager.state.phase,'blocked');assert.equal(downloads,0);
});

test('Chromium download transport exposes redirects and streams all chunks with backpressure', async t => {
  const { EventEmitter } = require('node:events');
  const { Readable } = require('node:stream');
  const { downloadFetcher } = require('../ollama.cjs');
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'secretary-chromium-test-'));
  t.after(()=>fs.rm(dir,{recursive:true,force:true}));
  const chunks=Array.from({length:100},(_,i)=>Buffer.alloc(16384,i));
  const body=Buffer.concat(chunks), urls=[];
  const net={request(options){
    urls.push(options.url);assert.equal(options.redirect,'manual');
    const request=new EventEmitter();
    request.abort=()=>request.emit('close');
    request.end=()=>queueMicrotask(()=>{
      if(urls.length===1)request.emit('redirect',302,'GET','https://release-assets.githubusercontent.com/test');
      else {const response=Readable.from(chunks);response.statusCode=200;request.emit('response',response);response.on('end',()=>request.emit('close'));}
    });
    return request;
  }};
  const artifact={url:'https://github.com/ollama/test',bytes:body.length,sha256:crypto.createHash('sha256').update(body).digest('hex')};
  await download(artifact,path.join(dir,'result'),downloadFetcher(net),AbortSignal.timeout(5000),()=>{});
  assert.deepEqual(await fs.readFile(path.join(dir,'result')),body);
  assert.deepEqual(urls,[artifact.url,'https://release-assets.githubusercontent.com/test']);
});

test('transport errors while downloading show the download error instead of generic operation', async () => {
  const manager=new OllamaManager({platform:'win32',inspector:async()=>hardware(),size:async()=>2*GiB,
    find:async()=>null,fetcher:async()=>version(false),downloader:async()=>{throw Error('net::ERR_CONNECTION_RESET');}});
  await manager.run('setup',selected);
  assert.equal(manager.state.phase,'error');assert.equal(manager.state.error,'download');
});


test('runtime status reports actual loaded model memory and clears stale data when offline', async () => {
  let offline=false, psFailure=false;
  const manager=new OllamaManager({fetcher:async url=>{
    if(offline)throw Error('offline');
    if(url.endsWith('/api/version'))return version(true);
    if(url.endsWith('/api/tags'))return new Response(JSON.stringify({models:[{name:'qwen3:4b'}]}));
    assert.ok(url.endsWith('/api/ps'));
    if(psFailure)throw Error('ps unavailable');
    return new Response(JSON.stringify({models:[{name:'qwen3:4b',size:4*GiB,size_vram:3*GiB,context_length:8192,details:{quantization_level:'Q4_K_M'}}]}));
  }});
  let status=await manager.runtimeStatus();
  assert.equal(status.available,true);assert.equal(status.models[0].sizeVRAM,3*GiB);
  assert.equal(status.models[0].contextLength,8192);assert.equal(status.models[0].quantization,'Q4_K_M');
  assert.deepEqual(status.installedModels,['qwen3:4b']);
  psFailure=true;status=await manager.runtimeStatus();assert.equal(status.available,true);assert.equal(status.models,null);
  offline=true;status=await manager.runtimeStatus();assert.equal(status.available,false);assert.equal(status.models,null);
});
