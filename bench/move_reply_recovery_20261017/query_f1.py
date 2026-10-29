"""Freeze then consume one exact original-state, original-ID public read."""
import argparse,sys
from pathlib import Path
sys.dont_write_bytecode=True
exec(compile(Path(__file__).with_name('support.py').read_bytes(),str(Path(__file__).with_name('support.py')),'exec'))
CASE=OLD/'exe-replyloss-02/execution/tmp/test_durable_receipt_lost_repl0'
STATE=CASE/'driver-state';PROJECT=CASE/'安全移动 中文 空格';OP='op-7fe40cc45ead43ebbf2e83f4'
OUT=ROOT/'f1-public-query-01'
p=argparse.ArgumentParser();p.add_argument('mode',choices=['freeze','execute']);p.add_argument('--sha256');a=p.parse_args()
e=env();e.update(KICAD_CLI_STATE_DIR=str(STATE),KICAD_CLI_ROOT=str(NATIVE),KICAD_CLI_OFFICIAL=str(NATIVE/'bin/kicad-cli.exe'),KICAD_CLI_DRIVER_SDK_PATH=str(SDK),KICAD_CONFIG_HOME=str(CASE/'configuration'))
def current():
    return {'method':{str(x):sha(x) for x in [Path(__file__),ROOT/'support.py']},'observer_source':tree(SOURCE/'bench'),
      'artifact':ident(EXE),'python':ident(PYTHON),'sdk_spec':tree(SDK.parent),'environment':e,
      'state_files':tree(STATE),'engineering_files':tree(PROJECT),'config_files':tree(CASE/'configuration'),
      'prior_inspection':ident(ROOT/'f1-investigation-02/FINDINGS.json'),'prior_rows':ident(ROOT/'f1-investigation-02/PRIVATE-ROWS.json')}
if a.mode=='freeze':
    assert read(ROOT/'f1-investigation-02/FINDINGS.json')['status']=='succeeded'
    OUT.mkdir(exist_ok=False)
    put(OUT/'ORIGINAL-PLAN.json',{'id':'F1-exact-original-state-readonly-query','operation_id':OP,'state':str(STATE),'inputs':current(),
       'command':[str(EXE),'operation','show','--id',OP,'--compact'],'expected':'original succeeded durable receipt; no native dispatch or engineering mutation',
       'public_query_count':1,'retry':False,'not_executed':['confirm','preview','resolve','old F4','source recovery01']})
    print(sha(OUT/'ORIGINAL-PLAN.json'));sys.exit(0)
assert a.sha256==sha(OUT/'ORIGINAL-PLAN.json')
plan=read(OUT/'ORIGINAL-PLAN.json');assert plan['inputs']==current()
before=rows(STATE);assert before==read(ROOT/'f1-investigation-02/PRIVATE-ROWS.json')
assert len(before['operations'])==1 and before['operations'][0]['operation_id']==OP
trace,value=public_call(['operation','show','--id',OP],CASE,e,OUT/'public')
after=rows(STATE);put(OUT/'PRIVATE-ROWS-AFTER.json',after)
status=value['data']['operation_status'];receipt=read(ROOT/'f1-investigation-02/DURABLE-RECEIPT.json')
checks={'public_ok':value['ok'] is True,'succeeded':status['status']=='succeeded','same_operation':status['operation_id']==OP,
 'original_durable_receipt_exact':status['receipt']==receipt,'logical_rows_unchanged':before==after,
 'all_engineering_unchanged':tree(PROJECT)==plan['inputs']['engineering_files'],
 'all_state_files_unchanged':tree(STATE)==plan['inputs']['state_files'],
 'config_unchanged':tree(CASE/'configuration')==plan['inputs']['config_files'],
 'no_native':all(Path(v['image']).name.lower() not in ['python.exe'] and v['image_sha256']==sha(EXE) for v in trace['processes'])}
put(OUT/'RESULT.json',{'status':'passed' if all(checks.values()) else 'failed','checks':checks,'plan_sha256':a.sha256,
 'original_test_result':'failed preserved; this is separately authorized later read','receipt_sha256':sha(ROOT/'f1-investigation-02/DURABLE-RECEIPT.json'),
 'processes':len(trace['processes']),'trace_sha256':sha(OUT/'public/TRACE.json'),'whole_job':trace['job_finalization']})
assert all(checks.values()),checks
print(json.dumps({'status':'passed','checks':checks}))
