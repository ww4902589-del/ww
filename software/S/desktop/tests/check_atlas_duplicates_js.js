const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('tools/build_atlas.py','utf8');
const start=source.indexOf('function uniqueImages(');
assert(start>=0,'missing content deduplication in real gallery template');
const code=source.slice(start,source.indexOf('function apply()',start));
const context={isMarked:(u)=>u==='saved-copy',usrGradeOf:()=>null};
vm.createContext(context);vm.runInContext(code,context);
const images=[
 {uid:'original',path:'desktop/batch/image.png',content_hash:'same'},
 {uid:'saved-copy',path:'desktop/presets/image.png',content_hash:'same'},
 {uid:'backup',path:'E/backup/image.png',content_hash:'same'},
 {uid:'different',path:'other/image.png',content_hash:'different'},
 {uid:'unknown-a',path:'a/unknown.png'}, {uid:'unknown-b',path:'b/unknown.png'}];
context.images=images;
const result=vm.runInContext('uniqueImages(images)',context);
assert.equal(result.length,4);
assert.equal(result[0].uid,'saved-copy','prefer an existing favorite');
assert.equal(images.length,6,'keep original records');
assert.equal(vm.runInContext("uniqueImages(images.filter(im=>im.path.startsWith('E/')))[0].uid",context),'backup');
assert(source.includes('hit=uniqueImages(D.images.filter(matches))'),'filter before deduplicating');
assert(source.includes('return {live:uniqueImages(live),dead}'),'favorites gallery also deduplicates');
console.log('PASS: identical copies, different contents, unknown hashes, folder filters, favorites and records');
