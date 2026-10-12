// Run the real frontend persistence code with controlled network timing.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('tools/build_atlas.py','utf8');
const start=src.indexOf('let mkBase=null');
const script=src.slice(start,src.indexOf('/* ---------- 风格评级',start));
function harness(){
 const storage=new Map(),requests=[];
 const ctx={SETS:[],ACTIVE:'a',_colorSeq:0,LS_KEY:'marks',DEFAULT_SET_NAME:'默认',useServer:true,
  location:{protocol:'http:'},refreshMarks(){},toast(){},console,
  localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
  fetch:async(url,options)=>{requests.push(JSON.parse(options.body));return {ok:true,json:async()=>JSON.parse(options.body).desired};}};
 vm.createContext(ctx);vm.runInContext(script,ctx);
 vm.runInContext(`mkBase={sets:[{id:'a',name:'A',paths:[],note:{},color:0}],active:'a'};mkAck=cloneDoc(mkBase);acceptMarks(mkBase);`,ctx);
 return {ctx,storage,requests};
}
(async()=>{
 let h=harness();vm.runInContext(`SETS[0].paths.add('one');mkPersist();SETS[0].paths.add('two');mkPersist();`,h.ctx);
 await vm.runInContext('mkQueue',h.ctx);
 assert.equal(h.requests.length,2);assert.deepEqual(h.requests[1].baseline.sets[0].paths,['one']);
 assert.deepEqual(h.requests[1].desired.sets[0].paths,['one','two']);assert(!h.storage.has('marks_pending'));
 h=harness();h.ctx.fetch=async()=>{throw Error('offline')};
 vm.runInContext(`SETS[0].paths.add('one');mkPersist();SETS[0].paths.add('two');mkPersist();`,h.ctx);
 await vm.runInContext('mkQueue',h.ctx);
 const pending=JSON.parse(h.storage.get('marks_pending'));
 assert.deepEqual(pending.baseline.sets[0].paths,[]);assert.deepEqual(pending.desired.sets[0].paths,['one','two']);
 h=harness();h.storage.set('marks',JSON.stringify({sets:[{id:'stale',paths:['deleted'],name:'Old',note:{}}],active:'stale'}));
 h.ctx.fetch=async()=>({ok:true,json:async()=>({sets:[{id:'a',paths:['remote'],name:'A',note:{},color:0}],active:'a'})});
 vm.runInContext('mkInit()',h.ctx);await new Promise(r=>setImmediate(r));
 assert.equal(h.ctx.SETS.length,1);assert.equal(h.ctx.SETS[0].id,'a');assert(!h.ctx.SETS[0].paths.has('deleted'));
 console.log('PASS: rapid saves, offline recovery, server-authoritative startup');
})().catch(e=>{console.error(e);process.exit(1)});
