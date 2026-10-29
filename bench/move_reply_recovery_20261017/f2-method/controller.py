"""F2: service completes once; an external receiver gets EOF, then queries the ID.

External evidence only. No product imports, test hooks, process interruption or
retry. Run each stage under the parent's owned controller wrapper with -B -S.
All private tokens and durable rows stay in the new, explicitly owned cycle.
"""
from __future__ import annotations

import argparse
import ast
import copy
from datetime import datetime, timezone
import hashlib
import importlib.abc
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import types

HERE = Path(__file__).resolve().parent
ROUND = HERE.parent
OLD = Path('D:/personalSpace/kicad-move-stability-20261016')
SOURCE = OLD / 'method-source-02'
INPUT = Path('D:/personalSpace/kicad-move-safety-20261016/frozen-engineering-input-01')
NATIVE = Path('D:/software/kicad/home')
SDK = Path('D:/personalSpace/kicad-contract-recovery-20261013/external-dependencies/_driver_sdk')
PYTHON = Path('D:/environment/Python/Python312/python.exe')
EXE = OLD / 'build-01/dist/kicad-cli.exe'
ZIP = OLD / 'engineer-move-stability-02.zip'
INTENT = Path('D:/personalSpace/kicad-move-safety-20261016/positive-intent/frozen-r4-translation-intent.json')
EXE_SHA = '2f6fa5640dd9757d637917bfed532ee97e1c54bbb3fcaba02db0558082133139'
ZIP_SHA = '2f3ffeb8a4b22ff4e636e050484991b1dcf5026069480f11fa5e3184cb0c86e7'
BASE_SHA = '59b5c3a7f5569031c2c098c01cdee5c5be2c71aed77d33eb5992126ac7b5cb1f'
SAVED_SHA = '06d9017e89e9e86533041df9d554289435df6edbe8852569d350acfeddd4176c'
CASE_ID = 'F2.final-exe.service-completed.receiver-lost-response.original-id-query'
MOVES = 'R4:99.5,110'
KEY = 'f2-r4-receiver-loss-20261017'
TRANSPORT_NAMES = {
    'digest', '_unique', '_reject_constant', '_finite', 'strict_json',
    'windows_argv', '_image_path', 'candidate_exit_decision',
    'inspect_candidate_trace', 'ObservationBindingError', '_same_json_value', 'verified_output',
}

def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(Path(path).read_bytes())

def put(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')

def identity(path):
    path = Path(path).resolve(strict=True)
    assert path.is_file() and not path.is_symlink()
    stat = path.stat()
    return {'path': str(path), 'sha256': sha(path), 'size': stat.st_size}

def files(root, metadata=False):
    root = Path(root)
    rows = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Reparse point in declared evidence input: ' + str(path))
        if path.is_file():
            stat = path.stat()
            value = {'sha256': sha(path), 'size': stat.st_size}
            if metadata:
                value.update(mtime_ns=stat.st_mtime_ns, inode=stat.st_ino)
            rows[path.relative_to(root).as_posix()] = value
    return rows

def source_files(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if '.git' in relative.parts:
            continue
        if '__pycache__' in relative.parts or path.suffix == '.pyc':
            raise ValueError('Unreviewed source cache is present: ' + str(path))
        if path.is_file():
            assert not path.is_symlink() and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400
            result[relative.as_posix()] = identity(path)
    return result

def method_files():
    rows = {}
    for directory in (HERE, ROUND / 'receiver-method'):
        for path in sorted(directory.glob('*.py')):
            rows[str(path.resolve())] = identity(path)
    return rows

def method_qualification():
    review = ROUND / 'receiver-method/REVIEW-AFTER-FIX.json'
    harmless = ROUND / 'harmless-receiver-01/RESULT.json'
    parent = ROUND / 'controllers/harmless-receiver/FINISH.json'
    record = read(review)
    assert record['p1_count'] == record['p2_count'] == 0
    for name, digest in record['reviewed_files'].items():
        assert sha(ROUND / 'receiver-method' / name) == digest
    result = read(harmless)
    assert result['passed'] and len(result['cases']) == 4
    assert result['product_processes'] == result['kicad_native_processes'] == 0
    assert all(case['passed'] and all(case['checks'].values()) for case in result['cases'])
    job = read(parent)
    assert job['exit'] == 0 and job['execution'] == 'completed'
    assert job['whole_job']['natural_tree_complete'] and not job['whole_job']['controlled_termination']
    return {str(path): identity(path) for path in (review, harmless, parent)}

def native_files():
    # The official preflight additionally freezes its complete native-tool and
    # resource closure. These identities bind entry points before that call;
    # the independent SDK reader's complete actual module providers are frozen
    # from its previously reviewed startup record and rechecked after this call.
    paths = {NATIVE / 'bin/kicad-cli.exe', NATIVE / 'bin/python.exe'}
    paths.update((NATIVE / 'bin').glob('*.dll'))
    for directory in ('resources', 'schemas', 'internat'):
        paths.update(path for path in (NATIVE / 'share/kicad' / directory).rglob('*') if path.is_file())
    previous = read(OLD / 'native-affected-cycle-03/positive-calls/positive-sdk/stdout')
    for row in previous['startup']['actual_module_sources']:
        for key in ('path', 'provider'):
            if row.get(key) and Path(row[key]).is_file():
                paths.add(Path(row[key]))
        if row.get('archive_sha256'):
            virtual = Path(row['path'])
            archive = next((p for p in [virtual, *virtual.parents] if p.suffix.lower() == '.zip'), None)
            assert archive is not None and archive.is_file() and sha(archive) == row['archive_sha256']
            paths.add(archive)
    return {str(p.resolve()): identity(p) for p in sorted(paths)}

def truth_inputs():
    native_cycle = OLD / 'native-affected-cycle-03'
    paths = {native_cycle / 'baseline-calls/baseline-sdk/stdout',
             native_cycle / 'positive-calls/positive-sdk/stdout',
             native_cycle / 'BASELINE-RESULT.json', native_cycle / 'POSITIVE-RESULT.json',
             native_cycle / 'positive-output/drc.json', native_cycle / 'ORIGINAL-PLAN.json',
             OLD / 'HISTORICAL-RENDER-CLOSURE-01.json', OLD / 'MANUAL-ROOT-02.json',
             ROUND / 'RENDER-REUSE-QUALIFICATION.json',
             ROUND / 'controllers/render-reuse-metadata/FINISH.json',
             Path('D:/personalSpace/kicad-move-safety-20261016/native-qa-cycle-04/ORIGINAL-PLAN.json'),
             Path('D:/personalSpace/kicad-move-safety-20261016/native-qa-cycle-04/RESULT.json')}
    paths.update(p for p in (native_cycle / 'positive').rglob('*') if p.is_file())
    paths.update(p for p in (native_cycle / 'configuration').rglob('*') if p.is_file())
    historical = read(OLD / 'HISTORICAL-RENDER-CLOSURE-01.json')
    assert historical['status'] == 'passed'
    for path, digest in historical['historical_method_files'].items():
        assert sha(path) == digest
        paths.add(Path(path))
    # Bind the original manual evidence and all of its exact image/source
    # references. This does not execute or assert today's render qualification.
    def manual_paths(value):
        if isinstance(value, dict):
            for item in value.values():
                manual_paths(item)
        elif isinstance(value, list):
            for item in value:
                manual_paths(item)
        elif isinstance(value, str) and value.startswith(('D:/', 'D:\\')):
            candidate = Path(value)
            if candidate.is_file():
                paths.add(candidate)
    manual_paths(read(OLD / 'MANUAL-ROOT-02.json'))
    return {str(path.resolve()): identity(path) for path in sorted(paths)}

def stable_environment():
    allowed = {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'PATH', 'PATHEXT',
               'TEMP', 'TMP', 'APPDATA', 'LOCALAPPDATA', 'USERPROFILE', 'HOMEDRIVE',
               'HOMEPATH', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}
    result = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    result.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PYTHONHASHSEED='0')
    return result

def environment(cycle):
    result = stable_environment()
    result.update(KICAD_CONFIG_HOME=str(cycle / 'configuration'),
                  KICAD_CLI_ROOT=str(NATIVE), KICAD_CLI_OFFICIAL=str(NATIVE / 'bin/kicad-cli.exe'),
                  KICAD_CLI_DRIVER_SDK_PATH=str(SDK), KICAD_CLI_STATE_DIR=str(cycle / 'driver-state'),
                  KICAD_CLI_SCOPE_FILE=str(cycle / 'move-scope.json'))
    return result

def binding(cycle):
    return {'methods': method_files(), 'receiver_method_qualification': method_qualification(),
            'method_source': source_files(SOURCE),
            'sdk': source_files(SDK), 'spec': source_files(SDK.parent / 'driver_spec'),
            'native': native_files(), 'truth_inputs': truth_inputs(), 'python': identity(PYTHON),
            'exe': identity(EXE), 'zip': identity(ZIP), 'intent': identity(INTENT),
            'environment': environment(cycle), 'configuration': files(cycle / 'configuration'),
            'scope': identity(cycle / 'move-scope.json'), 'original_input': files(INPUT),
            'prepared': identity(cycle / 'PREPARATION.json')}

def assert_binding(cycle, value):
    actual = binding(cycle)
    if actual != value:
        keys = [key for key in value if actual.get(key) != value[key]]
        raise ValueError('Frozen execution input drift before process dispatch: ' + ','.join(keys))

class RawSources(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Compile explicitly frozen bench source bytes; never consult pyc/site."""
    def locate(self, name):
        if name == 'bench' or name.startswith('bench.'):
            path = SOURCE.joinpath(*name.split('.'))
        elif name in {'native_observation', 'owned_process', 'win_debugging', 'process_evidence'}:
            path = SOURCE / 'bench' / name
        else:
            return None
        if path.is_dir():
            initial = path / '__init__.py'
            return initial if initial.is_file() else path
        return path.with_suffix('.py')

    def find_spec(self, fullname, path=None, target=None):
        filename = self.locate(fullname)
        if filename is None:
            return None
        assert filename.is_file() or filename.is_dir()
        return importlib.util.spec_from_loader(fullname, self, origin=str(filename),
                                              is_package=filename.is_dir() or filename.name == '__init__.py')

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        filename = self.locate(module.__name__)
        module.__file__ = str(filename)
        if filename.is_dir():
            module.__path__ = [str(filename)]
            return
        if filename.name == '__init__.py':
            module.__path__ = [str(filename.parent)]
        exec(compile(filename.read_bytes(), str(filename), 'exec'), module.__dict__)

def observers():
    if not any(isinstance(x, RawSources) for x in sys.meta_path):
        sys.meta_path.insert(0, RawSources())
    from bench.persistence_acceptance.process_tree import observe
    from bench.native_observation import inspect_trace
    from owned_process import WindowsJob
    return observe, inspect_trace, WindowsJob

def transport_policy():
    filename = SOURCE / 'tests/move_safety_transport.py'
    parsed = ast.parse(filename.read_bytes(), filename=str(filename))
    selected = [node for node in parsed.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and node.name in TRANSPORT_NAMES]
    assert {node.name for node in selected} == TRANSPORT_NAMES
    # Exactly the reviewed source definitions execute. Module-level pytest,
    # JSON Schema imports and test collection do not execute in this controller.
    module = types.ModuleType('f2_frozen_transport_policy')
    module.__dict__.update(hashlib=hashlib, json=json, math=math, os=os, Path=Path,
                           MOVE_REFUSAL_EXITS=frozenset({2, 3, 4, 6, 7}))
    constant = [node for node in parsed.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'MOVE_REFUSAL_EXITS' for t in node.targets)]
    assert len(constant) == 1 and ast.literal_eval(constant[0].value.args[0]) == {2, 3, 4, 6, 7}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(filename), 'exec'), module.__dict__)
    return module

def receiver_path_policy():
    filename = ROUND / 'receiver-method/validate_receiver.py'
    parsed = ast.parse(filename.read_bytes(), filename=str(filename))
    selected = [node for node in parsed.body if isinstance(node, ast.FunctionDef)
                and node.name == 'private_path_in_caller_input']
    assert len(selected) == 1
    namespace = {'re': re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(filename), 'exec'), namespace)
    return namespace['private_path_in_caller_input']

def process_counts(trace):
    rows = trace['processes']
    official = str((NATIVE / 'bin/kicad-cli.exe').resolve()).casefold()
    return {'debug_process_instances': len(rows),
            'public_candidate_instances': sum(r['image_sha256'] == EXE_SHA for r in rows),
            'official_cli_instances': sum(str(Path(r['image']).resolve()).casefold() == official
                                           and r['image_sha256'] == sha(NATIVE / 'bin/kicad-cli.exe') for r in rows),
            'other_instances': [{'image': r['image'], 'sha256': r['image_sha256'],
                                 'pid': r['pid'], 'birth': r['creation_filetime']}
                                for r in rows if r['image_sha256'] != EXE_SHA and
                                str(Path(r['image']).resolve()).casefold() != official]}

def call(cycle, label, argv, *, allowed=(0,), public=True, env=None):
    observe, inspect, _ = observers()
    output = cycle / 'calls' / label
    output.parent.mkdir(exist_ok=True)
    actual_env = environment(cycle) if env is None else env
    put(cycle / (label + '-INVOCATION.json'), {'command': argv, 'cwd': str(cycle / 'R4 回复丢失 工程'),
          'environment': actual_env, 'allowed_exit_codes': list(allowed), 'automatic_retry': False})
    trace = observe(argv, cycle / 'R4 回复丢失 工程', actual_env, output, timeout=300)
    if public:
        policy = transport_policy()
        classification, details = policy.inspect_candidate_trace(output, argv,
             cwd=cycle / 'R4 回复丢失 工程', allowed_exit_codes=allowed, artifact_sha256=EXE_SHA)
        put(output / 'CLASSIFICATION.json', dict(classification, candidate_policy=details))
        assert classification['may_validate_functional_output'], classification
        trace, raw = policy.verified_output(output, trace, details)
        value = policy.strict_json(raw)
    else:
        classification = inspect(output, allowed_exit_codes=allowed)
        put(output / 'CLASSIFICATION.json', classification)
        assert classification['may_validate_functional_output'], classification
        assert sha(output / 'stdout') == trace['stdout_sha256']
        value = read(output / 'stdout')
    assert trace['observation_schema'] == 3
    job = trace['job_finalization']
    assert job['natural_tree_complete'] and not job['controlled_termination']
    assert not job['after_cleanup']['members'] and not job['control_events']
    put(output / 'COUNTS.json', process_counts(trace))
    return trace, value

def rows(cycle):
    database = cycle / 'driver-state/operations.sqlite3'
    assert database.is_file()
    assert not any(Path(str(database) + ending).exists() for ending in ('-wal', '-shm', '-journal'))
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        result = {name: [dict(row) for row in connection.execute('SELECT * FROM ' + name + ' ORDER BY 1')]
                  for name in ('operations', 'consumed_tokens', 'scope_usage')}
    assert len(result['operations']) == 1, 'Only this new F2 operation is permitted'
    return result

def engineering(cycle):
    return files(cycle / 'R4 回复丢失 工程', metadata=True)

def state_after_observe(cycle, before):
    after = files(cycle / 'driver-state', metadata=True)
    assert set(after) == set(before)
    for name in before:
        if name != 'operations.sqlite3':
            assert after[name] == before[name], 'Non-SQLite state artifact drift: ' + name
    # Public show can prepare schema/prune/settle. Logical relevant rows must
    # remain exact in this succeeded single-operation case; SQLite physical
    # metadata is observed separately and never used as an electrical-write test.
    return {'before': before['operations.sqlite3'], 'after': after['operations.sqlite3'],
            'physical_sqlite_changed': after['operations.sqlite3'] != before['operations.sqlite3'],
            'non_database_state_files_unchanged': True}

def original_plan(args):
    assert args.plan and args.plan_sha256 and sha(args.plan) == args.plan_sha256
    plan = read(args.plan)
    cycle = Path(plan['cycle']).resolve(strict=True)
    assert plan['case_id'] == CASE_ID and cycle.parent == ROUND.resolve()
    assert not list(cycle.glob('*-STOP.json')), 'A stopped cycle cannot resume'
    return cycle, plan

def current_preflight_reports(preflight):
    expected_ignored = sorted(['missing_courtyard', 'track_not_centered_on_via',
          'tuning_profile_track_geometries', 'footprint_filters_mismatch', 'footprint_type_mismatch'])
    result = {}
    for role, expected_sha in [('baseline', BASE_SHA), ('candidate', SAVED_SHA)]:
        formal = preflight['formal'][role]
        report = Path(formal['report'])
        board = Path(preflight['staging']['stages'][role]['board'])
        assert sha(report) == formal['sha256'] and sha(board) == expected_sha
        value = read(report)
        assert value['kicad_version'] == '10.0.6' and value['coordinate_units'] == 'mm'
        assert value['source'] == board.name == 'edit.kicad_pcb'
        assert len(value['violations']) == 0 and len(value['unconnected_items']) == 7
        assert sorted(row['key'] for row in value['ignored_checks']) == expected_ignored
        assert value['included_severities'] == ['error', 'warning', 'exclusion']
        result[role] = {'board': identity(board), 'official_report': identity(report),
              'native_record': identity(formal['record']), 'violations': 0,
              'unconnected_items': 7, 'ignored_keys': expected_ignored,
              'report_stage': 'one current public preview, before formal engineering save',
              'schematic_parity': 'not_executed; empty report array is not parity acceptance'}
    return result

def prepare(args):
    cycle = Path(args.cycle).resolve()
    assert cycle.parent == ROUND.resolve() and not cycle.exists()
    assert sha(EXE) == EXE_SHA and sha(ZIP) == ZIP_SHA
    originals = files(INPUT)
    assert len(originals) == 8 and sha(INPUT / 'edit.kicad_pcb') == BASE_SHA
    cycle.mkdir()
    project = cycle / 'R4 回复丢失 工程'
    shutil.copytree(INPUT, project)
    configuration_source = OLD / 'native-affected-cycle-03/configuration'
    shutil.copytree(configuration_source, cycle / 'configuration')
    common = cycle / 'configuration/10.0/kicad_common.json'
    before = read(common)
    after = copy.deepcopy(before)
    after['system']['working_dir'] = str(project)
    common.write_text(json.dumps(after, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    scope = {'scope_id': 'owned-f2-r4-receiver-loss-20261017', 'tool': 'kicad-cli',
             'granted_by': 'user-authorized isolated acceptance', 'granted_at': now(),
             'expires_at': None, 'effects': ['observe', 'change', 'persist'], 'dangerous': False,
             'roots': [str(cycle)], 'outputs': [],
             'notes': 'Only this new R4 copy. Exactly one confirm; no F1/F4/source-recovery access.'}
    put(cycle / 'move-scope.json', scope)
    assert files(project) == originals
    put(cycle / 'PREPARATION.json', {'at': now(), 'case_id': CASE_ID,
         'product_native_or_test_processes': 0, 'original_source': str(INPUT),
         'original_files': originals, 'configuration_source': str(configuration_source),
         'configuration_source_files': files(configuration_source),
         'configuration_prepared_files': files(cycle / 'configuration'),
         'explicit_pre_freeze_metadata_change': {'file': '10.0/kicad_common.json',
              'pointer': '/system/working_dir', 'old': before['system']['working_dir'],
              'new': after['system']['working_dir']},
         'configuration_initializer': 'byte copy of exact previously initialized KiCad10.0.6 preferences, followed by declared working_dir relocation',
         'scope': identity(cycle / 'move-scope.json'),
         'future_preview_native_budget': {'preparation_version': 1, 'preparation_drc': 1,
                                         'formal_baseline_drc': 1, 'formal_candidate_drc': 1},
         'old_f1': 'failed attempt retained; F2 covers completed service / lost receiver reply only',
         'not_covered': ['real process interruption recovery', 'write-before-save interruption', 'power loss']})
    return {'stage': 'prepare', 'cycle': str(cycle), 'preparation': identity(cycle / 'PREPARATION.json')}

def freeze(args):
    cycle = Path(args.cycle).resolve(strict=True)
    assert cycle.parent == ROUND.resolve() and not (cycle / 'driver-state').exists()
    assert not list(cycle.glob('*-STOP.json'))
    assert files(cycle / 'R4 回复丢失 工程') == files(INPUT)
    argv = [str(EXE), 'board', 'footprint', 'move', '--board',
            str(cycle / 'R4 回复丢失 工程/edit.kicad_pcb'), '--moves', MOVES]
    plan = {'schema_version': 1, 'case_id': CASE_ID, 'at': now(), 'cycle': str(cycle),
            'binding': binding(cycle), 'engineering_before': engineering(cycle), 'argv': argv,
            'request': {'reference': 'R4', 'uuid': '6c5beb16-fbc7-5af2-918d-da26a4b5251c',
                        'from_nm': [100000000, 110000000], 'to_nm': [99500000, 110000000],
                        'delta_nm': [-500000, 0], 'moves': MOVES, 'idempotency_key': KEY},
            'selected_ids': [CASE_ID],
            'requirements': ['one normal preview, engineering unchanged', 'same original operation/token/request',
                 'one natural complete confirm privately recorded', 'external caller EOF / zero service response bytes',
                 'public query original receipt in new process, no engineering/table/usage write',
                 'spent same-token/same-key confirmation refuses, no second engineering/table/usage write',
                 'new independent native saved-board readback, expected geometry and protected bytes',
                 'exact qualification only for historical native DRC/full-image reuse'],
            'transport_ast_names': sorted(TRANSPORT_NAMES),
            'transport_ast_constant': {'MOVE_REFUSAL_EXITS': [2, 3, 4, 6, 7]},
            'receiver_ast_names': ['private_path_in_caller_input'],
            'path_semantics': {'cycle_project_state_config': 'execution identity; cannot relocate after freeze',
                 'calls_and_private_evidence': 'delivery location only, never service request inputs',
                 'temporary_preflight_paths': 'generated by one preview, then frozen by exact manifest'},
            'automatic_retry': False, 'old_f1_removed_from_history': False,
            'real_process_interruption_qualification': 'still not proved by F2'}
    put(cycle / 'ORIGINAL-PLAN.json', plan)
    return {'stage': 'freeze', 'plan': identity(cycle / 'ORIGINAL-PLAN.json')}

def preview(args):
    cycle, plan = original_plan(args)
    assert_binding(cycle, plan['binding'])
    assert engineering(cycle) == plan['engineering_before']
    put(cycle / 'PREVIEW-START.json', {'at': now(), 'original_plan': identity(args.plan), 'dispatch_budget': 1})
    trace, value = call(cycle, 'preview', [*plan['argv'], '--dry-run', '--compact'])
    assert value['ok'] is True and trace['exit'] == 0
    data = value['data']
    assert data['preview']['extensions']['preflight']['status'] == 'passed'
    assert data['preview']['extensions']['preflight']['candidate_sha256'] == SAVED_SHA
    preflight_reports = current_preflight_reports(data['preview']['extensions']['preflight'])
    assert engineering(cycle) == plan['engineering_before']
    assert_binding(cycle, plan['binding'])
    counts = process_counts(trace)
    assert counts['official_cli_instances'] == 4, counts
    put(cycle / 'PREVIEW-FINISH.json', {'at': now(), 'status': 'passed',
            'original_plan_sha256': sha(args.plan), 'response': identity(cycle / 'calls/preview/stdout'),
            'operation_id': data['preview']['operation_id'], 'engineering_unchanged': True,
            'counts': counts, 'whole_job': trace['job_finalization'], 'preflight_reports': preflight_reports})
    return {'stage': 'preview', 'operation_id': data['preview']['operation_id'], 'status': 'passed'}

def freeze_confirm(args):
    cycle, plan = original_plan(args)
    assert_binding(cycle, plan['binding'])
    assert engineering(cycle) == plan['engineering_before']
    finish = read(cycle / 'PREVIEW-FINISH.json')
    assert finish['status'] == 'passed' and finish['original_plan_sha256'] == sha(args.plan)
    assert identity(cycle / 'calls/preview/stdout') == finish['response']
    assert sha(cycle / 'calls/preview/stdout') == read(cycle / 'calls/preview/TRACE.json')['stdout_sha256']
    data = read(cycle / 'calls/preview/stdout')['data']
    preview = data['preview']
    current = rows(cycle)
    assert current['operations'][0]['operation_id'] == preview['operation_id']
    assert current['operations'][0]['status'] == 'planned'
    assert not current['consumed_tokens'] and not current['scope_usage']
    preflight = preview['extensions']['preflight']
    assert current_preflight_reports(preflight) == finish['preflight_reports']
    workspace = Path(preflight['workspace']).resolve(strict=True)
    assert workspace.parent == (cycle / 'driver-state/move-preflights').resolve(strict=True)
    request = [*plan['argv'], '--confirm', data['confirm_token'], '--idempotency-key', KEY, '--compact']
    frozen = {'at': now(), 'case_id': CASE_ID, 'cycle': str(cycle),
            'original_plan': identity(args.plan), 'binding': plan['binding'],
            'preview_response': identity(cycle / 'calls/preview/stdout'),
            'operation_id': preview['operation_id'], 'request_argv': request,
            'request_sha256': hashlib.sha256(json.dumps(request, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest(),
            'planned_rows': current, 'engineering_before': plan['engineering_before'],
            'preflight_workspace': str(workspace), 'preflight_files': files(workspace, metadata=True),
            'preflight_input_binding': preflight['input_binding'],
            'full_preview': preview, 'expected_candidate_sha256': SAVED_SHA,
            'relay_dispatch_budget': 1, 'automatic_retry': False}
    put(cycle / 'CONFIRM-PLAN.json', frozen)
    return {'stage': 'freeze-confirm', 'plan': identity(cycle / 'CONFIRM-PLAN.json'),
            'operation_id': preview['operation_id']}

def confirm_plan(args):
    cycle, frozen = original_plan(args)
    assert_binding(cycle, frozen['binding'])
    original = frozen['original_plan']
    assert sha(original['path']) == original['sha256']
    assert sha(frozen['preview_response']['path']) == frozen['preview_response']['sha256']
    assert engineering(cycle) == frozen['engineering_before']
    assert files(Path(frozen['preflight_workspace']), metadata=True) == frozen['preflight_files']
    assert rows(cycle) == frozen['planned_rows']
    return cycle, frozen

def execute_confirm(args):
    cycle, frozen = confirm_plan(args)
    put(cycle / 'CONFIRM-START.json', {'at': now(), 'confirm_plan': identity(args.plan),
                                     'dispatch_budget': 1, 'automatic_retry': False})
    _, _, WindowsJob = observers()
    relay_path = ROUND / 'receiver-method/response_relay.py'
    relay = types.ModuleType('f2_external_response_relay')
    relay.__file__ = str(relay_path)
    sys.modules[relay.__name__] = relay
    exec(compile(relay_path.read_bytes(), str(relay_path), 'exec'), relay.__dict__)
    caller_env = {k: v for k, v in stable_environment().items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC', 'TEMP', 'TMP',
        'PYTHONDONTWRITEBYTECODE', 'PYTHONIOENCODING'}}
    spec = relay.CallerSpec(str(PYTHON), sha(PYTHON), str(ROUND / 'receiver-method/receiver_probe.py'),
             sha(ROUND / 'receiver-method/receiver_probe.py'), frozen['operation_id'],
             frozen['request_sha256'], str(cycle / 'caller-work'), caller_env)
    (cycle / 'caller-work').mkdir(exist_ok=False)
    path_policy = receiver_path_policy()
    decoded_inputs = [*spec.command(), *[item for pair in caller_env.items() for item in pair]]
    private_directories = [cycle, cycle / 'calls', cycle / 'driver-state',
                           cycle / 'private-caller-evidence', cycle / 'private-relay-evidence']
    no_private_paths = all(not path_policy(value, private_directories, cycle / 'caller-work')
                           for value in decoded_inputs)
    put(cycle / 'CALLER-ISOLATION.json', {'at': now(), 'decoded_argv_and_environment_checked': True,
            'source': identity(ROUND / 'receiver-method/validate_receiver.py'),
            'extracted_unchanged_function': 'private_path_in_caller_input',
            'caller_own_empty_cwd_is_only_exception': True,
            'private_service_or_evidence_paths_in_inputs': not no_private_paths,
            'raw_service_response_in_inputs': False})
    assert no_private_paths, 'Private service evidence path would reach simulated caller'
    caller = relay.OwnedCaller(spec, cycle / 'private-caller-evidence', WindowsJob)
    def execute_once():
        # No output or receipt is returned to caller. This descriptor is written
        # only to the controller's separate private evidence channel.
        confirm_plan(args)
        trace, response = call(cycle, 'confirm', frozen['request_argv'])
        assert response['ok'] is True and trace['exit'] == 0
        assert response['data']['receipt']['operation_id'] == frozen['operation_id']
        assert process_counts(trace)['official_cli_instances'] == 0
        return {'actual_exit': trace['exit'], 'whole_job': trace['job_finalization'],
                'response': identity(cycle / 'calls/confirm/stdout'),
                'raw_response_given_to_caller': False, 'counts': process_counts(trace)}
    result = relay.lose_one_response(caller=caller, execute_service_once=execute_once,
            private_output=cycle / 'private-relay-evidence',
            binding={'case_id': CASE_ID, 'confirm_plan': identity(args.plan),
                     'operation_id': frozen['operation_id'], 'request_sha256': frozen['request_sha256']})
    assert result['dispatches'] == 1 and result['service_callback_returned']
    assert result['service_callback_error'] is None and result['response_lost_at_caller']
    assert sha(cycle / 'R4 回复丢失 工程/edit.kicad_pcb') == SAVED_SHA
    durable = rows(cycle)
    record = durable['operations'][0]
    receipt = json.loads(record['receipt'])
    assert record['operation_id'] == frozen['operation_id'] and record['status'] == 'succeeded'
    assert record['lease_until'] is None and record['resolution'] is None
    assert receipt == read(cycle / 'calls/confirm/stdout')['data']['receipt']
    assert receipt['plan_id'] == frozen['full_preview']['plan_id']
    assert receipt['authorization_scope_id'] == read(cycle / 'move-scope.json')['scope_id']
    assert receipt['replayed'] is False
    assert receipt['outcome']['execution'] == 'completed' and receipt['outcome']['persistence'] == 'saved'
    assert receipt['outcome']['change'] == 'applied' and receipt['outcome']['verification']['status'] == 'passed'
    assert len(durable['consumed_tokens']) == 1 and len(durable['scope_usage']) == 1
    assert durable['scope_usage'][0]['used'] == 1
    put(cycle / 'CONFIRM-FINISH.json', {'at': now(), 'status': 'passed',
          'confirm_plan_sha256': sha(args.plan), 'operation_id': frozen['operation_id'],
          'engineering_saved': engineering(cycle), 'durable_rows': durable,
          'state_files_saved': files(cycle / 'driver-state', metadata=True),
          'durable_receipt': receipt, 'relay_result': identity(cycle / 'private-relay-evidence/RELAY-FINISH.json'),
          'service_completed_naturally': True, 'simulated_caller_response_bytes': 0,
          'coverage': 'completed service / receiver lost reply; not killed-process recovery'})
    return {'stage': 'execute-confirm', 'status': 'passed', 'operation_id': frozen['operation_id']}

def verify(args):
    cycle, frozen = original_plan(args)
    assert_binding(cycle, frozen['binding'])
    completed = read(cycle / 'CONFIRM-FINISH.json')
    assert completed['status'] == 'passed' and completed['confirm_plan_sha256'] == sha(args.plan)
    assert engineering(cycle) == completed['engineering_saved']
    assert rows(cycle) == completed['durable_rows']
    initial_state_observation = state_after_observe(cycle, completed['state_files_saved'])
    current_reports = current_preflight_reports(frozen['full_preview']['extensions']['preflight'])
    assert sha(cycle / 'R4 回复丢失 工程/edit.kicad_pcb') == current_reports['candidate']['board']['sha256']
    query_trace, queried = call(cycle, 'query', [str(EXE), 'operation', 'show', '--id', frozen['operation_id'], '--compact'])
    assert queried['ok'] and queried['data']['operation_status']['receipt'] == completed['durable_receipt']
    assert queried['data']['operation_status']['operation_id'] == frozen['operation_id']
    assert queried['data']['operation_status']['status'] == 'succeeded'
    assert engineering(cycle) == completed['engineering_saved'] and rows(cycle) == completed['durable_rows']
    query_state_observation = state_after_observe(cycle, completed['state_files_saved'])
    assert process_counts(query_trace)['official_cli_instances'] == 0
    spent_trace, spent = call(cycle, 'spent-confirm', frozen['request_argv'], allowed=(6,))
    assert not spent['ok'] and spent['error']['code'] == 'E_CONFLICT'
    assert spent['error']['details']['conflict'] == 'token_used'
    assert spent['error']['details']['dispatch_state'] == 'not_started'
    assert engineering(cycle) == completed['engineering_saved'] and rows(cycle) == completed['durable_rows']
    spent_state_observation = state_after_observe(cycle, completed['state_files_saved'])
    assert process_counts(spent_trace)['official_cli_instances'] == 0
    native_argv = [str(NATIVE / 'bin/python.exe'), '-B', '-S',
                   str(SOURCE / 'bench/saved_scene_native_read.py'),
                   str(cycle / 'R4 回复丢失 工程/edit.kicad_pcb')]
    native_trace, native = call(cycle, 'independent-saved-sdk', native_argv, public=False)
    assert native['input_unchanged'] and native['input_sha256'] == SAVED_SHA and native['native_version'] == '10.0.6'
    assert engineering(cycle) == completed['engineering_saved'] and rows(cycle) == completed['durable_rows']
    native_state_observation = state_after_observe(cycle, completed['state_files_saved'])
    baseline = read(OLD / 'native-affected-cycle-03/baseline-calls/baseline-sdk/stdout')
    assert native['startup'] == baseline['startup']
    assert native['reader_sha256'] == baseline['reader_sha256']
    assert native['pcbnew_wrapper_sha256'] == baseline['pcbnew_wrapper_sha256']
    expected = copy.deepcopy(baseline['footprints'])
    r4 = next(row for row in expected if row['ref'] == 'R4')
    assert r4['position_nm'] == [100000000, 110000000] and not r4['locked']
    r4['position_nm'][0] -= 500000
    for pad in r4['pads']:
        pad['position_nm'][0] -= 500000
    assert native['footprints'] == expected
    for key in ('tracks', 'zones', 'drawings'):
        assert native[key] == baseline[key]
    # A full byte identity to the previously independently checked saved R4
    # scene protects properties not represented by this semantic consumer.
    original = read(cycle / 'PREPARATION.json')['original_files']
    saved_files = files(cycle / 'R4 回复丢失 工程')
    expected_files = copy.deepcopy(original)
    expected_files['edit.kicad_pcb'] = dict(expected_files['edit.kicad_pcb'], sha256=SAVED_SHA,
                                          size=(OLD / 'native-affected-cycle-03/positive/edit.kicad_pcb').stat().st_size)
    assert saved_files == expected_files
    assert_binding(cycle, frozen['binding'])
    reuse = qualify_reuse(cycle)
    put(cycle / 'VERIFY-FINISH.json', {'at': now(), 'status': 'passed', 'case_id': CASE_ID,
          'confirm_plan_sha256': sha(args.plan), 'operation_id': frozen['operation_id'],
          'query_receipt_exact': True, 'query_engineering_rows_usage_unchanged': True,
          'sqlite_physical_observations': {'before_query': initial_state_observation,
              'after_query': query_state_observation, 'after_spent_confirmation': spent_state_observation,
              'after_native_readback': native_state_observation},
          'spent_confirmation': 'E_CONFLICT/token_used; no second engineering or logical DB mutation',
          'native_saved_readback': identity(cycle / 'calls/independent-saved-sdk/stdout'),
          'geometry': {'R4_position_nm': [99500000, 110000000], 'pad_world_x_nm': [98587500, 100412500],
                       'y_nm': 110000000, 'rotation_deg': 0, 'layer': 'F.Cu',
                       'uuid_nets_schematic_paths_preserved': True},
          'protection': 'Other 11 footprints, all pad non-position attributes, copper, zones, graphics and all 7 non-board files unchanged',
          'reuse': reuse, 'old_f1': 'failed original attempt retained; actual interruption qualification not supplied by F2',
          'current_preview_official_reports': current_reports,
          'current_candidate_report_reuse': 'saved board is exactly the checked candidate; this is pre-save candidate DRC reuse, not a new post-save DRC invocation',
          'new_counts': {'public_EXE_invocations': 4, 'official_cli_preflight_invocations': 4,
                         'independent_native_SDK_readback_invocations': 1, 'new_render_invocations': 0,
                         'new_full_image_views': 0, 'caller_processes': 1},
          'whole_jobs': {'query': query_trace['job_finalization'], 'spent': spent_trace['job_finalization'],
                         'native_readback': native_trace['job_finalization']}})
    return {'stage': 'verify', 'status': 'passed', 'operation_id': frozen['operation_id'],
            'result': identity(cycle / 'VERIFY-FINISH.json')}

def qualify_reuse(cycle):
    old = OLD / 'native-affected-cycle-03'
    for relative in files(INPUT):
        current = cycle / 'R4 回复丢失 工程' / relative
        assert sha(current) == sha(old / 'positive' / relative)
    prepared = read(cycle / 'PREPARATION.json')
    assert prepared['configuration_source_files'] == files(old / 'configuration')
    current_configuration = files(cycle / 'configuration')
    assert current_configuration == prepared['configuration_prepared_files']
    before, after = read(old / 'configuration/10.0/kicad_common.json'), read(cycle / 'configuration/10.0/kicad_common.json')
    expected = copy.deepcopy(before)
    expected['system']['working_dir'] = str(cycle / 'R4 回复丢失 工程')
    assert expected == after
    drc = read(old / 'positive-output/drc.json')
    assert len(drc['violations']) == 0 and len(drc['unconnected_items']) == 7
    assert sorted(row['key'] for row in drc['ignored_checks']) == sorted([
        'missing_courtyard', 'track_not_centered_on_via', 'tuning_profile_track_geometries',
        'footprint_filters_mismatch', 'footprint_type_mismatch'])
    previous = read(old / 'POSITIVE-RESULT.json')
    assert previous['status'] == 'affected_native_consumers_completed_visual_reuse_pending'
    assert previous['checks']['native']['status'] == previous['checks']['drc']['status'] == 'passed'
    visual = read(ROUND / 'RENDER-REUSE-QUALIFICATION.json')
    assert visual['status'] == 'qualified_historical_reuse'
    assert visual['exact_full_image_associations_unchanged'] and visual['method_files'] == 36
    assert visual['dependency_groups'] == 30 and visual['new_image_view'] == visual['new_render'] == 0
    visual_parent = read(ROUND / 'controllers/render-reuse-metadata/FINISH.json')
    assert visual_parent['exit'] == 0 and visual_parent['execution'] == 'completed'
    assert visual_parent['whole_job']['natural_tree_complete'] and not visual_parent['whole_job']['controlled_termination']
    return {'status': 'exact saved-board + 7 engineering files + prepared preferences qualification',
            'working_directory_difference': prepared['explicit_pre_freeze_metadata_change'],
            'official_DRC': identity(old / 'positive-output/drc.json'),
            'official_DRC_result': identity(old / 'POSITIVE-RESULT.json'),
            'violations': 0, 'unconnected': 7, 'ignored': 5,
            'full_image_closure': identity(OLD / 'HISTORICAL-RENDER-CLOSURE-01.json'),
            'full_image_new_qualification': identity(ROUND / 'RENDER-REUSE-QUALIFICATION.json'),
            'full_image_closure_asserted_here': 'exact saved engineering identity and parent-qualified retained render closure',
            'full_image_qualification': 'Six historical full views reused after new metadata-only closure audit; no new full-image view or render is claimed.',
            'scope': 'historical evidence reuse, not new DRC/render execution or whole-board sign-off'}

def validate_method(args):
    # This finite pre-run check imports only the frozen external observation
    # methods and extracts the unchanged transport policy. No Job is created,
    # no state is opened and no product/native/pytest process is launched.
    observe, inspect, WindowsJob = observers()
    policy = transport_policy()
    assert callable(observe) and callable(inspect) and callable(WindowsJob)
    assert callable(policy.inspect_candidate_trace) and callable(policy.verified_output)
    assert policy.strict_json(b'{"ok":true}') == {'ok': True}
    try:
        policy.strict_json(b'{"ok":true,"ok":false}')
    except AssertionError:
        pass
    else:
        raise AssertionError('Strict JSON policy did not reject duplicate members')
    method_qualification()
    return {'stage': 'validate-method', 'status': 'passed', 'process_launches_from_check': 0,
            'product_native_or_pytest_collection': 0, 'state_access': 0,
            'transport_ast_names': sorted(TRANSPORT_NAMES),
            'module_level_pytest_and_schema_dependencies_executed': False,
            'raw_source_no_pyc': True}

def main():
    assert sys.dont_write_bytecode and sys.flags.no_site
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['validate-method', 'prepare', 'freeze', 'preview', 'freeze-confirm', 'execute-confirm', 'verify'])
    parser.add_argument('--cycle')
    parser.add_argument('--plan')
    parser.add_argument('--plan-sha256')
    args = parser.parse_args()
    fn = {'validate-method': validate_method, 'prepare': prepare, 'freeze': freeze, 'preview': preview, 'freeze-confirm': freeze_confirm,
          'execute-confirm': execute_confirm, 'verify': verify}[args.stage]
    try:
        result = fn(args)
    except BaseException as exc:
        # No retry/replacement preview/re-freeze after a failed dispatch. Exact
        # artifacts remain; an operation of uncertain state is never confirmed.
        if args.cycle:
            candidate = Path(args.cycle)
        elif args.plan and Path(args.plan).is_file():
            candidate = Path(read(args.plan)['cycle'])
        else:
            candidate = None
        if candidate is not None and candidate.is_dir() and candidate.parent.resolve() == ROUND.resolve():
            stop = candidate / (args.stage + '-STOP.json')
            if not stop.exists():
                put(stop, {'at': now(), 'stage': args.stage, 'type': type(exc).__name__,
                           'message': str(exc), 'automatic_retry': False, 'resumable_write': False})
        raise
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
