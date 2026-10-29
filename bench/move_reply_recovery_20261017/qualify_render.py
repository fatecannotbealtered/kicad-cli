"""Recheck exact retained full-image dependency closure, no render or product call."""
import sys
from pathlib import Path
exec(compile(Path(__file__).with_name('support.py').read_bytes(),str(Path(__file__).with_name('support.py')),'exec'))
original=Path('D:/personalSpace/kicad-move-safety-20261016/native-qa-cycle-04/ORIGINAL-PLAN.json')
assert sha(original)=='c361d5151e538c1814fab9519e990bd07fe16edc3fc83eaf5ce7a318a9cfba18'
plan=read(original);b=plan['binding'];config=b['config']
assert all(sha(p)==h for p,h in b['methods'].items())
put(ROOT/'RENDER-REUSE-ORIGINAL-PLAN.json',{'purpose':'read-only exact historical image closure qualification','original':ident(original),
 'methods':b['methods'],'expected_dependencies':b['dependencies'],'script':ident(__file__),
 'historical_closure':ident(OLD/'HISTORICAL-RENDER-CLOSURE-01.json'),'prior_full_views':6,
 'new_native_or_product_or_render_or_image_view':0,'do_not_claim':'a new visual review or new rendering'})
source=Path(config['repository']);sys.path[:0]=[str(source/'bench'),str(source)]
from bench.saved_scene_acceptance import dependency_identity
actual=dependency_identity(config)
assert actual==b['dependencies']
manual=read(OLD/'MANUAL-ROOT-02.json')
for scene in manual['scenes'].values():
    for v in scene['file_association'].values(): assert sha(v['path'])==v['sha256']
put(ROOT/'RENDER-REUSE-QUALIFICATION.json',{'status':'qualified_historical_reuse','original_plan_sha256':sha(ROOT/'RENDER-REUSE-ORIGINAL-PLAN.json'),
 'method_files':len(b['methods']),'dependency_groups':len(actual),'exact_full_image_associations_unchanged':True,
 'historic_manual_record':ident(OLD/'MANUAL-ROOT-02.json'),'historic_full_views':6,
 'new_product':0,'new_native':0,'new_render':0,'new_image_view':0,
 'condition':'New formal saved R4 must equal the exact historical positive engineering files; separate F2 verifier checks this.',
 'manufacturing_signoff':False})
print(json.dumps({'status':'qualified_historical_reuse','groups':len(actual),'new_render':0,'new_image_view':0}))
