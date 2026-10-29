"""One authorized F1 only: stable copy, immutable read, no product dispatch."""
import hashlib, json, shutil, sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parent
OLD=Path('D:/personalSpace/kicad-move-stability-20261016')
REPO=Path(os.environ['KICAD_REVIEW_SOURCE'])
CASE=OLD/'exe-replyloss-02/execution/tmp/test_durable_receipt_lost_repl0'
OP='op-7fe40cc45ead43ebbf2e83f4'
sha=lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p: json.loads(Path(p).read_bytes())
def put(p,v):
    with Path(p).open('x',encoding='utf-8',newline='\n') as f: json.dump(v,f,ensure_ascii=False,indent=2); f.write('\n')
def files(root):
    return {str(p.relative_to(root)).replace('\\','/'):{'sha256':sha(p),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns} for p in sorted(root.rglob('*')) if p.is_file()}
def identity(p): return {'path':str(p),'sha256':sha(p),'size':p.stat().st_size}

out=ROOT/'f1-investigation-02'
out.mkdir(exist_ok=False)
start=read(CASE/'owned-reply-loss/START.json')
assert start['operation_id']==OP and Path(start['state']).resolve()==(CASE/'driver-state').resolve()
job=read(CASE/'owned-reply-loss/JOB-FINALIZATION.json')
# Exact original complete terminal evidence, not a current reused PID lookup.
assert sha(CASE/'owned-reply-loss/JOB-FINALIZATION.json')=='cd765a447da2fc6e0b2dd58496db4cefc4bed41818f4a688dbfbb64e99af24b8'
assert sha(CASE/'owned-reply-loss/CONTROL-FINISH.json')=='4397a74df25790a25b0c9b662083dec7f1b86b7ef7932ea86898ad6d70ac95bf'
assert sha(OLD/'build-01/dist/kicad-cli.exe')=='2f6fa5640dd9757d637917bfed532ee97e1c54bbb3fcaba02db0558082133139'
assert sha(OLD/'engineer-move-stability-02.zip')=='2f3ffeb8a4b22ff4e636e050484991b1dcf5026069480f11fa5e3184cb0c86e7'
historical=read(OLD/'ASSET-PROTECTION-FINAL-02.json')
assert len(historical['historical_EOL_18'])==18
for row in historical['historical_EOL_18']: assert sha(REPO/row['file'])==row['physical_sha256']
state=CASE/'driver-state'
before=files(state)
assert {p.name for p in state.iterdir() if p.is_file()}=={'operations.sqlite3','confirm.secret'}, 'Do not inspect a live/WAL/foreign store'
assert {p.name for p in state.iterdir() if p.is_dir()}=={'move-preflights'}
assert {p.name for p in (state/'move-preflights').iterdir()}=={OP}, 'Only this exact own F1 preflight permitted'
engineering=files(CASE/'安全移动 中文 空格')
put(out/'ORIGINAL-PLAN.json',{
    'kind':'authorized-exact-F1-forensic-read-v1','at':datetime.now(timezone.utc).isoformat(),
    'operation_id':OP,'original_state':str(state),'original_registered_tree':'terminated; exact hashed Job evidence, no PID reuse inference',
    'inputs':{n:identity(CASE/'owned-reply-loss'/n) for n in ['START.json','CONTROL-FINISH.json','JOB-FINALIZATION.json']},
    'original_state_metadata':before,'engineering_before':engineering,
    'actions':['copy stable original SQLite without WAL','immutable read on copy only; exact own operation and related rows'],
    'prohibited':['confirm','preview','resolve','replay','old F4','old source recovery01'],
    'secret_policy':'Secret hashed locally for protection only; no secret contents disclosed or copied',
    'historical_18_unchanged':True,'artifact':identity(OLD/'build-01/dist/kicad-cli.exe'),'zip':identity(OLD/'engineer-move-stability-02.zip')})
dbcopy=out/'original-operations.sqlite3'
shutil.copyfile(state/'operations.sqlite3',dbcopy)
assert sha(dbcopy)==before['operations.sqlite3']['sha256'] and files(state)==before
with sqlite3.connect(dbcopy.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as db:
    db.row_factory=sqlite3.Row
    assert db.execute('SELECT count(*) FROM operations').fetchone()[0]==1,'Stop before reading foreign operations'
    rows=[dict(r) for r in db.execute('SELECT * FROM operations WHERE operation_id=?',(OP,))]
    assert len(rows)==1
    row=rows[0]
    tokens=[dict(r) for r in db.execute('SELECT * FROM consumed_tokens WHERE operation_id=?',(OP,))]
    # Single-case scope table has no unrelated operation; preserve it exactly.
    usage=[dict(r) for r in db.execute('SELECT * FROM scope_usage')]
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
logical={'operations':rows,'consumed_tokens':tokens,'scope_usage':usage}
put(out/'PRIVATE-ROWS.json',logical)
receipt=json.loads(row['receipt']) if row['receipt'] else None
if receipt is not None: put(out/'DURABLE-RECEIPT.json',receipt)
assert files(state)==before and files(CASE/'安全移动 中文 空格')==engineering
summary={'operation_id':OP,'status':row['status'],'receipt_present':bool(receipt),'receipt_sha256':sha(out/'DURABLE-RECEIPT.json') if receipt else None,
    'started_at':row['started_at'],'finished_at':row['finished_at'],'lease_until':row['lease_until'],
    'extra':json.loads(row['extra']) if row['extra'] else None,'resolution':row['resolution'],
    'sqlite_integrity':integrity,'engineering_saved_candidate_exact':engineering['edit.kicad_pcb']['sha256']==start['candidate_sha256'],
    'other_seven_protected':all(engineering[k]['sha256']==v['sha256'] for k,v in start['source_files'].items() if k!='edit.kicad_pcb'),
    'state_and_engineering_unchanged_by_inspection':True,'original_test_result':'failed; preserved, not converted to pass',
    'rows_sha256':sha(out/'PRIVATE-ROWS.json'),'public_query':'not yet executed; freeze separately after actual status known'}
put(out/'FINDINGS.json',summary)
print(json.dumps({k:v for k,v in summary.items() if k!='extra'},ensure_ascii=False))
