"""Content identity for the files consumed by the managed unittest workspace."""

from pathlib import Path

from relayforge.journal.state import payload_hash
from relayforge.workspaces.manager import reject_links

IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".cache",
    "build",
    "dist",
    "logs",
}


def manifest(root: Path) -> dict[str, str]:
    import hashlib

    reject_links(root)
    if not root.is_dir():
        raise ValueError("Managed test workspace missing")
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRECTORIES for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".pyo", ".log"}:
            continue
        reject_links(path)
        if path.is_file():
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def fingerprint(files: dict[str, str]) -> str:
    # Sorted relative names + raw-byte content hashes include uncommitted changes.
    return payload_hash(files)
