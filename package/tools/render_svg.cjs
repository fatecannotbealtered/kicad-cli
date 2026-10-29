// Optional verification renderer. Core SCH commands do not need Node or sharp.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const [input, output, sharpModule] = process.argv.slice(2);
function fail(code, message) {
  process.stdout.write(JSON.stringify({ok:false, error:{code,message,retryable:false}})+'\n');
  process.exit(4);
}
if (!input || !output || !sharpModule) fail('E_USAGE', 'Use render_svg.cjs input.svg new.png absolute-sharp-module; no automatic install.');
if (!path.isAbsolute(sharpModule)) fail('E_CONFIG', 'Select an already legal absolute sharp module; use an SVG/PDF viewer if unavailable.');
if (fs.existsSync(output)) fail('E_CONFLICT', 'Output already exists; select a new owned output.');
let sharp;
try { sharp = require(sharpModule); } catch { fail('E_CONFIG', 'Selected sharp module cannot load; core product runtime is unaffected.'); }
(async () => {
  const raw = fs.readFileSync(input);
  // Preserve the full SVG canvas and all geometry; no crop, resize, recolor or redraw.
  const result = await sharp(raw, {density:180}).flatten({background:'#ffffff'}).png().toBuffer();
  fs.writeFileSync(output, result, {flag:'wx'});
  const metadata = await sharp(result).metadata();
  process.stdout.write(JSON.stringify({ok:true,data:{input_sha256:crypto.createHash('sha256').update(raw).digest('hex'),output_sha256:crypto.createHash('sha256').update(result).digest('hex'),width:metadata.width,height:metadata.height,density:180,full_canvas:true,cropped:false,resized:false,sharp_version:sharp.versions.sharp,node_version:process.version}})+'\n');
})().catch(() => fail('E_IO','Full-page rendering failed; preserve the input and diagnostics.'));
