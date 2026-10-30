"""Bounded persistent MVP demo: real workers, local mock only, no model calls.

Run with ordinary services started using -NoWorker. Never modifies old runs.
The child outage is a local query fault; write/Journal/gate paths are unmodified.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from relayforge.config import ROOT, Settings
from relayforge.db import Database
from relayforge.journal.service import Journal
from relayforge.journal.state import Conflict, logical_action_id
from relayforge.models import Effect, RecoveryDecision, Run, RunEvent
from relayforge.providers.mock_external import MockExternal
from relayforge.providers.workspace_tools import WorkspaceTools
from relayforge.runtime.checkpoints import postgres_checkpointer
from relayforge.runtime.executor import ApprovalRequired, ToolExecutor
from relayforge.runtime.forks import Forks
from relayforge.runtime.tasks import Tasks
from relayforge.runtime.worker_lock import LocalWorkerLock
from relayforge.workspaces.fingerprint import manifest
from relayforge.workspaces.manager import Workspaces


def event(db, run_id, kind, data):
    with db.sessions.begin() as session:
        session.add(RunEvent(run_id=run_id, type=kind, data=data))


def child(run_id, outage):
    settings = Settings()
    if settings.model_provider != 'mock':
        raise RuntimeError('Demo requires the existing mock coding provider')
    db = Database(settings)
    with db.sessions() as session:
        marker = session.scalar(select(RunEvent).where(
            RunEvent.run_id == run_id, RunEvent.type == 'recovery.demo_created'))
        if not marker:
            raise RuntimeError('Child may operate only on its own demo run')
    event(db, run_id, 'recovery.demo_process', {'pid': os.getpid(), 'query_outage': outage})
    if outage:
        def unavailable(*args, **kwargs):
            raise TimeoutError('Local simulated status-query outage')
        MockExternal.lookup = unavailable
        event(db, run_id, 'fault.query_unavailable', {'scope': 'local_mock_lookup_only'})
    db.close()
    from relayforge.worker import main
    sys.argv = ['relayforge.worker', '--once', '--run-id', run_id]
    main()  # Includes the real os._exit(86) after the mock commits.


def demonstrate(resume=None):
    settings = Settings()
    if settings.model_provider != 'mock':
        raise RuntimeError('Refusing to invoke a configured real model')
    ledger = ROOT / '.local/processes.json'
    if ledger.exists() and any(p['Role'] == 'worker' for p in json.loads(ledger.read_text(encoding='utf-8-sig'))):
        raise RuntimeError('Stop owned services and restart scripts/start.ps1 -NoWorker first')
    db = Database(settings)
    if not db.ready():
        raise RuntimeError('PostgreSQL unavailable')
    workspaces = Workspaces(settings.workspace_root)
    executor = ToolExecutor(Journal(db.sessions), MockExternal(settings.mock_external_path), WorkspaceTools(workspaces))
    tasks = Tasks(db.sessions, workspaces, ROOT / 'demo/sample_repo')
    directory = Path(resume).resolve() if resume else ROOT / '.local/recovery-demos' / str(uuid4())
    if resume:
        if directory.parent != (ROOT / '.local/recovery-demos').resolve():
            raise ValueError('Resume requires a project-owned recovery demo directory')
        report = json.loads((directory / 'observations.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        if report.get('result') == 'PASSED':
            print(json.dumps({'result': 'ALREADY_VERIFIED', 'directory': str(directory)}))
            return
    else:
        directory.mkdir(parents=True)
        report = {'started_at': datetime.now(UTC).isoformat(), 'scope': 'New deterministic mock runs, not model experiments or the historical 360', 'cases': {}}

    def save():
        # Append-only observations retain even an interrupted demo's evidence.
        with (directory / 'observations.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(report, ensure_ascii=False) + '\n')

    def process(run_id, label, *, outage=False, expected=0):
        command = [sys.executable, str(Path(__file__).resolve()), '--child-run', run_id]
        if outage:
            command.append('--query-outage')
        log_path = directory / (label + '.log')
        if log_path.exists():
            log_path = directory / (label + '-' + str(uuid4()) + '.log')
        with log_path.open('x', encoding='utf-8') as log:
            completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=120, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if completed.returncode != expected:
            raise RuntimeError(f'{label} exited {completed.returncode}, expected {expected}; retained logs in {directory}')
        return completed.returncode

    def observe(run_id):
        with db.sessions() as session:
            run = session.get(Run, run_id)
            effect = session.scalar(select(Effect).where(Effect.run_id == run_id, Effect.logical_action_id == logical_action_id('publish')))
            decisions = session.scalars(select(RecoveryDecision).where(RecoveryDecision.effect_id == effect.effect_id).order_by(RecoveryDecision.created_at)).all()
            processes = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.type == 'recovery.demo_process').order_by(RunEvent.id)).all()
            with sqlite3.connect(settings.mock_external_path.as_uri() + '?mode=ro', uri=True) as truth:
                n = truth.execute('SELECT count(*) FROM requests WHERE action_key=?', (effect.idempotency_key,)).fetchone()[0]
                rows = truth.execute('SELECT external_id FROM operations WHERE action_key=?', (effect.idempotency_key,)).fetchall()
            return {'run_id': run.id, 'branch_id': run.branch_id, 'run_status': run.status, 'effect_id': effect.effect_id, 'action_id': effect.logical_action_id, 'effect_status': effect.status, 'receipt': effect.external_receipt, 'attempt_count': effect.attempt_count, 'method': effect.reconciliation_method, 'requests': n, 'operations': len(rows), 'external_ids': [r[0] for r in rows], 'processes': [e.data for e in processes], 'decisions': [{'id': d.id, 'decision': d.decision, 'evidence': d.evidence} for d in decisions]}

    with LocalWorkerLock(ROOT / '.local/worker.lock'):
        for name in ('A', 'B'):
            if name in report['cases']:
                continue
            _, run_id = tasks.create('MVP recovery ' + name + ' (local deterministic demo)', fault='after_external_before_receipt')
            event(db, run_id, 'recovery.demo_created', {'demo_id': directory.name, 'scenario': name, 'not_model_driven': True})
            report['cases'][name] = {'run_id': run_id}
    save()
    for name in ('A', 'B'):
        run_id = report['cases'][name]['run_id']
        if 'after_recovery' in report['cases'][name]:
            continue
        before = report['cases'][name].get('before_recovery')
        if before is None:
            process(run_id, name + '-crash', expected=86)
            before = observe(run_id)
            assert before['run_status'] == 'RUNNING' and before['effect_status'] == 'IN_FLIGHT'
            assert before['receipt'] is None and before['requests'] == before['operations'] == 1
            report['cases'][name]['before_recovery'] = before
            event(db, run_id, 'recovery.demo_observation', {'phase': 'after_process_exit_before_recovery', **before})
            save()
        process(run_id, name + '-recover', outage=name == 'B')
        after = observe(run_id)
        assert after['requests'] == after['operations'] == after['attempt_count'] == 1
        assert after['processes'][-1]['pid'] != before['processes'][-1]['pid']
        if name == 'A':
            assert after['run_status'] == 'SUCCEEDED' and after['effect_status'] == 'CONFIRMED'
            assert after['method'] == 'provider_query' and after['receipt']['external_id'] == before['external_ids'][0]
        else:
            assert after['run_status'] == 'NEEDS_REVIEW' and after['effect_status'] == 'UNKNOWN'
            assert after['receipt'] is None and after['method'] == 'provider_query_unavailable'
        report['cases'][name]['after_recovery'] = after
        event(db, run_id, 'recovery.demo_observation', {'phase': 'after_recovery', **after})
        save()

    parent = report['cases']['A']['after_recovery']
    with LocalWorkerLock(ROOT / '.local/worker.lock'):
        args = (parent['run_id'], parent['branch_id'], parent['action_id'], 'mock.create')
        receipt = executor.execute(*args, {'subject': 'sample-repair-result'})
        assert receipt == parent['receipt']
        try:
            executor.execute(*args, {'subject': 'different-intent'})
        except Conflict:
            pass
        else:
            raise AssertionError('Changed parameters were not refused')
        original_manifest = manifest(workspaces.branch_path(parent['branch_id']))
        with postgres_checkpointer(settings) as saver:
            forks = Forks(executor, saver)
            checkpoint = next(c for c in forks.history(parent['run_id']) if c['stage'] == 'inspect')
            fork_id = forks.create(parent['run_id'], checkpoint['checkpoint_id'])
        event(db, fork_id, 'recovery.demo_created', {'demo_id': directory.name, 'scenario': 'C', 'not_model_driven': True})
    process(fork_id, 'C-fork')
    with db.sessions() as session, LocalWorkerLock(ROOT / '.local/worker.lock'):
        fork = session.get(Run, fork_id)
        assert fork.status == 'SUCCEEDED' and fork.branch_id != parent['branch_id']
        assert workspaces.branch_path(fork.branch_id).resolve() != workspaces.branch_path(parent['branch_id']).resolve()
        assert manifest(workspaces.branch_path(parent['branch_id'])) == original_manifest
        assert session.scalar(select(Effect).where(Effect.run_id == fork_id, Effect.logical_action_id == parent['action_id'])) is None
        try:
            executor.execute(fork_id, fork.branch_id, str(uuid4()), 'mock.create', {'subject': 'new-branch-action'})
        except ApprovalRequired:
            pending = session.scalar(select(Effect).where(Effect.run_id == fork_id, Effect.tool_name == 'mock.create'))
            assert pending.status == 'PREPARED' and pending.attempt_count == 0
            assert executor.external.request_count(pending.idempotency_key) == 0
        else:
            raise AssertionError('New fork write bypassed approval')
        report['cases']['C'] = {'run_id': fork_id, 'branch_id': fork.branch_id, 'parent_run_id': parent['run_id'], 'checkpoint_id': checkpoint['checkpoint_id'], 'duplicate_reused_receipt': receipt, 'changed_parameters': 'CONFLICT', 'parent_workspace_unchanged': True, 'new_action_effect_id': pending.effect_id, 'new_action_state': 'PREPARED', 'new_action_requests': 0}
    assert observe(parent['run_id'])['requests'] == observe(parent['run_id'])['operations'] == 1
    report['completed_at'] = datetime.now(UTC).isoformat()
    report['result'] = 'PASSED'
    save()
    with (directory / 'verified.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'result': 'PASSED', 'directory': str(directory), 'run_ids': {k: v['run_id'] for k, v in report['cases'].items()}}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--child-run', help=argparse.SUPPRESS)
    parser.add_argument('--query-outage', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--resume', help='Continue an interrupted project-owned demo without recreating completed runs')
    options = parser.parse_args()
    if options.child_run:
        child(options.child_run, options.query_outage)
    else:
        demonstrate(options.resume)
