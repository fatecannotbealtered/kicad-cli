"""Separately frozen remaining reads/refusal after the original F2 harness stopped.

Never executes original verify(), preview, successful confirm, or resolve.
Original failure and all original source/plan bytes remain immutable.
"""
import argparse,copy,hashlib,json,sys,types
from pathlib import Path
ROOT=Path(__file__).resolve().parent
CYCLE=ROOT/'f2-cycle-01';OUT=ROOT/'f2-readonly-02'
SOURCE=ROOT/'f2-method/controller.py'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def put(p,v):
    with Path(p).open('x',encoding='utf-8',newline='\n') as f:json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
assert sys.dont_write_bytecode and sys.flags.no_site
assert sha(SOURCE)=='223a2e48ef83a7047ba4e7d9b81f8bdf636da234108a10f31e92065e357b1b12'
m=types.ModuleType('original_f2_immutable_helpers');m.__file__=str(SOURCE);sys.modules[m.__name__]=m
exec(compile(SOURCE.read_bytes(),str(SOURCE),'exec'),m.__dict__)
p=argparse.ArgumentParser();p.add_argument('stage',choices=['freeze','execute']);p.add_argument('--plan-sha256');a=p.parse_args()
def job(v):
    assert v['natural_tree_complete'] and not v['controlled_termination'] and not v['control_events']
    assert not v['before_close']['members'] and not v['after_cleanup']['members']
def completed():
    frozen=read(CYCLE/'CONFIRM-PLAN.json');op=frozen['operation_id']
    assert sha(CYCLE/'CONFIRM-PLAN.json')=='68735bc05eeb2a9dd1101b3425b554cf001dd9b01658190028ea72a97312d40a'
    stop=read(CYCLE/'execute-confirm-STOP.json')
    assert stop['stage']=='execute-confirm' and stop['type']=='KeyError' and stop['message']=="'replayed'"
    assert stop['automatic_retry'] is False and stop['resumable_write'] is False
    assert not (CYCLE/'CONFIRM-FINISH.json').exists() and not (CYCLE/'VERIFY-FINISH.json').exists()
    m.assert_binding(CYCLE,frozen['binding'])
    relay=read(CYCLE/'private-relay-evidence/RELAY-FINISH.json')
    assert relay['dispatches']==1 and relay['service_callback_returned'] and relay['service_callback_error'] is None
    assert relay['response_lost_at_caller'] and relay['service_outcome']['actual_exit']==0
    job(relay['service_outcome']['whole_job'])
    for label in ['preview','confirm']:
        t=read(CYCLE/'calls'/label/'TRACE.json');job(t['job_finalization'])
        assert t['observation_schema']==3 and t['complete_tree'] and not t['timed_out'] and t['exit']==0
        assert sha(CYCLE/'calls'/label/'stdout')==t['stdout_sha256']
        assert sha(CYCLE/'calls'/label/'stderr')==t['stderr_sha256']
        assert read(CYCLE/'calls'/label/'CLASSIFICATION.json')['may_validate_functional_output']
    controller=read(ROOT/'controllers/f2-confirm/FINISH.json')
    assert controller['exit']==1;job(controller['whole_job'])
    actual=m.rows(CYCLE);r=actual['operations'][0]
    response=read(CYCLE/'calls/confirm/stdout');receipt=response['data']['receipt']
    assert response['ok'] and r['operation_id']==op and r['status']=='succeeded'
    assert r['lease_until'] is None and r['resolution'] is None and json.loads(r['receipt'])==receipt
    assert receipt['operation_id']==op and receipt['plan_id']==frozen['full_preview']['plan_id']
    assert receipt['outcome']['execution']=='completed' and receipt['outcome']['persistence']=='saved'
    assert receipt['outcome']['change']=='applied' and receipt['outcome']['verification']['status']=='passed'
    assert len(actual['consumed_tokens'])==len(actual['scope_usage'])==1 and actual['scope_usage'][0]['used']==1
    assert actual['consumed_tokens'][0]['operation_id']==op
    assert sha(CYCLE/'R4 回复丢失 工程/edit.kicad_pcb')==m.SAVED_SHA
    # Qualify the actual saved engineering against the ORIGINAL pre-write
    # manifest, not merely a newly accepted post-write snapshot.
    saved=m.engineering(CYCLE);original=frozen['engineering_before']
    assert len(original)==8 and set(saved)==set(original)
    for name,identity in original.items():
        if name!='edit.kicad_pcb':
            assert saved[name]==identity,'Protected engineering metadata changed: '+name
    assert saved['edit.kicad_pcb']['sha256']==m.SAVED_SHA
    assert saved['edit.kicad_pcb']['size']==(m.OLD/'native-affected-cycle-03/positive/edit.kicad_pcb').stat().st_size
    return frozen,actual,receipt
frozen,before,receipt=completed()
if a.stage=='freeze':
    OUT.mkdir(exist_ok=False)
    assert not any((CYCLE/'calls'/x).exists() for x in ['query','spent-confirm','independent-saved-sdk'])
    inputs=[Path(__file__),SOURCE,CYCLE/'execute-confirm-STOP.json',CYCLE/'CONFIRM-PLAN.json',
      CYCLE/'private-relay-evidence/RELAY-FINISH.json',ROOT/'controllers/f2-confirm/FINISH.json']
    inputs += [CYCLE/'calls'/label/name for label in ['preview','confirm'] for name in ['TRACE.json','stdout','stderr','CLASSIFICATION.json']]
    put(OUT/'ORIGINAL-PLAN.json',{'case_id':'F2R.completed-original-service.remaining-read-only-and-known-spent-refusal',
      'operation_id':frozen['operation_id'],'original_F2_status':'failed_preserved_optional_field_assertion',
      'method_inputs':{str(x):sha(x) for x in inputs},'binding':frozen['binding'],
      'rows_before':before,'receipt':receipt,'engineering_saved':m.engineering(CYCLE),
      'state_files_before':m.files(CYCLE/'driver-state',metadata=True),
      'allowed_actions':['one original-ID public show','one known-consumed same-token rejection; E_CONFLICT/token_used, zero dispatch','one independent native saved read'],
      'not_allowed':['preview','successful confirm','resolve','regenerate preflight','F1 retry','F4','source recovery01'],
      'automatic_retry':False,'old_STOP_ignored_for_writing':False,
      'source_failure_semantics':'Receipt optional replay marker is not required. This separate plan never changes the old result or re-dispatches its write.'})
    print(json.dumps({'plan_sha256':sha(OUT/'ORIGINAL-PLAN.json'),'operation_id':frozen['operation_id']}));sys.exit(0)
assert sha(OUT/'ORIGINAL-PLAN.json')==a.plan_sha256
plan=read(OUT/'ORIGINAL-PLAN.json')
assert all(sha(path)==v for path,v in plan['method_inputs'].items())
assert before==plan['rows_before'] and m.engineering(CYCLE)==plan['engineering_saved']
assert m.files(CYCLE/'driver-state',metadata=True)==plan['state_files_before']
put(OUT/'EXECUTION-START.json',{'plan_sha256':a.plan_sha256,'operation_id':plan['operation_id'],'one_attempt':True})
try:
    qt,q=m.call(CYCLE,'query',[str(m.EXE),'operation','show','--id',plan['operation_id'],'--compact'])
    status=q['data']['operation_status']
    assert q['ok'] and status['status']=='succeeded' and status['operation_id']==plan['operation_id'] and status['receipt']==receipt
    assert m.engineering(CYCLE)==plan['engineering_saved'] and m.rows(CYCLE)==before
    query_state=m.state_after_observe(CYCLE,plan['state_files_before'])
    assert m.process_counts(qt)['official_cli_instances']==0
    # This is a deliberately required rejection for a proven consumed token,
    # never a retry of an unknown or incomplete engineering write.
    st,s=m.call(CYCLE,'spent-confirm',frozen['request_argv'],allowed=(6,))
    assert not s['ok'] and s['error']['code']=='E_CONFLICT'
    assert s['error']['details']['conflict']=='token_used' and s['error']['details']['dispatch_state']=='not_started'
    assert m.engineering(CYCLE)==plan['engineering_saved'] and m.rows(CYCLE)==before
    spent_state=m.state_after_observe(CYCLE,plan['state_files_before'])
    assert m.process_counts(st)['official_cli_instances']==0
    nt,n=m.call(CYCLE,'independent-saved-sdk',[str(m.NATIVE/'bin/python.exe'),'-B','-S',str(m.SOURCE/'bench/saved_scene_native_read.py'),str(CYCLE/'R4 回复丢失 工程/edit.kicad_pcb')],public=False)
    assert n['input_unchanged'] and n['input_sha256']==m.SAVED_SHA and n['native_version']=='10.0.6'
    baseline=read(m.OLD/'native-affected-cycle-03/baseline-calls/baseline-sdk/stdout')
    expected=copy.deepcopy(baseline['footprints']);r4=next(v for v in expected if v['ref']=='R4')
    assert r4['position_nm']==[100000000,110000000] and not r4['locked']
    r4['position_nm'][0]-=500000
    for pad in r4['pads']:pad['position_nm'][0]-=500000
    assert n['footprints']==expected
    for key in ['tracks','zones','drawings']:assert n[key]==baseline[key]
    assert n['startup']['actual_module_sources']==baseline['startup']['actual_module_sources']
    assert m.engineering(CYCLE)==plan['engineering_saved'] and m.rows(CYCLE)==before
    native_state=m.state_after_observe(CYCLE,plan['state_files_before'])
    m.assert_binding(CYCLE,plan['binding'])
    reuse=m.qualify_reuse(CYCLE)
    reports=m.current_preflight_reports(frozen['full_preview']['extensions']['preflight'])
    assert reports['candidate']['board']['sha256']==sha(CYCLE/'R4 回复丢失 工程/edit.kicad_pcb')
    put(OUT/'RESULT.json',{'status':'passed','case_id':plan['case_id'],'operation_id':plan['operation_id'],
      'original_F2_status':'failed_preserved_optional_replayed_field_assertion','original_STOP_unchanged':True,
      'plan_sha256':a.plan_sha256,'confirm_plan_sha256':sha(CYCLE/'CONFIRM-PLAN.json'),
      'query_receipt_exact':True,'query_engineering_rows_usage_unchanged':True,
      'known_spent_confirm':'E_CONFLICT/token_used/not_started; no second engineering mutation',
      'service_completed_naturally':True,'simulated_caller_response_bytes':0,
      'native_saved_readback':m.identity(CYCLE/'calls/independent-saved-sdk/stdout'),
      'geometry':{'R4_position_nm':[99500000,110000000],'pad_world_x_nm':[98587500,100412500],'y_nm':110000000,'rotation_deg':0,'layer':'F.Cu','uuid_nets_schematic_paths_preserved':True},
      'reuse':reuse,'current_preflight_reports_reused_for_exact_saved_board':reports,
      'new_counts':{'public_EXE_invocations':2,'official_cli_preflight_invocations':0,'independent_native_SDK_readback_invocations':1,'new_render_invocations':0,'new_full_image_views':0},
      'whole_jobs':{'query':qt['job_finalization'],'spent':st['job_finalization'],'native_readback':nt['job_finalization']},
      'state_physical_before':plan['state_files_before'],'state_physical_after':m.files(CYCLE/'driver-state',metadata=True),
      'protected_engineering_original_manifest_qualified':True,'non_board_seven_original_metadata_unchanged':True,
      'state_observations':{'query':query_state,'spent':spent_state,'native':native_state},
      'coverage':'Only original completed-service evidence plus new remaining reads/refusal. Not killed-process recovery; no original confirmation rerun.'})
    print(json.dumps({'status':'passed','operation_id':plan['operation_id'],'original_F2_failed_preserved':True}))
except BaseException as exc:
    put(OUT/'STOP.json',{'type':type(exc).__name__,'message':str(exc),'automatic_retry':False})
    raise
