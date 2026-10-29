"""External evidence support; stdlib only, no tested implementation imports."""
import hashlib,json,os,sys,types,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parent
OLD=Path('D:/personalSpace/kicad-move-stability-20261016')
SOURCE=OLD/'method-source-02'
PYTHON=Path('D:/environment/Python/Python312/python.exe')
EXE=OLD/'build-01/dist/kicad-cli.exe'
SDK=Path('D:/personalSpace/kicad-contract-recovery-20261013/external-dependencies/_driver_sdk')
NATIVE=Path('D:/software/kicad/home')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
def put(p,v):
    with Path(p).open('x',encoding='utf-8',newline='\n') as f: json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def ident(p):
    p=Path(p).resolve(strict=True)
    return {'path':str(p),'sha256':sha(p),'size':p.stat().st_size}
def tree(p): return {f.relative_to(p).as_posix():sha(f) for f in sorted(Path(p).rglob('*')) if f.is_file()}
def raw(name,p):
    m=types.ModuleType(name);m.__file__=str(p);sys.modules[name]=m
    exec(compile(Path(p).read_bytes(),str(p),'exec'),m.__dict__);return m
def env():
    keys={'SYSTEMROOT','WINDIR','SYSTEMDRIVE','COMSPEC','PATH','PATHEXT','TEMP','TMP','APPDATA','LOCALAPPDATA','USERPROFILE','HOMEDRIVE','HOMEPATH','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS'}
    e={k:v for k,v in os.environ.items() if k.upper() in keys}
    e.update(PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8',PYTHONHASHSEED='0')
    return e
def observer():
    sys.path[:0]=[str(SOURCE),str(SOURCE/'bench')]
    from bench.persistence_acceptance.process_tree import observe
    from native_observation import inspect_trace
    return observe,inspect_trace
def rows(state):
    db=Path(state)/'operations.sqlite3'
    if not db.exists(): return {'operations':[],'consumed_tokens':[],'scope_usage':[]}
    assert not any((Path(str(db)+s)).exists() for s in ['-wal','-shm','-journal'])
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as c:
        c.row_factory=sqlite3.Row
        return {n:[dict(r) for r in c.execute('SELECT * FROM '+n+' ORDER BY 1')] for n in ['operations','consumed_tokens','scope_usage']}
def public_call(args,cwd,e,out,allowed=(0,)):
    observe,inspect=observer()
    command=[str(EXE),*args,'--compact']
    trace=observe(command,cwd,e,out,timeout=300)
    generic=inspect(out,allowed_exit_codes=allowed)
    put(Path(out)/'CLASSIFICATION.json',generic)
    assert generic['may_validate_functional_output'],generic
    data=read(Path(out)/'stdout')
    assert type(data['ok']) is bool and trace['exit'] in allowed
    assert trace['job_finalization']['natural_tree_complete'] and not trace['job_finalization']['controlled_termination']
    return trace,data
