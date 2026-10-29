"""Bootstrap observer for one declared Python controller; never retry."""
import sys
from pathlib import Path
exec(compile(Path(__file__).with_name('support.py').read_bytes(),str(Path(__file__).with_name('support.py')),'exec'))
name=sys.argv[1];script=Path(sys.argv[2]).resolve(strict=True);arguments=sys.argv[3:]
out=ROOT/'controllers'/name;out.mkdir(parents=True,exist_ok=False)
sys.path[:0]=[str(SOURCE/'bench')]
from process_evidence import run_recorded
command=[str(PYTHON),'-B','-S',str(script),*arguments]
inputs=[script,ROOT/'support.py',Path(__file__),*(SOURCE/'bench').glob('*.py')]
for v in arguments:
    if Path(v).is_file(): inputs.append(Path(v))
put(out/'ORIGINAL-PLAN.json',{'command':command,'inputs':[ident(x) for x in inputs],'environment':env(),'retry':False})
rec,_=run_recorded(out,'controller',command,cwd=ROOT,env=env(),timeout=1200,stdio='files',inputs=inputs,role=name)
put(out/'FINISH.json',{'exit':rec['exit'],'status':rec['status'],'execution':rec['execution'],'whole_job':rec['job_finalization'],'record_sha256':sha(out/'controller.call.json')})
assert rec['exit']==0 and rec['state']=='finished' and rec['job_finalization']['natural_tree_complete'] and not rec['job_finalization']['controlled_termination']
print(json.dumps({'controller':name,'status':rec['status'],'exit':rec['exit'],'whole_job_natural':rec['job_finalization']['natural_tree_complete']}))
