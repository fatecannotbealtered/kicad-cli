"""Finite independent Job observer entry; no product CAD implementation imports."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time

BASE = Path(__file__).resolve().parent
# Fresh cache lookup root prevents reading an older own-helper pyc. -B alone
# is not claimed to prevent cached-bytecode reads. Standard-library startup
# remains outside this narrow own-helper source guarantee.
cache_root=BASE/'.method-runtime'/('pyc-'+str(os.getpid())+'-'+str(time.time_ns()))
cache_root.mkdir(parents=True,exist_ok=False)
sys.pycache_prefix=str(cache_root)
sys.dont_write_bytecode=True
sys.path.insert(0, str(BASE))
from observer.process_tree import observe
from observer.native_observation import inspect_trace
for name in ('native_observation','owned_process','win_debugging','process_evidence'):
    actual=Path(sys.modules[name].__file__).resolve()
    if actual!=(BASE/'observer'/(name+'.py')).resolve():
        raise ValueError('Own observer helper loaded from undeclared source: '+name)
if list(cache_root.rglob('*.pyc')):
    raise ValueError('Fresh own-helper cache root unexpectedly contains bytecode')

def environment(profile):
    profile = Path(profile).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for k in list(env):
        if k.startswith('KICAD') or k in ('PYTHONHOME', 'PYTHONPATH', 'KIPRJMOD'):
            env.pop(k)
    for k, sub in [('HOME','home'),('USERPROFILE','home'),('APPDATA','appdata'),('LOCALAPPDATA','localappdata'),('KICAD_CONFIG_HOME','kicad'),('TEMP','tmp'),('TMP','tmp')]:
        p = profile/sub
        p.mkdir(exist_ok=True)
        env[k] = str(p)
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PYTHONHASHSEED='0', LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8')
    return env

def run(argv, output, profile, cwd, allowed=(0,), extra=None):
    output = Path(output).resolve()
    env = environment(profile)
    if extra:
        env.update(extra)
    trace = observe([str(x) for x in argv], str(Path(cwd).resolve()), env, output, timeout=120)
    disposition = inspect_trace(output, allowed_exit_codes=allowed)
    row = {'argv':list(map(str,argv)), 'cwd':str(Path(cwd).resolve()), 'profile':str(Path(profile).resolve()),
           'classification':disposition, 'environment':{k:env.get(k) for k in ('HOME','USERPROFILE','APPDATA','LOCALAPPDATA','KICAD_CONFIG_HOME','TEMP','TMP','PYTHONDONTWRITEBYTECODE','LANG','LC_ALL')}}
    (output/'CALL.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not disposition['may_validate_functional_output']:
        raise RuntimeError('Process observation not sufficient for functional use: '+disposition['status'])
    return trace
