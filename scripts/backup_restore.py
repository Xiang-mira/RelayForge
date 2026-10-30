"""Cold local backups and non-overwriting restore into a new isolated database."""

import argparse
import hashlib
import json
import re
import shutil
import socket
import sqlite3
import subprocess
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from relayforge.config import ROOT, Settings
from relayforge.runtime.worker_lock import LocalWorkerLock
from relayforge.workspaces.docker_env import docker_cli, docker_environment
from relayforge.workspaces.manager import reject_links
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_manifest(root):
    result = {}
    for path in sorted(root.rglob("*")):
        reject_links(path)
        if path.is_file() and path != root / "backup-manifest.json":
            result[path.relative_to(root).as_posix()] = {
                "sha256": digest(path),
                "bytes": path.stat().st_size,
            }
    return result


def table_state(engine):
    with engine.connect() as connection:
        tables = (
            connection.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
                )
            )
            .scalars()
            .all()
        )
        assert tables and all(
            re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in tables
        )
        return {
            name: dict(
                connection.execute(
                    text(
                        f"SELECT count(*) AS rows, "
                        f"md5(coalesce(string_agg(md5(row_to_json(t)::text), '' ORDER BY md5(row_to_json(t)::text)), '')) AS content_digest "
                        f'FROM "{name}" AS t'
                    )
                )
                .mappings()
                .one()
            )
            for name in tables
        }


def engine_for(url):
    return create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})


def require_stopped(settings, stack):
    if (ROOT / ".local/processes.json").exists():
        raise RuntimeError(
            "Stop owned services with stop.ps1 -KeepDatabase before cold backup/restore"
        )
    for port in [settings.api_port, 5173]:
        with socket.socket() as connection:
            connection.settimeout(0.3)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(
                    "API/frontend still reachable; cold operation refused"
                )
    for name in ["supervisor", "evaluation", "worker"]:
        stack.enter_context(LocalWorkerLock(ROOT / f".local/{name}.lock"))


def docker_pg(settings, arguments, *, output=None, input_stream=None):
    url = make_url(settings.database_url.get_secret_value())
    cli = docker_cli()
    result = subprocess.run(
        [cli, "compose", "exec", "-T", "postgres", *arguments, "-U", url.username],
        cwd=ROOT,
        env=docker_environment(cli),
        stdin=input_stream,
        stdout=output if output is not None else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Database archive command failed; partial local artifacts preserved"
        )


def safe_copy(source, destination):
    reject_links(source)
    if not source.is_dir():
        raise RuntimeError("Required runtime directory is missing")
    for path in source.rglob("*"):
        reject_links(path)
    shutil.copytree(source, destination)


def backup(settings, identity):
    destination = ROOT / ".local/backups" / identity
    reject_links(destination)
    destination.mkdir(parents=True, exist_ok=False)
    url = make_url(settings.database_url.get_secret_value())
    engine = engine_for(url)
    try:
        before = table_state(engine)
        with (destination / "database.dump").open("xb") as output:
            docker_pg(
                settings,
                ["pg_dump", "-Fc", "--no-owner", "--no-privileges", "-d", url.database],
                output=output,
            )
        for name in ["workspaces", "snapshots", "workspace-receipts"]:
            source = (
                settings.workspace_root
                if name == "workspaces"
                else settings.workspace_root.parent / name
            )
            safe_copy(source, destination / "runtime" / name)
        truth = destination / "runtime/demo-data/external.sqlite3"
        truth.parent.mkdir(parents=True)
        with (
            closing(
                sqlite3.connect(
                    f"{settings.mock_external_path.as_uri()}?mode=ro", uri=True
                )
            ) as source,
            closing(sqlite3.connect(truth)) as target,
        ):
            source.backup(target)
            target.commit()
            assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        if (ROOT / "artifacts/evaluations").exists():
            safe_copy(ROOT / "artifacts/evaluations", destination / "evaluations")
        if before != table_state(engine):
            raise RuntimeError(
                "Database changed during cold backup; archive not accepted"
            )
        manifest = {
            "format": 1,
            "backup_id": identity,
            "created_at": datetime.now(UTC).isoformat(),
            "postgres_major": engine.dialect.server_version_info[0],
            "table_state": before,
            "files": tree_manifest(destination),
            "scope": "PostgreSQL business/checkpoints, managed workspaces/snapshots/receipts, independent mock truth, evaluation artifacts; excludes .env/source/tools/images",
        }
        (destination / "backup-manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        return {
            "backup_id": identity,
            "path": str(destination),
            "files": len(manifest["files"]),
            "tables": len(before),
            "bytes": sum(row["bytes"] for row in manifest["files"].values()),
        }
    finally:
        engine.dispose()


def restore(settings, backup_id, identity):
    source = ROOT / ".local/backups" / backup_id
    reject_links(source)
    manifest = json.loads((source / "backup-manifest.json").read_text(encoding="utf-8"))
    if manifest["format"] != 1 or manifest["backup_id"] != backup_id:
        raise RuntimeError("Unsupported backup manifest")
    for relative in manifest["files"]:
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or ".." in pure.parts
            or "\\" in relative
            or ":" in relative
        ):
            raise RuntimeError("Invalid backup path")
    if tree_manifest(source) != manifest["files"]:
        raise RuntimeError("Backup integrity check failed; no restore attempted")
    destination = ROOT / ".local/restores" / identity
    reject_links(destination)
    destination.mkdir(parents=True, exist_ok=False)
    target_name = "relayforge_restore_" + UUID(identity).hex
    assert re.fullmatch(r"relayforge_restore_[0-9a-f]{32}", target_name)
    original = make_url(settings.database_url.get_secret_value())
    admin = engine_for(original)
    restored = None
    try:
        original_state = table_state(admin)
        with admin.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            if connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"),
                {"name": target_name},
            ):
                raise RuntimeError("Restore database already exists; never overwritten")
            if admin.dialect.server_version_info[0] != manifest["postgres_major"]:
                raise RuntimeError(
                    "Restore requires the matching PostgreSQL major version"
                )
            connection.execute(text(f'CREATE DATABASE "{target_name}"'))
        with (source / "database.dump").open("rb") as input_stream:
            docker_pg(
                settings,
                [
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "-d",
                    target_name,
                ],
                input_stream=input_stream,
            )
        safe_copy(source / "runtime", destination / "runtime")
        if (source / "evaluations").exists():
            safe_copy(source / "evaluations", destination / "evaluations")
        restored = engine_for(original.set(database=target_name))
        if table_state(restored) != manifest["table_state"]:
            raise RuntimeError("Restored table contents do not match the cold backup")
        for relative, row in manifest["files"].items():
            if relative == "database.dump":
                continue
            copied = destination / relative
            if digest(copied) != row["sha256"] or copied.stat().st_size != row["bytes"]:
                raise RuntimeError("Restored runtime files do not match backup")
        with closing(
            sqlite3.connect(destination / "runtime/demo-data/external.sqlite3")
        ) as truth:
            assert truth.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with restored.connect() as connection:
            # Read real opaque receipts and checkpoint rows from the independent target.
            confirmed = connection.scalar(
                text(
                    "SELECT count(*) FROM effects WHERE status='CONFIRMED' AND external_receipt IS NOT NULL"
                )
            )
            checkpoints = connection.scalar(text("SELECT count(*) FROM checkpoints"))
        if table_state(admin) != original_state:
            raise RuntimeError(
                "Active database changed during isolated restore; verification failed"
            )
        report = {
            "restore_id": identity,
            "backup_id": backup_id,
            "database": target_name,
            "runtime_root": str(destination / "runtime"),
            "table_counts_match": True,
            "table_contents_match": True,
            "runtime_hashes_match": True,
            "mock_truth_integrity": "ok",
            "confirmed_receipts": confirmed,
            "checkpoints": checkpoints,
            "production_database_untouched": True,
        }
        (destination / "restore-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report
    finally:
        if restored is not None:
            restored.dispose()
        admin.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--backup-id")
    args = parser.parse_args()
    if args.action == "restore" and not args.backup_id:
        parser.error("restore requires --backup-id")
    if args.backup_id and str(UUID(args.backup_id)) != args.backup_id:
        parser.error("Canonical backup UUID required")
    settings = Settings()
    with ExitStack() as stack:
        require_stopped(settings, stack)
        identity = str(uuid4())
        report = (
            backup(settings, identity)
            if args.action == "backup"
            else restore(settings, args.backup_id, identity)
        )
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    except (
        SQLAlchemyError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        ValueError,
        KeyError,
        AssertionError,
    ) as exc:
        raise SystemExit(
            f"Cold operation failed ({type(exc).__name__}); partial artifacts preserved, no success claimed"
        ) from None
