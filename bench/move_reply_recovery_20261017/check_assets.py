"""Static compiler-input and old physical-artifact witnesses; no state access."""
import sys,zipfile
import os
from pathlib import Path
exec(compile(Path(__file__).with_name('support.py').read_bytes(),str(Path(__file__).with_name('support.py')),'exec'))
REPO=Path(os.environ['KICAD_REVIEW_SOURCE'])
build=read(OLD/'BUILD-01.json');old=read(OLD/'ASSET-PROTECTION-FINAL-02.json')
baseline=read(OLD/'BASELINE.json')
assert len(build['compile_identity'])==205
for name,digest in build['compile_identity'].items():
    assert sha(OLD/'build-source-01'/name)==digest
    assert sha(OLD/'method-source-02'/name)==digest
    assert sha(OLD/'package-source-02'/name)==digest
    assert hashlib.sha256((REPO/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest
assert all(sha(REPO/r['file'])==r['physical_sha256'] for r in old['historical_EOL_18'])
assert sha(EXE)==old['new_EXE_sha256'] and sha(OLD/'engineer-move-stability-02.zip')==old['new_ZIP_sha256']
assert all(sha(v['path'])==v['sha256'] for v in baseline['assets'].values())
config=Path(os.environ['APPDATA'])/'kicad'
current={p.relative_to(config).as_posix():{'sha256':sha(p),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sorted(config.rglob('*')) if p.is_file()}
assert current==baseline['global_KiCad_regular_files_before']
out=ROOT/('ASSETS-'+sys.argv[1]+'.json')
put(out,{'status':'passed','compile_inputs':205,'product_compiler_bytes_equal_clean_build_method_package':True,
 'main_18_historical_physical_bytes_preserved':old['historical_EOL_18'],'main_not_build_site':True,
 'exe':ident(EXE),'immutable_zip':ident(OLD/'engineer-move-stability-02.zip'),
 'old_assets_unchanged':baseline['assets'],'global_regular_files_unchanged':len(current),
 'compile_identity':build['compile_identity'],'product_rebuild':'unnecessary; no product compilation input changed',
 'scope':'Static identity only; no operation state opened, no native invocation or old F4 accessed'})
print(json.dumps({'status':'passed','compile_inputs':205,'historical_18_unchanged':True,'global_files':len(current),'artifact':sha(EXE),'record_sha256':sha(out)}))
