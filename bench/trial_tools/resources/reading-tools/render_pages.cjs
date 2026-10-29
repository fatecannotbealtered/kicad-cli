'use strict';
// Finite Host-only rasterizer. It never invokes KiCad, a shell or another child.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const Module = require('node:module');
const child = require('node:child_process');
const [planFile, expectedPlan, mode] = process.argv.slice(2);
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const fileHash = p => hash(fs.readFileSync(p));
const need = (ok, why) => { if (!ok) throw new Error(why); };
const ident = p => ({path:p, sha256:fileHash(p), size:fs.statSync(p).size});
const writeJSON = (p,v) => fs.writeFileSync(p, JSON.stringify(v,null,2)+'\n', {flag:'wx'});
need(fileHash(planFile) === expectedPlan, 'Frozen plan changed');
const plan = JSON.parse(fs.readFileSync(planFile));
need(['qualify','render-pages'].includes(mode), 'Unknown phase');
for (const row of [...plan.runtime_files, ...plan.fixed_files]) {
  need(fileHash(row.path) === row.sha256 && fs.statSync(row.path).size === row.size, 'Changed declared input '+row.path);
}
need(fileHash(process.execPath) === plan.node.sha256, 'Different Node executable');
need(process.platform === 'win32' && process.arch === 'x64', 'Unqualified platform');
need(!process.env.NODE_OPTIONS && !process.env.NODE_PATH && process.env.NODE_DISABLE_COMPILE_CACHE === '1', 'Ambient Node code/cache injection');
need(process.env.SHARP_IGNORE_GLOBAL_LIBVIPS === '1', 'Global libvips discovery must stay disabled');
const normalized = p => fs.realpathSync(p).toLowerCase();
const allowed = new Map([...plan.runtime_files, ...plan.fixed_files].map(r => [normalized(r.path),r]));
const loaded = new Map();
const originalLoad = Module._load;
Module._load = function(request, parent, isMain) {
  const resolved = Module._resolveFilename(request,parent,isMain);
  if (!Module.isBuiltin(resolved)) {
    const r = allowed.get(normalized(resolved));
    need(r && fileHash(resolved) === r.sha256, 'Undeclared code load '+resolved);
    loaded.set(normalized(resolved), ident(resolved));
  }
  return originalLoad.apply(this,arguments);
};
for (const key of ['spawn','spawnSync','exec','execSync','execFile','execFileSync','fork']) {
  child[key] = () => { throw new Error('Renderer child-process dispatch refused'); };
}
const sharp = require(plan.sharp_entry);
sharp.cache(false);
sharp.concurrency(1);
function noExternalOrVisibleFonts(svg) {
  need(!/<(?:image|script|foreignObject)\b/i.test(svg) && !/\bhref\s*=/i.test(svg), 'External or executable SVG content refused');
  for (const tag of svg.match(/<text\b[^>]*>/g) || []) need(/\bopacity="0"/.test(tag), 'Visible font-dependent text refused');
}
async function render(svg, output, density) {
  noExternalOrVisibleFonts(svg);
  need(!fs.existsSync(output), 'No-clobber output exists');
  const root=svg.match(/<svg\b[^>]*>/);need(root,'SVG root missing');
  const dimensions={};let rasterRoot=root[0];
  for(const key of ['width','height']){
    const m=root[0].match(new RegExp('\\b'+key+'="([0-9.]+)(mm|px)?"'));
    need(m,'Explicit finite SVG viewport dimension required');
    const value=Number(m[1]);need(value>0&&Number.isFinite(value),'Positive viewport');
    const px=Math.round(value*density/(m[2]==='mm'?25.4:72));need(px>0,'Nonempty raster viewport');
    dimensions[key]={svg:m[0],pixels:px};rasterRoot=rasterRoot.replace(m[0],key+'="'+px+'px"');
  }
  need(dimensions.width.pixels*dimensions.height.pixels<=20000000,'Declared viewport exceeds20M pixel bound');
  const rasterSVG=svg.replace(root[0],rasterRoot);
  const info = await sharp(Buffer.from(rasterSVG), {density:72, limitInputPixels:20000000, failOn:'error'})
    .flatten({background:"#ffffff"}).ensureAlpha().png({compressionLevel:6, adaptiveFiltering:false, palette:false}).toFile(output);
  need(info.format==='png' && info.channels===4, 'Expected RGBA PNG');
  need(info.width===dimensions.width.pixels&&info.height===dimensions.height.pixels,'Full raster viewport size');
  return {output:ident(output), width:info.width, height:info.height, channels:info.channels, density,
    raster_viewport:dimensions,loader_density:72,render_buffer_sha256:hash(Buffer.from(rasterSVG)),
    coverage:'Only root physical dimensions converted to explicit output pixels; original viewBox/all content preserved. No resize/extract/crop/flip.', memory:process.memoryUsage()};
}
// Independent minimal PNG byte decoder for the known RGBA8 fixture, using built-in zlib.
function pixels(p) {
  const b=fs.readFileSync(p); need(b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])), 'PNG signature');
  let w,h,depth,colour,offset=8,parts=[];
  while(offset<b.length){const len=b.readUInt32BE(offset),tag=b.toString('ascii',offset+4,offset+8),d=b.subarray(offset+8,offset+8+len);
    if(tag==='IHDR'){w=d.readUInt32BE(0);h=d.readUInt32BE(4);depth=d[8];colour=d[9];need(d[12]===0,'Interlace refused');}
    if(tag==='IDAT')parts.push(d); offset+=12+len;
  }
  need(depth===8 && colour===6,'RGBA8 fixture required');const packed=zlib.inflateSync(Buffer.concat(parts)),stride=w*4,out=Buffer.alloc(stride*h);
  const paeth=(a,b,c)=>{const p=a+b-c,pa=Math.abs(p-a),pb=Math.abs(p-b),pc=Math.abs(p-c);return pa<=pb&&pa<=pc?a:pb<=pc?b:c;};
  for(let y=0;y<h;y++){const type=packed[y*(stride+1)];need(type<=4,'PNG filter');for(let x=0;x<stride;x++){
    const a=x>=4?out[y*stride+x-4]:0,b=y?out[(y-1)*stride+x]:0,c=y&&x>=4?out[(y-1)*stride+x-4]:0;
    out[y*stride+x]=(packed[y*(stride+1)+1+x]+[0,a,b,Math.floor((a+b)/2),paeth(a,b,c)][type])&255;
  }}return {w,h,at:(x,y)=>[...out.subarray((y*w+x)*4,(y*w+x)*4+4)]};
}
function unchanged(){for(const r of [...plan.runtime_files,...plan.fixed_files])need(fileHash(r.path)===r.sha256,'Input changed '+r.path);}
function validatePages(report, rows){
 need(report.schema==='finite-native-hierarchy-SVG-page-identity/v2'&&report.status==='passed','Passed native page identity v2 required');
 need(JSON.stringify(Object.keys(report.pages).sort())===JSON.stringify(['A','B','root']),'Exactly root/A/B page identities');
 need(JSON.stringify(Object.keys(report.source_schematic_bindings).sort())===JSON.stringify(['A','B','root']),'Exactly three source bindings');
 need(rows.length===3&&new Set(rows.map(r=>r.name)).size===3,'Exactly three distinct output pages');
 const rawNames=new Set();
 for(const row of rows){
  need(['root','A','B'].includes(row.name),'Unknown page');const page=report.pages[row.name];
  need(page.source_svg.sha256===row.native.sha256&&path.basename(page.source_svg.path)===path.basename(row.native.path),'Page SVG identity differs');
  need(row.derivative.path===row.native.path&&row.derivative.sha256===row.native.sha256,'Native source bytes must be the actual renderer input');
  need(row.check.path===plan.page_identity.path&&row.check.sha256===plan.page_identity.sha256,'Wrong page identity check');
  need(report.raw_SVG_hashes[path.basename(row.native.path)]===row.native.sha256,'Raw page output set differs');
  need(!rawNames.has(path.basename(row.native.path)),'Duplicate raw page');rawNames.add(path.basename(row.native.path));
  need(fileHash(row.native.path)===row.native.sha256,'Raw page changed');
  const svg=fs.readFileSync(row.native.path,'utf8'),root=svg.match(/<svg\b[^>]*>/);need(root,'Native SVG root');
  for(const key of ['width','height','viewBox']){const m=root[0].match(new RegExp('\\b'+key+'="([^"]+)"'));need(m&&m[1]===page.observation.viewport[key],'Page viewport differs from identity report');}
  const source=report.source_schematic_bindings[row.name];need(fileHash(source.path)===source.sha256,'Saved source SCH changed');
 }
 need(Object.keys(report.raw_SVG_hashes).length===3&&rawNames.size===3,'Exact three raw SVG outputs required');
}
async function main(){
 const outputs=[];let qualification;
 if(mode==='qualify'){
  const svg=fs.readFileSync(plan.fixture.path,'utf8');
  const basic=path.join(plan.directory,'qualification','fixture.png');outputs.push(await render(svg,basic,72));
  const p=pixels(basic);need(p.w===64&&p.h===48,'Fixture full viewport');
  const samples=[['background',0,0,[17,24,39,255]],['red',5,5,[255,0,0,255]],['mirror-green',58,18,[0,255,0,255]],['circle-blue',28,23,[0,0,255,255]],['path-yellow',30,38,[255,255,0,255]]];
  for(const [name,x,y,v] of samples)need(JSON.stringify(p.at(x,y))===JSON.stringify(v),'Fixture pixel '+name);
  const a4='<svg xmlns="http://www.w3.org/2000/svg" width="297.0022mm" height="210.0072mm" viewBox="0 0 297.0022 210.0072"><rect width="297.0022" height="210.0072" fill="#ffffff"/><rect x="2" y="2" width="8" height="8" fill="#ff0000"/><rect x="287" y="200" width="8" height="8" fill="#0000ff"/><path d="M20 20 L270 190" stroke="#00ff00" fill="none" stroke-width="1"/></svg>';
  for(const density of [144,288]){
   const output=path.join(plan.directory,'qualification','A4-'+density+'.png');const r=await render(a4,output,density);outputs.push(r);
   need(r.width===Math.round(297.0022*density/25.4)&&r.height===Math.round(210.0072*density/25.4)&&r.width*r.height<=20000000,'A4 full viewport density bound');
   const pp=pixels(output),scale=density/25.4;
   need(JSON.stringify(pp.at(0,0))===JSON.stringify([255,255,255,255]),'A4 white background');
   need(JSON.stringify(pp.at(Math.round(6*scale),Math.round(6*scale)))===JSON.stringify([255,0,0,255]),'A4 top-left retained');
   need(JSON.stringify(pp.at(Math.round(291*scale),Math.round(204*scale)))===JSON.stringify([0,0,255,255]),'A4 bottom-right retained');
  }
  const report=JSON.parse(fs.readFileSync(plan.page_identity.path));validatePages(report,plan.views);
  const bad={...report,status:'failed'};let refused=false;try{validatePages(bad,plan.views);}catch(_){refused=true;}need(refused,'Failed page identity must refuse');
  const duplicates=[plan.views[0],plan.views[0],plan.views[2]];refused=false;try{validatePages(report,duplicates);}catch(_){refused=true;}need(refused,'Duplicate page must refuse');
  qualification={status:'passed_bounded_native_page_renderer',checks:['px viewport/paths/circle/mirror/color','A4 full viewport144/288dpi below20M','source byte/viewport/page report binding','failed page identity refused','duplicate page refused'],limits:'Host rasterization and finite page-identity consumer only; no new Native/engineering write, UUID extraction, OCR or manufacturing claim.'};
 }else{
  const qpath=plan.qualification?.path||path.join(plan.directory,'qualification','RESULT.json');
  if(plan.qualification)need(fileHash(qpath)===plan.qualification.sha256,'Qualified page renderer evidence changed');
  const q=JSON.parse(fs.readFileSync(qpath));need(q.qualification.status==='passed_bounded_native_page_renderer'&&q.method.sha256===fileHash(__filename)&&q.node.sha256===fileHash(process.execPath),'Exact page renderer/runtime not qualified');
  if(!plan.qualification)need(q.plan_sha256===expectedPlan,'Unbound local qualification');
  const report=JSON.parse(fs.readFileSync(plan.page_identity.path));validatePages(report,plan.views);
  for(const row of plan.views){const svg=fs.readFileSync(row.native.path,'utf8');outputs.push({name:row.name,page:report.pages[row.name].expected_markers,source_schematic:report.source_schematic_bindings[row.name],native:row.native,native_bytes_unchanged:true,page_identity:plan.page_identity,
    ...await render(svg,path.join(plan.directory,'images',row.name+'.png'),plan.density)});}
  validatePages(report,plan.views);
 }
 unchanged();const actual=process.report.getReport();
 const result={schema:'finite-native-page-sharp-renderer/v1',phase:mode,plan_sha256:expectedPlan,method:ident(__filename),node:ident(process.execPath),versions:sharp.versions,qualification,outputs,
  page_identity:plan.page_identity,caller_additional_inputs:plan.caller_additional_inputs,actual_JS_or_addon_sources:[...loaded.values()],actual_shared_objects:actual.sharedObjects.filter(p=>fs.existsSync(p)).map(ident),
  source_SVG_files_changed:false,source_viewBox_changed:false,raster_background:'#ffffff',renderer_margin_claim:null,UUID_binding:'Caller additional inputs and exact source SCH hashes; this rasterizer does not extract or fabricate UUIDs.',
  KiCad_Native_calls:0,formal_engineering_writes:0,child_dispatches:0,limits:'Native SVG title/content/viewBox remain original bytes. Only in-memory root physical dimensions become explicit raster pixels. Identity proof remains finite semantic/path evidence and requires actual full-page viewing.'};
 writeJSON(path.join(plan.directory,mode==='qualify'?'qualification/RESULT.json':'RESULT.json'),result);
 console.log(JSON.stringify({phase:mode,outputs:outputs.length,status:qualification?.status,rss:process.memoryUsage().rss}));
}
main().catch(e=>{console.error(e.stack);process.exitCode=1;});
