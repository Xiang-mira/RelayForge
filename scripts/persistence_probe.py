"""Non-destructive PostgreSQL volume probe; run prepare, restart DB, then verify.

Creates a visibly labelled demo run with an unexecuted PREPARED intent. Does not
invent an external receipt/success, execute code, or delete any user records.
"""
import json
import sys
from pathlib import Path

from relayforge.config import Settings
from relayforge.db import Database
from relayforge.journal.service import Journal
from relayforge.journal.state import EffectClass
from relayforge.models import Branch, Run, Task

root = Path(__file__).resolve().parents[1]
state_path = root / ".local" / "persistence-probe.json"
db = Database(Settings())
try:
    if sys.argv[1] == "prepare":
        with db.sessions.begin() as session:
            task = Task(title="Demo Data: PostgreSQL persistence probe (no tool executed)", template="fix_addition")
            session.add(task)
            session.flush()
            branch = Branch(task_id=task.id)
            session.add(branch)
            session.flush()
            run = Run(task_id=task.id, branch_id=branch.id, status="NEEDS_REVIEW")
            session.add(run)
            session.flush()
            run_id, branch_id = run.id, branch.id
        effect = Journal(db.sessions).prepare(run_id, branch_id, "persistence-probe", "mock.create", EffectClass.IDEMPOTENT_WRITE, {"subject": "unexecuted persistence probe"}, "allow")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"effect_id": effect.effect_id, "run_id": run_id, "payload_hash": effect.payload_hash}), encoding="utf-8")
        print("Persisted PREPARED demo intent; zero tool calls or invented successes")
    elif sys.argv[1] == "verify":
        stored = json.loads(state_path.read_text(encoding="utf-8"))
        effect = Journal(db.sessions).get(stored["effect_id"])
        assert effect.run_id == stored["run_id"]
        assert effect.payload_hash == stored["payload_hash"]
        assert effect.status == "PREPARED" and effect.attempt_count == 0
        report = {"database_restart_persistence": "verified", "effect_id": effect.effect_id,
                  "status": effect.status, "attempt_count": effect.attempt_count}
        output = root / ".local" / "reports" / "persistence-probe.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Fresh-process read verified unchanged intent after database restart")
    else:
        raise ValueError("Use prepare or verify")
finally:
    db.close()
