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
need(['qualify','render','render-bound'].includes(mode), 'Unknown phase');
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
const title = /<title>[^<]*<\/title>/g;
function transfer(current, previous, derivative) {
  const a = current.match(title), b = previous.match(title), c = derivative.match(title);
  need(a && b && c && a.length===1 && b.length===1 && c.length===1, 'Exactly one literal metadata title required');
  need(current.replace(title,'<title/>') === previous.replace(title,'<title/>'), 'Native content differs beyond metadata title');
  need(c[0] === b[0], 'Previous derivative title is not its native source title');
  return derivative.replace(title,a[0]);
}
function noExternalOrVisibleFonts(svg) {
  need(!/<(?:image|script|foreignObject)\b/i.test(svg) && !/\bhref\s*=/i.test(svg), 'External or executable SVG content refused');
  for (const tag of svg.match(/<text\b[^>]*>/g) || []) need(/\bopacity="0"/.test(tag), 'Visible font-dependent text refused');
}
function validateNewGeometry(row) {
  const check=JSON.parse(fs.readFileSync(row.check.path));
  need(check.status==='geometry_preserved_pending_actual_view' && check.raw_sha256===row.native.sha256 &&
    check.derivative_sha256===row.derivative.sha256 && check.all_original_elements_preserved===true &&
    check.every_primitive_inside_reading_viewport===true && check.geometry_or_opacity_edits===0 && check.margin_mm==='2',
    'New derivative independent geometry check is missing or differs');
  return check;
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
    .ensureAlpha().png({compressionLevel:6, adaptiveFiltering:false, palette:false}).toFile(output);
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
function inputRowsUnchanged(){for(const r of [...plan.runtime_files,...plan.fixed_files])need(fileHash(r.path)===r.sha256,'Input changed during renderer execution '+r.path);}
async function main(){
 const outputs=[];let qualification;
 if(mode==='qualify'){
   const svg=fs.readFileSync(plan.fixture.path,'utf8'),out=path.join(plan.directory,'qualification','fixture.png');
   outputs.push(await render(svg,out,72));const p=pixels(out);need(p.w===64&&p.h===48,'Full fixture viewport dimensions');
   const samples=[['background',0,0,[17,24,39,255]],['red-rect',5,5,[255,0,0,255]],['mirrored-green',58,18,[0,255,0,255]],['blue-circle',28,23,[0,0,255,255]],['yellow-path',30,38,[255,255,0,255]]];
   for(const [name,x,y,expected] of samples)need(JSON.stringify(p.at(x,y))===JSON.stringify(expected),'Fixture pixel '+name);
   const physical=svg.replace('width="64px"','width="25.4mm"').replace('height="48px"','height="19.05mm"');
   const physicalOut=path.join(plan.directory,'qualification','physical-576dpi.png');outputs.push(await render(physical,physicalOut,576));
   const pp=pixels(physicalOut);need(pp.w===576&&pp.h===432,'Physical mm viewport at target576dpi');
   for(const [name,x,y,expected] of samples)need(JSON.stringify(pp.at(Math.floor(x*9),Math.floor(y*9)))===JSON.stringify(expected),'Physical fixture pixel '+name);
   const current='<svg><title>new</title><path d="M0 0L1 1"/></svg>',previous=current.replace('new','old');
   need(transfer(current,previous,previous)===current,'Exact title transfer');let refused=false;
   try{transfer(current.replace('L1 1','L2 1'),previous,previous);}catch(_){refused=true;}need(refused,'Changed geometry must refuse');
   qualification={status:'passed_bounded_host_renderer_fixture',checks:samples.map(x=>x[0]).concat(['full-viewport','physical-mm-576dpi-full-viewport-and-pixels','exact-title-transfer','geometry-change-refused']),
     limits:'Finite stroked SVG fixture; not arbitrary fonts, KiCad Native acceptance or engineering authorization.'};
 }else if(mode==='render-bound'){
   need(plan.qualification && fileHash(plan.qualification.path)===plan.qualification.sha256,'Bound qualification changed');
   const q=JSON.parse(fs.readFileSync(plan.qualification.path));
   need(q.method.sha256===fileHash(__filename)&&q.qualification.status==='passed_bounded_host_renderer_fixture','Exact renderer not qualified');
   for(const row of plan.views){
     validateNewGeometry(row);
     const svg=fs.readFileSync(row.derivative.path,'utf8');
     outputs.push({name:row.name,layers:row.layers,mirrored:row.mirrored,native:row.native,derivative:row.derivative,independent_geometry_check:row.check,
       ...await render(svg,path.join(plan.directory,'images',row.name+'.png'),plan.density)});
   }
 }else{
   const q=JSON.parse(fs.readFileSync(path.join(plan.directory,'qualification','RESULT.json')));need(q.plan_sha256===expectedPlan&&q.qualification.status==='passed_bounded_host_renderer_fixture','Qualification missing');
   for(const row of plan.views){
     const current=fs.readFileSync(row.native.path,'utf8'),previous=fs.readFileSync(row.previous_native.path,'utf8'),oldDerivative=fs.readFileSync(row.previous_derivative.path,'utf8');
     const proof=JSON.parse(fs.readFileSync(row.previous_proof.path)),check=JSON.parse(fs.readFileSync(row.previous_check.path));
     need(proof.raw_sha256===row.previous_native.sha256&&proof.derivative_sha256===row.previous_derivative.sha256,'Previous proof hashes');
     need(check.raw_sha256===proof.raw_sha256&&check.derivative_sha256===proof.derivative_sha256&&check.all_original_elements_preserved&&check.every_primitive_inside_reading_viewport&&check.margin_mm==='2'&&check.geometry_or_opacity_edits===0,'Previous geometry proof');
     const derived=transfer(current,previous,oldDerivative),derivative=path.join(plan.directory,'derivative',row.name+'.svg');fs.writeFileSync(derivative,derived,{flag:'wx'});
     const png=await render(derived,path.join(plan.directory,'images',row.name+'.png'),plan.density);
     const transferProof={schema:'title-only-native-equivalence-derivative-transfer/v1',current_native:row.native,previous_native:row.previous_native,
       previous_derivative:row.previous_derivative,previous_proof:row.previous_proof,previous_check:row.previous_check,
       changes:'Only the single literal native <title> metadata is transferred. All other native bytes equal prior proved geometry; derivative geometry/palette/viewBox remain identical.',
       derivative:ident(derivative),layers:row.layers,mirrored:row.mirrored,margin_mm:'2',geometry_or_opacity_edits:0,actual_view_performed:false};
     writeJSON(path.join(plan.directory,'derivative',row.name+'-proof.json'),transferProof);
     outputs.push({name:row.name,layers:row.layers,mirrored:row.mirrored,native:row.native,derivative:ident(derivative),...png});
   }
 }
 inputRowsUnchanged();const report=process.report.getReport();
 const result={schema:'finite-sharp-host-renderer/v1',phase:mode,plan_sha256:expectedPlan,method:ident(__filename),renderer:'sharp '+sharp.versions.sharp,
   versions:sharp.versions,qualification,outputs,actual_JS_or_addon_sources:[...loaded.values()],
   actual_shared_objects:report.sharedObjects.filter(p=>fs.existsSync(p)).map(ident),node:ident(process.execPath),execArgv:process.execArgv,
   cache:sharp.cache(),concurrency:sharp.concurrency(),child_dispatches:0,KiCad_Native_calls:0,formal_engineering_writes:0,
   limits:'Declared five-package renderer boundary plus actual OS shared-object observation; not whole-machine or full font-file provenance. Visible KiCad glyphs are stroked paths; visible font text/external references refuse. Expired sync2 confirmation is not renewed.'};
 writeJSON(path.join(plan.directory,mode==='qualify'?'qualification/RESULT.json':'RESULT.json'),result);
 console.log(JSON.stringify({phase:mode,outputs:outputs.length,qualification:qualification?.status,rss:process.memoryUsage().rss}));
}
main().catch(e=>{console.error(e.stack);process.exitCode=1;});
