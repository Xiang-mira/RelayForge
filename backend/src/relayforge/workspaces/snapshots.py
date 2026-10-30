import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from relayforge.workspaces.manager import UnsafePath, Workspaces, reject_links


class Snapshots:
    def __init__(self, workspaces: Workspaces):
        self.workspaces = workspaces
        self.root = workspaces.root.parent / "snapshots"
        reject_links(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, identity: str) -> Path:
        if str(UUID(identity)) != identity:
            raise UnsafePath("Canonical snapshot UUID required")
        path = self.root / identity
        reject_links(path)
        return path

    @staticmethod
    def manifest(source: Path) -> dict:
        reject_links(source)
        if not source.is_dir():
            raise UnsafePath("Snapshot directory is missing")
        result = {}
        for path in sorted(source.rglob("*")):
            reject_links(path)
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            if path.name.startswith(".env") or ".git" in path.parts:
                raise UnsafePath("Credential/config files are not snapshot inputs")
            if path.stat().st_size > 1_000_000 or len(result) >= 200:
                raise ValueError("Managed snapshot size limit exceeded")
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def capture(self, branch_id: str) -> str:
        source = self.workspaces.branch_path(branch_id)
        before = self.manifest(source)
        identity = str(uuid4())
        destination = self.path(identity)
        staging = self.path(str(uuid4()))
        (staging / "files").mkdir(parents=True)
        for relative in before:
            target = staging / "files" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        if self.manifest(staging / "files") != before or self.manifest(source) != before:
            raise ValueError("Workspace changed during snapshot; no checkpoint accepted")
        with (staging / "manifest.json").open("x", encoding="utf-8") as stream:
            json.dump(before, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        staging.rename(destination)
        return identity

    def verified_source(self, identity: str) -> Path:
        root = self.path(identity)
        manifest_path = root / "manifest.json"
        reject_links(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = root / "files"
        if self.manifest(files) != manifest:
            raise ValueError("Snapshot manifest mismatch; restore refused")
        return files

    def restore(self, identity: str, branch_id: str):
        self.workspaces.create(branch_id, self.verified_source(identity))
