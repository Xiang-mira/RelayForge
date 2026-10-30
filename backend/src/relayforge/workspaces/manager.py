import difflib
import shutil
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID


class UnsafePath(ValueError):
    pass


def reject_links(path: Path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise UnsafePath("Symbolic links are forbidden in managed workspace paths")
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise UnsafePath("Reparse points are forbidden in managed workspace paths")


class Workspaces:
    def __init__(self, root: Path):
        reject_links(root)
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def branch_path(self, branch_id: str) -> Path:
        if str(UUID(branch_id)) != branch_id:
            raise UnsafePath("Canonical branch UUID required")
        path = self.root / branch_id
        reject_links(path)
        return path

    def file(self, branch_id: str, relative: str) -> Path:
        if not relative or "\\" in relative or ":" in relative or "\x00" in relative:
            raise UnsafePath("Use a normalized relative path")
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or PureWindowsPath(relative).is_absolute()
            or any(part in ("..", ".") for part in relative.split("/"))
        ):
            raise UnsafePath("Path traversal is forbidden")
        if any(part.endswith((".", " ")) for part in pure.parts):
            raise UnsafePath("Ambiguous Windows paths are forbidden")
        if any(
            part.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }
            for part in pure.parts
        ):
            raise UnsafePath("Reserved device name")
        base = self.branch_path(branch_id)
        path = base.joinpath(*pure.parts)
        reject_links(path)
        if not path.resolve().is_relative_to(base.resolve()):
            raise UnsafePath("Path escaped the branch")
        return path

    def create(self, branch_id: str, source: Path) -> Path:
        destination = self.branch_path(branch_id)
        reject_links(source)
        if destination.exists():
            raise FileExistsError("Workspace already exists; never overwritten")
        for path in source.rglob("*"):
            reject_links(path)
        shutil.copytree(source, destination)
        return destination

    def read(self, branch_id: str, relative: str) -> str:
        path = self.file(branch_id, relative)
        if path.stat().st_size > 1_000_000:
            raise ValueError("File exceeds managed text limit")
        return path.read_text(encoding="utf-8")

    def write(self, branch_id: str, relative: str, content: str):
        if len(content.encode("utf-8")) > 1_000_000:
            raise ValueError("File exceeds managed text limit")
        path = self.file(branch_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        reject_links(path)
        path.write_text(content, encoding="utf-8")

    def diff(self, branch_id: str, relative: str, original: str) -> str:
        return "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                self.read(branch_id, relative).splitlines(keepends=True),
                fromfile="before/" + relative,
                tofile="after/" + relative,
            )
        )
