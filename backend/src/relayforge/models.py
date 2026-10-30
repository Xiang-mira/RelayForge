from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from relayforge.db import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    template: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("branches.id"))
    checkpoint_id: Mapped[str | None] = mapped_column(String(200))
    initial_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    workspace: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (CheckConstraint("mode IN ('baseline','journal','full')", name="ck_run_mode"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    fault: Mapped[str] = mapped_column(String(80), default="none")
    mode: Mapped[str] = mapped_column(String(24), default="full", server_default="full")
    execution_config: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    evaluation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_runs.id"), index=True
    )
    owner: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    test_exit_code: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


    @property
    def execution_source(self):
        if self.execution_config.get("agent_evidence"):
            return "model_client_export"
        if self.execution_config.get("controller") == "mcp":
            return "controlled_tools"
        return "deterministic_evaluation" if self.evaluation_run_id else "preset_task"


class Effect(Base):
    __tablename__ = "effects"
    __table_args__ = (
        UniqueConstraint("branch_id", "logical_action_id", name="uq_effect_branch_action"),
        CheckConstraint(
            "status IN ('PREPARED','IN_FLIGHT','CONFIRMED','FAILED','UNKNOWN')",
            name="ck_effect_status",
        ),
        CheckConstraint(
            "effect_class IN ('READ_ONLY','WORKSPACE_MUTATION','IDEMPOTENT_WRITE','NON_IDEMPOTENT_WRITE','UNKNOWN_OUTCOME')",
            name="ck_effect_class",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_effect_attempts"),
    )
    effect_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"), index=True)
    logical_action_id: Mapped[str] = mapped_column(String(100))
    parent_effect_id: Mapped[str | None] = mapped_column(ForeignKey("effects.effect_id"))
    tool_name: Mapped[str] = mapped_column(String(80))
    effect_class: Mapped[str] = mapped_column(String(40))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="PREPARED", index=True)
    external_receipt: Mapped[dict | None] = mapped_column(JSON)
    reconciliation_method: Mapped[str | None] = mapped_column(String(80))
    reconciliation_result: Mapped[dict | None] = mapped_column(JSON)
    policy_decision: Mapped[str] = mapped_column(String(10), default="ask")
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approvals.id", use_alter=True, name="fk_effect_approval")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    effect_id: Mapped[str] = mapped_column(ForeignKey("effects.effect_id"), index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    effect_id: Mapped[str | None] = mapped_column(ForeignKey("effects.effect_id"))
    type: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecoveryDecision(Base):
    __tablename__ = "recovery_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    effect_id: Mapped[str] = mapped_column(ForeignKey("effects.effect_id"), index=True)
    evidence: Mapped[dict] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StreamEvent(Base):
    __tablename__ = "stream_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("run_events.id"), unique=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)


class EffectReference(Base):
    __tablename__ = "effect_references"
    __table_args__ = (UniqueConstraint("branch_id", "effect_id", name="uq_branch_reference"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    branch_id: Mapped[str] = mapped_column(ForeignKey("branches.id"))
    effect_id: Mapped[str] = mapped_column(ForeignKey("effects.effect_id"))


class EvaluationSuite(Base):
    __tablename__ = "evaluation_suites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("evaluation_suites.id"))
    mode: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint("suite_id", "template", "fault", "seed", name="uq_evaluation_case"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    suite_id: Mapped[str] = mapped_column(ForeignKey("evaluation_suites.id"))
    template: Mapped[str] = mapped_column(String(80))
    fault: Mapped[str] = mapped_column(String(80))
    seed: Mapped[int] = mapped_column(Integer)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "case_id", name="uq_evaluation_result"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    evaluation_run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("evaluation_cases.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    observed: Mapped[dict] = mapped_column(JSON)
